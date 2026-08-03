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
from answer_extractor.grid_detect import locate_section_bubbles
from answer_extractor.template import Template
from tests.synth import render_sheet


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
