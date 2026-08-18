"""Regression coverage for the bug found against a real filled-in ACT
sheet: the sheet's actual bubble grid was shifted by ~20px (nearly a full
row) from the template's calibrated coordinates, despite the page being
resized to the exact target dimensions, causing most answers to be read
from the wrong bubble entirely. grid_detect corrects for this by locating
the real bubble positions on each sheet instead of trusting fixed
template coordinates.
"""
import cv2
import numpy as np

from answer_extractor.detect import evaluate_sheet
from answer_extractor.grid_detect import (
    _Box,
    _cluster_rows,
    _column_group_max_distance,
    _drop_size_outlier_boxes,
    _drop_sparse_rows,
    _find_glyph_boxes,
    _match_to_slots,
    _positions_uniform,
    _resolve_extra_boxes_by_column_shift,
    _retry_with_column_shift,
    _uniform_shift_match,
    locate_section_bubbles,
)
from answer_extractor.template import Template
from tests.synth import fill_bubble, make_blank_sheet, render_sheet


# -- _match_to_slots: pure logic tests ----------------------------------------
#
# Regression coverage for a second real bug found against a real scanned ACT
# sheet: a question-number label (e.g. "34") occasionally has a glyph sized
# like a real bubble and gets swept into the same row. The old
# implementation truncated to `items[:n_slots]`, which -- when that made the
# item count exactly match the slot count -- forced every item into a slot
# left-to-right with no way to reject the spurious one, silently shifting
# every real bubble in the row one slot over and never sampling the actual
# (in the real case, solidly marked) last bubble at all.


def test_match_to_slots_exact_match():
    result = _match_to_slots([10.0, 20.0, 30.0], lambda x: x, [10.0, 20.0, 30.0])
    assert result == [10.0, 20.0, 30.0]


def test_match_to_slots_missing_item_in_middle_does_not_shift_later_ones():
    # Slot 1 (nominal 20.0) has no detected item; slots 0 and 2 shouldn't
    # cascade into the wrong item as a result.
    result = _match_to_slots([10.0, 30.0], lambda x: x, [10.0, 20.0, 30.0])
    assert result == [10.0, None, 30.0]


def test_match_to_slots_extra_item_beyond_max_distance_is_dropped():
    # Real scenario: a question-number label sitting ~26px left of the
    # first real bubble, with 4 real bubbles at their normal ~4px jitter.
    # Without a distance cap, forcing all 5 items into 4 slots shifts F/G/H
    # into G/H/J's slots and drops the real (marked) J bubble entirely.
    items = [1244.5, 1274.5, 1305.0, 1336.0, 1366.5]
    nominal = [1270.5, 1301.17, 1331.83, 1362.5]
    result = _match_to_slots(items, lambda x: x, nominal, max_distance=15.3)
    assert result == [1274.5, 1305.0, 1336.0, 1366.5]


def test_match_to_slots_extra_item_within_max_distance_still_wins_on_cost():
    # A stray item close enough to plausibly be the real bubble should
    # still be preferred over leaving that slot empty.
    items = [10.0, 21.0, 30.0]
    nominal = [10.0, 20.0, 30.0]
    result = _match_to_slots(items, lambda x: x, nominal, max_distance=15.0)
    assert result == [10.0, 21.0, 30.0]


def test_match_to_slots_far_item_with_no_nearby_alternative_leaves_slot_empty():
    # Mirrors the real row-35 case: the only item near slot 0 is implausibly
    # far away (the label), and no other item is close enough either --
    # slot 0 should come back empty (falls back to the section's median
    # shift) rather than being stolen by the far-off item.
    items = [1244.5, 1335.5, 1366.5]
    nominal = [1270.5, 1301.17, 1331.83, 1362.5]
    result = _match_to_slots(items, lambda x: x, nominal, max_distance=15.3)
    assert result == [None, None, 1335.5, 1366.5]


# -- _column_group_max_distance / locate_section_bubbles's column-group ------
# matching: a seventh real bug, on a real scanned legacy-template sheet.
# Column-group-to-column matching (unlike box-to-slot matching within a
# column, which _match_to_slots' max_distance cap already protected) had
# no equivalent cap: a row where one column's own bubbles were faint
# enough to fragment its group (see _split_columns) could still end up
# with exactly as many groups as active columns by coincidence, and
# _match_to_slots -- maximizing match count with no per-pair distance
# limit -- filled every column with whichever group was *relatively*
# closest, however far that actually was. On the real sheet this
# surfaced on, that meant every column from the fragmented one onward
# grabbed its *neighbor's* group instead of its own -- a full
# column-width away, not a plausible scan shift -- misreading four
# separate questions (39, 46, 59, 65) from the wrong column entirely.


def test_column_group_max_distance_is_half_the_smallest_gap():
    # Real nominal column centers from the sheet above (English section,
    # six 4-choice columns spaced ~214-215px apart).
    nominal_cxs = [336.0, 550.5, 766.0, 980.0, 1194.0, 1409.0]
    assert _column_group_max_distance(nominal_cxs) == 107.0


