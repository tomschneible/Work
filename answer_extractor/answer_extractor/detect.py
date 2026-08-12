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
    # True when every choice in this question has essentially no genuinely
    # dark ink at all (see _dark_fraction / _UNREADABLE_MAX) -- a scan/print
    # quality problem (a faded block of the page, not what a blank bubble
    # normally looks like on the rest of *this* sheet), rather than a
    # confident read of "nothing is marked here". Always implies answer ==
    # "" -- kept distinct from an ordinary blank so callers can flag it for
    # a human rather than treat it as equivalent to "student left it blank".
    unreadable: bool = False


def _value_channel(image: np.ndarray) -> np.ndarray:
    """max(B, G, R) per pixel (equivalent to HSV "Value") -- see binarize
    for why this, rather than grayscale luminance, is what separates a
    printed "dropout" accent color from a genuine dark, neutral mark."""
    value = image if image.ndim == 2 else np.max(image, axis=2)
    return value.astype(np.uint8)


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
    value = _value_channel(image)
    blurred = cv2.GaussianBlur(value, (3, 3), 0)
    # Otsu picks a global split between the (light) paper/print and (dark)
    # marks; robust across scan brightness without needing a fixed threshold.
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


_DARK_PIXEL_VALUE = 60  # out of 255; much stricter than Otsu's scan-relative split -- see _dark_fraction


def _dark_fraction(value: np.ndarray, x: int, y: int, radius: int, thresh: int = _DARK_PIXEL_VALUE) -> float:
    """Fraction of a bubble's sampled area that's *genuinely* dark (near-
    black), not just "darker than Otsu's split for this scan". A checkmark
    or pen stroke is real dark ink concentrated in a small area (this stays
    meaningfully >0 even though score_bubbles's area-based fill ratio
    barely registers it); a faded/faint region of the page or a smudged
    eraser mark is gray, not black, however much *area* it covers (this
    stays low even when area-based fill ratio reads it as substantial).
    Area and darkness are complementary signals -- neither alone tells the
    whole story, which is why this exists alongside score_bubbles rather
    than replacing it."""
    h, w = value.shape
    r = max(1, int(radius * 0.85))
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    if x0 >= x1 or y0 >= y1:
        return 0.0
    patch = value[y0:y1, x0:x1]
    mask = np.zeros_like(patch, dtype=np.uint8)
    cv2.circle(mask, (x - x0, y - y0), r, 255, -1)
    sampled = patch[mask > 0]
    if sampled.size == 0:
        return 0.0
    return float(np.mean(sampled < thresh))


# Absolute floor for _dark_fraction, independent of any per-sheet
# calibration: a question where *every* choice falls below this has
# essentially no dark ink anywhere in the row -- not even the printed
# ring/letter, which normally still contributes some (see the real
# example in _find_mark_floor's comment, where a faded block of a page
# read 0.0-0.16 there against a normal ~0.2-0.36 baseline for genuinely
# blank questions elsewhere on the *same* sheet). This is deliberately
# much stricter than that per-sheet baseline -- it's meant to catch only
# the extreme "the print itself didn't survive the scan" case, not
# ordinary blanks, so it needs no sheet-specific calibration to be safe.
_UNREADABLE_MAX = 0.05

# How much larger than its neighbors a gap in a sheet's own dark_fraction
# distribution must be before it's trusted as a real cluster boundary
# rather than noise -- see _find_mark_floor. Calibrated against six real
# sheets: four had no real problem here and their largest gap was pure
# noise (0.024-0.043); the two with a real faded-print/smudged-eraser
# problem had a much more decisive gap (0.107-0.15). This sits well
# between them, so sheets without the problem are simply left alone
# rather than risking a threshold derived from noise.
_MARK_FLOOR_MIN_GAP = 0.08


