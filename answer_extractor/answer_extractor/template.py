"""Bubble sheet template: describes sheet geometry and how to derive bubble
pixel coordinates for every question, without hardcoding any one sheet layout.

A sheet is modeled as one or more named **sections** (e.g. separate tests on
an ACT-style answer sheet, each restarting question numbering at 1). A
single-section sheet is just a template with one section.

A template is a YAML file (see templates/*.yaml) with:

  page:
    width, height          - pixel size the sheet is warped to before sampling
  sections:                 - one or more independently-numbered question blocks
    - name: <str>            - shown in spreadsheet column headers, e.g. "English"
      choices:                - OPTIONAL per-section override of the top-level
                                 `choices` block below (same even/odd shape) --
                                 e.g. a legacy ACT sheet's Math section uses 5
                                 choices (A-E/F-K) while every other section on
                                 the same physical sheet uses 4 (A-D/F-J). Falls
                                 back to the template-level choices when omitted,
                                 so single-choice-set sheets don't need this at all.
      dynamic_choices:        - OPTIONAL, default false. True for a sheet
                                 family confirmed NOT to reliably alternate
                                 `choices`' two groups by odd/even question
                                 parity -- e.g. a real sheet where questions 5
                                 and 6 both print A/B/C/D instead of the second
                                 flipping to F/G/H/J (see
                                 choice_group_detect.py's own module
                                 docstring for the full evidence: two nominally
                                 identical printed forms of this sheet broke
                                 alternation at *different* rows, so no static
                                 per-question list can be right for both,
                                 unlike a fixed, hand-verified irregularity).
                                 When true, evaluate_sheet reads each row's
                                 actual choices off the scanned image itself
                                 (comparing each question's own printed glyph
                                 against a nearby already-resolved one -- never
                                 against a bundled reference image; the
                                 template still declares `choices`/the
                                 section's own override above, used as: (1)
                                 the two candidate groups to distinguish
                                 between, (2) question 1's own starting group
                                 (always the "odd" list, the same convention
                                 every other template already uses), and (3)
                                 the fallback -- flagged low_confidence -- for
                                 a row too damaged/marked to read either way.
      optional:               - OPTIONAL, default false. True for a section a
                                 student may choose not to take at all (e.g.
                                 Science on the enhanced ACT). One left
                                 entirely blank then counts as not taken
                                 rather than as a page of blanks to review
                                 (see pipeline.SheetResult.untaken_sections).
      columns:                - one or more question column-groups within this section
        - first_question, last_question   - question numbers, local to this section
          x_start              - x pixel coordinate of the first (leftmost) bubble
          y_start               - y pixel coordinate of the first question's row
          row_height            - vertical spacing between consecutive questions
  bubble_spacing_x         - horizontal spacing between adjacent choice bubbles
  bubble_radius            - approximate bubble radius in pixels
  choices:
    even: [A, B, C, D]      - answer letters for even-numbered questions
    odd:  [F, G, H, J]      - answer letters for odd-numbered questions
  thresholds:
    fill_ratio_min          - minimum darkness fraction to count as "marked",
                               measured after subtracting each question's own
                               baseline ink level (the printed ring + choice
                               letter, present even on a truly blank bubble --
                               see detect._baseline_adjust), not raw darkness
    relative_margin         - how close to the darkest bubble another bubble
                               must be to also count as "marked" (catches
                               multiple-answer and light/partial marks)
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

# A question is uniquely identified by (section_name, question_number), since
# question numbering restarts at 1 in each section on multi-test sheets.
QuestionKey = Tuple[str, int]


@dataclasses.dataclass(frozen=True)
class Bubble:
    section: str
    question: int
    choice: str
    x: int
    y: int


@dataclasses.dataclass(frozen=True)
class ColumnSpec:
    first_question: int
    last_question: int
    x_start: float
    y_start: float
    row_height: float


@dataclasses.dataclass(frozen=True)
class Section:
    name: str
    columns: List[ColumnSpec]
    # Per-section override of the template's even/odd choice letters, or
    # None to fall back to the template-level default -- see the module
    # docstring's `choices` entry under `sections`.
    even_choices: Optional[List[str]] = None
    odd_choices: Optional[List[str]] = None
    # See the module docstring's `dynamic_choices` entry under `sections`.
    dynamic_choices: bool = False
    # See the module docstring's `optional` entry under `sections`.
    optional: bool = False

    @property
    def num_questions(self) -> int:
        return max(c.last_question for c in self.columns)


@dataclasses.dataclass(frozen=True)
class Thresholds:
    fill_ratio_min: float = 0.35
    relative_margin: float = 0.15


@dataclasses.dataclass(frozen=True)
class Template:
    page_width: int
    page_height: int
    sections: List[Section]
    bubble_spacing_x: float
    bubble_radius: int
    even_choices: List[str]
    odd_choices: List[str]
    thresholds: Thresholds
    # Per-question choices, resolved at runtime for a `dynamic_choices`
    # section from the actual scanned image (see choice_group_detect.py) --
    # never set by from_yaml/from_dict itself, only via with_resolved_choices.
    # Checked by choices_for before falling back to even/odd, so every other
    # method (bubbles, and every caller of choices_for in detect.py/
    # grid_detect.py) picks up a resolved choice list transparently, with no
    # changes of its own needed.
    question_choices_override: Optional[Dict[QuestionKey, List[str]]] = None

    def _section(self, section_name: str) -> Section:
        for section in self.sections:
            if section.name == section_name:
                return section
        raise KeyError(f"No such section: {section_name!r}")

    def choices_for(self, section_name: str, question: int) -> List[str]:
        if self.question_choices_override is not None:
            resolved = self.question_choices_override.get((section_name, question))
            if resolved is not None:
                return resolved
        section = self._section(section_name)
        even = section.even_choices if section.even_choices is not None else self.even_choices
        odd = section.odd_choices if section.odd_choices is not None else self.odd_choices
        return even if question % 2 == 0 else odd

    def with_resolved_choices(self, overrides: Dict[QuestionKey, List[str]]) -> "Template":
        """A copy of this template with `overrides` added to (and taking
        precedence over) any `question_choices_override` it already has --
        see choice_group_detect.resolve_dynamic_choices, the only real
        caller. Merges rather than replaces so resolving one
        `dynamic_choices` section doesn't discard another's already-resolved
        entries."""
        merged = {**(self.question_choices_override or {}), **overrides}
        return dataclasses.replace(self, question_choices_override=merged)

    def bubbles(self) -> Dict[QuestionKey, List[Bubble]]:
        """Return {(section_name, question_number): [Bubble, ...]}."""
        result: Dict[QuestionKey, List[Bubble]] = {}
        for section in self.sections:
            for col in section.columns:
                for question in range(col.first_question, col.last_question + 1):
                    row_index = question - col.first_question
                    y = col.y_start + row_index * col.row_height
                    choices = self.choices_for(section.name, question)
                    bubbles = [
                        Bubble(
                            section=section.name,
                            question=question,
                            choice=choice,
                            x=round(col.x_start + i * self.bubble_spacing_x),
                            y=round(y),
                        )
                        for i, choice in enumerate(choices)
                    ]
                    result[(section.name, question)] = bubbles
        return result

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Template":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        page = data["page"]
        sections = [cls._parse_section(s) for s in data["sections"]]
        choices = data.get("choices", {})
        thresholds_data = data.get("thresholds", {})
        thresholds = Thresholds(
            fill_ratio_min=thresholds_data.get("fill_ratio_min", 0.35),
            relative_margin=thresholds_data.get("relative_margin", 0.15),
        )
        return cls(
            page_width=page["width"],
            page_height=page["height"],
            sections=sections,
            bubble_spacing_x=data["bubble_spacing_x"],
            bubble_radius=data["bubble_radius"],
            even_choices=list(choices.get("even", ["A", "B", "C", "D"])),
            odd_choices=list(choices.get("odd", ["F", "G", "H", "J"])),
            thresholds=thresholds,
        )

    @staticmethod
    def _parse_section(data: dict) -> Section:
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
        choices = data.get("choices")
        even_choices = list(choices["even"]) if choices and "even" in choices else None
        odd_choices = list(choices["odd"]) if choices and "odd" in choices else None
        return Section(
            name=data["name"],
            columns=columns,
            even_choices=even_choices,
            odd_choices=odd_choices,
            dynamic_choices=bool(data.get("dynamic_choices", False)),
            optional=bool(data.get("optional", False)),
        )

    def validate(self) -> None:
        """Sanity-check the template and raise ValueError on obvious problems."""
        if not self.sections:
            raise ValueError("Template must define at least one section")

        names = [s.name for s in self.sections]
        duplicate_names = {n for n in names if names.count(n) > 1}
        if duplicate_names:
            raise ValueError(f"Duplicate section names: {sorted(duplicate_names)}")

        for section in self.sections:
            if not section.columns:
                raise ValueError(f"Section {section.name!r} must define at least one column")
            seen = set()
            for col in section.columns:
                if col.first_question > col.last_question:
                    raise ValueError(
                        f"[{section.name}] Column first_question ({col.first_question}) > "
                        f"last_question ({col.last_question})"
                    )
                for q in range(col.first_question, col.last_question + 1):
                    if q in seen:
                        raise ValueError(
                            f"[{section.name}] Question {q} is defined in more than one column"
                        )
                    seen.add(q)
            expected = set(range(1, max(seen) + 1))
            missing = expected - seen
            if missing:
                raise ValueError(f"[{section.name}] Template is missing questions: {sorted(missing)}")

        for bubbles in self.bubbles().values():
            for b in bubbles:
                if not (0 <= b.x <= self.page_width) or not (0 <= b.y <= self.page_height):
                    raise ValueError(
                        f"[{b.section}] Bubble for question {b.question} choice {b.choice} at "
                        f"({b.x}, {b.y}) falls outside the page "
                        f"({self.page_width}x{self.page_height})"
                    )
