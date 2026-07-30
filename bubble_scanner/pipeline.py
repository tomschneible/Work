"""End-to-end: scanned sheet(s) in, per-sheet answer results out."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable, List

from .align import align_to_template
from .detect import QuestionResult, evaluate_sheet
from .loading import load_sheets
from .template import Template


@dataclasses.dataclass
class SheetResult:
    label: str
    source: str
    used_contour_alignment: bool
    questions: List[QuestionResult]
    fallback_sections: List[str] = dataclasses.field(default_factory=list)

    @property
    def has_review_items(self) -> bool:
        return (
            any(q.answer in ("", "MULTIPLE") or q.low_confidence for q in self.questions)
            or bool(self.fallback_sections)
        )


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
