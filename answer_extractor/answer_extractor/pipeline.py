"""End-to-end: scanned sheet(s) in, per-sheet answer results out."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable, List, Tuple

import cv2
import numpy as np

from .align import align_to_template
from .detect import QuestionResult, evaluate_sheet
from .loading import iter_source_files, load_sheets
from .template import Template
from .template_detect import DEFAULT_TEMPLATES_DIR, DetectionResult, detect_template


@dataclasses.dataclass
class SheetResult:
    label: str
    source: str
    used_contour_alignment: bool
    questions: List[QuestionResult]
    fallback_sections: List[str] = dataclasses.field(default_factory=list)
    # Which template's file this sheet was scored against -- "" when the
    # caller supplied a fixed template rather than auto-detecting one.
    template_name: str = ""

    @property
    def has_review_items(self) -> bool:
        return (
            any(q.answer in ("", "MULTIPLE") or q.low_confidence for q in self.questions)
            or bool(self.fallback_sections)
        )


@dataclasses.dataclass
class UndetectedSheet:
    """A sheet auto-detection couldn't confidently match to any template
    -- excluded from the results rather than guessed at, per
    template_detect's module docstring. `reason` is human-readable, meant
    to be shown to the user (e.g. surfaced as a CLI warning)."""

    label: str
    source: str
    reason: str


def process_path(path: str | Path, template: Template) -> List[SheetResult]:
    results = []
    for label, image in load_sheets(path):
        alignment = align_to_template(image, template.page_width, template.page_height)
        questions, fallback_sections = evaluate_sheet(alignment.image, template)
        results.append(
            SheetResult(
                label=label,
                source=str(path),
                used_contour_alignment=alignment.used_contour,
                questions=questions,
                fallback_sections=fallback_sections,
            )
        )
    return results


def process_paths(paths: Iterable[str | Path], template: Template) -> List[SheetResult]:
    """Process several files/directories (e.g. a batch of dropped PDFs) into
    one combined result list, de-duplicating sheet labels that collide
    across inputs (e.g. two different PDFs both containing a "page1")."""
    results: List[SheetResult] = []
    seen_labels: dict[str, int] = {}
    for path in paths:
        for result in process_path(path, template):
            label = result.label
            if label in seen_labels:
                seen_labels[label] += 1
                result.label = f"{label}_{seen_labels[label]}"
            else:
                seen_labels[label] = 0
            results.append(result)
    return results


def process_path_auto(
    path: str | Path, templates_dir: str | Path = DEFAULT_TEMPLATES_DIR
) -> Tuple[List[SheetResult], List[UndetectedSheet]]:
    """Like process_path, but figures out which template each individual
    sheet is (see template_detect) instead of taking one as a fixed
    argument -- so a batch can freely mix sheet formats. Sheets that can't
    be confidently matched to a template are returned separately rather
    than silently skipped or guessed at.

    Each source file (`path` itself, or each file found by walking it if
    it's a directory -- see iter_source_files) gives at most ONE result:
    its LAST page that matches a template. Pages are checked from the last
    one back and checking stops at the first match, so nothing before it
    is even rendered -- a whole multi-page test booklet PDF (not just the
    bubble sheet cropped out on its own), whose answer sheet is always
    the last page that's actually a bubble sheet, costs one page's work
    instead of dozens. Keeping only the last match is also a safeguard:
    a template_detect module docstring assumption ("a wrong template's
    sections essentially never all agree by coincidence") turned out not
    to hold for dense justified body text -- confirmed against a real
    50-page booklet where two ordinary passage pages structurally matched
    a template, alongside the one genuine bubble sheet at the very end. A
    batch scan of many *different* students' sheets concatenated into one
    PDF would lose all but the last student's under this same rule, so
    don't combine unrelated students' sheets into one file when using
    this function.

    A page that doesn't match upright is tried turned around too (see
    _detect_any_orientation) -- a sheet fed into the scanner upside down
    or sideways reads the same as one fed the right way.

    A file with no matching page at all is reported once: with its own
    reason for a single page, or as one entry for the whole file (giving
    its last page's reason) for several."""
    results: List[SheetResult] = []
    undetected: List[UndetectedSheet] = []
    for file_path in iter_source_files(path):
        failures: List[UndetectedSheet] = []
        for label, image in load_sheets(file_path, last_first=True):
            detection = _detect_any_orientation(image, templates_dir)
            if detection.match is None:
                reason = detection.describe_failure()
                failures.append(UndetectedSheet(label=label, source=str(file_path), reason=reason))
                continue
            match = detection.match
            questions, fallback_sections = evaluate_sheet(match.aligned_image, match.template)
            results.append(
                SheetResult(
                    label=label,
                    source=str(file_path),
                    used_contour_alignment=match.used_contour,
                    questions=questions,
                    fallback_sections=fallback_sections,
                    template_name=match.path.stem,
                )
            )
            break
        else:
            if len(failures) == 1:
                undetected.extend(failures)
            elif failures:
                undetected.append(
                    UndetectedSheet(
                        label=file_path.stem,
                        source=str(file_path),
                        reason=f"none of its {len(failures)} pages matched a known template -- "
                        f"last page: {failures[0].reason}",
                    )
                )
    return results, undetected


def _detect_any_orientation(image: np.ndarray, templates_dir: str | Path) -> DetectionResult:
    """detect_template, retried with `image` turned around when it doesn't
    match as-is: upside down, then a quarter-turn each way -- the
    quarter-turns first for a landscape page, a sideways portrait sheet
    being the likelier reason for it. The upright attempt's own failure is
    what's reported if none match -- the orientation the page actually
    came in."""
    detection = detect_template(image, templates_dir)
    if detection.match is not None:
        return detection
    height, width = image.shape[:2]
    quarter_turns = [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]
    turns = [cv2.ROTATE_180, *quarter_turns] if height >= width else [*quarter_turns, cv2.ROTATE_180]
    for turn in turns:
        turned = detect_template(cv2.rotate(image, turn), templates_dir)
        if turned.match is not None:
            return turned
    return detection


def process_paths_auto(
    paths: Iterable[str | Path], templates_dir: str | Path = DEFAULT_TEMPLATES_DIR
) -> Tuple[List[SheetResult], List[UndetectedSheet]]:
    """Auto-detecting counterpart to process_paths -- see process_path_auto."""
    results: List[SheetResult] = []
    undetected: List[UndetectedSheet] = []
    seen_labels: dict[str, int] = {}
    for path in paths:
        path_results, path_undetected = process_path_auto(path, templates_dir)
        for result in path_results:
            label = result.label
            if label in seen_labels:
                seen_labels[label] += 1
                result.label = f"{label}_{seen_labels[label]}"
            else:
                seen_labels[label] = 0
            results.append(result)
        undetected.extend(path_undetected)
    return results, undetected
