import cv2
import numpy as np
import pytest

from answer_extractor.detect import (
    QuestionResult,
    _baseline_adjust,
    _crop_patch,
    _dark_fraction,
    _find_mark_floor,
    _infer_from_answer_pattern,
    _partial_mark_agrees_with_fill_ratio,
    _partial_mark_choice,
    _reconsider_low_confidence_pattern,
    _residual_ratio,
    _solid_fill_choice,
    _solidity,
    _value_channel,
    binarize,
    build_choice_templates,
    decide_answer,
    evaluate_sheet,
    score_bubbles,
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


def test_partial_mark_agrees_with_fill_ratio_when_it_leads_the_ranking():
    fill_ratios = {"A": 0.8, "B": 0.6, "C": 0.6, "D": 0.65}  # baseline 0.6 -> A leads at 0.2
    assert _partial_mark_agrees_with_fill_ratio("A", fill_ratios)


def test_partial_mark_disagrees_with_fill_ratio_when_a_different_choice_leads():
    # Regression case: a real scan's residual signal alone picked "A" for
    # Science Q19, but A was genuinely unmarked (bold print, confirmed
    # against the source scan) and fill_ratio's own (sub-threshold)
    # ranking -- what's left once _partial_mark_choice already required
    # score_bubbles to have given up -- put a *different* choice (D) on
    # top. Trusting the residual pick anyway silently overrode a genuine
    # (if faint) mark elsewhere in the row with the wrong letter.
    fill_ratios = {"A": 0.628, "B": 0.589, "C": 0.589, "D": 0.652}  # baseline B/C -> D leads, not A
    assert not _partial_mark_agrees_with_fill_ratio("A", fill_ratios)


def test_partial_mark_choice_none_for_a_bolder_printed_letter_across_a_blank_stretch():
    # Regression case from a real scan: a different form printed "G"
    # noticeably bolder than its neighbors across an entire section a
    # student left blank (confirmed against the source scan and an
    # explicit "did not finish" annotation on the page) -- its residual
    # gap reached 0.044, just under the strict threshold. Two more
    # permissive variants of this check (a looser tie-break pair, and a
    # check that summed fill_ratio's raw gap with the residual gap) were
    # tried and reverted after this same sheet proved neither has a safe
    # threshold: this sheet's false-positive ceiling on those checks
    # (0.13 combined, 0.044 tie-break) exceeded the weakest real signal
    # either was built to catch on a different sheet (0.055, 0.029).
    residuals = {"F": 0.049, "G": 0.093, "H": 0.045, "J": 0.041}
    assert _partial_mark_choice(residuals) is None


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


def test_infer_from_answer_pattern_fills_two_consecutive_blanks_in_a_long_run():
    # Real case (Goldman): two blanks sitting back-to-back in the middle of
    # a 20+ question guessing run. An earlier version of this only ever
    # checked a blank's *immediate* neighbor -- for two adjacent blanks,
    # each one's immediate neighbor on one side is the *other* blank, not
    # a real answer, so neither ever got matched even though the run
    # obviously continues straight through both. Q5 and Q6 blank here,
    # comfortably bracketed by a long run on both sides.
    template = _pattern_template()
    results = (
        [_qr(q, _idx0(q)) for q in (1, 2, 3, 4)]
        + [_qr(5, ""), _qr(6, "")]
        + [_qr(q, _idx0(q)) for q in (7, 8, 9, 10)]
    )
    updated = _infer_from_answer_pattern(results, template)
    q5 = next(r for r in updated if r.question == 5)
    q6 = next(r for r in updated if r.question == 6)
    assert q5.answer == _idx0(5)
    assert q5.pattern_inferred
    assert q5.low_confidence
    assert q6.answer == _idx0(6)
    assert q6.pattern_inferred


def test_infer_from_answer_pattern_leaves_a_short_consecutive_blank_run_blank():
    # Same shape as the short-single-blank case, but with two consecutive
    # blanks -- still nowhere near _PATTERN_MIN_TOTAL_RUN, so neither
    # should be inferred just because the multi-blank path now exists.
    template = _pattern_template()
    results = [_qr(1, _idx0(1)), _qr(2, ""), _qr(3, ""), _qr(4, _idx0(4))]
    updated = _infer_from_answer_pattern(results, template)
    q2 = next(r for r in updated if r.question == 2)
    q3 = next(r for r in updated if r.question == 3)
    assert q2.answer == ""
    assert not q2.pattern_inferred
    assert q3.answer == ""
    assert not q3.pattern_inferred


def test_infer_from_answer_pattern_leaves_a_multiple_run_between_mismatched_sides_blank():
    # Three consecutive MULTIPLE results bracketed by long runs that don't
    # agree with each other -- not a real pattern, both sides of the whole
    # run must still agree, same as the single-gap case.
    template = _pattern_template()
    idx1 = {1: "G", 0: "B"}  # index 1 of odd/even choices, keyed by parity
    results = (
        [_qr(q, _idx0(q)) for q in (1, 2, 3, 4)]
        + [_qr(5, "MULTIPLE"), _qr(6, "MULTIPLE"), _qr(7, "MULTIPLE")]
        + [_qr(q, idx1[q % 2]) for q in (8, 9, 10, 11)]
    )
    updated = _infer_from_answer_pattern(results, template)
    for q in (5, 6, 7):
        r = next(r for r in updated if r.question == q)
        assert r.answer == "MULTIPLE"
        assert not r.pattern_inferred


def test_infer_from_answer_pattern_bridges_scattered_non_adjacent_blanks():
    # Real case (Goldman): a single long guessing run (20+ questions) with
    # several blanks scattered through it, none adjacent to each other.
    # Each individual blank IS bracketed by the same choice index on both
    # sides once you skip past the *other* blanks -- but an earlier version
    # of this only counted up to the very next blank, which undercounted
    # the real evidence and left every one of them below the threshold.
    # Q3, Q6, and Q9 are blank here, each isolated from the others by real
    # answers, all part of one long idx0 run from Q1 to Q12.
    template = _pattern_template()
    blanks = {3, 6, 9}
    results = [_qr(q, "" if q in blanks else _idx0(q)) for q in range(1, 13)]
    updated = _infer_from_answer_pattern(results, template)
    for q in blanks:
        r = next(r for r in updated if r.question == q)
        assert r.answer == _idx0(q)
        assert r.pattern_inferred


def test_infer_from_answer_pattern_a_genuine_mismatch_still_blocks_inference():
    # "Skip past other blanks" must only ever skip *blanks* -- a genuine,
    # different real answer immediately next to the gap still blocks
    # inference outright, no matter how long a matching run sits further
    # away on the other side. Q11 is a real, different answer (index 1)
    # sitting right next to the blank at Q12; Q13 onward goes back to a
    # long idx0 run, but Q12's nearest real neighbors (Q11 and Q13) simply
    # disagree, which must never be bridged over.
    template = _pattern_template()
    idx1 = {1: "G", 0: "B"}  # index 1 of odd/even choices, keyed by parity
    results = (
        [_qr(q, _idx0(q)) for q in range(1, 11)]  # Q1-10: long idx0 run
        + [_qr(11, idx1[11 % 2])]  # genuine mismatch immediately before the gap
        + [_qr(12, "")]
        + [_qr(q, _idx0(q)) for q in range(13, 21)]  # long idx0 run resumes after
    )
    updated = _infer_from_answer_pattern(results, template)
    q12 = next(r for r in updated if r.question == 12)
    assert q12.answer == ""
    assert not q12.pattern_inferred


def test_infer_from_answer_pattern_fills_a_sections_last_question_given_a_long_one_sided_run():
    # Real case (Goldman): a section's very last question has no
    # right-side neighbor to ever confirm against -- only the left side,
    # held to _BOUNDARY_PATTERN_MIN_RUN (double the two-sided minimum) as
    # its margin of safety. Q1-17 idx0 (17, comfortably over 16), Q18
    # (the last question) blank.
    template = _pattern_template()
    results = [_qr(q, _idx0(q)) for q in range(1, 18)] + [_qr(18, "")]
    updated = _infer_from_answer_pattern(results, template)
    q18 = next(r for r in updated if r.question == 18)
    assert q18.answer == _idx0(18)
    assert q18.pattern_inferred
    assert q18.low_confidence


def test_infer_from_answer_pattern_requires_double_the_run_at_the_last_question():
    # The exact same run length that's plenty for an ordinary mid-section
    # gap (>= _PATTERN_MIN_TOTAL_RUN = 8) must NOT be enough at a
    # section's last question, which has no right side to confirm
    # against at all -- this is the specific higher bar
    # _BOUNDARY_PATTERN_MIN_RUN exists for. 9 questions of idx0 run (over
    # 8, comfortably under 16) followed by the blank last question.
    template = _pattern_template()
    results = [_qr(q, _idx0(q)) for q in range(1, 10)] + [_qr(10, "")]
    updated = _infer_from_answer_pattern(results, template)
    q10 = next(r for r in updated if r.question == 10)
    assert q10.answer == ""
    assert not q10.pattern_inferred


def test_infer_from_answer_pattern_never_infers_a_last_question_with_no_real_run_before_it():
    # Real case (Vinca, Rizza): a student who genuinely ran out of time
    # and left the true end of a section blank is common -- and in every
    # real example of it, the questions immediately preceding the blank
    # tail were an ordinary mix of answers, not a matching-index run at
    # all. This mirrors that shape: each question cycles through a
    # different choice index than the one before it (0,1,2,3,0,1,2,...),
    # so no two adjacent answers ever share a choice index and there's no
    # run to even measure, regardless of how low the threshold might be.
    template = _pattern_template()
    results = [
        _qr(q, template.choices_for("Answers", q)[(q - 1) % 4]) for q in range(1, 18)
    ] + [_qr(18, "")]
    updated = _infer_from_answer_pattern(results, template)
    q18 = next(r for r in updated if r.question == 18)
    assert q18.answer == ""
    assert not q18.pattern_inferred


# -- _reconsider_low_confidence_pattern: pure logic tests ---------------------
#
# Real case (Goldman): a sheet printed one choice letter structurally
# bolder than the others across an entire section, occasionally letting it
# narrowly outscore a genuine but lighter mark elsewhere in the same
# question -- fill_ratio's own low_confidence flag already says "this read
# is shaky"; a long, otherwise-unbroken guessing-pattern run bracketing it
# is strong enough independent evidence to override it.


def test_reconsider_low_confidence_pattern_overrides_a_disagreeing_low_confidence_answer():
    template = _pattern_template()
    idx1 = {1: "G", 0: "B"}  # index 1 of odd/even choices, keyed by parity
    # Q1-6 and Q8-13 idx0, Q7 low-confidence idx1 (disagrees) in the middle --
    # 6 on each side, comfortably over the total.
    results = (
        [_qr(q, _idx0(q)) for q in range(1, 7)]
        + [_qr(7, idx1[7 % 2], low_confidence=True)]
        + [_qr(q, _idx0(q)) for q in range(8, 14)]
    )
    updated = _reconsider_low_confidence_pattern(results, template)
    q7 = next(r for r in updated if r.question == 7)
    assert q7.answer == _idx0(7)
    assert q7.pattern_inferred
    assert q7.low_confidence


def test_reconsider_low_confidence_pattern_never_touches_a_confident_answer():
    # Identical shape to the case above, except Q7's disagreeing answer is
    # NOT low_confidence -- a confidently-read answer is never
    # second-guessed here, however surprising it looks next to the pattern.
    template = _pattern_template()
    idx1 = {1: "G", 0: "B"}
    results = (
        [_qr(q, _idx0(q)) for q in range(1, 7)]
        + [_qr(7, idx1[7 % 2], low_confidence=False)]
        + [_qr(q, _idx0(q)) for q in range(8, 14)]
    )
    updated = _reconsider_low_confidence_pattern(results, template)
    q7 = next(r for r in updated if r.question == 7)
    assert q7.answer == idx1[7 % 2]
    assert not q7.pattern_inferred


def test_reconsider_low_confidence_pattern_leaves_agreement_alone():
    # Q7 is low_confidence, but it already agrees with the surrounding
    # pattern -- nothing to override, and it shouldn't get flagged
    # pattern_inferred just because it happened to be reconsidered.
    template = _pattern_template()
    results = (
        [_qr(q, _idx0(q)) for q in range(1, 7)]
        + [_qr(7, _idx0(7), low_confidence=True)]
        + [_qr(q, _idx0(q)) for q in range(8, 14)]
    )
    updated = _reconsider_low_confidence_pattern(results, template)
    q7 = next(r for r in updated if r.question == 7)
    assert q7.answer == _idx0(7)
    assert not q7.pattern_inferred


def test_reconsider_low_confidence_pattern_requires_the_full_run_threshold():
    # Same shape, but only 2 on each side -- nowhere near
    # _PATTERN_MIN_TOTAL_RUN, so the disagreeing low-confidence answer is
    # left exactly as read.
    template = _pattern_template()
    idx1 = {1: "G", 0: "B"}
    results = (
        [_qr(q, _idx0(q)) for q in (1, 2)]
        + [_qr(3, idx1[3 % 2], low_confidence=True)]
        + [_qr(q, _idx0(q)) for q in (4, 5)]
    )
    updated = _reconsider_low_confidence_pattern(results, template)
    q3 = next(r for r in updated if r.question == 3)
    assert q3.answer == idx1[3 % 2]
    assert not q3.pattern_inferred


def test_reconsider_low_confidence_pattern_does_not_use_its_own_answer_as_evidence():
    # A low-confidence answer must not count as supporting evidence for
    # *itself* -- its own slot is treated as blank when finding its
    # neighbors and run length, otherwise a bubble already suspected of
    # being wrong could validate its own reading. Q7's immediate right
    # neighbor is Q8, which is *also* low-confidence and disagrees with
    # the long idx0 run -- if Q7 (idx1, matching Q8) were allowed to count
    # towards its own evidence, this could look self-consistent; it must
    # not be inferred regardless, since Q8 disagrees with the actual run.
    template = _pattern_template()
    idx1 = {1: "G", 0: "B"}
    results = (
        [_qr(q, _idx0(q)) for q in range(1, 7)]
        + [_qr(7, idx1[7 % 2], low_confidence=True)]
        + [_qr(8, idx1[8 % 2], low_confidence=True)]
        + [_qr(q, _idx0(q)) for q in range(9, 15)]
    )
    updated = _reconsider_low_confidence_pattern(results, template)
    q7 = next(r for r in updated if r.question == 7)
    # Q7's real (skip-self) neighbors are Q6 (idx0) and Q8 (idx1) -- they
    # disagree, so Q7 is left untouched.
    assert q7.answer == idx1[7 % 2]
    assert not q7.pattern_inferred


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


# -- _dark_fraction / _find_mark_floor: real-scan "unreadable region" fix ----
#
# User-reported: a sheet with a block of ~18 questions the print itself
# printed too faint to trust (confirmed against the source scan: barely
# legible even zoomed in) read as scattered wrong single answers and
# MULTIPLEs instead of blank, and separately, a few individual smudged-
# eraser marks elsewhere on the same sheet read a wrong single letter.
# Neither is an area problem (score_bubbles) or an unusual-vs-normal-for-
# this-letter problem (the residual checks) -- both are genuinely gray,
# not black, ink, which those checks don't measure at all.


def test_dark_fraction_counts_only_genuinely_dark_pixels():
    value = np.full((41, 41), 255, dtype=np.uint8)
    value[15:26, 15:26] = 0  # solid 11x11 dark square, centered in the sample circle
    # _dark_fraction samples a circle of radius int(18*0.85)=15 (area ~707px);
    # the 121px dark square sits entirely inside it -> ~121/707 =~ 0.171.
    assert _dark_fraction(value, 20, 20, radius=18) == pytest.approx(0.171, abs=0.01)


def test_dark_fraction_ignores_gray_that_is_not_dark_enough():
    value = np.full((41, 41), 255, dtype=np.uint8)
    value[15:26, 15:26] = 110  # matches a real faded-print/smudge measurement, not real ink
    assert _dark_fraction(value, 20, 20, radius=18) == 0.0


# -- _solidity / _solid_fill_choice: real-scan "heavy baseline print" fix --
#
# User-reported: a sheet whose printed bubbles are a thick ring + bold
# letter even completely unmarked pushed every choice's raw fill_ratio
# high enough (0.6-0.85 for a genuinely *unmarked* choice) that
# score_bubbles's area-based signal, even after per-question baseline
# subtraction, couldn't always tell a real, fully solid mark apart from
# that shared baseline -- several confirmed-genuine marks read as blank
# outright, and roughly 40% of the sheet's otherwise-correct answers were
# flagged low_confidence. _dark_fraction and the residual/partial-mark
# signal were tried first and rejected: both are *also* area/darkness
# based and compressed the same way on this sheet. What's different about
# a genuine mark isn't how much area or darkness it has -- printed ring
# ink and letter glyphs already have plenty of both -- it's that a real
# mark is *solid*, uniformly thick throughout, where a ring, a letter
# stroke, or a scribble/X-out/partial-erasure mark are all just a few px
# wide. Erosion is what actually separates those.


def _binary_disk(size: int, cx: int, cy: int, r: int) -> np.ndarray:
    patch = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(patch, (cx, cy), r, 255, -1)
    return patch


def _binary_ring(size: int, cx: int, cy: int, r: int, thickness: int) -> np.ndarray:
    patch = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(patch, (cx, cy), r, 255, thickness)
    return patch


def test_solidity_is_high_for_a_genuinely_solid_fill():
    # At real bubble-sample-radius scale (here 15px), eroding by
    # _SOLIDITY_ERODE_PX still leaves the bulk of a genuinely solid disk
    # intact -- nowhere near the ~0.0 a ring or scribble of the same
    # overall area collapses to (see the two tests below).
    binary = _binary_disk(41, 20, 20, 15)
    assert _solidity(binary, 20, 20, radius=18) > 0.5


def test_solidity_is_low_for_a_thin_printed_ring():
    # Same overall dark *area* as a real bubble's printed outline, but as
    # a thin annulus -- erosion should collapse nearly all of it.
    binary = _binary_ring(41, 20, 20, 15, thickness=3)
    assert _solidity(binary, 20, 20, radius=18) < 0.15


def test_solidity_is_low_for_a_scattered_scribble():
    # Several small, separated blobs (a real X-out/scribble's shape) --
    # none individually thick enough to survive erosion, unlike one
    # continuous solid fill of the same total area.
    binary = np.zeros((41, 41), dtype=np.uint8)
    for cx, cy in [(14, 14), (26, 14), (14, 26), (26, 26), (20, 20)]:
        cv2.circle(binary, (cx, cy), 3, 255, -1)
    assert _solidity(binary, 20, 20, radius=18) < 0.15


def test_solidity_returns_zero_for_an_empty_bubble():
    binary = np.zeros((41, 41), dtype=np.uint8)
    assert _solidity(binary, 20, 20, radius=18) == 0.0


def test_solid_fill_choice_returns_the_solid_leader():
    binary = np.zeros((80, 80), dtype=np.uint8)
    # F: a thin ring only (unmarked baseline). G: a genuine solid fill.
    cv2.circle(binary, (20, 20), 15, 255, 3)
    cv2.circle(binary, (60, 20), 15, 255, -1)
    bubbles = [("F", 20, 20), ("G", 60, 20)]
    fill_ratios = {"F": 0.20, "G": 0.35}  # G leads fill_ratio's own adjusted ranking
    assert _solid_fill_choice(fill_ratios, binary, bubbles, radius=18) == "G"


def test_solid_fill_choice_none_when_the_leader_is_not_solid():
    # fill_ratio's own leader is a thin ring, not a real mark -- must not
    # be promoted just because it's the highest of a weak field.
    binary = np.zeros((80, 80), dtype=np.uint8)
    cv2.circle(binary, (20, 20), 15, 255, 3)
    cv2.circle(binary, (60, 20), 12, 255, 2)
    bubbles = [("F", 20, 20), ("G", 60, 20)]
    fill_ratios = {"F": 0.20, "G": 0.15}
    assert _solid_fill_choice(fill_ratios, binary, bubbles, radius=18) is None


def _thick_ring_template() -> Template:
    data = {
        "page": {"width": 900, "height": 700},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 3, "x_start": 150, "y_start": 100, "row_height": 100},
                ],
            }
        ],
        "bubble_spacing_x": 70,
        "bubble_radius": 20,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.225, "relative_margin": 0.13},
    }
    return Template.from_dict(data)