def test_column_group_max_distance_none_for_a_single_column():
    assert _column_group_max_distance([336.0]) is None


def test_match_to_slots_without_a_cap_misassigns_every_fragmented_column():
    # Real column-group means from the sheet above, row 6 (questions 7,
    # 20, 33, 46, 59, 72): column 2's own bubbles (row 6 = question 20)
    # were faint enough that _split_columns fragmented what should have
    # been one 4-box group into a 1-box and a 2-box piece, leaving 6
    # groups total for 6 active columns -- coincidentally matching count.
    # Without a distance cap, every group past the fragmented one gets
    # force-matched to the *next* column over.
    groups_mean_cx = [334.4, 473.0, 579.2, 747.7, 963.5, 1131.8]
    nominal_cxs = [336.0, 550.5, 766.0, 980.0, 1194.0, 1409.0]
    result = _match_to_slots(groups_mean_cx, lambda x: x, nominal_cxs)
    assert result == groups_mean_cx  # every column "filled" -- silently, all but the first two wrongly


def test_match_to_slots_with_the_column_group_cap_rejects_the_misassigned_ones():
    # Same real data as above, this time with _column_group_max_distance's
    # own cap applied. The one genuinely ambiguous group (473.0 -- itself
    # only a fragment, not clearly any column's real group) is now too
    # costly to keep pinned to column 2: the DP's own order-preserving
    # search, freed by the cap from having to accept every same-index
    # pairing, finds a *better* (more matches, all within the cap)
    # alignment by skipping it instead and sliding columns 2-5 each back
    # to their own *real* group -- exactly recovering the four questions
    # (46, 59, 65, and column 5's own row) the uncapped version misread
    # from their neighbor. Column 6, with nothing left close enough,
    # correctly comes back unmatched rather than stealing column 5's.
    groups_mean_cx = [334.4, 473.0, 579.2, 747.7, 963.5, 1131.8]
    nominal_cxs = [336.0, 550.5, 766.0, 980.0, 1194.0, 1409.0]
    result = _match_to_slots(
        groups_mean_cx, lambda x: x, nominal_cxs, max_distance=_column_group_max_distance(nominal_cxs)
    )
    assert result == [334.4, 579.2, 747.7, 963.5, 1131.8, None]


# -- _drop_size_outlier_boxes: pure logic tests -------------------------------
#
# Regression coverage for a real bug found against a real scanned sheet
# whose print was bold enough that a question-number label's glyph (e.g.
# "40") crossed _find_glyph_boxes's size filter and landed in the same
# row/column-group as its 4 real bubble choices. _match_to_slots's
# position-based matching (max_distance) normally rejects such a label as
# too far from a real slot, but here the label happened to sit *closer* to
# the nominal first-slot position than the real bubble did -- stealing
# that slot's match and silently dropping the real, rightmost (marked)
# bubble from the row entirely. Area -- a signal position-based matching
# never looks at -- separates them reliably: real bubble glyphs measured
# 15-38% larger by area than the stray label across every real case found.


def _box(cx: float, w: int, h: int) -> _Box:
    return _Box(x=round(cx - w / 2), y=0, w=w, h=h)


def test_drop_size_outlier_boxes_drops_a_decisively_smaller_extra_box():
    # Real shape from the sheet that motivated this: a "40" label (w=24,
    # h=17) mixed in with 4 real bubbles (w=26-29, h=19-21).
    label = _box(925, 24, 17)
    real = [_box(955, 28, 20), _box(986, 27, 20), _box(1017, 27, 20), _box(1050, 29, 21)]
    result = _drop_size_outlier_boxes([label] + real, target_count=4)
    assert result == real


def test_drop_size_outlier_boxes_leaves_boxes_alone_without_a_decisive_gap():
    # Real counterexample found: a genuine 5-glyph row with no label
    # present at all -- every box within ~4% of every other by area, far
    # under _SIZE_OUTLIER_MIN_AREA_RATIO. Must not guess which one to drop.
    boxes = [_box(678, 31, 20), _box(705, 27, 19), _box(736, 27, 20), _box(767, 27, 20), _box(797, 26, 20)]
    result = _drop_size_outlier_boxes(boxes, target_count=4)
    assert result == boxes


def test_drop_size_outlier_boxes_is_a_noop_when_count_already_matches():
    boxes = [_box(700, 27, 19), _box(730, 27, 19)]
    assert _drop_size_outlier_boxes(boxes, target_count=2) == boxes
    assert _drop_size_outlier_boxes(boxes, target_count=3) == boxes