def _find_mark_floor(dark_fractions: List[float]) -> "float | None":
    """Find a floor, specific to *this sheet*, below which a currently-
    single-answered question's winning choice shouldn't be trusted as a
    genuine mark -- by locating the widest gap in the sheet's own
    dark_fraction distribution (for every question fill_ratio's ordinary
    checks already decided has one answer), restricted to a plausible
    range so the search can't land inside the cluster of genuine marks
    themselves or the cluster of near-zero blanks.

    This is deliberately sheet-relative rather than a fixed constant: a
    real scan had one sheet's confirmed-genuine (if faint) mark measure
    darker than *another* sheet's confirmed false positive, so no single
    number works for every sheet -- but each sheet's own genuine marks
    were reliably far darker than that same sheet's faint-print/smudge
    artifacts, which is what a per-sheet gap search finds safely. Returns
    None (meaning: don't second-guess anything) when there isn't a
    decisive-enough gap to trust -- see _MARK_FLOOR_MIN_GAP.
    """
    ordered = sorted(dark_fractions)
    best_gap = 0.0
    best_threshold = None
    for lower, upper in zip(ordered, ordered[1:]):
        if not (0.1 <= lower <= 0.6 or 0.1 <= upper <= 0.6):
            continue
        gap = upper - lower
        if gap > best_gap:
            best_gap = gap
            best_threshold = (lower + upper) / 2
    if best_gap < _MARK_FLOOR_MIN_GAP:
        return None
    return best_threshold


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


# Thresholds for _partial_mark_choice, calibrated against five real scanned
# ACT sheets (see grid_detect/detect module history). This is deliberately
# the *only* ink-based override left for the blank/MULTIPLE path -- two
# more permissive variants (a looser tie-break pair for fill_ratio's own
# near-ties, and a check that summed fill_ratio's raw gap with the
# residual gap) were tried and then reverted: a fifth real sheet turned
# out to print two choice letters noticeably bolder than the others across
# an entire section a student left blank, and that sheet's false-positive
# ceiling on both of those looser checks (0.13 combined, 0.044 tie-break
# residual gap) *exceeded* the weakest real signal either was built to
# catch on the sheet that motivated them (0.055 combined, 0.029 tie-break)
# -- there is no fixed threshold that admits one and excludes the other,
# so trying to tune around it just moves the false positive somewhere
# else. The numbers below are what's left standing after that: the
# confirmed-blank ceiling across two different sheets is now 0.044 (one
# sheet) / 0.035 (another, one of its two examples a near-total tie at
# 0.002); real checkmark-style marks on a third sheet ranged roughly
# 0.03-0.1. These sit above the confirmed-blank ceiling with real margin,
# catching roughly half of a checkmarked sheet's affected questions rather
# than all of them, in exchange for not reviving a confirmed-blank
# question as a false mark -- on *any* sheet tested so far, not just the
# one used to originally calibrate it.
_PARTIAL_MARK_MIN_TOP = 0.06
_PARTIAL_MARK_MIN_GAP = 0.06


def _partial_mark_choice(
    residuals: Dict[str, float], min_top: float = _PARTIAL_MARK_MIN_TOP, min_gap: float = _PARTIAL_MARK_MIN_GAP
) -> "str | None":
    """If exactly one choice stands out as a clear, isolated leader in the
    residual-ink signal, return it; otherwise None. Only meant to be
    consulted when score_bubbles's ordinary fill-ratio signal already came
    back blank or MULTIPLE (see evaluate_sheet) -- this never overrides an
    already-confident fill-ratio answer, since the residual signal alone
    isn't reliable enough for that (see the threshold comment above)."""
    if len(residuals) < 2:
        return None
    ranked = sorted(residuals.items(), key=lambda kv: kv[1], reverse=True)
    (top_choice, top_value), (_, second_value) = ranked[0], ranked[1]
    if top_value >= min_top and top_value - second_value >= min_gap:
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