def _draw_heavy_baseline(image: np.ndarray, template: Template) -> None:
    """Redraw every bubble's printed ring much thicker than
    make_blank_sheet's default -- simulating the real sheet whose bold
    print style is what motivated this whole signal -- so that even a
    completely unmarked choice's fill_ratio lands well above what this
    template's thresholds normally treat as baseline noise."""
    for bubbles in template.bubbles().values():
        for b in bubbles:
            cv2.circle(image, (b.x, b.y), template.bubble_radius, (0, 0, 0), 20)


def test_evaluate_sheet_rescues_a_solid_mark_a_heavy_baseline_print_hides_from_fill_ratio():
    template = _thick_ring_template()
    image = make_blank_sheet(template, letters=True)
    _draw_heavy_baseline(image, template)
    marked = next(b for b in template.bubbles()[("Answers", 1)] if b.choice == "G")
    fill_bubble(image, marked.x, marked.y, template.bubble_radius, coverage=1.0, darkness=10)

    binary = binarize(image)
    bubbles = [(b.choice, b.x, b.y) for b in template.bubbles()[("Answers", 1)]]
    fr = score_bubbles(binary, bubbles, template.bubble_radius)
    fr_answer, _, _ = decide_answer(fr, template.thresholds.fill_ratio_min, template.thresholds.relative_margin)
    assert fr_answer == "", "fixture should reproduce the real bug: fill_ratio alone reads this row blank"

    results, _ = evaluate_sheet(image, template)
    q1 = next(r for r in results if r.question == 1)
    assert q1.answer == "G"
    assert q1.low_confidence  # always flagged, like the partial-mark override


