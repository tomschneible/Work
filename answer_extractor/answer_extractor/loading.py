"""Load scanned sheets from image files or PDFs into OpenCV (BGR) images."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Tuple

import cv2
import numpy as np

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_SUFFIXES = {".pdf"}

# Render PDF pages at this resolution; higher DPI gives cleaner bubble edges
# at the cost of speed. 200 DPI matches common flatbed scanner defaults.
PDF_RENDER_DPI = 200


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
            pix = doc[page_index].get_pixmap(matrix=matrix)
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
