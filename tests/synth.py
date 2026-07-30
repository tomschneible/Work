"""Generate synthetic bubble sheet images for testing, without needing a
real scanned sample. Draws bubble outlines from a Template and fills
requested choices at a controllable darkness/coverage, so the pipeline's
fill-ratio/threshold logic can be exercised end-to-end.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

import cv2
import numpy as np

from bubble_scanner.template import Template


def make_blank_sheet(template: Template, with_border: bool = True) -> np.ndarray:
    image = np.full((template.page_height, template.page_width, 3), 255, dtype=np.uint8)
    if with_border:
        cv2.rectangle(
            image,
            (10, 10),
            (template.page_width - 11, template.page_height - 11),
            (0, 0, 0),
            4,
        )
    for bubbles in template.bubbles().values():
        for b in bubbles:
            cv2.circle(image, (b.x, b.y), template.bubble_radius, (0, 0, 0), 2)
    return image


def fill_bubble(
    image: np.ndarray,
    x: int,
    y: int,
    radius: int,
    coverage: float = 1.0,
    darkness: int = 20,
) -> None:
    """Fill a bubble. `coverage` is the fraction of the bubble's *area*
    covered by the mark (1.0 = fully filled), simulating a partial/sloppy
    mark; `darkness` simulates a light pencil mark (higher = lighter)."""
    fill_radius = max(1, int(radius * 0.9 * (coverage ** 0.5)))
    color = (darkness, darkness, darkness)
    cv2.circle(image, (x, y), fill_radius, color, -1)


def render_sheet(
    template: Template,
    answers: Dict[int, Iterable[str]],
    coverage: float = 1.0,
    darkness: int = 20,
    with_border: bool = True,
) -> np.ndarray:
    """Render a sheet where `answers[question]` lists the choice letters to
    mark for that question (empty/absent -> left blank)."""
    image = make_blank_sheet(template, with_border=with_border)
    bubbles_by_q = template.bubbles()
    for question, marks in answers.items():
        for bubble in bubbles_by_q[question]:
            if bubble.choice in marks:
                fill_bubble(
                    image, bubble.x, bubble.y, template.bubble_radius, coverage, darkness
                )
    return image