def test_evaluate_sheet_does_not_promote_a_checkmark_even_with_a_heavy_baseline():
    # Same heavy-baseline print, but the "mark" is a thin checkmark (two
    # short, non-crossing 1px strokes -- real checkmark-style marking is
    # explicitly called out as an "incorrect mark" on a real ACT sheet's
    # own instructions, but real students do it anyway) rather than a
    # genuine solid fill -- must stay blank, not get promoted just because
    # it's the row's highest fill_ratio.
    template = _thick_ring_template()
    image = make_blank_sheet(template, letters=True)
    _draw_heavy_baseline(image, template)
    marked = next(b for b in template.bubbles()[("Answers", 1)] if b.choice == "G")
    x, y = marked.x, marked.y
    cv2.line(image, (x - 8, y), (x - 2, y + 7), (10, 10, 10), 1)
    cv2.line(image, (x - 2, y + 7), (x + 9, y - 8), (10, 10, 10), 1)

    results, _ = evaluate_sheet(image, template)
    q1 = next(r for r in results if r.question == 1)
    assert q1.answer == ""


def test_evaluate_sheet_solidity_never_clears_low_confidence_on_a_partial_mark_derived_answer():
    # Regression coverage for a real bug in an early version of this fix:
    # the low_confidence-clearing branch (meant only for an answer
    # fill_ratio settled on *directly*) also fired on an answer that came
    # from the partial-mark override instead, clearing low_confidence
    # there too -- that override's own accuracy assumes every answer it
    # supplies keeps getting a human glance (see _PARTIAL_MARK_MIN_GAP's
    # comment), regardless of how solid the promoted bubble's ink happens
    # to look. Reuses the exact fixture that first caught this.
    template = make_template()
    answers = {1: ["F"], 2: ["B"], 4: ["A"], 5: ["J"], 6: ["D"]}
    image = render_sheet(template, answers, letters=True)
    bubble = next(b for b in template.bubbles()[("Answers", 3)] if b.choice == "F")
    fill_bubble(image, bubble.x, bubble.y, template.bubble_radius, coverage=0.4, darkness=30)

    results, _ = evaluate_sheet(image, template)
    q3 = next(r for r in results if r.question == 3)
    assert q3.answer == "F"
    assert q3.low_confidence  # always flagged when it came from the secondary signal


