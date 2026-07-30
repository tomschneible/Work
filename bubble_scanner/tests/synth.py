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


def make_blank_sheet(
    template: Template,
    with_border: bool = True,
    ink_color=(0, 0, 0),
    letters: bool = False,
    x_shift: int = 0,
    y_shift: int = 0,
) -> np.ndarray:
    """Render an unmarked sheet. `ink_color` (BGR) lets tests simulate sheets
    printed in a saturated "dropout" accent color (e.g. coral, as real ACT
    sheets use) instead of plain black -- `letters=True` additionally draws
    a bold letter inside each bubble, mimicking how much ink such sheets
    actually put inside an unmarked bubble. `x_shift`/`y_shift` draw every
    bubble that many pixels away from the template's nominal position, to
    simulate a real sheet whose actual print/scan drifted from the
    template's calibrated coordinates (see grid_detect module docstring)."""
    image = np.full((template.page_height, template.page_width, 3), 255, dtype=np.uint8)
    if with_border:
        cv2.rectangle(
            image,
            (10, 10),
            (template.page_width - 11, template.page_height - 11),
            (0, 0, 0),
            4,
        )
    thickness = 2 if not letters else 3
    for bubbles in template.bubbles().values():
        for b in bubbles:
            x, y = b.x + x_shift, b.y + y_shift
            cv2.circle(image, (x, y), template.bubble_radius, ink_color, thickness)
            if letters:
                cv2.putText(
                    image,
                    b.choice,
                    (x - 6, y + 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    ink_color,
                    thickness,
                    cv2.LINE_AA,
                )
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
    answers: Dict,
    coverage: float = 1.0,
    darkness: int = 20,
    with_border: bool = True,
    ink_color=(0, 0, 0),
    letters: bool = False,
    x_shift: int = 0,
    y_shift: int = 0,
) -> np.ndarray:
    """Render a sheet where `answers[key]` lists the choice letters to mark
    for that question (empty/absent -> left blank). `key` is either a plain
    question number (only valid when the template has exactly one section)
    or a (section_name, question) tuple for multi-section templates.
    `x_shift`/`y_shift` are passed through to make_blank_sheet -- marks are
    drawn at the same shifted position as their bubble."""
    image = make_blank_sheet(
        template,
        with_border=with_border,
        ink_color=ink_color,
        letters=letters,
        x_shift=x_shift,
        y_shift=y_shift,
    )
    bubbles_by_q = template.bubbles()

    if len(template.sections) == 1:
        only_section = template.sections[0].name
    else:
        only_section = None

    for key, marks in answers.items():
        if isinstance(key, tuple):
            section_question = key
        else:
            if only_section is None:
                raise ValueError(
                    "Plain int question keys require a single-section template; "
                    "use (section_name, question) tuples instead"
                )
            section_question = (only_section, key)
        for bubble in bubbles_by_q[section_question]:
            if bubble.choice in marks:
                fill_bubble(
                    image,
                    bubble.x + x_shift,
                    bubble.y + y_shift,
                    template.bubble_radius,
                    coverage,
                    darkness,
                )
    return image
