import cv2
import numpy as np

from answer_extractor.align import align_to_template
from answer_extractor.template import Template
from tests.synth import render_sheet


def make_template() -> Template:
    data = {
        "page": {"width": 900, "height": 700},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 4, "x_start": 150, "y_start": 100, "row_height": 80},
                ],
            }
        ],
        "bubble_spacing_x": 60,
        "bubble_radius": 18,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
    }
    return Template.from_dict(data)


def test_align_finds_bordered_sheet_against_background():
    template = make_template()
    sheet = render_sheet(template, {1: ["F"]}, with_border=True)

    # Simulate a photographed sheet sitting on a dark background, slightly
    # padded/offset from a straight-on crop.
    canvas = np.full((900, 1100, 3), 40, dtype=np.uint8)
    canvas[80:80 + sheet.shape[0], 120:120 + sheet.shape[1]] = sheet

    result = align_to_template(canvas, template.page_width, template.page_height)
    assert result.used_contour
    assert result.image.shape[:2] == (template.page_height, template.page_width)


def test_align_falls_back_to_resize_without_border():
    template = make_template()
    # A flat white image has no contour to detect at all.
    blank = np.full((500, 500, 3), 255, dtype=np.uint8)
    result = align_to_template(blank, template.page_width, template.page_height)
    assert not result.used_contour
    assert result.image.shape[:2] == (template.page_height, template.page_width)
