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

        for question in range(1, section.num_questions + 1):
            if detected is not None:
                bubbles = detected[question]
            else:
                bubbles = [(b.choice, b.x, b.y) for b in all_bubbles[(section.name, question)]]

            fill_ratios = score_bubbles(binary, bubbles, template.bubble_radius)
            answer, candidates, low_confidence = decide_answer(
                fill_ratios,
                template.thresholds.fill_ratio_min,
                template.thresholds.relative_margin,
            )
            results.append(
                QuestionResult(
                    section=section.name,
                    question=question,
                    answer=answer,
                    candidates=candidates,
                    fill_ratios=fill_ratios,
                    low_confidence=low_confidence,
                )
            )
    return results, fallback_sections
