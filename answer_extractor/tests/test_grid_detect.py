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
from answer_extractor.grid_detect import _Box, _drop_sparse_rows, _match_to_slots, locate_section_bubbles
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


def _row(n: int) -> list:
    return [_Box(x=i, y=0, w=10, h=10) for i in range(n)]


def test_drop_sparse_rows_removes_a_stray_low_count_row():
    rows = [_row(20), _row(21), _row(19), _row(3)]
    result = _drop_sparse_rows(rows)
    assert [len(r) for r in result] == [20, 21, 19]


def test_drop_sparse_rows_keeps_a_genuinely_short_row():
    # A column with fewer active questions near the bottom of a section
    # (e.g. Math's 5th column having 9 questions where the others have 10)
    # only shrinks one column's contribution to that row, not the whole
    # row -- it shouldn't be treated the same as a handful of stray boxes.
    rows = [_row(20), _row(21), _row(16), _row(19)]
    result = _drop_sparse_rows(rows)
    assert [len(r) for r in result] == [20, 21, 16, 19]


def test_drop_sparse_rows_is_a_no_op_when_nothing_is_sparse():
    rows = [_row(20), _row(19), _row(18)]
    assert _drop_sparse_rows(rows) == rows


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