def test_find_mark_floor_locates_the_gap_between_two_clusters():
    # A cluster of weak artifacts (0.05-0.15) and a cluster of genuine
    # marks (0.5-0.6), each internally noisy but clearly separated.
    weak = [0.05, 0.08, 0.1, 0.12, 0.15, 0.07, 0.09, 0.11, 0.13, 0.06]
    strong = [0.5, 0.55, 0.6, 0.52, 0.58, 0.51, 0.56, 0.59, 0.53, 0.57]
    floor = _find_mark_floor(weak + strong)
    assert floor is not None
    assert 0.15 < floor < 0.5


def test_find_mark_floor_none_when_the_sheet_has_no_such_problem():
    # No real gap -- a normal, continuously-varying spread of genuine
    # marks, same as most sheets tested against this. Must not invent a
    # threshold from noise.
    values = [0.3 + 0.02 * i for i in range(15)]
    assert _find_mark_floor(values) is None


def test_find_mark_floor_ignores_a_gap_that_would_isolate_only_a_tiny_minority():
    # Regression coverage for a real scan whose ink was heavily and
    # uniformly toned across the *entire* sheet (not a faded minority
    # block): a long, continuous spread of 204 genuinely correct answers
    # (confirmed against the source scan) plus one that happened to read
    # darker than the rest. The widest raw gap in that distribution fell
    # between the single highest value and its neighbor -- comfortably
    # over _MARK_FLOOR_MIN_GAP purely because the top value sits apart
    # from a long tail, not because of any real two-cluster split. Trusting
    # it wiped all 204 correct answers to blank. A genuinely faded/smudged
    # region is supposed to be the *exception* on a sheet, not the norm, so
    # a candidate gap whose "genuine" side would be the smaller side must
    # never be trusted.
    continuous_tail = [0.04 + 0.002 * i for i in range(204)]  # spread 0.04-0.446, no real gap
    lone_outlier = [0.6]
    assert _find_mark_floor(continuous_tail + lone_outlier) is None


