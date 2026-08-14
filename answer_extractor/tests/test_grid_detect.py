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
    _drop_size_outlier_boxes,
    _drop_sparse_rows,
    _match_to_slots,
    _uniform_shift_match,
    locate_section_bubbles,
)
from answer_extractor.template import Template
from tests.synth import render_sheet


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
