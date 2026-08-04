"""Locate the actual bubble grid on a scanned sheet, rather than trusting a
template's fixed pixel coordinates.

Why this exists: answer_extractor.template.Template coordinates are
calibrated against one reference render. Real-world inputs -- even a
"born-digital" PDF at a very slightly different page size, let alone an
actual photographed/scanned sheet -- routinely drift from that reference
by enough to land sampling circles on the wrong bubble entirely (observed:
up to a full row off on a real sample, despite page dimensions differing
by under 1%). A plain whole-page resize/perspective-warp doesn't fix this
because the drift isn't a uniform page-wide scale -- it is close to that,
but not exactly, so small residual errors compound at tight ~30px bubble
spacing.

The fix: for each section, detect the actual printed bubble glyphs
(circled letter/digit shapes) within a generous region around where the
template expects that section, cluster them into rows and column-groups
(the same technique used to calibrate templates in the first place, just
run per-sheet instead of once by hand), and snap each expected bubble to
its nearest detected glyph. Occasional individual misses (a heavy/sloppy
mark's ink merging into an oversized contour that fails the glyph size
filter, say) don't sink the whole section: any bubble that can't be
matched directly falls back to its nominal template position corrected by
the section's median observed shift, which is still far more accurate
than an uncorrected nominal position.

Falls back to the template's fixed nominal coordinates (no correction) only
for a section where detection can't establish the expected row structure
at all -- see locate_section_bubbles's return value.

Also observed on a real scan: the opposite of a missing glyph -- a
question-number label (e.g. "34") whose glyph happened to be sized like a
real bubble slipped into a row's group as a 5th item for 4 slots. See
_match_to_slots for how that's handled without displacing the real
(possibly genuinely marked) bubbles next to it.
"""
from __future__ import annotations

import dataclasses
import statistics
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

import cv2
import numpy as np

from .template import Section, Template

_T = TypeVar("_T")

# Bubble glyph size filter is expressed relative to bubble_radius so it
# scales with the template rather than assuming one fixed pixel size.
# Deliberately snug on the low end: printed question-number digits (esp.
# 2-digit numbers like "41") sit just outside a real choice glyph's size
# and must NOT pass this filter, or they get mistaken for a 5th bubble.
_MIN_SIZE_RATIO = 2.05
_MAX_SIZE_RATIO = 3.2
_MIN_HEIGHT_RATIO = 1.5
_MAX_HEIGHT_RATIO = 2.6

# Row clustering tolerance and column-group gap threshold, in units of
# bubble_radius / bubble_spacing so they scale with the template.
_ROW_Y_TOLERANCE_RATIO = 0.75
_COLUMN_GAP_RATIO = 3.5


@dataclasses.dataclass(frozen=True)
class _Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def _find_glyph_boxes(gray: np.ndarray, radius: float, roi) -> List[_Box]:
    x0, y0, x1, y1 = roi
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
    if x0 >= x1 or y0 >= y1:
        return []

    patch = gray[y0:y1, x0:x1]
    _, binary = cv2.threshold(patch, 200, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_w, max_w = radius * _MIN_SIZE_RATIO, radius * _MAX_SIZE_RATIO
    min_h, max_h = radius * _MIN_HEIGHT_RATIO, radius * _MAX_HEIGHT_RATIO

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if min_w <= w <= max_w and min_h <= h <= max_h:
            boxes.append(_Box(x=x + x0, y=y + y0, w=w, h=h))
    return boxes


def _cluster_rows(boxes: List[_Box], y_tolerance: float) -> List[List[_Box]]:
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: b.cy)
    rows: List[List[_Box]] = [[ordered[0]]]
    for box in ordered[1:]:
        if box.cy - rows[-1][-1].cy > y_tolerance:
            rows.append([box])
        else:
            rows[-1].append(box)
    return rows


def _drop_sparse_rows(rows: List[List[_Box]], expected_rows: int) -> List[List[_Box]]:
    """If there are more clustered "rows" than the section expects, drop
    the sparsest ones down to exactly `expected_rows` -- real bubble rows
    span every column (close to `num_columns * len(choices)` boxes), while
    stray marks in the ROI's generous vertical padding (observed on real
    scans: a few glyph-sized characters from the *next* section's header
    bleeding in; a pencil smudge dragged between two rows) cluster into
    their own sparse row with only a handful of boxes.

    Only trims an actual *excess* -- if the count is already at or below
    `expected_rows`, every row is left alone, even a genuinely sparse one
    (observed on another real scan: a real question row with only 2
    detected boxes, everything else in it lost to the same smudge, still
    the correct row and not to be discarded). Dropping only when there's
    a surplus to explain is what keeps this from trading one failure mode
    for the opposite one: previously an undropped extra row made the
    section's count come out one too many and fell back to uncorrected
    nominal coordinates for *every* question in it; blindly dropping
    "sparse-looking" rows regardless of surplus did the same by removing a
    real row instead.
    """
    if len(rows) <= expected_rows:
        return rows
    keep = sorted(rows, key=len, reverse=True)[:expected_rows]
    keep_ids = {id(r) for r in keep}
    return [r for r in rows if id(r) in keep_ids]


