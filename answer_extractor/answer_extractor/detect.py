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
    # True when `answer` came from _solid_fill_choice's blank->promoted
    # rescue (a heavy printed baseline hid a genuine mark from fill_ratio's
    # own floor entirely -- see evaluate_sheet), not from fill_ratio's
    # ordinary ranking. Always implies low_confidence, like
    # pattern_inferred, but for a different reason worth keeping distinct:
    # this signal's own check (erosion-verified solidity) is already
    # independent, real evidence of a genuine mark -- see
    # _apply_readability_checks, which trusts it over _dark_fraction's
    # strict near-black floor the same way it already trusts a directly-
    # confident fill_ratio answer.
    solid_fill: bool = False


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


_SOLIDITY_ERODE_PX = 3  # structuring-element radius; see _solidity


def _solidity(binary: np.ndarray, x: int, y: int, radius: int, erode_px: int = _SOLIDITY_ERODE_PX) -> float:
    """Fraction of a bubble's binarized (dark) area that survives erosion
    by `erode_px` pixels -- separates a genuine, complete fill from
    everything else that can make score_bubbles's plain area-based
    fill_ratio look substantial without one: a printed ring outline, a
    bold choice letter, or a scribble/X-out/partial-erasure mark are all
    made of strokes only a few px wide, so eroding by a few px collapses
    nearly all of that area; a solid fill is uniformly thick throughout
    and mostly survives the same erosion.

    Found chasing a real scan (see _SOLID_FILL_MIN and its use in
    evaluate_sheet) whose printed bubble style -- a heavy ring and bold
    letter even when completely unmarked -- pushed every choice's raw
    fill_ratio high enough that a genuine, unambiguous, fully solid mark
    sometimes couldn't be told apart from that baseline by area alone. A
    plain "largest connected component" version of this same idea was
    tried first and discarded: the printed ring is one continuous loop
    that typically touches whatever ink is inside it (the letter, a
    scribble, a real mark alike), merging everything into a single
    component regardless of its actual shape and defeating the whole
    point. Erosion has no such blind spot -- it only cares about stroke
    thickness, not connectivity."""
    h, w = binary.shape
    r = max(1, int(radius * 0.85))
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    if x0 >= x1 or y0 >= y1:
        return 0.0
    patch = binary[y0:y1, x0:x1]
    mask = np.zeros_like(patch, dtype=np.uint8)
    cv2.circle(mask, (x - x0, y - y0), r, 255, -1)
    masked = cv2.bitwise_and(patch, mask)
    total = cv2.countNonZero(masked)
    if total == 0:
        return 0.0
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode_px + 1, 2 * erode_px + 1))
    eroded = cv2.erode(masked, kernel)
    return float(cv2.countNonZero(eroded) / total)


# Calibrated against twelve real scanned sheets (six different physical
# forms). The highest solidity found among every question this project has
# actually confirmed (by eye, against the source scan) should stay
# blank/unmarked -- a genuinely ambiguous partial mark, scribble, or
# cross-out, never a clean fill -- was 0.291 (a partial mark whose ink,
# while fragmented, happened to survive erosion better than most: see this
# constant's real counterexample in tests). The lowest solidity found
# among confirmed genuine, fully solid marks on the one real sheet whose
# baseline ink was high enough to need this override at all was 0.277 --
# below that same ambiguous-mark ceiling, meaning this signal can't
# safely tell every genuine mark on that sheet apart from every genuinely
# ambiguous mark elsewhere; some of that sheet's fainter (but real) marks
# are left flagged rather than guessed at, same as any other signal in
# this module when the evidence runs out. 0.32 sits with real margin
# above the confirmed ambiguous-mark ceiling.
_SOLID_FILL_MIN = 0.32