def _fade_bubble(image: np.ndarray, x: int, y: int, radius: int, light_value: int = 170) -> None:
    """Simulate a faded/washed-out print: cap how dark *any* ink already in
    this bubble's neighborhood (ring, printed letter, whatever) is allowed
    to get, rather than drawing a new mark on top of it. This is what real
    scan/print degradation actually looks like -- a whole region reads gray
    however solidly it was originally printed -- unlike fill_bubble, which
    only controls a fresh mark and leaves the surrounding ring/letter at
    full black."""
    r = radius + 4
    y0, y1 = max(0, y - r), y + r + 1
    x0, x1 = max(0, x - r), x + r + 1
    patch = image[y0:y1, x0:x1]
    dark = patch.min(axis=2) < light_value
    patch[dark] = (light_value, light_value, light_value)


def test_evaluate_sheet_flags_a_faded_block_as_unreadable_and_blank():
    template = _pattern_template()  # 20 questions, enough for _find_mark_floor's data requirement
    # Every question gets a normal, solidly-marked answer *except* a block
    # near the end, which simulates a faded/faint block of the page: all
    # its ink (ring, letter, and a low-coverage mark) is capped to a light
    # gray, the same shape as the real case (a whole stretch reading noise
    # instead of blank).
    answers = {q: [_idx0(q)] for q in range(1, 15)}
    image = render_sheet(template, answers, letters=True)
    for q in range(15, 21):
        for bubble in template.bubbles()[("Answers", q)]:
            _fade_bubble(image, bubble.x, bubble.y, template.bubble_radius)
        marked = next(b for b in template.bubbles()[("Answers", q)] if b.choice == _idx0(q))
        fill_bubble(image, marked.x, marked.y, template.bubble_radius, coverage=0.3, darkness=180)

    results, _ = evaluate_sheet(image, template)
    by_q = {r.question: r for r in results}
    for q in range(15, 21):
        assert by_q[q].answer == ""
    # At least the faintest of them should be flagged as a distinct
    # "unreadable" problem rather than an ordinary confident blank.
    assert any(by_q[q].unreadable for q in range(15, 21))
    for q in range(1, 15):
        assert by_q[q].answer == _idx0(q)  # untouched -- these are genuine, unambiguous marks


