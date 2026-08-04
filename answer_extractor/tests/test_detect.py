import numpy as np
import pytest

from answer_extractor.detect import (
    _baseline_adjust,
    _crop_patch,
    _partial_mark_choice,
    _residual_ratio,
    build_choice_templates,
    decide_answer,
    evaluate_sheet,
)
from answer_extractor.template import Template
from tests.synth import fill_bubble, make_blank_sheet, render_sheet


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


def test_decide_answer_unusually_bold_unmarked_choice_is_not_multiple():
    # Regression case from a real scan: one unmarked bubble happened to be
    # printed more boldly than its neighbors (thicker ring + letter, not a
    # student mark -- confirmed against the source image), landing only
    # ~0.146 below the genuinely marked choice after baseline subtraction.
    # relative_margin=0.13 (act_answer_sheet.yaml's tuned value) must not
    # flag this as MULTIPLE.
    ratios = {"F": 0.988, "G": 0.735, "H": 0.842, "J": 0.597}
    answer, candidates, _ = decide_answer(ratios, 0.20, 0.13)
    assert answer == "F"
    assert candidates == ["F"]


# -- partial-mark detection (checkmarks/scribbles instead of solid fills) ----
#
# Real ACT sheets explicitly instruct against marking with a checkmark
# instead of filling the bubble in -- but real students do it anyway. A
# checkmark only covers a small fraction of the bubble's area, often too
# little for score_bubbles's area-based fill ratio to distinguish from the
# printed ring + letter's own ink, so evaluate_sheet only consults this
# secondary "how much extra ink does this bubble have vs. its usual
# unmarked appearance" signal for questions the ordinary fill-ratio signal
# already gave up on (blank or MULTIPLE) -- see the threshold comment on
# _PARTIAL_MARK_MIN_TOP/_PARTIAL_MARK_MIN_GAP for why it's deliberately not
# trusted to override an already-confident fill-ratio answer.


def test_partial_mark_choice_picks_a_clear_isolated_leader():
    residuals = {"A": 0.02, "B": 0.03, "C": 0.12, "D": 0.01}
    assert _partial_mark_choice(residuals) == "C"


def test_partial_mark_choice_none_when_nothing_clears_the_floor():
    residuals = {"A": 0.02, "B": 0.03, "C": 0.05, "D": 0.01}
    assert _partial_mark_choice(residuals) is None


def test_partial_mark_choice_none_when_top_two_are_not_isolated():
    # Regression case from a real scan: a question confirmed genuinely
    # blank still had one choice edge out the others by a small amount --
    # not enough of a gap to trust as a real, isolated mark.
    residuals = {"A": 0.145, "B": 0.066, "C": 0.13, "D": 0.18}
    assert _partial_mark_choice(residuals) is None


def test_build_choice_templates_and_residual_ratio_isolate_extra_ink():
    # A 21x21 all-white image with a 5x5 dark square baked into every "A"
    # occurrence except one, which additionally has a second dark square
    # elsewhere in the bubble -- the common square is "normal" ink (an
    # unmarked bubble's ring/letter) and shouldn't count; the extra one
    # should.
    binary = np.zeros((60, 60), dtype=np.uint8)
    common_coords = [(10, 10), (10, 30), (10, 50), (30, 10)]
    for x, y in common_coords:
        binary[y - 2 : y + 3, x - 2 : x + 3] = 255
    marked_x, marked_y = 30, 30
    binary[marked_y - 2 : marked_y + 3, marked_x - 2 : marked_x + 3] = 255
    binary[marked_y - 2 : marked_y + 3, marked_x + 4 : marked_x + 9] = 255  # the "extra" ink

    all_coords = common_coords + [(marked_x, marked_y)]
    templates = build_choice_templates(binary, {"A": all_coords}, radius=8)

    unmarked_ratio = _residual_ratio(binary, 10, 10, radius=8, choice_template=templates["A"])
    marked_ratio = _residual_ratio(binary, marked_x, marked_y, radius=8, choice_template=templates["A"])
    assert marked_ratio > unmarked_ratio
    assert unmarked_ratio == pytest.approx(0.0, abs=1e-6)


def test_evaluate_sheet_catches_a_light_partial_mark_fill_ratio_alone_misses():
    template = make_template()
    # Fill every question solidly except Q3, whose F is marked at low
    # enough coverage/darkness that fill_ratio alone reads it as blank
    # (verified directly: decide_answer returns "" at this template's
    # thresholds), so the only way it comes through is via the
    # residual/partial-mark path.
    answers = {1: ["F"], 2: ["B"], 4: ["A"], 5: ["J"], 6: ["D"]}
    image = render_sheet(template, answers, letters=True)
    bubble = next(b for b in template.bubbles()[("Answers", 3)] if b.choice == "F")
    fill_bubble(image, bubble.x, bubble.y, template.bubble_radius, coverage=0.4, darkness=30)

    results, _ = evaluate_sheet(image, template)
    q3 = next(r for r in results if r.question == 3)
    assert q3.answer == "F"
    assert q3.low_confidence  # always flagged when it came from the secondary signal


def test_evaluate_sheet_never_overrides_an_already_confident_answer():
    # Even if a bubble happens to look unusual under the residual signal,
    # evaluate_sheet must not touch a question fill_ratio already answered
    # confidently -- the secondary signal is only ever consulted when
    # fill_ratio itself came back blank or MULTIPLE.
    template = make_template()
    answers = {1: ["F"], 2: ["B"], 3: ["H"], 4: ["A"], 5: ["J"], 6: ["D"]}
    image = render_sheet(template, answers, letters=True)

    results, _ = evaluate_sheet(image, template)
    by_q = {r.question: r for r in results}
    assert by_q[3].answer == "H"
    assert not by_q[3].low_confidence


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
