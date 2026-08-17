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

And on a third real scan: a whole column sitting together, as a rigid
unit, further from its nominal position than _match_to_slots' own
per-box distance cap allows -- every bubble still correctly spaced
relative to its neighbors, just uniformly offset. See
_uniform_shift_match for how a shift like that is told apart from a
merely coincidental count match (a missing bubble swapped for a
same-sized stray label).
"""
from __future__ import annotations

import dataclasses
import statistics
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

import cv2
import numpy as np

from .template import ColumnSpec, Section, Template

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

# Row clustering tolerance, in units of bubble_radius so it scales with
# the template.
_ROW_Y_TOLERANCE_RATIO = 0.75


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


def _max_choices_in_section(template: Template, section: Section) -> int:
    """The most choices any question in this section has. Almost always
    the same for every question in a section (e.g. a legacy ACT sheet's
    Math section uses 5 choices throughout, every other section on the
    same sheet uses 4) -- computed as a max across both parities rather
    than assumed constant so callers sizing a threshold/ROI around it
    can't come up short even if a section ever did mix choice counts."""
    return max(
        len(template.choices_for(section.name, 1)),
        len(template.choices_for(section.name, 2)),
    )


def _column_gap_threshold(template: Template, section: Section) -> float:
    """How far apart (in x) two adjacent detected glyphs must be before
    they're treated as belonging to different question-columns rather
    than different choices of the same question -- derived from this
    section's own actual column positions and choice count, not a fixed
    multiple of bubble_spacing_x: a legacy ACT sheet's 5-choice Math
    section packs one more bubble into the same physical column-to-column
    spacing as every 4-choice section on the same page, shrinking the
    real gap between columns enough that a threshold calibrated for 4
    choices would merge two of its columns into one (confirmed against
    the real sheet's own measurements: a fixed 3.5x ratio's threshold
    exceeded that section's actual inter-column gap). Splits the
    difference between the largest genuine *within*-column gap (one
    bubble_spacing_x) and the smallest actual gap between this section's
    own columns, so it adapts to however many choices this section
    really has instead of assuming 4."""
    n_choices = _max_choices_in_section(template, section)
    column_width = (n_choices - 1) * template.bubble_spacing_x
    x_starts = sorted({c.x_start for c in section.columns})
    if len(x_starts) < 2:
        return column_width  # only one column -- nothing adjacent to confuse it with
    inter_column_gaps = [x_starts[i + 1] - x_starts[i] - column_width for i in range(len(x_starts) - 1)]
    return (template.bubble_spacing_x + min(inter_column_gaps)) / 2


# How much larger, by area, the smallest *kept* box must be than the
# largest *dropped* box before _drop_size_outlier_boxes trusts an
# area-based drop. Calibrated against every real stray-label row found
# across eleven real sheets (six different physical forms): the label was
# reliably 15-38% smaller in area than the real bubbles kept alongside it
# in every one of them but one, a genuine 5-glyph row with no label
# present at all -- every box within 4% of every other by area, correctly
# left alone rather than guessed at. 1.10 sits comfortably between the two.
_SIZE_OUTLIER_MIN_AREA_RATIO = 1.10