def _split_columns(row_boxes: List[_Box], gap_threshold: float) -> List[List[_Box]]:
    ordered = sorted(row_boxes, key=lambda b: b.cx)
    groups: List[List[_Box]] = [[ordered[0]]]
    for box in ordered[1:]:
        if box.cx - groups[-1][-1].cx > gap_threshold:
            groups.append([box])
        else:
            groups[-1].append(box)
    return groups


def _section_roi(section: Section, template: Template) -> Tuple[int, int, int, int]:
    row_height = section.columns[0].row_height
    x_starts = [c.x_start for c in section.columns]
    y_starts = [c.y_start for c in section.columns]
    max_rows = max(c.last_question - c.first_question + 1 for c in section.columns)
    x0 = min(x_starts) - template.bubble_spacing_x
    x1 = max(x_starts) + 3.5 * template.bubble_spacing_x + template.bubble_radius * 2
    y0 = min(y_starts) - 2 * row_height
    y1 = max(y_starts) + max_rows * row_height + 2 * row_height
    return int(x0), int(y0), int(x1), int(y1)


def _match_to_slots(
    items: List[_T],
    item_x: Callable[[_T], float],
    nominal_xs: Sequence[float],
    max_distance: Optional[float] = None,
) -> List[Optional[_T]]:
    """Assign `items` (already sorted left-to-right) to the nominal slot
    that minimizes total x distance, preserving left-to-right order on
    *both* sides -- i.e. a missing item can be anywhere in the sequence
    (not just the last slot) without shifting everything after it into the
    wrong slot, and likewise a spurious extra item (real example: a
    question-number label whose glyph happened to be sized like a bubble
    and got swept up into the same row) doesn't get force-fit into a slot
    and shift everything after *it*. Returns a list the same length as
    `nominal_xs`, with None for any slot that had no item assigned; any
    item left over (more items than slots, or too far from every
    remaining slot) is simply not included in the result.

    A greedy "each item claims its nearest slot" approach breaks exactly
    this case: if the first real item is missing, the second item is
    nearest to slot 0, not slot 1, and greedy assignment shifts every
    subsequent item one slot to the left.

    This is a small order-preserving (non-crossing) alignment, solved by
    DP: at each (item, slot) position, either skip the item, skip the
    slot, or match them (only allowed within `max_distance`, when given).
    We maximize the number of matches first, and minimize total distance
    among those as a tiebreak -- so an item is only left unmatched (or a
    slot left empty) when matching it would leave *fewer* slots filled
    overall, not just because a slightly cheaper pairing exists elsewhere.
    `max_distance` is what keeps a wildly-out-of-place item (that label)
    from being counted as a "match" purely because leaving a slot empty
    looks worse on the match-count objective; without it, a lone stray
    item several bubbles away could still get force-matched to whatever
    slot remains, same failure as the truncation this replaced.
    """
    n_items = len(items)
    n_slots = len(nominal_xs)
    if n_slots == 0:
        return []
    if n_items == 0:
        return [None] * n_slots

    xs = [item_x(it) for it in items]

    # dp[i][j] = (-matches, cost): the best (most matches, then lowest
    # total distance) achievable aligning items[:i] with slots[:j].
    # Matches are negated so plain tuple comparison (min()) simultaneously
    # maximizes match count and minimizes cost.
    dp: List[List[Tuple[int, float]]] = [[(0, 0.0)] * (n_slots + 1) for _ in range(n_items + 1)]
    back: List[List[Optional[str]]] = [[None] * (n_slots + 1) for _ in range(n_items + 1)]

    for i in range(n_items + 1):
        for j in range(n_slots + 1):
            if i == 0 and j == 0:
                continue
            best_key: Optional[Tuple[int, float]] = None
            best_choice: Optional[str] = None
            if i > 0 and (best_key is None or dp[i - 1][j] < best_key):
                best_key, best_choice = dp[i - 1][j], "skip_item"
            if j > 0 and (best_key is None or dp[i][j - 1] < best_key):
                best_key, best_choice = dp[i][j - 1], "skip_slot"
            if i > 0 and j > 0:
                dist = abs(xs[i - 1] - nominal_xs[j - 1])
                if max_distance is None or dist <= max_distance:
                    prev_matches, prev_cost = dp[i - 1][j - 1]
                    key = (prev_matches - 1, prev_cost + dist)
                    if best_key is None or key < best_key:
                        best_key, best_choice = key, "match"
            dp[i][j] = best_key
            back[i][j] = best_choice

    result: List[Optional[_T]] = [None] * n_slots
    i, j = n_items, n_slots
    while i > 0 or j > 0:
        choice = back[i][j]
        if choice == "match":
            result[j - 1] = items[i - 1]
            i -= 1
            j -= 1
        elif choice == "skip_item":
            i -= 1
        else:
            j -= 1
    return result