# -- _uniform_shift_match: pure logic tests -----------------------------------
#
# Regression coverage for a fourth real bug, found on a real scanned sheet
# whose whole column sat ~20px beyond _match_to_slots' own max_distance cap:
# every real bubble was still exactly where its neighbors were relative to
# each other (uniformly shifted, not individually jittered), but the cap
# rejected each one as "too far from its slot" and the DP settled for a
# partial, one-slot-over match instead -- silently reading the *next*
# choice's ink for every slot in the row (confirmed against the real sheet:
# read "H" where the true, solidly-filled mark was "G"). Equal box/slot
# counts alone don't excuse a row from the cap, though -- confirmed against
# a second real sheet where a merged two-digit question-number label
# happened to sit almost exactly one bubble-spacing from the row's true
# first slot (coincidentally as tight a cluster as a genuine shared shift)
# while the row's real last bubble -- solidly filled, its ink bleeding past
# the printed oval -- failed _find_glyph_boxes's size filter and went
# undetected. That combination is caught by the same area comparison
# _drop_size_outlier_boxes already makes for the surplus case: a genuine
# shifted row's boxes are close in size to each other; a label mixed in is
# reliably smaller.


def test_uniform_shift_match_accepts_a_tightly_clustered_shift_beyond_the_cap():
    # Real deltas from the sheet that motivated this: every box ~19-20px
    # right of its nominal slot, well beyond match_to_slots' max_distance
    # cap (half of bubble_spacing_x, here ~15.3), but within a couple of px
    # of each other.
    nominal = [290.0, 320.67, 351.34, 382.0]
    boxes = [_box(310.0, 26, 19), _box(340.0, 28, 20), _box(371.5, 27, 20), _box(402.5, 27, 20)]
    result = _uniform_shift_match(boxes, nominal, radius=11)
    assert result == boxes


def test_uniform_shift_match_rejects_a_missing_bubble_plus_stray_label():
    # Real shape from the sheet that motivated the size-outlier guard: a
    # merged "32" label (w=23, h=17) sitting ~30px left of nominal slot 0,
    # with the row's real last bubble undetected -- leaving the count
    # coincidentally right and the deltas coincidentally clustered (every
    # box ~30-32px off nominal) even though slot 0's "box" isn't a bubble
    # at all.
    nominal = [719.5, 750.17, 780.84, 811.51]
    boxes = [_box(689.5, 23, 17), _box(718.5, 27, 20), _box(749.0, 26, 20), _box(779.5, 27, 20)]
    assert _uniform_shift_match(boxes, nominal, radius=11) is None


def test_uniform_shift_match_rejects_when_only_one_box_is_actually_off():
    # A stray item sits ~30px off while the other three are already near
    # their own nominal slots (~1px) -- no shared shift at all, just a
    # coincidental count match. The position spread alone (not the size
    # check) is what catches this: nothing here needs to look label-sized
    # for it to be obviously not a rigid shift.
    nominal = [290.0, 320.67, 351.34, 382.0]
    boxes = [_box(260.0, 26, 19), _box(321.0, 27, 20), _box(352.0, 27, 20), _box(383.0, 27, 20)]
    assert _uniform_shift_match(boxes, nominal, radius=11) is None


def test_uniform_shift_match_returns_none_on_count_mismatch():
    nominal = [290.0, 320.67, 351.34, 382.0]
    boxes = [_box(310.0, 26, 19), _box(340.0, 28, 20), _box(371.5, 27, 20)]
    assert _uniform_shift_match(boxes, nominal, radius=11) is None


def test_uniform_shift_match_returns_none_for_empty_boxes():
    assert _uniform_shift_match([], [290.0, 320.67], radius=11) is None


def test_uniform_shift_match_rejects_a_real_window_whose_own_bubble_prints_undersized():
    # Real shape from a second real scan: the row's real, correct choices
    # (not a label -- these four are the genuine A/B/C/D bubbles), one of
    # which (C, area 513) happens to print small enough next to its
    # neighbors (areas 638, 620, 609) to trip the size-outlier veto on its
    # own, even though nothing here is a stray label at all. Confirms
    # _resolve_extra_boxes_by_column_shift's deliberate choice to check
    # only _positions_uniform, not this full function, is necessary --
    # this exact window is what it needs to rescue.
    nominal = [719.5, 750.17, 780.84, 811.51]
    real = [_box(716.5, 29, 22), _box(746.5, 31, 20), _box(778.5, 27, 19), _box(808.5, 29, 21)]
    assert _uniform_shift_match(real, nominal, radius=11) is None


def test_positions_uniform_accepts_the_same_window_uniform_shift_match_rejects():
    # Same real window as above -- position-only agreement doesn't care
    # that C happens to print smaller than its neighbors.
    nominal = [719.5, 750.17, 780.84, 811.51]
    real = [_box(716.5, 29, 22), _box(746.5, 31, 20), _box(778.5, 27, 19), _box(808.5, 29, 21)]
    assert _positions_uniform(real, nominal, radius=11)


def test_positions_uniform_rejects_a_scattered_run():
    nominal = [290.0, 320.67, 351.34, 382.0]
    boxes = [_box(260.0, 26, 19), _box(321.0, 27, 20), _box(352.0, 27, 20), _box(383.0, 27, 20)]
    assert not _positions_uniform(boxes, nominal, radius=11)


