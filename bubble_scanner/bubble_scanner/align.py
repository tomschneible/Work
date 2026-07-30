"""Perspective-correct a photographed/scanned sheet so bubble template
coordinates (defined against a fixed reference page size) line up.

Approach follows the standard OMR trick: find the largest 4-sided contour in
the image (the sheet's outer edge against its background), order its
corners, and warp that quadrilateral onto the template's reference page
size. If no clean 4-sided contour is found (e.g. the scan is already
cropped tight to the sheet), the image is resized to the reference page
size directly and a warning is surfaced to the caller.
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


def align_to_template(image: np.ndarray, page_width: int, page_height: int) -> AlignmentResult:
    corners = _find_sheet_corners(image)
    if corners is None:
        resized = cv2.resize(image, (page_width, page_height), interpolation=cv2.INTER_AREA)
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
