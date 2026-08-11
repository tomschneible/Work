import numpy as np
import pytest

from answer_extractor.detect import (
    QuestionResult,
    _baseline_adjust,
    _combined_partial_mark_choice,
    _crop_patch,
    _infer_from_answer_pattern,
    _partial_mark_choice,
    _raw_top_gap,
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


def test_partial_mark_choice_looser_thresholds_catch_a_smaller_gap():
    # The tie-break call site (evaluate_sheet, only reached when
    # fill_ratio's own top two choices are already a near-tie -- see
    # _raw_top_gap) passes looser thresholds than the blank/MULTIPLE path,
    # since arbitrating between choices fill_ratio already found plausible
    # needs less of a safety margin than detecting a mark from nothing.
    from answer_extractor.detect import _TIEBREAK_MIN_GAP, _TIEBREAK_MIN_TOP

    residuals = {"F": 0.088, "G": 0.059, "H": 0.038, "J": 0.049}
    assert _partial_mark_choice(residuals) is None  # too weak for the strict thresholds
    assert _partial_mark_choice(residuals, _TIEBREAK_MIN_TOP, _TIEBREAK_MIN_GAP) == "F"


# -- _combined_partial_mark_choice: pure logic tests --------------------------
#
# A user-reported second round: several questions on the same checkmarked
# sheet stayed blank even after the strict residual-only check above,
# because neither fill_ratio's own raw gap nor the residual gap alone
# cleared its bar -- but a real (if faint) mark nudges *both* signals in
# the same direction even when neither individually is convincing, while a
# truly blank question doesn't. This combines them as a second, additive
# check (never a replacement for the strict one, which stays as-is).


def test_combined_partial_mark_choice_catches_a_mark_neither_signal_alone_would():
    # Real scan case: fill_ratio's raw gap (0.059, English Q23-shaped) and
    # the residual gap (0.036) are each too weak alone -- 0.059 < 0.10
    # isn't decisive for fill_ratio, 0.036 < 0.045 misses the strict
    # residual bar -- but together (0.095) clear _COMBINED_MIN_GAP.
    fill_ratios = {"A": 0.708, "B": 0.648, "C": 0.581, "D": 0.64}
    residuals = {"A": 0.09, "B": 0.053, "C": 0.054, "D": 0.043}
    assert _partial_mark_choice(residuals) is None  # confirms neither alone would catch it
    assert _combined_partial_mark_choice(fill_ratios, residuals) == "A"


def test_combined_partial_mark_choice_none_for_a_confirmed_blank_question():
    # Regression case: the same two questions independently confirmed
    # genuinely blank (see _PARTIAL_MARK_MIN_GAP's comment) must also stay
    # blank under the combined check, not just the strict one.
    fill_ratios = {"A": 0.455, "B": 0.51, "C": 0.474, "D": 0.502}
    residuals = {"A": 0.145, "B": 0.066, "C": 0.13, "D": 0.18}
    assert _combined_partial_mark_choice(fill_ratios, residuals) is None


def test_combined_partial_mark_choice_can_pick_a_choice_fill_ratio_did_not_favor():
    # Residual is the more specific signal for *which* choice a partial
    # mark is on -- the combined check trusts its top choice even when it
    # differs from fill_ratio's own (noisier) raw top choice.
    fill_ratios = {"F": 0.597, "G": 0.625, "H": 0.569, "J": 0.451}  # raw top: G
    residuals = {"F": 0.074, "G": 0.038, "H": 0.033, "J": 0.04}  # residual top: F
    assert _combined_partial_mark_choice(fill_ratios, residuals) == "F"


# -- _infer_from_answer_pattern: pure logic tests -----------------------------
#
# User follow-up: even the combined ink-based check above still left a
# handful of questions blank on the checkmarked sheet. Every one of them
# sat in the middle of an otherwise unbroken run of 20-30+ consecutive
# questions where the student marked the same choice *position* (e.g.
# always the 1st of A/B/C/D, always the 1st of F/G/H/J) -- real behavior
# for a student guessing/rushing through the end of a section. This is a
# last-resort, context-only inference: it never looks at the blank
# bubble's own ink, only at what's confidently known about its neighbors.


def _qr(question: int, answer: str, low_confidence: bool = False) -> QuestionResult:
    candidates = [] if answer in ("", "MULTIPLE") else [answer]
    return QuestionResult(
        section="Answers",
        question=question,
        answer=answer,
        candidates=candidates,
        fill_ratios={},
        low_confidence=low_confidence,
    )


def _pattern_template() -> Template:
    return Template.from_dict(
        {
            "page": {"width": 900, "height": 700},
            "sections": [
                {
                    "name": "Answers",
                    "columns": [{"first_question": 1, "last_question": 20, "x_start": 150, "y_start": 100, "row_height": 30}],
                }
            ],
            "bubble_spacing_x": 60,
            "bubble_radius": 18,
            "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
            "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
        }
    )


def _idx0(question: int) -> str:
    """The choice at index 0 for `question`'s own odd/even choice set --
    "F" for odd, "A" for even, per _pattern_template -- so test sequences
    are correct by construction instead of hand-alternated and error-prone."""
    return "F" if question % 2 else "A"


def test_infer_from_answer_pattern_fills_a_gap_in_a_long_bracketing_run():
    template = _pattern_template()
    # Q1-4 and Q6-9 all at index 0 of their own choice set, Q5 blank in the
    # middle -- 4 on each side, comfortably over the total.
    results = [_qr(q, _idx0(q)) for q in (1, 2, 3, 4)] + [_qr(5, "")] + [_qr(q, _idx0(q)) for q in (6, 7, 8, 9)]
    updated = _infer_from_answer_pattern(results, template)
    q5 = next(r for r in updated if r.question == 5)
    assert q5.answer == _idx0(5)
    assert q5.pattern_inferred
    assert q5.low_confidence


def test_infer_from_answer_pattern_leaves_a_short_run_blank():
    template = _pattern_template()
    # Only 1 on each side of the gap -- nowhere near _PATTERN_MIN_TOTAL_RUN.
    results = [_qr(1, _idx0(1)), _qr(2, ""), _qr(3, _idx0(3))]
    updated = _infer_from_answer_pattern(results, template)
    q2 = next(r for r in updated if r.question == 2)
    assert q2.answer == ""
    assert not q2.pattern_inferred


def test_infer_from_answer_pattern_requires_agreement_on_both_sides():
    template = _pattern_template()
    # Long runs on both sides, but they don't agree with each other --
    # index 0 on the left, index 1 on the right. Not a real pattern.
    idx1 = {1: "G", 0: "B"}  # index 1 of odd/even choices, keyed by parity
    results = [_qr(q, _idx0(q)) for q in (1, 2, 3, 4)] + [_qr(5, "")] + [_qr(q, idx1[q % 2]) for q in (6, 7, 8, 9)]
    updated = _infer_from_answer_pattern(results, template)
    q5 = next(r for r in updated if r.question == 5)
    assert q5.answer == ""


def test_infer_from_answer_pattern_never_touches_a_directly_detected_answer():
    # Even a low-confidence *directly detected* answer must never be
    # second-guessed by this context-only inference -- real pixel evidence
    # always wins, however weak.
    template = _pattern_template()
    results = (
        [_qr(q, _idx0(q)) for q in (1, 2, 3, 4)]
        + [_qr(5, "H", low_confidence=True)]  # a real (if shaky) detected answer
        + [_qr(q, _idx0(q)) for q in (6, 7, 8, 9)]
    )
    updated = _infer_from_answer_pattern(results, template)
    q5 = next(r for r in updated if r.question == 5)
    assert q5.answer == "H"
    assert not q5.pattern_inferred


def test_infer_from_answer_pattern_counts_the_total_across_both_sides():
    # Mirrors a real case: a run that starts right after a genuine earlier
    # answer, leaving only a short run on one side of the gap -- too short
    # alone, but the combined total across both sides is what's actually
    # thresholded (see _PATTERN_MIN_TOTAL_RUN's comment for why).
    template = _pattern_template()
    results = (
        [_qr(1, "H")]  # a genuine unrelated earlier answer, not part of the pattern
        + [_qr(q, _idx0(q)) for q in (2, 3)]
        + [_qr(4, "")]
        + [_qr(q, _idx0(q)) for q in (5, 6, 7, 8, 9, 10)]
    )
    updated = _infer_from_answer_pattern(results, template)
    q4 = next(r for r in updated if r.question == 4)
    assert q4.answer == _idx0(4)
    assert q4.pattern_inferred


# -- _raw_top_gap: pure logic tests -------------------------------------------
#
# Regression coverage for a real scan where fill_ratio's own pick was
# outright wrong -- not just "low confidence" -- because two choices'
# *raw* fill ratios were a near-tie (0.664 vs 0.644) even though the
# baseline-adjusted gap that low_confidence is computed from made it look
# like an ordinary borderline call. A real answer that's simply darker
# than the rest by a wide raw margin (confirmed against two other real,
# unrelated scans) can still fall in low_confidence range after baseline
# adjustment, so low_confidence alone can't distinguish "genuinely close
# call" from "clearly darkest, just not by enough after adjustment" --
# _raw_top_gap is what evaluate_sheet uses to tell them apart before
# deciding whether the residual signal should be allowed to arbitrate.


def test_raw_top_gap_computes_the_difference_between_the_top_two():
    assert _raw_top_gap({"F": 0.664, "G": 0.644, "H": 0.557, "J": 0.458}) == pytest.approx(0.02)


def test_raw_top_gap_none_with_fewer_than_two_choices():
    assert _raw_top_gap({"A": 0.5}) is None
    assert _raw_top_gap({}) is None


def test_raw_top_gap_large_for_a_decisive_pick():
    # A real, clearly-darkest answer (confirmed against the source scan)
    # that still landed in low_confidence range after baseline adjustment
    # -- its raw gap is nowhere near the near-tie case above.
    assert _raw_top_gap({"A": 0.573, "B": 0.771, "C": 0.561, "D": 0.514}) == pytest.approx(0.198)


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
