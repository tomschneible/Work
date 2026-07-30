"""Score how "filled in" each bubble is, and turn those scores into an
answer decision per question — including BLANK and MULTIPLE outcomes and
tolerance for light or partial marks.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List

import cv2
import numpy as np

from .template import Bubble, Template


@dataclasses.dataclass(frozen=True)
class QuestionResult:
    question: int
    answer: str  # a single choice letter, "" (blank), or "MULTIPLE"
    candidates: List[str]  # every choice detected as marked (0, 1, or 2+)
    fill_ratios: Dict[str, float]  # choice -> fill ratio, for auditing
    low_confidence: bool  # marked bubble(s) only marginally above threshold


def _binarize(image: np.ndarray) -> np.ndarray:
    """Return a binary image where marked (dark) pixels are 255."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    # Otsu picks a global split between the (light) paper and (dark) marks;
    # robust across scan brightness without needing a fixed threshold.
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


def score_bubbles(image: np.ndarray, bubbles: List[Bubble], radius: int) -> Dict[str, float]:
    binary = _binarize(image)
    return {b.choice: _bubble_fill_ratio(binary, b.x, b.y, radius) for b in bubbles}


def decide_answer(
    fill_ratios: Dict[str, float],
    fill_ratio_min: float,
    relative_margin: float,
) -> tuple[str, List[str], bool]:
    """Turn per-choice fill ratios into (answer, candidates, low_confidence).

    A choice counts as "marked" if its fill ratio clears the absolute floor
    AND is within `relative_margin` of the darkest bubble in the question.
    The relative check is what catches genuinely multiple answers (two
    bubbles both solidly filled) while the absolute floor keeps stray pencil
    smudges or scan noise from being read as an answer.
    """
    if not fill_ratios:
        return "", [], False

    max_ratio = max(fill_ratios.values())
    if max_ratio < fill_ratio_min:
        return "", [], False

    candidates = [
        choice
        for choice, ratio in fill_ratios.items()
        if ratio >= fill_ratio_min and ratio >= max_ratio - relative_margin
    ]
    # Preserve a stable, human-friendly order (matches choice definition order).
    candidates = [c for c in fill_ratios if c in candidates]

    low_confidence = max_ratio < fill_ratio_min + relative_margin

    if len(candidates) == 1:
        return candidates[0], candidates, low_confidence
    return "MULTIPLE", candidates, low_confidence


def evaluate_sheet(image: np.ndarray, template: Template) -> List[QuestionResult]:
    results = []
    for question, bubbles in sorted(template.bubbles().items()):
        fill_ratios = score_bubbles(image, bubbles, template.bubble_radius)
        answer, candidates, low_confidence = decide_answer(
            fill_ratios,
            template.thresholds.fill_ratio_min,
            template.thresholds.relative_margin,
        )
        results.append(
            QuestionResult(
                question=question,
                answer=answer,
                candidates=candidates,
                fill_ratios=fill_ratios,
                low_confidence=low_confidence,
            )
        )
    return results