def _nearest_non_blank(
    choice_indices: List["int | None"], start: int, step: int, skip_index: "int | None" = None
) -> "int | None":
    """Walk from `start` in direction `step` (+1 or -1) until the first
    non-blank/MULTIPLE entry (skipping over any number of blank/MULTIPLE
    entries along the way), and return its choice index -- or None if the
    list runs out first. `skip_index`, if given, is also treated as if
    blank regardless of its actual value -- used to ask "what would this
    one question's own neighbors be, ignoring its own (possibly
    unreliable) answer?" without having to build a whole new list."""
    n = len(choice_indices)
    j = start
    while 0 <= j < n:
        if j != skip_index and choice_indices[j] is not None:
            return choice_indices[j]
        j += step
    return None


def _count_matching_run(
    choice_indices: List["int | None"], start: int, step: int, target: int, skip_index: "int | None" = None
) -> int:
    """Walk from `start` in direction `step`, counting consecutive
    *non-blank* entries equal to `target` -- blank/MULTIPLE entries along
    the way are skipped over, not counted and not treated as a break, so a
    scattered blank in the middle of an otherwise-unbroken run doesn't cut
    the count short. Stops at the first non-blank entry that isn't
    `target`, or the end of the list. `skip_index`, if given, is treated
    as blank too -- see _nearest_non_blank."""
    n = len(choice_indices)
    count = 0
    j = start
    while 0 <= j < n:
        if j == skip_index:
            j += step
            continue
        v = choice_indices[j]
        if v is None:
            j += step
            continue
        if v != target:
            break
        count += 1
        j += step
    return count


def _infer_from_answer_pattern(
    section_results: List[QuestionResult], template: Template
) -> List[QuestionResult]:
    """Fill in blank/MULTIPLE questions that sit in the middle of a long,
    unbroken run of same-choice-index answers on both sides, e.g. index 0
    meaning "A" on an odd question and "F" on an even one. Only questions
    already blank or MULTIPLE after every ink-based check above are
    touched; a question with any single directly-detected answer, however
    low-confidence, is left exactly as-is -- this never overrides real
    pixel evidence, only fills a genuine gap using its context.

    Operates on the whole contiguous stretch of blank/MULTIPLE questions at
    once, not just a single one at a time -- a real scan had two guessed
    questions blank back-to-back in the middle of an otherwise unbroken
    run. It also looks past *other*, non-adjacent blanks/MULTIPLEs on
    either side rather than treating them as a hard stop: a real sheet had
    a single guessing run spanning 20+ questions with several scattered
    blanks in it (not all next to each other), and each individual blank
    was clearly part of the same run -- bracketed by the same choice index
    on both sides once you skip past the *other* blanks -- but capping the
    run count at the very next blank badly undercounted the real evidence
    behind each one. Concretely: for each contiguous blank/MULTIPLE run,
    find the nearest *non-blank* neighbor past each end (skipping over any
    other blanks along the way), and if those two neighbors agree, count
    how long that same-index run actually is the same way (skipping
    blanks, stopping only at a genuine mismatch or the section's edge).
    A single isolated blank with no other blanks nearby behaves exactly as
    before -- this only changes what happens when there's more than one."""
    choice_indices: List["int | None"] = []
    for r in section_results:
        if r.answer in ("", "MULTIPLE"):
            choice_indices.append(None)
            continue
        choices = template.choices_for(r.question)
        choice_indices.append(choices.index(r.answer) if r.answer in choices else None)

    n = len(section_results)
    updated = list(section_results)

    i = 0
    while i < n:
        if section_results[i].answer not in ("", "MULTIPLE"):
            i += 1
            continue

        run_start = i
        run_end = i
        while run_end + 1 < n and section_results[run_end + 1].answer in ("", "MULTIPLE"):
            run_end += 1

        left = _nearest_non_blank(choice_indices, run_start - 1, -1)
        right = _nearest_non_blank(choice_indices, run_end + 1, 1)
        if left is not None and right is not None and left == right:
            target = left
            left_run = _count_matching_run(choice_indices, run_start - 1, -1, target)
            right_run = _count_matching_run(choice_indices, run_end + 1, 1, target)

            if left_run + right_run >= _PATTERN_MIN_TOTAL_RUN:
                for k in range(run_start, run_end + 1):
                    r = section_results[k]
                    choices = template.choices_for(r.question)
                    inferred = choices[target]
                    updated[k] = dataclasses.replace(
                        r, answer=inferred, candidates=[inferred], low_confidence=True, pattern_inferred=True
                    )

        i = run_end + 1

    return updated


