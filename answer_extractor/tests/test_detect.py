import pytest

from answer_extractor.detect import _baseline_adjust, decide_answer, evaluate_sheet
from answer_extractor.template import Template
from tests.synth import render_sheet


def make_template() -> Template:
    data = {
        "page": {"width": 900, "height": 700},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 6, "x_start": 150, "y_start": 100, "row_height": 80},
                ],
            }
        ],
        "bubble_spacing_x": 60,
        "bubble_radius": 18,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
    }
    return Template.from_dict(data)


# -- decide_answer: pure logic tests -----------------------------------------


def test_single_clear_mark():
    ratios = {"A": 0.9, "B": 0.02, "C": 0.0, "D": 0.01}
    answer, candidates, low_conf = decide_answer(ratios, 0.35, 0.15)
    assert answer == "A"
    assert candidates == ["A"]
    assert not low_conf


def test_blank_question():
    ratios = {"A": 0.02, "B": 0.0, "C": 0.01, "D": 0.0}
    answer, candidates, _ = decide_answer(ratios, 0.35, 0.15)
    assert answer == ""
    assert candidates == []


def test_multiple_marks_detected():
    ratios = {"A": 0.9, "B": 0.85, "C": 0.0, "D": 0.0}
    answer, candidates, _ = decide_answer(ratios, 0.35, 0.15)
    assert answer == "MULTIPLE"
    assert candidates == ["A", "B"]


def test_light_partial_mark_still_counts():
    # A sloppy/partial mark at 0.4 should still register given the 0.35 floor.
    ratios = {"F": 0.4, "G": 0.03, "H": 0.0, "J": 0.02}
    answer, candidates, low_conf = decide_answer(ratios, 0.35, 0.15)
    assert answer == "F"
    assert low_conf  # close to the floor -> flagged for human review


def test_uneven_double_mark_within_margin_is_multiple():
    # One bubble darker than the other, but still close enough to both be
    # "real" marks rather than one mark plus stray noise.
    ratios = {"A": 0.9, "B": 0.78, "C": 0.0, "D": 0.0}
    answer, candidates, _ = decide_answer(ratios, 0.35, 0.15)
    assert answer == "MULTIPLE"
    assert set(candidates) == {"A", "B"}


def test_stray_smudge_below_floor_does_not_trigger_multiple():
    ratios = {"A": 0.9, "B": 0.2, "C": 0.0, "D": 0.0}
    answer, candidates, _ = decide_answer(ratios, 0.35, 0.15)
    assert answer == "A"
    assert candidates == ["A"]


# -- _baseline_adjust / real-scan "high baseline ink" regression -------------
#
# Regression coverage for a real bug found against a real scanned ACT sheet:
# the printed bubble ring + choice letter are themselves dark ink, so even a
# truly blank bubble isn't near 0 -- on that scan, blank bubbles measured
# ~0.38-0.51 raw. Since fill_ratio_min (0.35) sat below that noise floor,
# every choice in a blank question cleared it and all four ended up within
# relative_margin of each other, reading as MULTIPLE despite nothing being
# marked. Subtracting each question's own minimum (a per-question estimate
# of that shared baseline) before deciding fixes it without needing raw
# pixel measurements to already be near 0, and does nothing when they
# already are (clean scans/synthetic tests are unaffected).


def test_baseline_adjust_subtracts_the_row_minimum():
    ratios = {"A": 0.47, "B": 0.48, "C": 0.38, "D": 0.51}
    assert _baseline_adjust(ratios) == pytest.approx({"A": 0.09, "B": 0.10, "C": 0.0, "D": 0.13})


def test_baseline_adjust_is_a_no_op_when_blanks_are_already_near_zero():
    ratios = {"A": 0.9, "B": 0.02, "C": 0.0, "D": 0.01}
    assert _baseline_adjust(ratios) == ratios


def test_decide_answer_high_shared_baseline_blank_question_is_not_multiple():
    # All four choices carry the same ~0.4-0.5 "printed ring + letter" ink
    # floor and nothing is actually marked -- must read blank, not MULTIPLE.
    ratios = {"A": 0.466, "B": 0.478, "C": 0.383, "D": 0.506}
    answer, candidates, _ = decide_answer(ratios, 0.20, 0.15)
    assert answer == ""
    assert candidates == []


def test_decide_answer_high_shared_baseline_single_mark_still_wins():
    # Same ~0.4-0.5 baseline on the unmarked choices, but one choice is
    # genuinely (if lightly) marked well above it.
    ratios = {"A": 0.502, "B": 0.514, "C": 0.771, "D": 0.526}
    answer, candidates, _ = decide_answer(ratios, 0.20, 0.15)
    assert answer == "C"
    assert candidates == ["C"]


# -- evaluate_sheet: rendered-image tests ------------------------------------


def test_evaluate_sheet_end_to_end():
    template = make_template()
    answers = {1: ["F"], 2: ["B"], 3: [], 4: ["A", "C"], 5: ["J"], 6: ["D"]}
    image = render_sheet(template, answers)

    results = {r.question: r for r in evaluate_sheet(image, template)[0]}
    assert results[1].answer == "F"
    assert results[2].answer == "B"
    assert results[3].answer == ""  # left blank
    assert results[4].answer == "MULTIPLE"
    assert set(results[4].candidates) == {"A", "C"}
    assert results[5].answer == "J"
    assert results[6].answer == "D"


def test_evaluate_sheet_tolerates_partial_sloppy_marks():
    template = make_template()
    answers = {1: ["G"]}
    # 55% coverage and lighter gray pencil-like mark instead of solid fill.
    image = render_sheet(template, answers, coverage=0.55, darkness=110)

    results = {r.question: r for r in evaluate_sheet(image, template)[0]}
    assert results[1].answer == "G"
