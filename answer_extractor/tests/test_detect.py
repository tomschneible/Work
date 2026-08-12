import numpy as np
import pytest

from answer_extractor.detect import (
    QuestionResult,
    _baseline_adjust,
    _crop_patch,
    _dark_fraction,
    _find_mark_floor,
    _infer_from_answer_pattern,
    _partial_mark_choice,
    _residual_ratio,
    _value_channel,
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
