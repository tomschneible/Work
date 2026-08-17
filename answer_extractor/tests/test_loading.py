"""Regression coverage for a real PDF whose declared page size didn't
match its content's actual resolution at all: 2502x3456 *points*
(34.75x48in -- nothing anyone prints or scans) for a page whose one
embedded image was exactly 2502x3456 *pixels*. Whatever produced that PDF
had set the page's point-size by treating the scan's pixel dimensions as
point dimensions -- effectively declaring "72 DPI" on a genuine ~300 DPI
scan. Rendering that page the normal way (PDF_RENDER_DPI's zoom applied to
its declared point size) produced a needlessly huge, over-rendered image
that grid_detect's structural check then failed to match against any
template at all -- see loading._MAX_PLAUSIBLE_PAGE_POINTS.
"""
from __future__ import annotations

import cv2
import fitz
import numpy as np

from answer_extractor.loading import PDF_RENDER_DPI, load_sheets


def _write_pdf_with_oversized_page(
    path: str, image: np.ndarray, page_width_pt: float, page_height_pt: float
) -> None:
    """A single-page PDF whose declared page size is `page_width_pt` x
    `page_height_pt`, with `image` inserted to fill it completely --
    mirrors the real file's shape (one image, full-bleed, on an oddly
    large declared page)."""
    ok, buf = cv2.imencode(".png", image)
    assert ok
    doc = fitz.open()
    page = doc.new_page(width=page_width_pt, height=page_height_pt)
    page.insert_image(fitz.Rect(0, 0, page_width_pt, page_height_pt), stream=buf.tobytes())
    doc.save(path)
    doc.close()


def test_load_sheets_prefers_the_embedded_image_over_an_implausible_page_size(tmp_path):
    # A real scan's resolution (deliberately not a round multiple of
    # PDF_RENDER_DPI, so a passing shape check couldn't be a coincidence),
    # placed on a page declared at that same pixel count in *points* --
    # the real failure shape.
    image = np.full((3456, 2502, 3), 255, dtype=np.uint8)
    cv2.putText(image, "TEST", (200, 400), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 8)

    pdf_path = tmp_path / "oversized_page.pdf"
    _write_pdf_with_oversized_page(str(pdf_path), image, page_width_pt=2502, page_height_pt=3456)

    labels_and_images = list(load_sheets(str(pdf_path)))
    assert len(labels_and_images) == 1
    _, loaded = labels_and_images[0]
    # The embedded image's own resolution, not PDF_RENDER_DPI applied to
    # the page's (unreliable) declared point size -- which would have
    # produced a needlessly huge, over-rendered image instead (roughly
    # 2502 * PDF_RENDER_DPI/72 wide).
    assert loaded.shape[:2] == (3456, 2502)


def test_load_sheets_still_zoom_renders_a_normal_page_size(tmp_path):
    # A real physical page size (US Letter) must still go through the
    # normal DPI-based render, not the embedded-image shortcut -- that
    # shortcut only exists for the implausible-page-size case.
    image = np.full((792, 612, 3), 255, dtype=np.uint8)
    pdf_path = tmp_path / "normal_page.pdf"
    _write_pdf_with_oversized_page(str(pdf_path), image, page_width_pt=612, page_height_pt=792)

    labels_and_images = list(load_sheets(str(pdf_path)))
    assert len(labels_and_images) == 1
    _, loaded = labels_and_images[0]
    expected_w = round(612 * PDF_RENDER_DPI / 72.0)
    expected_h = round(792 * PDF_RENDER_DPI / 72.0)
    assert loaded.shape[:2] == (expected_h, expected_w)