def _drop_size_outlier_boxes(boxes: List[_Box], target_count: int) -> List[_Box]:
    """If `boxes` has more than `target_count` items, drop the smallest
    ones (by area) down to `target_count` -- but only when they're a
    *decisively* smaller cluster than the rest, not just marginally
    smaller by chance (see _SIZE_OUTLIER_MIN_AREA_RATIO).

    Exists because a stray question-number label occasionally passes
    _find_glyph_boxes's per-page size filter (deliberately loose, since it
    has to work across an unknown scan/print without per-sheet tuning) and
    lands in the same row/column-group as the real bubbles next to it.
    Normally the downstream position-based matching (_match_to_slots's
    max_distance cap) correctly rejects that label as too far from a real
    bubble slot -- confirmed working on every sheet tested, including
    several with far *more* stray-label rows than the one that motivated
    this function. But on one real scan, the label happened to sit closer
    to a nominal slot than the real bubble did, so position-based matching
    picked it as the *cheaper* match -- quietly stealing that slot and
    dropping the real, rightmost bubble in the row (the actual marked one,
    in the case that surfaced this) from being sampled at all.

    Area is a signal position-based matching doesn't use at all: a printed
    question-number glyph is reliably smaller than the printed ring+letter
    bubbles next to it, even though both pass the same per-page min/max
    size filter -- and unlike position, nothing about where the label
    happens to land changes that. Only acting on a decisive gap, rather
    than always dropping the single smallest box regardless of margin,
    matters just as much: a row that's short a genuine bubble for some
    other reason shouldn't have a real bubble discarded on a coin-flip
    area difference -- see this function's one real counterexample.
    """
    n_extra = len(boxes) - target_count
    if n_extra <= 0:
        return boxes
    ordered = sorted(boxes, key=lambda b: b.w * b.h)
    dropped, kept = ordered[:n_extra], ordered[n_extra:]
    largest_dropped_area = dropped[-1].w * dropped[-1].h
    smallest_kept_area = kept[0].w * kept[0].h
    if largest_dropped_area <= 0 or smallest_kept_area / largest_dropped_area < _SIZE_OUTLIER_MIN_AREA_RATIO:
        return boxes
    dropped_ids = {id(b) for b in dropped}
    return [b for b in boxes if id(b) not in dropped_ids]


def _section_roi(section: Section, template: Template) -> Tuple[int, int, int, int]:
    row_height = section.columns[0].row_height
    x_starts = [c.x_start for c in section.columns]
    y_starts = [c.y_start for c in section.columns]
    max_rows = max(c.last_question - c.first_question + 1 for c in section.columns)
    column_width = (_max_choices_in_section(template, section) - 1) * template.bubble_spacing_x
    x0 = min(x_starts) - template.bubble_spacing_x
    x1 = max(x_starts) + column_width + 0.5 * template.bubble_spacing_x + template.bubble_radius * 2
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


def _uniform_shift_match(
    boxes_sorted: List[_Box], nominal_xs: Sequence[float], radius: float
) -> Optional[List[_Box]]:
    """When `boxes_sorted` (left-to-right) has exactly as many items as
    `nominal_xs` has slots, and every box is offset from its
    corresponding (same-index) nominal slot by nearly the same amount,
    treat this as one rigid unit that has drifted together and pair them
    positionally -- even where that offset is larger than
    _match_to_slots' own max_distance cap tolerates. Returns None (defer
    to the capped DP instead) when the counts differ, or when the
    per-box offsets aren't tightly clustered -- e.g. one box sitting ~30px
    off while the rest sit near 0 means the count matched by coincidence
    (a genuinely missing bubble alongside an unrelated stray box), not a
    shared shift, and trusting position blindly there is exactly the
    mistake the cap exists to prevent (confirmed against a real sheet:
    doing so quietly relabeled a section's true first choice as a stray
    label sitting a full bubble-spacing to its left).

    Position agreement alone isn't quite enough, either: confirmed
    against a second real sheet where a merged two-digit question-number
    label happened to sit almost exactly one bubble-spacing left of the
    row's true first bubble -- close enough to its "slot" that the whole
    row's offsets clustered just as tightly as a genuine shared shift,
    while the row's real last bubble (solidly filled, its ink bleeding
    past the printed oval) was the one that failed _find_glyph_boxes's
    size filter and went undetected, leaving the count coincidentally
    right for entirely the wrong reason. A real shifted row's boxes are
    all bubbles and stay close in size to each other; probing whether
    *any single one* of them looks like a size outlier against the rest
    (the same comparison _drop_size_outlier_boxes already makes for the
    surplus case) catches this the same way.
    """
    if len(boxes_sorted) != len(nominal_xs) or not boxes_sorted:
        return None
    deltas = [b.cx - nx for b, nx in zip(boxes_sorted, nominal_xs)]
    if max(deltas) - min(deltas) > radius:
        return None
    if len(boxes_sorted) >= 2 and len(_drop_size_outlier_boxes(boxes_sorted, len(boxes_sorted) - 1)) != len(
        boxes_sorted
    ):
        return None
    return boxes_sorted