def _reconsider_low_confidence_pattern(
    section_results: List[QuestionResult], template: Template
) -> List[QuestionResult]:
    """Second-guess a `low_confidence` *single-answer* question (unlike
    _infer_from_answer_pattern, which only ever touches blank/MULTIPLE)
    when the same run-of-matching-choice-index context that function
    trusts elsewhere disagrees with it -- e.g. a bubble sheet that prints
    one choice letter structurally bolder than the others across an
    entire section, occasionally letting that letter narrowly outscore a
    genuine but lighter mark on a different choice.

    This is deliberately much more conservative than it might look:
    - Only ever reconsiders a question fill_ratio *itself already
      flagged* as low_confidence -- a confidently-read answer, however
      surprising, is never second-guessed here.
    - The evidence is the same guessing-pattern signal already trusted
      for blanks/MULTIPLE (_PATTERN_MIN_TOTAL_RUN, i.e. a run long enough
      that landing on it by chance is vanishingly unlikely), not the
      residual/reference-template signal -- a more permissive version of
      *that* check was tried and reverted after it revived a confirmed
      false positive on a real sheet (see _PARTIAL_MARK_MIN_GAP's
      comment). This is a different, independently-justified signal, and
      was checked against that same sheet's low-confidence answers before
      shipping: it proposes no changes there at all.
    - Every other low-confidence answer nearby (not just confident ones)
      still counts as normal context when checking *this* question, but
      the question actually being reconsidered is treated as if it were
      blank for finding its own neighbors and run length -- otherwise a
      bubble that's already suspected of being wrong would count as its
      own supporting evidence.

    Only ever overrides the answer itself (still marking it
    `pattern_inferred` for visibility) -- if the pattern happens to agree
    with what's already there, nothing changes."""
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
        if r.answer in ("", "MULTIPLE") or not r.low_confidence:
            continue

        left = _nearest_non_blank(choice_indices, i - 1, -1, skip_index=i)
        right = _nearest_non_blank(choice_indices, i + 1, 1, skip_index=i)
        if left is None or right is None or left != right:
            continue
        target = left

        left_run = _count_matching_run(choice_indices, i - 1, -1, target, skip_index=i)
        right_run = _count_matching_run(choice_indices, i + 1, 1, target, skip_index=i)
        if left_run + right_run < _PATTERN_MIN_TOTAL_RUN:
            continue

        choices = template.choices_for(r.question)
        predicted = choices[target]
        if predicted == r.answer:
            continue  # pattern agrees with the ink read -- nothing to change

        updated[i] = dataclasses.replace(
            r, answer=predicted, candidates=[predicted], low_confidence=True, pattern_inferred=True
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


def _apply_readability_checks(
    results: List[QuestionResult],
    bubbles_by_qkey: Dict[Tuple[str, int], List[Tuple[str, int, int]]],
    value: np.ndarray,
    radius: int,
) -> List[QuestionResult]:
    """Whole-sheet final pass: catch answers that ink-based scoring got
    wrong not because of *how much* area was dark (score_bubbles) or how
    it compared to that letter's usual appearance (the residual checks),
    but because the ink itself isn't genuinely dark -- a faded/faint block
    of the page, or a smudged eraser mark, real cases found on a real
    scan. See _dark_fraction, _UNREADABLE_MAX, and _find_mark_floor.

    Two independent checks, in order:
    1. Absolute: a question where every choice has essentially no dark
       ink anywhere gets flagged `unreadable` and forced blank, regardless
       of what it currently reads -- this needs no per-sheet calibration
       to be safe (see _UNREADABLE_MAX).
    2. Sheet-relative: among whatever's left, a question (single-answer or
       MULTIPLE alike) whose best candidate falls below *this sheet's* own
       floor separating its genuine marks from weaker artifacts gets
       downgraded to an ordinary blank (not `unreadable` -- the row does
       have some real ink, just not enough to trust as a deliberate mark).
       Skipped entirely if the sheet doesn't show a decisive enough gap to
       derive a safe floor from (see _find_mark_floor) -- most sheets
       don't have this problem, and are left untouched.
    """
    dark_fractions_by_result: List[Dict[str, float]] = []
    for r in results:
        bubbles = bubbles_by_qkey[(r.section, r.question)]
        dark_fractions_by_result.append({choice: _dark_fraction(value, x, y, radius) for choice, x, y in bubbles})

    updated = list(results)
    for i, r in enumerate(results):
        fractions = dark_fractions_by_result[i]
        if fractions and max(fractions.values()) < _UNREADABLE_MAX:
            updated[i] = dataclasses.replace(r, answer="", candidates=[], low_confidence=False, unreadable=True)

    winner_fractions = [
        dark_fractions_by_result[i][r.answer]
        for i, r in enumerate(updated)
        if r.answer not in ("", "MULTIPLE") and not r.pattern_inferred
    ]
    floor = _find_mark_floor(winner_fractions) if len(winner_fractions) >= 20 else None
    if floor is not None:
        for i, r in enumerate(updated):
            if r.answer == "" or r.pattern_inferred or r.unreadable:
                continue
            # A MULTIPLE result has no single "winning" choice; if even its
            # best candidate doesn't clear the floor, none of them do --
            # the same faint/smudged-mark problem, just with fill_ratio's
            # own relative-margin check also caught in it.
            fractions = dark_fractions_by_result[i]
            best = fractions[r.answer] if r.answer != "MULTIPLE" else max(fractions.values())
            if best < floor:
                updated[i] = dataclasses.replace(r, answer="", candidates=[], low_confidence=False)
    return updated


def evaluate_sheet(image: np.ndarray, template: Template) -> Tuple[List[QuestionResult], List[str]]:
    """Score every bubble on a sheet.

    Returns (results, sections_using_fixed_coordinates) -- the second list
    names any sections where grid_detect couldn't establish the expected
    bubble layout on this specific sheet and fell back to the template's
    fixed nominal coordinates uncorrected, which is a real accuracy risk
    (see grid_detect module docstring) worth surfacing to the caller.
    """
    binary = binarize(image)
    value = _value_channel(image)
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    all_bubbles = template.bubbles()
    results = []
    fallback_sections = []
    bubbles_by_qkey: Dict[Tuple[str, int], List[Tuple[str, int, int]]] = {}
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
            bubbles_by_qkey[(section.name, question)] = bubbles
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
            # it. Deliberately *not* extended to fill_ratio's own
            # low-confidence single answers (tried, then reverted -- see the
            # threshold comment above): only a question with no fill-ratio
            # answer at all gets a second opinion from the residual signal.
            if answer in ("", "MULTIPLE"):
                residuals = {
                    choice: _residual_ratio(binary, x, y, template.bubble_radius, choice_templates[choice])
                    for choice, x, y in bubbles
                    if choice in choice_templates
                }
                partial_mark = _partial_mark_choice(residuals)
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

        # Last resort, after every ink-based check above. Order matters:
        # reconsidering a low-confidence single answer first (rare, and
        # deliberately conservative -- see _reconsider_low_confidence_pattern)
        # means a corrected answer is then available as real context for
        # filling a still-blank/MULTIPLE neighbor right next to it, not just
        # the reverse.
        section_results = _reconsider_low_confidence_pattern(section_results, template)
        results.extend(_infer_from_answer_pattern(section_results, template))

    results = _apply_readability_checks(results, bubbles_by_qkey, value, template.bubble_radius)
    return results, fallback_sections