# -- _retry_with_column_shift: pure logic tests -------------------------------
#
# Regression coverage for a fifth real bug, found on a real photographed
# (not flatbed-scanned) sheet whose one rightmost column sat, row after
# row, a consistent ~43px left of nominal -- confirmed on a neighboring row
# in the same column that had all 4 real boxes and passed
# _uniform_shift_match outright. A row in that same column with only 2 of
# its 4 real boxes detected doesn't qualify for that whole-row check at all
# (it needs every slot filled to verify internal consistency), so the
# plain capped match in _match_to_slots ran against the *raw*, uncorrected
# nominal positions instead -- and matched its 2 real boxes to the wrong
# two slots entirely, silently reading the *next* choice's ink. Both boxes
# were within the ordinary cap of the *shifted* nominal positions, just not
# the unshifted ones.


def test_retry_with_column_shift_recovers_a_short_row_using_the_columns_own_offset():
    # Real deltas from the sheet that motivated this: every confirmed box
    # in the column ~43px left of nominal. This row only has 2 of its 4
    # real boxes (the other 2 genuinely undetected, not just off nominal).
    nominal = [1270.5, 1301.17, 1331.83, 1362.5]
    boxes = [_box(1288.8, 27, 20), _box(1319.5, 27, 20)]  # the row's real H, J boxes, ~43px left of nominal
    samples = [-43.0, -43.0, -42.5, -43.5]  # from _uniform_shift_match elsewhere in this column
    result = _retry_with_column_shift(boxes, nominal, samples, bubble_spacing_x=30.67)
    assert result == [None, None, boxes[0], boxes[1]]


def test_retry_with_column_shift_returns_none_below_the_minimum_sample_count():
    # A single sample (e.g. from one other row's uniform match) isn't
    # enough to trust a column's own shift over the section-wide one --
    # see _MIN_COLUMN_SHIFT_SAMPLES.
    nominal = [1270.5, 1301.17, 1331.83, 1362.5]
    boxes = [_box(1335.0, 27, 20), _box(1368.0, 27, 20)]
    result = _retry_with_column_shift(boxes, nominal, [-43.0], bubble_spacing_x=30.67)
    assert result is None


def test_retry_with_column_shift_returns_none_without_any_samples():
    nominal = [1270.5, 1301.17, 1331.83, 1362.5]
    boxes = [_box(1335.0, 27, 20), _box(1368.0, 27, 20)]
    result = _retry_with_column_shift(boxes, nominal, None, bubble_spacing_x=30.67)
    assert result is None


# -- _resolve_extra_boxes_by_column_shift: pure logic tests -------------------
#
# Regression coverage for a sixth real bug, found on a real scanned sheet:
# a question-number label (e.g. "12") printed just left of a row's real
# first bubble, close enough in size to the real bubbles that
# _drop_size_outlier_boxes correctly declined to drop it as a decisive
# outlier (see that function's own docstring on why that refusal is by
# design). Passed through with 5 boxes for 4 slots, the capped
# position-based match picked whichever contiguous 4 minimized total
# distance -- the label plus the row's first 3 real bubbles, silently
# dropping the row's real *last* bubble (the one genuinely marked, in the
# case that surfaced this) instead of the label. Both the label and the
# true last bubble sat within the ordinary cap of their respective
# (wrong) slot, and both the "keep the label" and "keep the true last
# bubble" 4-box windows passed _uniform_shift_match's own internal-
# consistency check on their own -- a label sized close enough to fool
# the area check is also close enough to fool that check by itself. What
# position alone can't see: this column's own already-established shift
# from _uniform_shift_match's confirmed matches on *other* rows in the
# same column, real deltas from the sheet that motivated this (~19.7px
# right of nominal, confirmed on 44 other boxes in the same column).


def test_resolve_extra_boxes_by_column_shift_drops_the_label_not_the_true_last_bubble():
    nominal = [290.0, 320.67, 351.34, 382.01]
    label = _box(277.0, 24, 17)  # the "12" question-number glyph, not a bubble at all
    real = [_box(309.5, 27, 20), _box(340.5, 27, 20), _box(371.0, 27, 20), _box(401.5, 27, 20)]
    boxes_sorted = [label] + real
    column_dx_samples = [19.83, 20.0, 19.66, 19.49] * 3  # from other, fully-matched rows in this column
    result = _resolve_extra_boxes_by_column_shift(boxes_sorted, nominal, column_dx_samples, radius=11)
    assert result == real


def test_resolve_extra_boxes_by_column_shift_returns_none_below_the_minimum_sample_count():
    nominal = [290.0, 320.67, 351.34, 382.01]
    label = _box(277.0, 24, 17)
    real = [_box(309.5, 27, 20), _box(340.5, 27, 20), _box(371.0, 27, 20), _box(401.5, 27, 20)]
    result = _resolve_extra_boxes_by_column_shift([label] + real, nominal, [19.83], radius=11)
    assert result is None