# How many individual box-to-slot dx samples a column needs (all from
# _uniform_shift_match's own confirmed matches -- see
# _retry_with_column_shift) before its own median shift is trusted over
# the section-wide one. One fully-matched row already contributes this
# many samples for a normal 4-choice column; the bar exists mainly to
# keep a column that's never once matched cleanly from being "estimated"
# off of a single stray value.
_MIN_COLUMN_SHIFT_SAMPLES = 2


def _retry_with_column_shift(
    boxes_sorted: List[_Box],
    nominal_xs: Sequence[float],
    column_dx_samples: Optional[List[float]],
    bubble_spacing_x: float,
) -> Optional[List[Optional[_Box]]]:
    """Re-run the capped box-to-slot match for one row/column-group,
    shifting `nominal_xs` by this column's own confirmed offset first --
    only ever called on a group _match_to_slots already left with at
    least one empty slot.

    Real case this was built from: a photographed (not flatbed-scanned)
    sheet whose one rightmost column sat, row after row, a consistent
    ~43px left of nominal -- confirmed on a neighboring row that had all
    4 real boxes and passed _uniform_shift_match outright. A row in that
    same column with only 2 of its 4 real boxes detected doesn't qualify
    for that whole-row check at all (it needs every slot filled to
    verify internal consistency), so the plain capped match ran against
    the *raw*, uncorrected nominal positions instead -- and matched its 2
    real boxes to the wrong two slots entirely (an off-by-one column
    match, the same failure _uniform_shift_match exists to prevent, just
    with a short row instead of a shifted one hiding it from that check).
    Both of those real boxes were within the ordinary cap of the
    *shifted* nominal positions, just not the unshifted ones.

    `column_dx_samples` -- gathered only from _uniform_shift_match's own
    confirmed matches elsewhere in this same column, never from a plain
    capped match (which, as above, can fill every slot while still being
    wrong) -- must clear _MIN_COLUMN_SHIFT_SAMPLES before being trusted;
    returns None otherwise, leaving the original (unshifted) match as-is.
    """
    if not column_dx_samples or len(column_dx_samples) < _MIN_COLUMN_SHIFT_SAMPLES:
        return None
    column_dx = statistics.median(column_dx_samples)
    shifted_nominal_xs = [x + column_dx for x in nominal_xs]
    return _match_to_slots(boxes_sorted, lambda b: b.cx, shifted_nominal_xs, max_distance=bubble_spacing_x / 2)


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

    gap_threshold = _column_gap_threshold(template, section)

    # nominal[question] = [(choice, x, y), ...]; detected[question] mirrors
    # it but with None standing in for a choice that couldn't be matched.
    nominal: Dict[int, List[Tuple[str, float, float]]] = {}
    detected: Dict[int, List[Optional[Tuple[float, float]]]] = {}

    # Per-column x-shift samples, keyed by that column's own x_start --
    # populated only from _uniform_shift_match's own confirmed matches
    # (never from the capped DP's, even when it fills every slot: with
    # too few real boxes for the whole-row consistency check to run at
    # all, the capped DP can still fill every slot by matching against
    # the *wrong* ones -- see _retry_with_column_shift's docstring for
    # the real case this was built from). Revisited once every row's
    # first pass is done, so a column's shift can be estimated from *any*
    # row in it, not just earlier ones.
    column_dx_samples: Dict[float, List[float]] = {}
    # (question, col, boxes_sorted, nominal_xs, choices) for every column-
    # group that came out of the first pass with at least one slot still
    # unmatched -- revisited in the second pass below.
    pending_retries: List[Tuple[int, ColumnSpec, List[_Box], List[float], List[str]]] = []
    # question -> its own column's x_start, so the final per-slot fallback
    # below can look up whether that column has its own decisively-
    # different shift on file (see column_dx there) for any slot still
    # unmatched after the retry pass.
    question_column_x_start: Dict[int, float] = {}

    for row_index, row_boxes in enumerate(rows):
        groups = _split_columns(row_boxes, gap_threshold)
        active_columns = [
            col for col in section.columns if row_index < (col.last_question - col.first_question + 1)
        ]
        # Match detected column-groups to expected columns, preserving
        # left-to-right order -- see _match_to_slots for why this can't be
        # a simple greedy nearest-match (a group missing from the middle
        # would otherwise shift every later column's match by one). Each
        # column's nominal center is the mean of *that column's own*
        # choice count, not a fixed half-of-4 offset -- a 5-choice column
        # (e.g. a legacy sheet's Math section) centers one choice further
        # right than a 4-choice one starting at the same x.
        groups_sorted = sorted(groups, key=lambda g: sum(b.cx for b in g) / len(g))
        column_nominal_cxs = [
            col.x_start
            + (len(template.choices_for(section.name, col.first_question + row_index)) - 1)
            / 2
            * template.bubble_spacing_x
            for col in active_columns
        ]
        matched_groups = _match_to_slots(
            groups_sorted, lambda g: sum(b.cx for b in g) / len(g), column_nominal_cxs
        )

        for col, group in zip(active_columns, matched_groups):
            question = col.first_question + row_index
            choices = template.choices_for(section.name, question)
            nominal_slots = [(choice, col.x_start + i * template.bubble_spacing_x) for i, choice in enumerate(choices)]
            nominal[question] = [(choice, x, col.y_start + row_index * col.row_height) for choice, x in nominal_slots]
            question_column_x_start[question] = col.x_start

            if group:
                group = _drop_size_outlier_boxes(group, len(choices))
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
            #
            # Equal box/slot counts alone do NOT excuse a row from this
            # cap -- confirmed against a real sheet where a row was short
            # its true first bubble (never detected -- lost to a merged
            # contour) but happened to pick up a stray label box far to
            # its left instead, leaving the count coincidentally right
            # while the *identity* of that first item was wrong. The cap
            # correctly left that slot for the section's median-shift
            # fallback rather than trusting the label. What the cap
            # doesn't handle: a real, otherwise-unremarkable row (or on
            # one scan, a whole column) can sit together, as a rigid
            # unit, further from nominal than the cap allows -- every box
            # still in its correct relative order and spacing, just
            # shifted uniformly beyond it. _uniform_shift_match tells
            # those two apart by how *consistent* the box-to-nominal
            # offset is across the whole row: a genuine rigid shift moves
            # every box by nearly the same amount, while a
            # missing-bubble-plus-stray-label swap shows one wildly
            # different offset next to several near-zero ones.
            uniform = _uniform_shift_match(boxes_sorted, nominal_xs, template.bubble_radius)
            if uniform is not None:
                matched_boxes = uniform
                column_dx_samples.setdefault(col.x_start, []).extend(
                    box.cx - nx for box, nx in zip(uniform, nominal_xs)
                )
            else:
                matched_boxes = _match_to_slots(
                    boxes_sorted, lambda b: b.cx, nominal_xs, max_distance=template.bubble_spacing_x / 2
                )
            detected[question] = [
                (box.cx, box.cy) if box is not None else None for box in matched_boxes
            ]
            if any(box is None for box in matched_boxes):
                pending_retries.append((question, col, boxes_sorted, nominal_xs, choices))

    # Section-wide median shift from every bubble that got a clean match on
    # the first pass, used to correct whatever's still unmatched below --
    # deliberately snapshotted *before* the retry pass runs, even though
    # retries can turn some of these same None slots into real matches too.
    # A retry only ever fires on a row that already had a detection problem
    # (see pending_retries above), so its newly-matched slots are real
    # boxes but a less representative sample of "this section's ordinary
    # shift" than the first pass's -- feeding them back into this median
    # would let a fixed row's own correction quietly nudge the *section-
    # wide* fallback estimate too, which every other, unrelated row's still-
    # unmatched slots also draw on. Confirmed against a real sheet: a row
    # with no detection problem of its own (both its answer's real box and
    # the section's own median_dx unaffected by anything above) still
    # flipped from a correct answer to blank, because two *other* rows'
    # retries added new samples to this median, nudging it by a few px --
    # just enough to move a fallback-estimated neighbor bubble's fill_ratio
    # across this row's own baseline-subtraction threshold (see
    # detect.decide_answer). Keeping this section-wide estimate exactly as
    # stable as it was before the retry mechanism existed is what avoids
    # that ripple.
    dxs = []
    dys = []
    for question, slots in detected.items():
        for (choice, nx, ny), match in zip(nominal[question], slots):
            if match is not None:
                dxs.append(match[0] - nx)
                dys.append(match[1] - ny)

    median_dx = statistics.median(dxs) if dxs else 0.0
    median_dy = statistics.median(dys) if dys else 0.0

    for question, col, boxes_sorted, nominal_xs, choices in pending_retries:
        retried = _retry_with_column_shift(
            boxes_sorted, nominal_xs, column_dx_samples.get(col.x_start), template.bubble_spacing_x
        )
        if retried is None:
            continue
        still_unmatched = sum(1 for m in detected[question] if m is None)
        newly_unmatched = sum(1 for m in retried if m is None)
        # Not just a strict improvement in fill count: the original match
        # (against *uncorrected* nominal) can "successfully" fill some
        # slots while getting their identity wrong -- see this function's
        # own docstring above. Once a column's shift is confirmed
        # elsewhere, its own shifted-nominal match is the more trustworthy
        # read for every slot in this row, not just the ones the original
        # pass happened to leave empty; only reject it if it's actually
        # worse (fills fewer slots than before).
        if newly_unmatched <= still_unmatched:
            detected[question] = [(box.cx, box.cy) if box is not None else None for box in retried]

    # A column's own dx is only preferred over the section-wide one here if
    # it's decisively different -- not just present with enough samples
    # (_MIN_COLUMN_SHIFT_SAMPLES). A slot that reaches this fallback loop
    # still None was never matched to any real detected box at all (unlike
    # _retry_with_column_shift above, which only ever re-matches boxes that
    # were actually found), so *some* per-column noise is expected even on
    # a column with no real problem -- confirmed against a real sheet whose
    # every column showed its own few-px median_dx purely from ordinary
    # sub-pixel detection noise, not a real shift. Trusting all of those
    # unconditionally nudged a handful of otherwise-fine rows' fallback
    # positions on that sheet by exactly that few px each -- individually
    # harmless, but enough of them summed sheet-wide moved a confidently-
    # read answer's dark_fraction (see detect._dark_fraction) just past a
    # new gap in the sheet's own distribution that hadn't existed before,
    # and it was wiped to blank (see detect._apply_readability_checks).
    # The real cases this correction exists for (see _retry_with_column_shift's
    # docstring) shifted by a large fraction of a whole bubble_spacing_x or
    # more -- an off-by-one-slot's worth of misalignment, not sub-pixel
    # noise. Confirmed noise ceiling on a real sheet with no real per-column
    # problem: every column's own median sat within ~6px of the section's
    # (see above); confirmed real shifts needing this correction ranged
    # from ~30px to ~150px on two different real sheets. Half of
    # bubble_spacing_x sits with comfortable margin below every real case
    # measured so far and well above that noise ceiling.
    column_dx = {
        x_start: statistics.median(samples)
        for x_start, samples in column_dx_samples.items()
        if len(samples) >= _MIN_COLUMN_SHIFT_SAMPLES
    }
    column_dx = {
        x_start: dx for x_start, dx in column_dx.items() if abs(dx - median_dx) >= template.bubble_spacing_x / 2
    }

    result: Dict[int, List[tuple]] = {}
    for question, slots in detected.items():
        dx = column_dx.get(question_column_x_start[question], median_dx)
        entries = []
        for (choice, nx, ny), match in zip(nominal[question], slots):
            if match is not None:
                x, y = match
            else:
                x, y = nx + dx, ny + median_dy
            entries.append((choice, round(x), round(y)))
        result[question] = entries

    return result