def _solid_fill_choice(
    fill_ratios: Dict[str, float],
    binary: np.ndarray,
    bubbles: List[Tuple[str, int, int]],
    radius: int,
    min_solidity: float = _SOLID_FILL_MIN,
) -> "str | None":
    """If fill_ratio's own (baseline-adjusted) leading choice is a
    genuinely solid fill -- not just the highest of a compressed, noisy
    field -- return it; otherwise None. Only meant to be consulted when
    score_bubbles's ordinary signal came back blank (see evaluate_sheet):
    unlike _partial_mark_choice, this never needs to *rank* choices
    against each other in the usual case -- fill_ratio's own adjusted
    ranking already did that -- it only asks whether the leader's own ink
    shape is trustworthy enough to promote past the absolute floor that
    decided blank in the first place. The exception is an exact tie at
    the top: found on a real sheet whose heavy, uniform baseline print
    made two adjacent bubbles measure *exactly* equal by area (down to
    many decimal places) while only one was actually erosion-solid --
    picking fill_ratio's own dict-order winner there is arbitrary and
    blind to which one is real, so every choice tied for the lead gets
    its own solidity checked and the most solid one wins.
    """
    adjusted = _baseline_adjust(fill_ratios)
    if not adjusted:
        return None
    max_ratio = max(adjusted.values())
    tied_for_top = [choice for choice, ratio in adjusted.items() if ratio == max_ratio]
    best_choice, best_solidity = None, 0.0
    for choice in tied_for_top:
        x, y = next((bx, by) for c, bx, by in bubbles if c == choice)
        solidity = _solidity(binary, x, y, radius)
        if solidity > best_solidity:
            best_choice, best_solidity = choice, solidity
    if best_choice is not None and best_solidity >= min_solidity:
        return best_choice
    return None


