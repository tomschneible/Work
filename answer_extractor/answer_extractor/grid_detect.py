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
"""
from __future__ import annotations

import dataclasses
import statistics
from itertools import combinations
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
    items: List[_T], item_x: Callable[[_T], float], nominal_xs: Sequence[float]
) -> List[Optional[_T]]:
    """Assign each of `items` (already sorted left-to-right, at most as
    many as `nominal_xs`) to the nominal slot that minimizes total x
    distance, preserving left-to-right order -- i.e. a missing item can be
    anywhere in the sequence (not just the last slot), and everything
    after it still lines up correctly instead of cascading into the wrong
    slot. Returns a list the same length as `nominal_xs`, with None for
    any slot that had no item assigned.

    A greedy "each item claims its nearest slot" approach breaks exactly
    this case: if the first real item is missing, the second item is
    nearest to slot 0, not slot 1, and greedy assignment shifts every
    subsequent item one slot to the left.
    """
    n_slots = len(nominal_xs)
    items = items[:n_slots]
    n_items = len(items)
    if n_items == 0:
        return [None] * n_slots

    best_combo = None
    best_cost = None
    for combo in combinations(range(n_slots), n_items):
        cost = sum(abs(item_x(items[i]) - nominal_xs[combo[i]]) for i in range(n_items))
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_combo = combo

    result: List[Optional[_T]] = [None] * n_slots
    for item_index, slot_index in enumerate(best_combo):
        result[slot_index] = items[item_index]
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
            matched_boxes = _match_to_slots(boxes_sorted, lambda b: b.cx, nominal_xs)
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