def test_evaluate_sheet_does_not_wipe_a_confident_answer_marked_in_light_pencil():
    # Regression coverage for a real scan marked throughout in genuinely
    # light pencil/graphite: score_bubbles' Otsu-relative binarization
    # correctly separated every mark from that sheet's own paper and
    # print regardless (the ring, the mark, everything sat at one
    # consistently light tone, but a consistently *lighter* tone than the
    # white paper is still exactly what Otsu's threshold is built to
    # find) -- but _dark_fraction's strict, sheet-independent near-black
    # cutoff (deliberately so, see its own docstring) never saw enough of
    # that light graphite to clear _UNREADABLE_MAX on over 70 already-
    # confident answers, silently wiping every one of them to blank. The
    # absolute-floor check must not overrule an answer score_bubbles
    # already found confidently on its own, independent terms.
    template = _pattern_template()
    answers = {q: [_idx0(q)] for q in range(1, 20) if q != 3}
    image = render_sheet(template, answers, ink_color=(90, 90, 90), coverage=1.0, darkness=90)
    marked = next(b for b in template.bubbles()[("Answers", 3)] if b.choice == _idx0(3))
    fill_bubble(image, marked.x, marked.y, template.bubble_radius, coverage=1.0, darkness=90)

    results, _ = evaluate_sheet(image, template)
    q3 = next(r for r in results if r.question == 3)
    assert q3.answer == _idx0(3)
    assert not q3.unreadable
    assert not q3.low_confidence


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
    # darkness=50 (not e.g. 110): a real pencil mark, however light overall,
    # has genuine near-black texture somewhere in it (confirmed against a
    # real faint checkmark: minimum pixel value 0) -- unlike this synthetic
    # helper's perfectly uniform fill, which needs to actually dip below
    # _DARK_PIXEL_VALUE to be realistic, or it reads as indistinguishable
    # from a faded/unreadable region of the page (see _apply_readability_checks).
    image = render_sheet(template, answers, coverage=0.55, darkness=50)

    results = {r.question: r for r in evaluate_sheet(image, template)[0]}
    assert results[1].answer == "G"
    assert not results[1].unreadable
