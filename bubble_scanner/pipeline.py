"""End-to-end: scanned sheet(s) in, per-sheet answer results out."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import List

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

    @property
    def has_review_items(self) -> bool:
        return any(q.answer in ("", "MULTIPLE") or q.low_confidence for q in self.questions)


def process_path(path: str | Path, template: Template) -> List[SheetResult]:
    results = []
    for label, image in load_sheets(path):
        alignment = align_to_template(image, template.page_width, template.page_height)
        questions = evaluate_sheet(alignment.image, template)
        results.append(
            SheetResult(
                label=label,
                source=str(path),
                used_contour_alignment=alignment.used_contour,
                questions=questions,
            )
        )
    return results
