"""Bubble sheet template: describes sheet geometry and how to derive bubble
pixel coordinates for every question, without hardcoding any one sheet layout.

A template is a YAML file (see templates/default_template.yaml) with:

  page:
    width, height        - pixel size the sheet is warped to before sampling
  columns:                - one or more question blocks (a sheet is usually
                             laid out in 2+ columns of questions)
    - first_question, last_question
      x_start              - x pixel coordinate of the first (leftmost) bubble
      y_start              - y pixel coordinate of the first question's row
      row_height           - vertical spacing between consecutive questions
  bubble_spacing_x        - horizontal spacing between adjacent choice bubbles
  bubble_radius           - approximate bubble radius in pixels
  choices:
    even: [A, B, C, D]    - answer letters for even-numbered questions
    odd:  [F, G, H, J]    - answer letters for odd-numbered questions
  thresholds:
    fill_ratio_min         - minimum darkness fraction to count as "marked"
    relative_margin        - how close to the darkest bubble another bubble
                              must be to also count as "marked" (catches
                              multiple-answer and light/partial marks)
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List

import yaml


@dataclasses.dataclass(frozen=True)
class Bubble:
    question: int
    choice: str
    x: int
    y: int


@dataclasses.dataclass(frozen=True)
class ColumnSpec:
    first_question: int
    last_question: int
    x_start: int
    y_start: int
    row_height: int


@dataclasses.dataclass(frozen=True)
class Thresholds:
    fill_ratio_min: float = 0.35
    relative_margin: float = 0.15


@dataclasses.dataclass(frozen=True)
class Template:
    page_width: int
    page_height: int
    columns: List[ColumnSpec]
    bubble_spacing_x: int
    bubble_radius: int
    even_choices: List[str]
    odd_choices: List[str]
    thresholds: Thresholds

    @property
    def num_questions(self) -> int:
        return max(c.last_question for c in self.columns)

    def choices_for(self, question: int) -> List[str]:
        return self.even_choices if question % 2 == 0 else self.odd_choices

    def bubbles(self) -> Dict[int, List[Bubble]]:
        """Return {question_number: [Bubble, ...]} for every question in the template."""
        result: Dict[int, List[Bubble]] = {}
        for col in self.columns:
            for question in range(col.first_question, col.last_question + 1):
                row_index = question - col.first_question
                y = col.y_start + row_index * col.row_height
                choices = self.choices_for(question)
                bubbles = [
                    Bubble(
                        question=question,
                        choice=choice,
                        x=col.x_start + i * self.bubble_spacing_x,
                        y=y,
                    )
                    for i, choice in enumerate(choices)
                ]
                result[question] = bubbles
        return result

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Template":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        page = data["page"]
        columns = [
            ColumnSpec(
                first_question=c["first_question"],
                last_question=c["last_question"],
                x_start=c["x_start"],
                y_start=c["y_start"],
                row_height=c["row_height"],
            )
            for c in data["columns"]
        ]
        choices = data.get("choices", {})
        thresholds_data = data.get("thresholds", {})
        thresholds = Thresholds(
            fill_ratio_min=thresholds_data.get("fill_ratio_min", 0.35),
            relative_margin=thresholds_data.get("relative_margin", 0.15),
        )
        return cls(
            page_width=page["width"],
            page_height=page["height"],
            columns=columns,
            bubble_spacing_x=data["bubble_spacing_x"],
            bubble_radius=data["bubble_radius"],
            even_choices=list(choices.get("even", ["A", "B", "C", "D"])),
            odd_choices=list(choices.get("odd", ["F", "G", "H", "J"])),
            thresholds=thresholds,
        )

    def validate(self) -> None:
        """Sanity-check the template and raise ValueError on obvious problems."""
        if not self.columns:
            raise ValueError("Template must define at least one column")
        seen = set()
        for col in self.columns:
            if col.first_question > col.last_question:
                raise ValueError(
                    f"Column first_question ({col.first_question}) > "
                    f"last_question ({col.last_question})"
                )
            for q in range(col.first_question, col.last_question + 1):
                if q in seen:
                    raise ValueError(f"Question {q} is defined in more than one column")
                seen.add(q)
        expected = set(range(1, max(seen) + 1))
        missing = expected - seen
        if missing:
            raise ValueError(f"Template is missing questions: {sorted(missing)}")
        for bubbles in self.bubbles().values():
            for b in bubbles:
                if not (0 <= b.x <= self.page_width) or not (0 <= b.y <= self.page_height):
                    raise ValueError(
                        f"Bubble for question {b.question} choice {b.choice} at "
                        f"({b.x}, {b.y}) falls outside the page "
                        f"({self.page_width}x{self.page_height})"
                    )