def _solidity_standout_choice(
    binary: np.ndarray,
    bubbles: List[Tuple[str, int, int]],
    radius: int,
    min_solidity: float = _SOLID_FILL_MIN,
) -> "str | None":
    """Last-resort rescue for a row where fill_ratio's own area-based
    ranking is simply misleading, not just too compressed to clear a
    floor -- a heavy, *uneven* baseline print can make an unmarked
    choice's ring+letter measure *more* raw area than a genuinely marked
    choice elsewhere in the same row, so the real mark never even reaches
    _solid_fill_choice's own check (which only ever reconsiders
    fill_ratio's own top pick(s)). Confirmed against a real sheet where
    the genuinely marked choice ranked *last* of four by fill_ratio, yet
    was the only one of the four whose ink actually survived erosion.

    Ignores fill_ratio's ranking entirely and checks every choice's own
    solidity directly; if exactly one clears `min_solidity`, it's
    trusted. Two or more clearing it is left alone rather than guessed
    at -- indistinguishable from this same signal's job on a genuine
    double-mark (see evaluate_sheet's MULTIPLE handling), and a real
    reason for solid_fill_choice's own exact-tie case above to exist
    rather than just reusing this."""
    solid = [choice for choice, x, y in bubbles if _solidity(binary, x, y, radius) >= min_solidity]
    if len(solid) == 1:
        return solid[0]
    return None


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
    themselves or the cluster of near-zero blanks, AND requiring the
    resulting "genuine" side to be at least as large as the "suspect"
    side (see below).

    This is deliberately sheet-relative rather than a fixed constant: a
    real scan had one sheet's confirmed-genuine (if faint) mark measure
    darker than *another* sheet's confirmed false positive, so no single
    number works for every sheet -- but each sheet's own genuine marks
    were reliably far darker than that same sheet's faint-print/smudge
    artifacts, which is what a per-sheet gap search finds safely. Returns
    None (meaning: don't second-guess anything) when there isn't a
    decisive-enough gap to trust -- see _MARK_FLOOR_MIN_GAP.

    The majority-side requirement guards a real failure found against a
    real scan whose ink was heavily and uniformly toned/smudged across
    the *entire* sheet (not a faded minority block): the widest raw gap
    in its dark_fraction distribution fell between its single highest
    value and its second-highest, isolating exactly one "genuine"
    question against 204 "suspect" ones -- comfortably over
    _MARK_FLOOR_MIN_GAP purely because that lone top value happened to
    sit apart from a long, continuous tail, not because it marked any
    real two-cluster split. Every one of those 204 was actually a
    correct, confidently-read answer (confirmed against the source
    scan), silently wiped to blank. This function's premise -- a
    genuinely faded/smudged region is the *exception* on a sheet, not
    the norm -- rules that candidate out: the side of the gap holding
    the sheet's normal, trustworthy reads should never be the smaller
    one. Confirmed this still finds the intended floor on both the
    original 50/50 calibration split and a real 14-genuine/6-faded block
    (see tests).
    """
    ordered = sorted(dark_fractions)
    n = len(ordered)
    best_gap = 0.0
    best_threshold = None
    for i in range(n - 1):
        lower, upper = ordered[i], ordered[i + 1]
        if not (0.1 <= lower <= 0.6 or 0.1 <= upper <= 0.6):
            continue
        below_count = i + 1
        above_count = n - below_count
        if above_count < below_count:
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


def _partial_mark_agrees_with_fill_ratio(partial_mark: str, fill_ratios: Dict[str, float]) -> bool:
    """Whether `partial_mark` (a _partial_mark_choice pick) also leads
    fill_ratio's own ranking for this question, after the same per-
    question baseline subtraction decide_answer itself uses -- checked
    before evaluate_sheet trusts the residual signal enough to override a
    blank/MULTIPLE fill-ratio answer.

    Residual and fill_ratio are independent signals (one pixel-level,
    against this specific letter's own usual appearance across the
    section; one area-level, against this question's other choices), and
    on a real scan whose printed letters varied unusually in ink density
    from row to row, the residual signal alone picked a choice fill_ratio
    didn't even rank first among this question's *own* choices -- a real
    sign something about that specific bubble's appearance is atypical
    for reasons other than a mark, not corroborating evidence of one.
    Requiring agreement doesn't touch the case both signals get right
    together (the common case, including every real partial mark this
    override was built to catch) or wrong together (irrecoverable either
    way); it only suppresses the case where they actively disagree, which
    is exactly when neither is trustworthy alone."""
    adjusted = _baseline_adjust(fill_ratios)
    if not adjusted:
        return False
    return partial_mark == max(adjusted, key=adjusted.get)


# How much larger a gap in one choice letter's own residual distribution
# (across every occurrence of that letter in the section) must be before
# it's trusted as a real "some of these are marked, some aren't" split,
# rather than noise -- see _find_letter_residual_floor. Residual values
# cluster far more tightly than dark_fraction's when genuinely unmarked
# (confirmed against five real cases across four sheets using this
# reversed-print style: every unmarked occurrence of the same letter sat
# within ~0.03 of its own cluster, with the genuine-mark gap itself
# landing at 0.052-0.10), so this is deliberately smaller than
# _MARK_FLOOR_MIN_GAP, which was calibrated for dark_fraction's much wider
# natural spread on a different signal entirely.
_LETTER_RESIDUAL_MIN_GAP = 0.05

# A letter needs to appear at least this many times in a section before
# its own residual distribution is trusted at all -- same bar
# _apply_readability_checks already uses for _find_mark_floor, for the
# same reason: a gap search over too few points can't tell a real cluster
# boundary from noise.
_LETTER_RESIDUAL_MIN_SAMPLES = 20


def _find_letter_residual_floor(residuals: List[float]) -> "float | None":
    """A gap search over one choice letter's own residual values across
    every occurrence of that letter in a section, not dark_fraction
    across a whole sheet -- shaped like _find_mark_floor's search, but
    its majority-side requirement does NOT carry over (see below).

    Built for a real sheet whose bubbles print as a solid dark oval with
    the letter cut out in white -- a mark there *fills in* part of that
    cutout rather than adding area the way it does everywhere else in
    this module, which score_bubbles' ordinary fill_ratio_min floor
    wasn't calibrated for. The residual signal (_residual_ratio) ranked
    the genuine mark correctly there, clearing _PARTIAL_MARK_MIN_TOP with
    real margin, but not by enough over that *row's* own runner-up to
    clear _PARTIAL_MARK_MIN_GAP -- the wrong comparison for this shape of
    problem, since a fill differs from this letter's own baseline in
    degree, not by standing out against unrelated choices sharing its
    row. What actually separates a genuine mark here: every *other*
    occurrence of the same letter elsewhere in the section, which
    cluster tightly when genuinely unmarked and leave a real, checkable
    gap below one that's actually been marked in. Confirmed on five real
    cases across four different sheets using this print style (not just
    one outlier form) -- gaps of 0.054, 0.052, 0.052, 0.0996, and 0.057,
    against adjacent same-cluster gaps under 0.024 on either side in
    every case.

    _find_mark_floor requires its "genuine" side to be at least as large
    as its "suspect" side, because dark_fraction there spans an entire
    sheet where most answers are expected to be genuinely marked. That
    assumption does not transfer here: this distribution is every
    occurrence of ONE letter across a section, and any specific letter is
    only the correct answer to a minority of the questions it appears in
    (roughly a quarter, for a 4-5 choice format) -- so the genuinely-
    marked cluster is *structurally* the smaller side, not the larger
    one. Requiring it to be the majority would reject real cases outright
    (confirmed: both real cases above have above_count of 7-8 against
    below_count of 12-16). What still needs guarding against is a single
    noisy outlier -- one stray high value -- masquerading as a second
    cluster, so the bar here is a minimum count on the smaller side
    instead of a majority: at least two occurrences, so one lone reading
    can never trigger this on its own.
    """
    if len(residuals) < _LETTER_RESIDUAL_MIN_SAMPLES:
        return None
    ordered = sorted(residuals)
    n = len(ordered)
    best_gap = 0.0
    best_threshold = None
    for i in range(n - 1):
        below_count = i + 1
        above_count = n - below_count
        if above_count < 2:
            continue
        gap = ordered[i + 1] - ordered[i]
        if gap > best_gap:
            best_gap = gap
            best_threshold = (ordered[i] + ordered[i + 1]) / 2
    if best_gap < _LETTER_RESIDUAL_MIN_GAP:
        return None
    return best_threshold


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
# direction -- except right at a section's own last question, which
# structurally has no "other side" to ever check (see
# _BOUNDARY_PATTERN_MIN_RUN below).
_PATTERN_MIN_TOTAL_RUN = 8

# A blank/MULTIPLE run touching a section's very last question has no
# right-side neighbor to confirm against at all -- not "a short run", none
# -- so inferring it can only ever look at the left side alone. That loses
# the two-sided cross-check's real value: distinguishing an actual
# continuing pattern from a student who was guessing right up until they
# genuinely ran out of time and stopped for real, which happens
# disproportionately at exactly this boundary. Checked against six real
# sheets before picking this: three had a genuine "stopped for real" blank
# tail at a section's end, and in every one of them the questions
# immediately preceding the tail were themselves a normal mix of answers,
# not a matching-index run at all -- so this rule structurally never even
# considers firing on any of them, at any of several thresholds tried.
# Doubling the two-sided minimum here is a deliberate margin on top of
# that, not a number the real data actually required.
_BOUNDARY_PATTERN_MIN_RUN = _PATTERN_MIN_TOTAL_RUN * 2


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
    before -- this only changes what happens when there's more than one.

    A run touching the section's very last question is a special case: it
    structurally has no right-side neighbor to ever confirm against, only
    a left side, so it's held to a much higher bar
    (_BOUNDARY_PATTERN_MIN_RUN) instead of the two-sided total -- see that
    constant's comment for why, and for real evidence this doesn't fire on
    the much more common case of a student genuinely running out of time
    and leaving the true end of a section blank."""
    choice_indices: List["int | None"] = []
    for r in section_results:
        if r.answer in ("", "MULTIPLE"):
            choice_indices.append(None)
            continue
        choices = template.choices_for(r.section, r.question)
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

        target = None
        total_run = 0
        if left is not None and right is not None and left == right:
            target = left
            total_run = _count_matching_run(
                choice_indices, run_start - 1, -1, target
            ) + _count_matching_run(choice_indices, run_end + 1, 1, target)
            threshold = _PATTERN_MIN_TOTAL_RUN
        elif run_end == n - 1 and left is not None:
            # Touches the section's last question, with no right-side
            # neighbor to check at all -- see _BOUNDARY_PATTERN_MIN_RUN.
            target = left
            total_run = _count_matching_run(choice_indices, run_start - 1, -1, target)
            threshold = _BOUNDARY_PATTERN_MIN_RUN

        if target is not None and total_run >= threshold:
            for k in range(run_start, run_end + 1):
                r = section_results[k]
                choices = template.choices_for(r.section, r.question)
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
        choices = template.choices_for(r.section, r.question)
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

        choices = template.choices_for(r.section, r.question)
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
       ink anywhere gets flagged `unreadable` and forced blank -- unless
       score_bubbles' own area-based signal already settled on a single
       choice, which this never overrules (see _UNREADABLE_MAX; confirmed
       against a real sheet marked throughout in genuinely light pencil
       that score_bubbles read correctly and this absolute floor did
       not). Deliberately *not* conditioned on low_confidence here, unlike
       check 2 below: decide_answer only ever returns a single candidate
       once it's already cleared relative_margin against every other
       choice in the row -- real, structural differentiation, not the
       coin-flip low_confidence alone would suggest. Found against a real
       sheet marked throughout in genuinely gray (not black) pencil:
       fill_ratio's own relative comparison correctly and decisively
       picked the true mark in every row (never MULTIPLE, never a wrong
       letter), just with a thin absolute margin over the sheet's
       (irrelevantly) inflated low_confidence floor -- and
       _dark_fraction's strict near-black scale, reading near-zero
       throughout for the exact same reason light pencil does, wiped
       those correct answers right back to blank. Confirmed safe across
       every previously-validated real sheet (2,900+ questions): loosening
       this exemption from "confident" to "any real single winner" didn't
       change a single answer on any of them -- the real faded-region
       problem this check exists to catch (user-reported as "scattered
       wrong single answers and MULTIPLEs" -- see this module's own test
       history) reliably still shows up as MULTIPLE or an outright blank
       here too, both still fully protected.
    2. Sheet-relative: among whatever's left, a question (single-answer or
       MULTIPLE alike) whose best candidate falls below *this sheet's* own
       floor separating its genuine marks from weaker artifacts gets
       downgraded to an ordinary blank (not `unreadable` -- the row does
       have some real ink, just not enough to trust as a deliberate mark).
       Skipped entirely if the sheet doesn't show a decisive enough gap to
       derive a safe floor from (see _find_mark_floor) -- most sheets
       don't have this problem, and are left untouched. Like check 1, this
       never overrules a single-answer result score_bubbles already found
       confidently and not low_confidence -- found against a real sheet
       (see grid_detect's per-column shift correction) where a handful of
       rows' fallback-estimated bubble positions (used when a box wasn't
       actually detected) shifted a few px sheet-wide once the fix landed,
       which was enough to open up a gap in the sheet's dark_fraction
       distribution that hadn't existed before and didn't reflect any real
       faded/smudged region -- silently wiping an otherwise confidently
       and correctly read answer to blank. A genuine smudge or faded mark
       that fools this floor is, per _find_mark_floor's own module
       history, the sheet-relative exception, not one that also happens to
       win score_bubbles' own independent area comparison decisively.

    Both checks also skip a `solid_fill` answer outright, regardless of
    low_confidence (which that signal always sets -- see QuestionResult).
    Found against a real sheet combining two problems at once: heavy
    baseline printing (needing _solid_fill_choice's erosion-verified
    rescue just to see the mark past fill_ratio's own inflated floor at
    all) *and* genuinely gray, not black, ink throughout that same region
    -- every one of that rescue's answers measured ~0 on _dark_fraction's
    strict near-black scale, both checks (1 directly, 2 via a floor
    computed from a majority of similarly near-zero neighbors) wiping the
    rescue right back to blank. _solid_fill_choice's own check (does this
    bubble's ink survive erosion, unlike a ring/letter/scribble) is
    already independent, real evidence of a genuine mark -- the same
    reason a directly-confident fill_ratio answer is exempted above, just
    reached through a different signal.
    """
    dark_fractions_by_result: List[Dict[str, float]] = []
    for r in results:
        bubbles = bubbles_by_qkey[(r.section, r.question)]
        dark_fractions_by_result.append({choice: _dark_fraction(value, x, y, radius) for choice, x, y in bubbles})

    updated = list(results)
    for i, r in enumerate(results):
        if r.answer not in ("", "MULTIPLE") or r.solid_fill:
            # score_bubbles already found one choice, decisively ahead of
            # the rest by area (relative_margin) -- real, structural
            # evidence of a mark that this absolute darkness floor has no
            # business overruling, regardless of low_confidence (see this
            # function's own docstring for why that's safe to drop here
            # specifically). Found against a real sheet marked throughout
            # in genuinely light pencil: score_bubbles' own Otsu-relative
            # binarization correctly separated every mark from that sheet's
            # paper/print regardless, but _dark_fraction's *strict, sheet-
            # independent* near-black threshold (deliberately so, see its
            # own docstring) never saw enough of that lighter graphite to
            # clear _UNREADABLE_MAX even once, on over 70 already-confident
            # answers -- silently wiping every one of them to blank. This
            # check exists to catch the opposite real problem (print that's
            # too faded to trust *any* signal on), not to second-guess a
            # signal that's already trustworthy on its own terms -- and a
            # solid_fill answer's own erosion-verified solidity is exactly
            # that, despite always carrying low_confidence too.
            continue
        fractions = dark_fractions_by_result[i]
        if fractions and max(fractions.values()) < _UNREADABLE_MAX:
            updated[i] = dataclasses.replace(r, answer="", candidates=[], low_confidence=False, unreadable=True)

    winner_fractions = [
        dark_fractions_by_result[i][r.answer]
        for i, r in enumerate(updated)
        if r.answer not in ("", "MULTIPLE") and not r.pattern_inferred and not r.solid_fill
    ]
    floor = _find_mark_floor(winner_fractions) if len(winner_fractions) >= 20 else None
    if floor is not None:
        for i, r in enumerate(updated):
            if r.answer == "" or r.pattern_inferred or r.unreadable or r.solid_fill:
                continue
            if r.answer != "MULTIPLE" and not r.low_confidence:
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
            # Captured before the partial-mark override below can touch
            # `answer` -- True only when fill_ratio's own signal already
            # settled on one choice directly, as opposed to via that
            # override (which only ever fires when this was still "" or
            # "MULTIPLE" here). Used below to keep the solidity-based
            # low_confidence clearing from ever reaching an answer the
            # partial-mark override supplied, which must always stay
            # flagged regardless of how solid its ink looks -- that
            # signal's own accuracy was calibrated on the assumption every
            # one of its answers gets a human glance (see
            # _PARTIAL_MARK_MIN_GAP's comment).
            direct_single_answer = answer not in ("", "MULTIPLE")

            # Blank/MULTIPLE: see if a partial mark (e.g. a checkmark) explains
            # it. Deliberately *not* extended to fill_ratio's own
            # low-confidence single answers (tried, then reverted -- see the
            # threshold comment above): only a question with no fill-ratio
            # answer at all gets a second opinion from the residual signal.
            solid_fill = False
            if answer in ("", "MULTIPLE"):
                residuals = {
                    choice: _residual_ratio(binary, x, y, template.bubble_radius, choice_templates[choice])
                    for choice, x, y in bubbles
                    if choice in choice_templates
                }
                partial_mark = _partial_mark_choice(residuals)
                # See _partial_mark_agrees_with_fill_ratio for why this
                # cross-check matters, not just whether fill_ratio already
                # agreed with `answer` (it didn't -- that's why we're here).
                if (
                    partial_mark is not None
                    and partial_mark != answer
                    and _partial_mark_agrees_with_fill_ratio(partial_mark, fill_ratios)
                ):
                    answer, candidates, low_confidence = partial_mark, [partial_mark], True
                    # Deliberately never clears low_confidence (see the test
                    # this fixed) -- this override's own accuracy assumes a
                    # human glance regardless of ink shape. But a *third*,
                    # independent signal (does this specific bubble's ink
                    # survive erosion -- unlike a ring/letter/scribble) can
                    # still tell _apply_readability_checks' dark_fraction-
                    # based floors this row has a genuine mark, the same way
                    # it already does for a directly-solid fill_ratio answer
                    # below -- found on a real sheet combining heavy
                    # baseline printing (which is what made partial_mark's
                    # residual signal, not fill_ratio's own ranking, the one
                    # to settle this row) with genuinely gray, not black,
                    # ink (which reads ~0 on that strict scale regardless of
                    # which signal picked the answer).
                    x, y = next((bx, by) for choice, bx, by in bubbles if choice == answer)
                    if _solidity(binary, x, y, template.bubble_radius) >= _SOLID_FILL_MIN:
                        solid_fill = True

                if answer == "":
                    # Still blank: fill_ratio failed its own absolute
                    # floor entirely (no candidates at all, not a near-
                    # tie -- MULTIPLE already had its chance above), and
                    # the residual signal's own isolated-leader check
                    # just failed too. See _find_letter_residual_floor for
                    # the real case this covers instead: comparing this
                    # occurrence's residual against every *other*
                    # occurrence of the same letter in the section, not
                    # against this row's own (irrelevant, for this
                    # problem) runner-up choices.
                    residual_top = max(residuals, key=residuals.get) if residuals else None
                    if (
                        residual_top is not None
                        and residuals[residual_top] >= _PARTIAL_MARK_MIN_TOP
                        and _partial_mark_agrees_with_fill_ratio(residual_top, fill_ratios)
                    ):
                        letter_residuals = [
                            _residual_ratio(binary, lx, ly, template.bubble_radius, choice_templates[residual_top])
                            for lx, ly in bubbles_by_choice.get(residual_top, [])
                        ]
                        floor = _find_letter_residual_floor(letter_residuals)
                        if floor is not None and residuals[residual_top] >= floor:
                            answer, candidates, low_confidence = residual_top, [residual_top], True

            # A still-blank question's own leading choice, promoted only if
            # its ink is a genuinely solid fill (see _solid_fill_choice) --
            # catches a sheet whose baseline print is heavy enough that a
            # real, unambiguous mark's area-based fill_ratio doesn't clear
            # fill_ratio_min at all. Distinct from the partial-mark override
            # above (which resolves a blank via how this bubble's ink
            # compares to that *letter's own* usual appearance): this one
            # never needed a blank-or-MULTIPLE precondition tied to a
            # ranking signal -- fill_ratio's own adjusted ranking is what's
            # being trusted here, just past a floor that was too strict for
            # this sheet. Kept low_confidence despite resolving the answer,
            # like the partial-mark override, since it's still worth a
            # human glance.
            if answer == "":
                solid_choice = _solid_fill_choice(fill_ratios, binary, bubbles, template.bubble_radius)
                if solid_choice is not None:
                    answer, candidates, low_confidence = solid_choice, [solid_choice], True
                    solid_fill = True
            elif direct_single_answer and low_confidence:
                # Same signal, the other direction: a single answer
                # fill_ratio already found confidently-ranked, just flagged
                # low_confidence because its baseline-adjusted margin over
                # this sheet's inflated floor was thin. A genuinely solid
                # fill clears that worry independently of fill_ratio's own
                # numbers.
                x, y = next((bx, by) for choice, bx, by in bubbles if choice == answer)
                if _solidity(binary, x, y, template.bubble_radius) >= _SOLID_FILL_MIN:
                    low_confidence = False

            # Absolute last resort, only reached if fill_ratio's own
            # ranking (direct, partial-mark, and solid-fill alike) never
            # settled on anything -- see _solidity_standout_choice for why
            # this is a distinct case from the ones above: a heavy,
            # *uneven* baseline print can rank a genuinely marked choice
            # below an unmarked one by raw area, so the real mark never
            # even reaches those checks (they only ever reconsider
            # fill_ratio's own leader).
            if answer in ("", "MULTIPLE"):
                standout = _solidity_standout_choice(binary, bubbles, template.bubble_radius)
                if standout is not None:
                    answer, candidates, low_confidence = standout, [standout], True
                    solid_fill = True

            section_results.append(
                QuestionResult(
                    section=section.name,
                    question=question,
                    answer=answer,
                    candidates=candidates,
                    fill_ratios=fill_ratios,
                    low_confidence=low_confidence,
                    solid_fill=solid_fill,
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