def test_resolve_extra_boxes_by_column_shift_returns_none_when_no_window_is_uniform():
    # Neither a genuine label-plus-shift shape nor any other internally
    # consistent run -- five boxes scattered with no shared offset at
    # all. Must not guess; the existing capped match is left standing.
    nominal = [290.0, 320.67, 351.34, 382.01]
    boxes_sorted = [_box(260.0, 24, 17), _box(300.0, 27, 20), _box(360.0, 27, 20), _box(395.0, 27, 20), _box(440.0, 27, 20)]
    column_dx_samples = [19.83, 20.0, 19.66, 19.49] * 3
    result = _resolve_extra_boxes_by_column_shift(boxes_sorted, nominal, column_dx_samples, radius=11)
    assert result is None


def test_resolve_extra_boxes_by_column_shift_rescues_a_window_whose_own_bubble_looks_undersized():
    # Real shape from a second real scan (English 39 on a legacy-template
    # sheet): the same "label sized close enough to survive the area
    # check" setup as the test above, but here the row's real, genuinely
    # marked last choice ("D") happens to print small enough itself (see
    # test_uniform_shift_match_rejects_a_real_window_whose_own_bubble_
    # prints_undersized) that the *correct* window -- not the label one --
    # is what a plain _uniform_shift_match call rejects. Without this
    # function skipping that veto (via _positions_uniform), the wrong
    # (label-containing) window would have been the only one accepted and
    # kept, exactly reproducing the real misread ("A" answered as "B").
    nominal = [719.5, 750.17, 780.84, 811.51]
    label = _box(688.5, 25, 20)  # the "39" question-number glyph
    real = [_box(716.5, 29, 22), _box(746.5, 31, 20), _box(778.5, 27, 19), _box(808.5, 29, 21)]
    boxes_sorted = [label] + real
    column_dx_samples = [-3.0, -3.17, -3.34, -3.01, -3.0, -2.67, -2.34, -2.51]  # this column's real, established shift
    result = _resolve_extra_boxes_by_column_shift(boxes_sorted, nominal, column_dx_samples, radius=11)
    assert result == real


# -- _drop_sparse_rows: pure logic tests --------------------------------------
#
# Regression coverage for a third real bug found against a real scanned ACT
# sheet, more damaging than the two above: a few glyph-sized characters from
# the *next* section's header (e.g. "TEST 2: MATHEMATICS") fell inside the
# current section's search window (padded generously to tolerate vertical
# drift -- see _section_roi) and clustered into their own sparse "row". That
# pushed the row count one over the expected count, which failed
# locate_section_bubbles's exact-match check for the *entire section* and
# fell back to uncorrected nominal coordinates for all of it -- English,
# Math, and Reading all silently fell back on the real sheet, corrupting the
# majority of that student's answers instead of just one row's.
#
# A first version of this fix dropped any row sparse relative to the
# others, with no regard for whether the count was actually over
# `expected_rows`. That broke a *different* real sheet: a pencil smudge
# dragged between two rows left a stray 2-box cluster above a section
# whose real row count already matched expected_rows exactly (one real row
# also lost most of its own boxes to the same smudge, coincidentally
# balancing the total back to the right count) -- dropping the "sparse"
# row made the count come out one *short* instead, which fails the same
# exact-match check from the other direction. Trimming only an actual
# excess, down to exactly expected_rows, fixes the real bug without ever
# discarding a row when there wasn't a surplus to explain in the first
# place.


def _row(n: int) -> list:
    return [_Box(x=i, y=0, w=10, h=10) for i in range(n)]


def test_drop_sparse_rows_trims_a_stray_low_count_row_down_to_expected():
    rows = [_row(20), _row(21), _row(19), _row(3)]
    result = _drop_sparse_rows(rows, expected_rows=3)
    assert [len(r) for r in result] == [20, 21, 19]


def test_drop_sparse_rows_leaves_a_genuinely_sparse_row_when_count_is_not_over():
    # Mirrors the real second bug: total count already equals
    # expected_rows, so nothing should be dropped even though one row is
    # much sparser than the rest (a real row degraded by a smudge, not a
    # stray extra one).
    rows = [_row(16), _row(17), _row(2), _row(19)]
    result = _drop_sparse_rows(rows, expected_rows=4)
    assert result == rows


def test_drop_sparse_rows_leaves_rows_alone_when_count_is_under_expected():
    rows = [_row(20), _row(3)]
    result = _drop_sparse_rows(rows, expected_rows=5)
    assert result == rows


def test_drop_sparse_rows_is_a_no_op_when_nothing_is_sparse():
    rows = [_row(20), _row(19), _row(18)]
    assert _drop_sparse_rows(rows, expected_rows=3) == rows


# -- locate_section_bubbles: column-shift integration test -------------------
#
# End-to-end reproduction of the real bug _retry_with_column_shift fixes
# (see its own docstring above): one column sits, row after row, a
# consistent offset from nominal -- confirmed on a fully-detected
# neighboring row in the same column -- while a *different* row in that
# same column only has some of its real boxes detected at all, and the
# marked one is among the missing. Without the fix, that short row's
# available boxes get matched against raw, uncorrected nominal positions
# and silently land in the wrong slots.