def locate_section_bubbles(
    gray: np.ndarray, template: Template, section: Section
) -> Optional[Dict[int, List[tuple]]]:
    """Try to detect the real bubble positions for one section.

    Returns {question_number: [(choice, x, y), ...]} -- using detected
    glyph positions wherever a clean match was found, and the section's
    median observed shift applied to the nominal position otherwise -- or
    None if detection couldn't establish the expected row structure at
    all, in which case callers should fall back to uncorrected nominal
    template coordinates.
    """
    radius = template.bubble_radius
    roi = _section_roi(section, template)
    boxes = _find_glyph_boxes(gray, radius, roi)
    if not boxes:
        return None

    row_tolerance = radius * _ROW_Y_TOLERANCE_RATIO
    rows = _cluster_rows(boxes, row_tolerance)

    expected_rows = max(c.last_question - c.first_question + 1 for c in section.columns)
    rows = _drop_sparse_rows(rows, expected_rows)
    if len(rows) != expected_rows:
        return None

    gap_threshold = template.bubble_spacing_x * _COLUMN_GAP_RATIO

    # nominal[question] = [(choice, x, y), ...]; detected[question] mirrors
    # it but with None standing in for a choice that couldn't be matched.
    nominal: Dict[int, List[Tuple[str, float, float]]] = {}
    detected: Dict[int, List[Optional[Tuple[float, float]]]] = {}

    for row_index, row_boxes in enumerate(rows):
        groups = _split_columns(row_boxes, gap_threshold)
        active_columns = [
            col for col in section.columns if row_index < (col.last_question - col.first_question + 1)
        ]
        # Match detected column-groups to expected columns, preserving
        # left-to-right order -- see _match_to_slots for why this can't be
        # a simple greedy nearest-match (a group missing from the middle
        # would otherwise shift every later column's match by one).
        groups_sorted = sorted(groups, key=lambda g: sum(b.cx for b in g) / len(g))
        column_nominal_cxs = [col.x_start + 1.5 * template.bubble_spacing_x for col in active_columns]
        matched_groups = _match_to_slots(
            groups_sorted, lambda g: sum(b.cx for b in g) / len(g), column_nominal_cxs
        )

        for col, group in zip(active_columns, matched_groups):
            question = col.first_question + row_index
            choices = template.choices_for(question)
            nominal_slots = [(choice, col.x_start + i * template.bubble_spacing_x) for i, choice in enumerate(choices)]
            nominal[question] = [(choice, x, col.y_start + row_index * col.row_height) for choice, x in nominal_slots]

            boxes_sorted = sorted(group, key=lambda b: b.cx) if group else []
            nominal_xs = [x for _, x in nominal_slots]
            # Cap how far a box can be from a slot and still count as that
            # bubble: a real bubble lands within a few px of nominal, but a
            # question-number label occasionally sized like a bubble glyph
            # (e.g. a bold 2-digit number) can slip into the same group,
            # roughly a full bubble-spacing away from the nearest real
            # choice. Without this cap it would still get force-matched to
            # whichever slot is otherwise least bad, silently displacing
            # the real (possibly genuinely marked) bubble there.
            matched_boxes = _match_to_slots(
                boxes_sorted, lambda b: b.cx, nominal_xs, max_distance=template.bubble_spacing_x / 2
            )
            detected[question] = [
                (box.cx, box.cy) if box is not None else None for box in matched_boxes
            ]

    # Section-wide median shift from every bubble that got a clean match,
    # used to correct the ones that didn't.
    dxs = []
    dys = []
    for question, slots in detected.items():
        for (choice, nx, ny), match in zip(nominal[question], slots):
            if match is not None:
                dxs.append(match[0] - nx)
                dys.append(match[1] - ny)

    median_dx = statistics.median(dxs) if dxs else 0.0
    median_dy = statistics.median(dys) if dys else 0.0

    result: Dict[int, List[tuple]] = {}
    for question, slots in detected.items():
        entries = []
        for (choice, nx, ny), match in zip(nominal[question], slots):
            if match is not None:
                x, y = match
            else:
                x, y = nx + median_dx, ny + median_dy
            entries.append((choice, round(x), round(y)))
        result[question] = entries

    return result
