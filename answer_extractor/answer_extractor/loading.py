"""Load scanned sheets from image files or PDFs into OpenCV (BGR) images."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional, Tuple

import cv2
import numpy as np

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_SUFFIXES = {".pdf"}

# Render PDF pages at this resolution; higher DPI gives cleaner bubble edges
# at the cost of speed. 200 DPI matches common flatbed scanner defaults.
PDF_RENDER_DPI = 200

# A real physical page/poster essentially never exceeds this in either
# dimension (20in). Found against a real PDF whose declared page size was
# 2502x3456 *points* -- 34.75x48in, nothing anyone prints or scans -- while
# its one embedded image was exactly 2502x3456 *pixels*: the page's own
# size had been set by treating the scan's pixel dimensions as point
# dimensions (an easy off-by-one-DPI-assumption mistake for whatever
# scanning software produced it, effectively declaring "72 DPI" on what
# was actually a genuine ~300 DPI scan). Rendering that page by applying
# PDF_RENDER_DPI's zoom to its (wrong) point size -- the normal path,
# correct for every real sheet on hand -- produced a needlessly huge
# render (6950x9600) that grid_detect's structural check then failed to
# match against *any* template at all. Any page this large in declared
# points is far more likely to be this same mistake than a genuine
# oversized physical page, so it's treated as a signal to prefer the
# embedded image's own pixel dimensions over the page's declared size.
_MAX_PLAUSIBLE_PAGE_POINTS = 20 * 72


def _extract_full_page_image(page) -> Optional[np.ndarray]:
    """If `page` has exactly one embedded image whose own aspect ratio
    plausibly matches the page's, decode and return it directly (BGR) --
    the image's native resolution, with no resampling through the page's
    declared (and here, unreliable) point-space geometry at all. Returns
    None if that doesn't cleanly apply (no images, more than one -- which
    page image represents "the sheet" would be a guess -- or a decode
    failure), so callers can fall back to the normal zoom-rendered path.
    """
    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    try:
        base = page.parent.extract_image(images[0][0])
        image = cv2.imdecode(np.frombuffer(base["image"], dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None
    if image is None:
        return None

    page_aspect = page.rect.width / page.rect.height
    image_aspect = image.shape[1] / image.shape[0]
    # A background/full-page scan's aspect ratio should closely match the
    # page's own -- generous tolerance (this project's own templates alone
    # span 0.727-0.773) rather than a decorative image that happens to be
    # the page's only one but covers just part of it.
    if not (0.5 < image_aspect / page_aspect < 2.0):
        return None
    return image


def iter_source_files(path: str | Path) -> Iterator[Path]:
    """Yield each individual image/PDF file at `path` -- itself if it's
    already a single file, or every matching file found by walking a
    directory. This is the unit callers that need to treat "everything
    from one source document" as a group (e.g. picking one page out of a
    multi-page PDF) should key on -- `load_sheets` on a *directory*
    otherwise gives no way to tell which yielded sheet came from which
    underlying file.
    """
    path = Path(path)
    if path.is_dir():
        for child in sorted(path.iterdir()):
            if child.suffix.lower() in IMAGE_SUFFIXES or child.suffix.lower() in PDF_SUFFIXES:
                yield from iter_source_files(child)
        return
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES or suffix in PDF_SUFFIXES:
        yield path
    else:
        raise ValueError(f"Unsupported file type: {path}")


def load_sheets(path: str | Path) -> Iterator[Tuple[str, np.ndarray]]:
    """Yield (label, image) pairs for every sheet found at `path`.

    `path` may be a single image file, a single PDF (each page becomes one
    sheet), or a directory containing any mix of the above.
    """
    path = Path(path)
    if path.is_dir():
        for child in sorted(path.iterdir()):
            if child.suffix.lower() in IMAGE_SUFFIXES or child.suffix.lower() in PDF_SUFFIXES:
                yield from load_sheets(child)
        return

    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Could not read image: {path}")
        yield path.stem, image
    elif suffix in PDF_SUFFIXES:
        yield from _load_pdf(path)
    else:
        raise ValueError(f"Unsupported file type: {path}")


def _load_pdf(path: Path) -> Iterator[Tuple[str, np.ndarray]]:
    import fitz  # PyMuPDF

    zoom = PDF_RENDER_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    doc = fitz.open(str(path))
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            image = None
            if page.rect.width > _MAX_PLAUSIBLE_PAGE_POINTS or page.rect.height > _MAX_PLAUSIBLE_PAGE_POINTS:
                image = _extract_full_page_image(page)
            if image is None:
                pix = page.get_pixmap(matrix=matrix)
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                if pix.n == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                else:  # grayscale
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            label = path.stem if len(doc) == 1 else f"{path.stem}_p{page_index + 1}"
            yield label, image
    finally:
        doc.close()