def make_two_column_template() -> Template:
    return Template.from_dict(
        {
            "page": {"width": 900, "height": 900},
            "sections": [
                {
                    "name": "Answers",
                    "columns": [
                        # Column A: rows are drawn shifted (see the test) to
                        # simulate the real per-column perspective drift.
                        {"first_question": 1, "last_question": 2, "x_start": 150, "y_start": 100, "row_height": 60},
                        # Column B: left alone, untouched by the shift, and
                        # far enough away that pasted regions below never
                        # reach it -- exercises that the fix doesn't perturb
                        # a column with no problem of its own.
                        {"first_question": 3, "last_question": 3, "x_start": 600, "y_start": 100, "row_height": 60},
                    ],
                }
            ],
            "bubble_spacing_x": 30,
            "bubble_radius": 11,
            "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
            "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
        }
    )


def test_locate_section_bubbles_recovers_a_short_row_in_a_shifted_column():
    template = make_two_column_template()
    section = template.sections[0]
    pad = template.bubble_radius + 6

    # Q1 (column A) drawn fully shifted +40px, all 4 boxes present -- this
    # is what lets _uniform_shift_match confirm column A's own offset.
    shifted = render_sheet(template, {1: ["F"]}, letters=True, x_shift=40)
    # Base canvas: everything else drawn unshifted (including column A's
    # Q2 row, which gets overwritten below). Q3 (odd) -> F/G/H/J.
    image = render_sheet(template, {3: ["G"]}, letters=True)

    bubbles = template.bubbles()
    q1_row_y = bubbles[("Answers", 1)][0].y
    image[q1_row_y - pad : q1_row_y + pad, :450] = shifted[q1_row_y - pad : q1_row_y + pad, :450]

    # Column A, row 2 (Q2, even -> A/B/C/D): only 2 of its 4 real boxes are
    # actually present -- A/B genuinely undetected (erased entirely, not
    # just unmarked), C/D drawn at their real, shifted (+40px) position, C
    # filled in as the genuine mark. Mirrors the real case: the row's
    # available boxes are within the retry's shifted cap, not the raw one.
    q2_row_y = bubbles[("Answers", 2)][0].y
    image[q2_row_y - pad : q2_row_y + pad, :450] = 255  # erase Q2's whole (unshifted) row first
    shifted_q2 = render_sheet(template, {2: ["C"]}, letters=True, x_shift=40)
    c_x = next(b.x for b in bubbles[("Answers", 2)] if b.choice == "C") + 40
    d_x = next(b.x for b in bubbles[("Answers", 2)] if b.choice == "D") + 40
    image[q2_row_y - pad : q2_row_y + pad, c_x - pad : d_x + pad] = shifted_q2[
        q2_row_y - pad : q2_row_y + pad, c_x - pad : d_x + pad
    ]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detected = locate_section_bubbles(gray, template, section)
    assert detected is not None

    results, _ = evaluate_sheet(image, template)
    by_q = {r.question: r.answer for r in results}
    # The real bug: without the column-shift retry, Q2's available C/D
    # boxes get matched to A/B's raw-nominal slots instead, silently
    # reading C's ink as "A".
    assert by_q[2] == "C"
    # Column B (never shifted, never missing a box) must read correctly
    # throughout -- confirms the fix doesn't perturb an unrelated column.
    assert by_q[3] == "G"


# -- locate_section_bubbles: stray-label-plus-shift integration test ---------
#
# End-to-end reproduction of the real bug _resolve_extra_boxes_by_column_
# shift fixes (see its own docstring above): a question-number label sized
# close enough to the real bubbles that _drop_size_outlier_boxes correctly
# declined to drop it, landing 5 boxes in a row with only 4 slots. The
# capped match filled every slot anyway -- the label plus the row's first
# 3 real (shifted) bubbles -- silently dropping the row's real *last*
# bubble, the one genuinely marked, and reading a solid, unrelated label
# glyph as the answer instead.


def test_locate_section_bubbles_drops_a_same_sized_label_not_the_true_last_bubble():
    template = make_two_column_template()
    section = template.sections[0]

    # Column A, Q1: fully shifted +20px, all 4 boxes present -- confirms
    # column A's own offset via _uniform_shift_match, same as the test
    # above. Column B, Q3: untouched, unshifted -- non-regression check.
    image = render_sheet(template, {1: ["F"], 3: ["G"]}, letters=True, x_shift=20)

    # Column A, Q2 (even -> A/B/C/D): all 4 real boxes are present, shifted
    # +20px like Q1, with C genuinely marked -- but a same-sized stray
    # label sits where the *unshifted* nominal A would be, giving this row
    # 5 boxes for 4 slots, none of them individually implausible for a
    # position-only match (see this function's own docstring).
    bubbles = template.bubbles()
    q2 = next(b for b in bubbles[("Answers", 2)] if b.choice == "A")
    marked = next(b for b in bubbles[("Answers", 2)] if b.choice == "C")
    cv2.circle(image, (q2.x - 10, q2.y), template.bubble_radius + 2, (0, 0, 0), -1)
    fill_bubble(image, marked.x + 20, marked.y, template.bubble_radius, coverage=1.0, darkness=20)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detected = locate_section_bubbles(gray, template, section)
    assert detected is not None

    results, _ = evaluate_sheet(image, template)
    by_q = {r.question: r.answer for r in results}
    # The real bug: without this fix, the label steals A's slot and every
    # real bubble after it shifts one slot over, reading the label's own
    # solid ink as "A" (or losing the real mark to MULTIPLE/ambiguity)
    # instead of the genuinely marked "C".
    assert by_q[2] == "C"
    # Neither of the other, unaffected rows should be disturbed.
    assert by_q[1] == "F"
    assert by_q[3] == "G"


