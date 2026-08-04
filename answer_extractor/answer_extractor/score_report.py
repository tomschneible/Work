"""Extract answers from text-based "Score Details" report PDFs (e.g. the
College Board SAT/PSAT Suite score report), as opposed to scanned bubble
sheets. These are exported/printed web pages with a real text layer and a
"Questions Overview" table (Question | Section | Correct Answer | Your
Answer | Actions) -- no image processing is needed, just text parsing.

Each test typically has multiple modules that restart question numbering
at 1 within the same named section (e.g. two "Reading and Writing"
modules), so a `module` counter (incrementing whenever question numbering
resets) is tracked to keep rows unambiguous.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable, List

import fitz  # PyMuPDF


@dataclasses.dataclass(frozen=True)
class ScoreReportRow:
    source: str
    module: int
    question: int
    section: str
    your_answer: str
    correct_answer: str = ""
    # Filled in by answer_extractor.answer_keys.annotate_rows, not by parsing
    # itself -- a plain "Module N" label (always available) upgraded to
    # "Module 2 (Easier)"/"Module 2 (Harder)" when a reference answer key
    # confidently identifies which second-module variant this is.
    test: str = ""
    module_label: str = ""


def _extract_lines(path: str | Path) -> List[str]:
    doc = fitz.open(str(path))
    try:
        lines: List[str] = []
        for page in doc:
            lines.extend(page.get_text().splitlines())
        return lines
    finally:
        doc.close()


def _parse_your_answer(raw: str) -> str:
    """The "Your Answer" cell is either a skipped question ("Omitted", no
    letter/value) or "<value>; Correct"/"<value>; Incorrect" -- strip the
    trailing status, leaving "" for an omitted question."""
    if raw == "Omitted":
        return ""
    if ";" in raw:
        return raw.rsplit(";", 1)[0].strip()
    return raw


def parse_score_report(path: str | Path) -> List[ScoreReportRow]:
    """Parse one score-report PDF into a flat list of answer rows, in the
    order questions appear in the "Questions Overview" table.

    Each row's rightmost column is a "Review" link, which -- unlike the
    surrounding fields -- never changes text and never spans more than one
    line, making it a reliable anchor. From each "Review" occurrence, walk
    backward: the "Your Answer" and "Correct Answer" cells are always the
    two lines immediately before it, and the section name is whatever
    non-numeric line(s) precede those, terminated by the question number.
    Scanning backward from a fixed anchor (rather than forward with fixed
    offsets) naturally handles section names that wrap to one or two lines
    and doesn't care what, if anything, follows "Review" (e.g. a "Domain"
    column present in some report layouts but not others).
    """
    path = Path(path)
    lines = [line.strip() for line in _extract_lines(path)]

    rows: List[ScoreReportRow] = []
    module = 1
    previous_question = None
    for j, line in enumerate(lines):
        if line != "Review" or j < 3:
            continue

        k = j - 3
        section_parts: List[str] = []
        while k >= 0 and not lines[k].isdigit():
            section_parts.append(lines[k])
            k -= 1
        if k < 0:
            continue  # No question number found above -- not a real row.

        question = int(lines[k])
        section = " ".join(reversed(section_parts))
        correct_answer = lines[j - 2]
        your_answer = _parse_your_answer(lines[j - 1])

        if previous_question is not None and question <= previous_question:
            module += 1
        previous_question = question

        rows.append(
            ScoreReportRow(
                source=path.stem,
                module=module,
                question=question,
                section=section,
                your_answer=your_answer,
                correct_answer=correct_answer,
            )
        )
    return rows


def group_by_module(rows: List[ScoreReportRow]) -> "dict[int, List[ScoreReportRow]]":
    """Group rows by their `module` counter, preserving counter order."""
    blocks: "dict[int, List[ScoreReportRow]]" = {}
    for row in rows:
        blocks.setdefault(row.module, []).append(row)
    return blocks


def section_module_index(rows: List[ScoreReportRow]) -> "dict[int, int]":
    """For each `module` counter value, return which occurrence (1st, 2nd,
    ...) of *that row's section* it is. This is robust to how sections are
    ordered in the PDF (e.g. all of one section's modules before the
    next), unlike the raw `module` counter which just counts resets
    globally regardless of section."""
    blocks = group_by_module(rows)
    counts: "dict[str, int]" = {}
    result: "dict[int, int]" = {}
    for module_num in sorted(blocks):
        section = blocks[module_num][0].section
        counts[section] = counts.get(section, 0) + 1
        result[module_num] = counts[section]
    return result


def base_module_labels(rows: List[ScoreReportRow]) -> "dict[int, str]":
    """Plain "Module N" labels (N = occurrence within that row's section)
    with no answer-key knowledge required -- always available, and what
    answer_extractor.answer_keys.annotate_rows upgrades to e.g.
    "Module 2 (Harder)" when a reference key confidently matches."""
    return {module_num: f"Module {idx}" for module_num, idx in section_module_index(rows).items()}


def _iter_pdfs(path: str | Path) -> List[Path]:
    path = Path(path)
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() == ".pdf")
    return [path]


def parse_score_reports(paths: Iterable[str | Path]) -> List[ScoreReportRow]:
    """Parse multiple score-report PDFs (or directories of them) into one
    combined list."""
    rows: List[ScoreReportRow] = []
    for path in paths:
        for pdf_path in _iter_pdfs(path):
            rows.extend(parse_score_report(pdf_path))
    return rows
