"""Score how "filled in" each bubble is, and turn those scores into an
answer decision per question — including BLANK and MULTIPLE outcomes and
tolerance for light or partial marks.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Tuple

import cv2
import numpy as np

from . import grid_detect
from .template import Bubble, Template


@dataclasses.dataclass(frozen=True)
class QuestionResult:
    section: str
    question: int
    answer: str  # a single choice letter, "" (blank), or "MULTIPLE"
    candidates: List[str]  # every choice detected as marked (0, 1, or 2+)
    fill_ratios: Dict[str, float]  # choice -> fill ratio, for auditing
    low_confidence: bool  # marked bubble(s) only marginally above threshold
    # True when `answer` wasn't detected from this bubble's own ink at all,
    # but inferred from a long, unbroken run of identically-positioned
    # answers immediately surrounding it (see _infer_from_answer_pattern) --
    # e.g. a student guessing/rushing through a stretch of questions by
    # marking the same choice position over and over. Always implies
    # low_confidence; kept as a separate flag so callers (export.py) can
    # tell "read directly off the page, just not decisively" apart from
    # "not read off this page at all, inferred from context" -- the latter
    # deserves more scrutiny before trusting it.
    pattern_inferred: bool = False


def binarize(image: np.ndarray) -> np.ndarray:
    """Return a binary image where marked (dark) pixels are 255.

    Many real answer sheets (including standard ACT sheets) print bubble
    outlines and choice letters in a saturated "dropout" accent color
    (e.g. coral/red) specifically so it can be distinguished from actual
    pencil/pen marks, which are dark AND essentially colorless (neutral
    gray/black). Grayscale luminance conflates the two -- a vivid coral
    print can have lower luminance than white paper despite being no mark
    at all. Using max(B, G, R) per pixel (equivalent to HSV "Value") avoids
    this: printed accent-color ink stays bright in whichever channel gives
    it its color, while a genuine dark, neutral mark stays low in all
    channels. For plain black-outline sheets this is equivalent to grayscale.
    """
    value = image if image.ndim == 2 else np.max(image, axis=2)
    value = value.astype(np.uint8)
    blurred = cv2.GaussianBlur(value, (3, 3), 0)
    # Otsu picks a global split between the (light) paper/print and (dark)
    # marks; robust across scan brightness without needing a fixed threshold.
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def _bubble_fill_ratio(binary: np.ndarray, x: int, y: int, radius: int) -> float:
    h, w = binary.shape
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return 0.0
    patch = binary[y0:y1, x0:x1]

    mask = np.zeros_like(patch)
    center = (x - x0, y - y0)
    # Sample slightly inside the nominal radius so stray marks just outside
    # one bubble's circle (e.g. touching the next bubble) don't count.
    cv2.circle(mask, center, max(1, int(radius * 0.85)), 255, -1)

    marked = cv2.countNonZero(cv2.bitwise_and(patch, mask))
    total = cv2.countNonZero(mask)
    if total == 0:
        return 0.0
    return marked / total


def score_bubbles(binary: np.ndarray, bubbles: List[Tuple[str, int, int]], radius: int) -> Dict[str, float]:
    """Score already-binarized image regions covered by each bubble.

    `bubbles` is a list of (choice, x, y) tuples, wherever they came from
    (a template's nominal coordinates or grid_detect's per-sheet detected
    positions)."""
    return {choice: _bubble_fill_ratio(binary, x, y, radius) for choice, x, y in bubbles}


_PATCH_PAD_EXTRA = 4  # beyond bubble_radius, room enough to capture the full ring+letter glyph


def _crop_patch(binary: np.ndarray, x: int, y: int, pad: int) -> np.ndarray:
    """Crop a (2*pad+1)-square float patch centered at (x, y), zero-padded
    past the image edge (rather than raising/clipping the patch size) so
    every patch for a given radius is the same shape and can be stacked."""
    size = 2 * pad + 1
    h, w = binary.shape
    patch = np.zeros((size, size), dtype=np.float32)
    x0, y0 = x - pad, y - pad
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(w, x0 + size), min(h, y0 + size)
    if sx0 < sx1 and sy0 < sy1:
        patch[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = binary[sy0:sy1, sx0:sx1]
    return patch


def build_choice_templates(
    binary: np.ndarray, bubbles_by_choice: Dict[str, List[Tuple[int, int]]], radius: int
) -> Dict[str, np.ndarray]:
    """Learn what an *unmarked* bubble normally looks like, per choice
    letter -- the printed ring outline plus that letter's own glyph --
    by averaging every occurrence of that letter across a section.

    This is the basis for `_partial_mark_choice`'s "does this bubble have
    ink beyond what's normally there" signal, used to catch marks too
    faint or small (e.g. a checkmark instead of a filled-in bubble --
    explicitly called out as an "incorrect mark" on a real ACT sheet's own
    instructions, but real students do it anyway) for score_bubbles's
    area-based fill ratio to reliably pick up. Most occurrences of a given
    letter are unmarked (typically ~3 of 4 per question), so the average
    is dominated by the unmarked appearance even without filtering out the
    marked minority first.
    """
    pad = radius + _PATCH_PAD_EXTRA
    templates: Dict[str, np.ndarray] = {}
    for choice, coords in bubbles_by_choice.items():
        if not coords:
            continue
        stack = np.stack([_crop_patch(binary, x, y, pad) for x, y in coords])
        templates[choice] = np.mean(stack, axis=0)
    return templates


def _residual_ratio(binary: np.ndarray, x: int, y: int, radius: int, choice_template: np.ndarray) -> float:
    """How much *extra* ink this specific bubble has beyond what's normally
    there for this choice letter (see `build_choice_templates`), as a
    fraction of the sampled area -- pixels that are dark here but not
    usually dark for this letter contribute; pixels that are dark here
    *and* usually dark (the ring, the letter's own strokes) don't."""
    pad = radius + _PATCH_PAD_EXTRA
    patch = _crop_patch(binary, x, y, pad)
    mask = np.zeros_like(patch, dtype=np.uint8)
    cv2.circle(mask, (pad, pad), max(1, int(radius * 0.85)), 255, -1)
    total = cv2.countNonZero(mask)
    if total == 0:
        return 0.0
    residual = np.clip(patch - choice_template, 0, 255)
    return float(np.sum(residual[mask > 0]) / (total * 255))


# Thresholds for _partial_mark_choice, calibrated against four real scanned
# ACT sheets (see grid_detect/detect module history): two questions
# independently confirmed genuinely blank on one sheet topped out at a
# 0.035 gap between the leading and second-place choice (one of them a
# near-total tie at 0.002); real checkmark-style marks on another sheet
# ranged roughly 0.03-0.1. There's no gap value that cleanly separates
# every real case in that data -- these sit above the confirmed-blank
# ceiling with a margin of safety, catching roughly half of that sheet's
# checkmarked questions rather than all of them, in exchange for not
# reviving a confirmed-blank question as a false mark again.
_PARTIAL_MARK_MIN_TOP = 0.06
_PARTIAL_MARK_MIN_GAP = 0.045

# A looser pair used only to *tie-break between two choices fill_ratio
# already considers close* (see _RAW_GAP_UNCERTAIN below), not to detect a
# mark from nothing -- so they don't need the same margin of safety.
# Calibrated the same way: every already-correct low-confidence single
# answer across three real sheets had its own residual signal agree with
# fill_ratio's pick by a wide margin (smallest observed: 0.11); these sit
# comfortably below that.
_TIEBREAK_MIN_TOP = 0.03
_TIEBREAK_MIN_GAP = 0.02


# Below this raw (unadjusted -- baseline-subtracting both sides of a
# comparison doesn't change their difference) gap between fill_ratio's top
# two choices, its own pick isn't actually decisive, whatever
# `low_confidence` says -- see _raw_top_gap. Calibrated the same way as
# _TIEBREAK_MIN_TOP/_TIEBREAK_MIN_GAP: two real (confirmed-correct, via the
# raw ink) picks that just happened to fall in low_confidence range had
# raw gaps of 0.20+; two real checkmark cases fill_ratio got outright
# wrong had raw gaps of 0.02 and 0.075. This sits in between.
_RAW_GAP_UNCERTAIN = 0.10


def _raw_top_gap(fill_ratios: Dict[str, float]) -> "float | None":
    """The gap between fill_ratio's largest and second-largest raw value,
    or None if there aren't at least two choices to compare."""
    if len(fill_ratios) < 2:
        return None
    ranked = sorted(fill_ratios.values(), reverse=True)
    return ranked[0] - ranked[1]


def _partial_mark_choice(
    residuals: Dict[str, float], min_top: float = _PARTIAL_MARK_MIN_TOP, min_gap: float = _PARTIAL_MARK_MIN_GAP
) -> "str | None":
    """If exactly one choice stands out as a clear, isolated leader in the
    residual-ink signal, return it; otherwise None. With the default
    thresholds, only meant to be consulted when score_bubbles's ordinary
    fill-ratio signal already came back blank or MULTIPLE (see
    evaluate_sheet) -- this never overrides an already-confident
    fill-ratio answer, since the residual signal alone isn't reliable
    enough for that (see the threshold comment above). Pass the looser
    `_TIEBREAK_MIN_TOP`/`_TIEBREAK_MIN_GAP` instead to use this as a
    tie-break between choices fill_ratio already found plausible."""
    if len(residuals) < 2:
        return None
    ranked = sorted(residuals.items(), key=lambda kv: kv[1], reverse=True)
    (top_choice, top_value), (_, second_value) = ranked[0], ranked[1]
    if top_value >= min_top and top_value - second_value >= min_gap:
        return top_choice
    return None


# A second, additive fallback for the blank/MULTIPLE path: neither
# fill_ratio's own raw top-2 gap nor the residual signal's gap always
# clears its own bar individually on a real partial mark, but a real mark
# tends to nudge *both* signals in the same direction even when neither
# alone is convincing, while a truly blank question doesn't. Summing them
# recovers several such cases without weakening either individual bar.
# Calibrated the same way: the same two confirmed-genuinely-blank
# questions peaked at a combined 0.043; real (if faint) marks on another
# sheet that neither individual check caught ranged 0.058-0.158. This sits
# above that ceiling with a real margin, not just barely.
_COMBINED_MIN_GAP = 0.055
_COMBINED_MIN_RESIDUAL_GAP = 0.01  # residual must show *some* real preference, not be a coin flip riding fill_ratio's gap alone


def _combined_partial_mark_choice(fill_ratios: Dict[str, float], residuals: Dict[str, float]) -> "str | None":
    """Like `_partial_mark_choice`, but combines fill_ratio's own raw
    top-2 gap with the residual gap rather than judging the residual
    signal in isolation -- see `_COMBINED_MIN_GAP`. Returns the residual
    signal's top choice (not necessarily fill_ratio's raw top choice --
    the two can disagree even when combined they indicate a real mark;
    residual is the more specific signal for *which* choice it's on)."""
    fill_gap = _raw_top_gap(fill_ratios)
    if fill_gap is None or len(residuals) < 2:
        return None
    ranked = sorted(residuals.items(), key=lambda kv: kv[1], reverse=True)
    (top_choice, top_value), (_, second_value) = ranked[0], ranked[1]
    residual_gap = top_value - second_value
    if residual_gap >= _COMBINED_MIN_RESIDUAL_GAP and fill_gap + residual_gap >= _COMBINED_MIN_GAP:
        return top_choice
    return None


# A guessing/rushing student marking the same *position* over and over --
# real behavior, common when time runs out -- leaves a distinctive
# signature: many consecutive questions (spanning both odd/even choice
# sets, e.g. always the 1st of A/B/C/D and the 1st of F/G/H/J alike) all
# landing on the same choice *index*. A run this long happening by chance
# on a genuinely mixed set of answers is vanishingly unlikely
# ((1/4)^_PATTERN_MIN_TOTAL_RUN, ignoring that it's also required on both
# sides at once), which is what makes it safe to use as a tie-breaker for
# a bubble whose own ink still doesn't clear even the combined check --
# unlike every check above, this one isn't reading the bubble's own ink at
# all, so it demands a much longer, unbroken run as its margin of safety
# instead of a pixel gap.
#
# The total (both sides combined) is what's thresholded, not each side
# individually: requiring, say, 6 on *each* side misses a real guessed
# question sitting one question after the run started, or one question
# before a section's last question -- there's only ever going to be a
# short run on the near side of a section boundary, no matter how real
# the pattern is. Both sides must still have at least one matching
# neighbor immediately adjacent, so a run is never inferred from only one
# direction.
_PATTERN_MIN_TOTAL_RUN = 8


def _infer_from_answer_pattern(
    section_results: List[QuestionResult], template: Template
) -> List[QuestionResult]:
    """Fill in blank/MULTIPLE questions that sit in the middle of a long,
    unbroken run of same-choice-index answers on both sides, e.g. index 0
    meaning "A" on an odd question and "F" on an even one. Only questions
    already blank or MULTIPLE after every ink-based check above are
    touched; a question with any single directly-detected answer, however
    low-confidence, is left exactly as-is -- this never overrides real
    pixel evidence, only fills a genuine gap using its context."""
    choice_indices: List["int | None"] = []
    for r in section_results:
        if r.answer in ("", "MULTIPLE"):
            choice_indices.append(None)
            continue
        choices = template.choices_for(r.question)
        choice_indices.append(choices.index(r.answer) if r.answer in choices else None)

    n = len(section_results)
    updated = list(section_results)
    for i, r in enumerate(section_results):
        if r.answer not in ("", "MULTIPLE"):
            continue
        left = choice_indices[i - 1] if i - 1 >= 0 else None
        right = choice_indices[i + 1] if i + 1 < n else None
        if left is None or right is None or left != right:
            continue
        target = left

        left_run = 0
        j = i - 1
        while j >= 0 and choice_indices[j] == target:
            left_run += 1
            j -= 1
        right_run = 0
        j = i + 1
        while j < n and choice_indices[j] == target:
            right_run += 1
            j += 1
        if left_run + right_run < _PATTERN_MIN_TOTAL_RUN:
            continue

        choices = template.choices_for(r.question)
        inferred = choices[target]
        updated[i] = dataclasses.replace(
            r, answer=inferred, candidates=[inferred], low_confidence=True, pattern_inferred=True
        )
    return updated


def _baseline_adjust(fill_ratios: Dict[str, float]) -> Dict[str, float]:
    """Subtract each question's own minimum fill ratio from every choice in
    it before deciding an answer.

    A printed bubble's outline and choice letter are themselves dark ink,
    not just the paper it sits on -- on some scans (bolder print, lower
    scan resolution, a tighter sample radius relative to the bubble) that
    baseline "ink floor" is high enough that even a completely blank bubble
    clears `fill_ratio_min` on its own, and all four choices in a question
    end up within `relative_margin` of each other purely from that shared
    baseline, which reads as MULTIPLE even though nothing was marked. Since
    the baseline is common to every choice in the same question (same font,
    same print, same scan pass), the least-filled choice in the row is a
    good per-question estimate of it; subtracting it out leaves only the
    genuine pencil/pen marking, and does nothing when there's no such
    baseline to begin with (subtracting 0 changes nothing), so clean scans
    are unaffected.
    """
    if not fill_ratios:
        return {}
    baseline = min(fill_ratios.values())
    return {choice: ratio - baseline for choice, ratio in fill_ratios.items()}


def decide_answer(
    fill_ratios: Dict[str, float],
    fill_ratio_min: float,
    relative_margin: float,
) -> tuple[str, List[str], bool]:
    """Turn per-choice fill ratios into (answer, candidates, low_confidence).

    A choice counts as "marked" if its fill ratio (after subtracting the
    question's own baseline ink level -- see `_baseline_adjust`) clears the
    absolute floor AND is within `relative_margin` of the darkest bubble in
    the question. The relative check is what catches genuinely multiple
    answers (two bubbles both solidly filled) while the absolute floor
    keeps stray pencil smudges or scan noise from being read as an answer.
    """
    if not fill_ratios:
        return "", [], False

    adjusted = _baseline_adjust(fill_ratios)
    max_ratio = max(adjusted.values())
    if max_ratio < fill_ratio_min:
        return "", [], False

    candidates = [
        choice
        for choice, ratio in adjusted.items()
        if ratio >= fill_ratio_min and ratio >= max_ratio - relative_margin
    ]
    # Preserve a stable, human-friendly order (matches choice definition order).
    candidates = [c for c in fill_ratios if c in candidates]

    low_confidence = max_ratio < fill_ratio_min + relative_margin

    if len(candidates) == 1:
        return candidates[0], candidates, low_confidence
    return "MULTIPLE", candidates, low_confidence


def evaluate_sheet(image: np.ndarray, template: Template) -> Tuple[List[QuestionResult], List[str]]:
    """Score every bubble on a sheet.

    Returns (results, sections_using_fixed_coordinates) -- the second list
    names any sections where grid_detect couldn't establish the expected
    bubble layout on this specific sheet and fell back to the template's
    fixed nominal coordinates uncorrected, which is a real accuracy risk
    (see grid_detect module docstring) worth surfacing to the caller.
    """
    binary = binarize(image)
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    all_bubbles = template.bubbles()
    results = []
    fallback_sections = []
    # Iterate in template-declared section order (not dict/alphabetical order)
    # so output columns follow the sheet's actual layout.
    for section in template.sections:
        detected = grid_detect.locate_section_bubbles(gray, template, section)
        if detected is None:
            fallback_sections.append(section.name)

        section_bubbles = {}
        bubbles_by_choice: Dict[str, List[Tuple[int, int]]] = {}
        for question in range(1, section.num_questions + 1):
            if detected is not None:
                bubbles = detected[question]
            else:
                bubbles = [(b.choice, b.x, b.y) for b in all_bubbles[(section.name, question)]]
            section_bubbles[question] = bubbles
            for choice, x, y in bubbles:
                bubbles_by_choice.setdefault(choice, []).append((x, y))
        # Built once per section and reused for every question in it (see
        # build_choice_templates) -- only actually consulted below for
        # questions the ordinary fill-ratio signal couldn't already decide.
        choice_templates = build_choice_templates(binary, bubbles_by_choice, template.bubble_radius)

        section_results: List[QuestionResult] = []
        for question in range(1, section.num_questions + 1):
            bubbles = section_bubbles[question]
            fill_ratios = score_bubbles(binary, bubbles, template.bubble_radius)
            answer, candidates, low_confidence = decide_answer(
                fill_ratios,
                template.thresholds.fill_ratio_min,
                template.thresholds.relative_margin,
            )

            # Blank/MULTIPLE: see if a partial mark (e.g. a checkmark) explains
            # it. A single answer whose own top two raw fill ratios are this
            # close together isn't actually a decisive pick regardless of
            # what `low_confidence` says (that's computed from the
            # baseline-adjusted scale, which can flag a real, clearly-darkest
            # answer as "low confidence" for unrelated reasons) -- let the
            # residual signal arbitrate between the choices fill_ratio itself
            # found plausible, rather than trusting a near-tie.
            raw_gap = _raw_top_gap(fill_ratios)
            uncertain_single = answer not in ("", "MULTIPLE") and raw_gap is not None and raw_gap < _RAW_GAP_UNCERTAIN
            if answer in ("", "MULTIPLE") or uncertain_single:
                residuals = {
                    choice: _residual_ratio(binary, x, y, template.bubble_radius, choice_templates[choice])
                    for choice, x, y in bubbles
                    if choice in choice_templates
                }
                if answer in ("", "MULTIPLE"):
                    partial_mark = _partial_mark_choice(residuals) or _combined_partial_mark_choice(
                        fill_ratios, residuals
                    )
                else:
                    partial_mark = _partial_mark_choice(residuals, _TIEBREAK_MIN_TOP, _TIEBREAK_MIN_GAP)
                if partial_mark is not None and partial_mark != answer:
                    answer, candidates, low_confidence = partial_mark, [partial_mark], True

            section_results.append(
                QuestionResult(
                    section=section.name,
                    question=question,
                    answer=answer,
                    candidates=candidates,
                    fill_ratios=fill_ratios,
                    low_confidence=low_confidence,
                )
            )

        # Last resort, after every ink-based check above: a question still
        # blank/MULTIPLE that sits in the middle of a long run of identical
        # answer positions on both sides (see _infer_from_answer_pattern).
        results.extend(_infer_from_answer_pattern(section_results, template))
    return results, fallback_sections