def test_drop_sparse_rows_trims_multiple_excess_rows():
    rows = [_row(20), _row(2), _row(19), _row(1), _row(21)]
    result = _drop_sparse_rows(rows, expected_rows=3)
    assert [len(r) for r in result] == [20, 19, 21]


def make_template() -> Template:
    # Mirrors the real act_answer_sheet.yaml structure closely enough to
    # exercise multi-column, multi-section detection (including a
    # short/uneven last column, like Math's 41-45).
    data = {
        "page": {"width": 900, "height": 900},
        "sections": [
            {
                "name": "English",
                "columns": [
                    {"first_question": 1, "last_question": 6, "x_start": 150, "y_start": 100, "row_height": 30},
                    {"first_question": 7, "last_question": 12, "x_start": 450, "y_start": 100, "row_height": 30},
                ],
            },
            {
                "name": "Math",
                "columns": [
                    {"first_question": 1, "last_question": 6, "x_start": 150, "y_start": 500, "row_height": 30},
                    {"first_question": 7, "last_question": 9, "x_start": 450, "y_start": 500, "row_height": 30},
                ],
            },
        ],
        "bubble_spacing_x": 30,
        "bubble_radius": 11,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
    }
    return Template.from_dict(data)


def test_locate_section_bubbles_recovers_shifted_grid():
    template = make_template()
    # Simulate the real-world bug: the sheet's actual content is offset
    # from the template's calibrated coordinates by nearly a full row.
    image = render_sheet(
        template, {("English", 1): ["F"]}, ink_color=(100, 110, 230), letters=True, y_shift=-20
    )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    section = template.sections[0]
    detected = locate_section_bubbles(gray, template, section)
    assert detected is not None

    nominal_y = section.columns[0].y_start
    detected_y = detected[1][0][2]  # (choice, x, y) for question 1's first choice
    assert abs(detected_y - (nominal_y - 20)) <= 2, "detected position should track the real shift"


def test_locate_section_bubbles_returns_none_when_nothing_detected():
    template = make_template()
    blank_white = 255 * np.ones((900, 900), dtype="uint8")
    section = template.sections[0]
    assert locate_section_bubbles(blank_white, template, section) is None


def test_evaluate_sheet_reads_correct_answers_despite_grid_shift():
    template = make_template()
    # Q1/Q7/Math-Q1/Math-Q9 are odd -> F/G/H/J; Q2 is even -> A/B/C/D.
    answers = {
        ("English", 1): ["F"],
        ("English", 2): ["B"],
        ("English", 7): ["H"],
        ("Math", 1): ["F"],
        ("Math", 9): ["J"],
    }
    # A shift like this previously caused answers to be read from the
    # adjacent row/bubble instead of the correct one.
    image = render_sheet(template, answers, ink_color=(100, 110, 230), letters=True, y_shift=-20)

    results, fallback_sections = evaluate_sheet(image, template)
    assert fallback_sections == []

    by_key = {(r.section, r.question): r.answer for r in results}
    assert by_key[("English", 1)] == "F"
    assert by_key[("English", 2)] == "B"
    assert by_key[("English", 7)] == "H"
    assert by_key[("Math", 1)] == "F"
    assert by_key[("Math", 9)] == "J"
    # Untouched questions should still read blank, not some other answer.
    assert by_key[("English", 3)] == ""


def test_evaluate_sheet_falls_back_gracefully_when_detection_fails():
    template = make_template()
    # A section drawn far outside where the template expects it (well
    # beyond grid_detect's search window) can't be located; evaluate_sheet
    # should still return a full (if less accurate) result rather than
    # crashing, and report the section as a fallback.
    image = render_sheet(template, {("English", 1): ["F"]}, y_shift=300)

    results, fallback_sections = evaluate_sheet(image, template)
    assert "English" in fallback_sections
    assert len(results) == template.sections[0].num_questions + template.sections[1].num_questions


# -- _find_glyph_boxes / locate_section_bubbles: shaded-row-band fallback ----
#
# Regression coverage for an eighth real bug, found on a real scanned
# sheet: it printed an alternating light-gray background band behind
# every other row (a legibility aid, common on official-style forms),
# measuring 130-230 in raw pixel value on the shaded rows -- well under
# _find_glyph_boxes's fixed global threshold (200). The *entire* band, not
# just the ink in it, binarized as foreground and merged into oversized
# blobs that failed the glyph size filter outright, losing every bubble in
# those rows and failing every candidate template's structural match, on
# every section of the sheet (each has some shaded rows). Fixed with an
# adaptive-threshold fallback (see _find_glyph_boxes's `adaptive`
# parameter), reached only once the ordinary fixed-threshold pass has
# already failed to find a section's expected row structure -- confirmed
# against the full regression fleet that this changes nothing for any
# section the fixed-threshold pass already read correctly.


