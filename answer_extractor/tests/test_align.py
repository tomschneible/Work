import cv2
import numpy as np

from answer_extractor.align import _resize_preserving_aspect, align_to_template
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


def test_align_ignores_a_printed_border_box_that_is_not_the_sheet_edge():
    # Regression coverage for a real scan (full-bleed -- the page fills the
    # whole image, no background around it, so there's no genuine sheet-
    # vs-background edge to find at all) whose answer sections were
    # grouped inside their own large printed border box, with real
    # whitespace above/below/beside it for a header, a marking-directions
    # box, and a logo box. That inner box was the largest, cleanest
    # 4-sided contour in the image -- comfortably over the old area floor
    # -- so it got warped to fill the *entire* template page, scattering
    # every section's real coordinates well outside grid_detect's own
    # matching tolerance and making template_detect fail to match
    # anything at all. A full-bleed page has no real corners to find, so
    # this must fall back to a plain resize, not the inner box.
    template = make_template()
    page = np.full((1000, 800, 3), 255, dtype=np.uint8)
    # A border box grouping the answer sections, well short of the page's
    # own edges on every side (leaves genuine margin for a header etc.,
    # unlike the sheet's own true edge against a background).
    cv2.rectangle(page, (100, 250), (700, 800), (0, 0, 0), 4)
    cv2.putText(page, "Header", (100, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)

    result = align_to_template(page, template.page_width, template.page_height)
    assert not result.used_contour
    assert result.image.shape[:2] == (template.page_height, template.page_width)


def test_resize_preserving_aspect_does_not_distort_a_mismatched_source():
    # Regression coverage for a real scan whose own aspect ratio (0.753)
    # differed enough from the template's (0.773, standard US Letter) that
    # plain cv2.resize -- stretching each axis independently to fill the
    # target -- introduced a real, position-dependent horizontal drift:
    # ~5px near the left edge of the page growing to ~25px two-thirds of
    # the way across, enough to push individual bubbles outside
    # grid_detect's own per-bubble matching tolerance. A single mark drawn
    # at a known fraction of the source's width/height should land at
    # that same fraction of the output, regardless of source aspect ratio.
    source = np.full((1100, 800, 3), 255, dtype=np.uint8)  # aspect 0.727, mismatched vs target below
    mark_frac_x, mark_frac_y = 0.75, 0.5
    cv2.circle(
        source, (round(800 * mark_frac_x), round(1100 * mark_frac_y)), 15, (0, 0, 0), -1
    )

    page_width, page_height = 1700, 2200  # aspect 0.773
    result = _resize_preserving_aspect(source, page_width, page_height)
    assert result.shape[:2] == (page_height, page_width)

    gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray < 128)
    found_x, found_y = xs.mean(), ys.mean()

    # A uniform scale (same factor on both axes) preserves the mark's
    # fractional position *within its own scaled content*, not within the
    # padded canvas -- compute the expected position accounting for the
    # letterboxing this introduces (padding on whichever axis has room to
    # spare after the other is scaled to fit exactly).
    scale = min(page_width / source.shape[1], page_height / source.shape[0])
    scaled_w, scaled_h = source.shape[1] * scale, source.shape[0] * scale
    x_off = (page_width - scaled_w) / 2
    y_off = (page_height - scaled_h) / 2
    expected_x = x_off + mark_frac_x * scaled_w
    expected_y = y_off + mark_frac_y * scaled_h

    assert abs(found_x - expected_x) < 3
    assert abs(found_y - expected_y) < 3
