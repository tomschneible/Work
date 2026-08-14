"""Perspective-correct a photographed/scanned sheet so bubble template
coordinates (defined against a fixed reference page size) line up.

Approach follows the standard OMR trick: find the largest 4-sided contour in
the image (the sheet's outer edge against its background), order its
corners, and warp that quadrilateral onto the template's reference page
size. If no clean 4-sided contour is found (e.g. the scan is already
cropped tight to the sheet), the image is scaled to fit the reference page
size *preserving its own aspect ratio* (see _resize_preserving_aspect) and
a warning is surfaced to the caller.
"""
from __future__ import annotations

import dataclasses

import cv2
import numpy as np


@dataclasses.dataclass
class AlignmentResult:
    image: np.ndarray
    used_contour: bool


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _find_sheet_corners(image: np.ndarray) -> np.ndarray | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 60, 160)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    image_area = image.shape[0] * image.shape[1]
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[:10]:
        area = cv2.contourArea(contour)
        if area < 0.2 * image_area:
            # Too small to plausibly be the whole sheet; contours are sorted
            # descending so nothing after this will be bigger.
            break
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype("float32")

    return None


def _resize_preserving_aspect(image: np.ndarray, page_width: int, page_height: int) -> np.ndarray:
    """Scale `image` by the *same* factor on both axes to fit within
    (page_width, page_height), then letterbox with white padding to reach
    that size exactly, keeping the content centered.

    Not simply cv2.resize(image, (page_width, page_height)): that stretches
    each axis independently to fill the target, which is only harmless
    when the source's own aspect ratio already matches the template's
    (true for a real scan/print of the same physical page size the
    template was calibrated against, e.g. US Letter -- 1700x2200 at 200
    DPI has the same 0.773 ratio as the paper itself). A source whose
    aspect ratio drifts from that -- confirmed on a real scan: 0.753
    instead of 0.773, a ~2.5% mismatch small enough to not be obviously
    wrong by eye -- gets stretched unevenly, and unlike a uniform
    shift/scale (which the per-section median-shift correction in
    grid_detect already absorbs fine), that unevenness grows with
    distance from the origin: content near the left edge lands close to
    where the template expects it, content two-thirds of the way across
    lands 20+px off -- enough to push individual bubbles outside
    grid_detect's own per-bubble matching tolerance and silently misread
    them. A uniform scale-then-pad has no such position-dependent
    component: whatever residual offset the padding introduces is a
    constant, which the existing shift-correction was already built to
    handle.
    """
    h, w = image.shape[:2]
    scale = min(page_width / w, page_height / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((page_height, page_width, 3), 255, dtype=np.uint8)
    x0 = (page_width - new_w) // 2
    y0 = (page_height - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def align_to_template(image: np.ndarray, page_width: int, page_height: int) -> AlignmentResult:
    corners = _find_sheet_corners(image)
    if corners is None:
        resized = _resize_preserving_aspect(image, page_width, page_height)
        return AlignmentResult(image=resized, used_contour=False)

    rect = _order_corners(corners)
    dst = np.array(
        [
            [0, 0],
            [page_width - 1, 0],
            [page_width - 1, page_height - 1],
            [0, page_height - 1],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, matrix, (page_width, page_height))
    return AlignmentResult(image=warped, used_contour=True)