def _shaded_row_canvas(radius: int) -> np.ndarray:
    """A synthetic two-row, 4-bubble-per-row patch: row 1 on a plain white
    background, row 2 on a light-gray shaded band (value 165, well under
    the fixed threshold) -- reproducing the real sheet's own measured
    shape closely enough that a fixed global threshold merges row 2's
    bubbles into its background the same way."""
    canvas = np.full((110, 400), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (0, 60), (400, 90), 165, -1)
    for y in (25, 75):
        for i in range(4):
            cx = 40 + i * 40
            cv2.ellipse(canvas, (cx, y), (round(radius * 1.27), round(radius * 0.91)), 0, 0, 360, 0, 2)
    return canvas


def test_find_glyph_boxes_fixed_threshold_loses_the_shaded_rows_bubbles():
    radius = 11
    canvas = _shaded_row_canvas(radius)
    boxes = _find_glyph_boxes(canvas, radius, (0, 0, canvas.shape[1], canvas.shape[0]), adaptive=False)
    # Only row 1's (unshaded) 4 bubbles survive; row 2's are gone entirely.
    assert len(boxes) == 4
    assert all(b.cy < 50 for b in boxes)


def test_find_glyph_boxes_adaptive_recovers_both_rows():
    radius = 11
    canvas = _shaded_row_canvas(radius)
    boxes = _find_glyph_boxes(canvas, radius, (0, 0, canvas.shape[1], canvas.shape[0]), adaptive=True)
    assert len(boxes) == 8
    assert sum(1 for b in boxes if b.cy < 50) == 4  # row 1, unshaded
    assert sum(1 for b in boxes if b.cy > 50) == 4  # row 2, shaded


def _shaded_template() -> Template:
    return Template.from_dict(
        {
            "page": {"width": 900, "height": 900},
            "sections": [
                {
                    "name": "Answers",
                    "columns": [{"first_question": 1, "last_question": 6, "x_start": 150, "y_start": 100, "row_height": 30}],
                }
            ],
            "bubble_spacing_x": 30,
            "bubble_radius": 11,
            "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
            "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
        }
    )


def _draw_shaded_bands(image: np.ndarray, template: Template, questions) -> None:
    bubbles = template.bubbles()
    pad = template.bubble_radius + 6
    for q in questions:
        y = bubbles[("Answers", q)][0].y
        cv2.rectangle(image, (0, y - pad), (image.shape[1], y + pad), (170, 170, 170), -1)
        for b in bubbles[("Answers", q)]:
            cv2.circle(image, (b.x, b.y), template.bubble_radius, (0, 0, 0), 3)
            cv2.putText(image, b.choice, (b.x - 6, b.y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)


def test_locate_section_bubbles_returns_none_for_a_shaded_section_without_the_fallback():
    # Confirms the fixture actually reproduces the real failure -- without
    # ever calling the adaptive fallback -- before checking the fix below.
    template = _shaded_template()
    image = make_blank_sheet(template, letters=True)
    _draw_shaded_bands(image, template, (2, 4, 6))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    boxes = _find_glyph_boxes(gray, template.bubble_radius, (0, 0, 900, 900), adaptive=False)
    row_tolerance = template.bubble_radius * 0.75
    rows = _drop_sparse_rows(_cluster_rows(boxes, row_tolerance), 6)
    assert len(rows) != 6


def test_locate_section_bubbles_recovers_a_fully_shaded_row_section():
    # Scoped to locate_section_bubbles's own job -- finding each shaded
    # row's real bubble positions -- rather than the full evaluate_sheet
    # answer, which also depends on binarize()'s separate, already-tested
    # baseline-adjustment handling of an elevated fill_ratio floor (see
    # test_detect.py's own heavy-baseline-print tests) that a flat
    # synthetic shading block doesn't reproduce faithfully enough to
    # exercise here.
    template = _shaded_template()
    image = make_blank_sheet(template, letters=True)
    _draw_shaded_bands(image, template, (2, 4, 6))

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    section = template.sections[0]
    detected = locate_section_bubbles(gray, template, section)
    assert detected is not None
    for q in range(1, 7):
        assert len(detected[q]) == 4
        # Every detected bubble should land within a couple px of its own
        # nominal position -- confirming the shaded rows' positions were
        # genuinely recovered, not just present in some wrong location.
        nominal = {b.choice: (b.x, b.y) for b in template.bubbles()[("Answers", q)]}
        for choice, x, y in detected[q]:
            nx, ny = nominal[choice]
            assert abs(x - nx) <= 2
            assert abs(y - ny) <= 2
