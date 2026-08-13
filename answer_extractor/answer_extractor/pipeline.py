"""End-to-end: scanned sheet(s) in, per-sheet answer results out."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable, List, Tuple

from .align import align_to_template
from .detect import QuestionResult, evaluate_sheet
from .loading import load_sheets
from .template import Template
from .template_detect import DEFAULT_TEMPLATES_DIR, detect_template


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
    than silently skipped or guessed at."""
    results: List[SheetResult] = []
    undetected: List[UndetectedSheet] = []
    for label, image in load_sheets(path):
        detection = detect_template(image, templates_dir)
        if detection.match is None:
            undetected.append(
                UndetectedSheet(label=label, source=str(path), reason=detection.describe_failure())
            )
            continue
        match = detection.match
        questions, fallback_sections = evaluate_sheet(match.aligned_image, match.template)
        results.append(
            SheetResult(
                label=label,
                source=str(path),
                used_contour_alignment=match.used_contour,
                questions=questions,
                fallback_sections=fallback_sections,
                template_name=match.path.stem,
            )
        )
    return results, undetected


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
