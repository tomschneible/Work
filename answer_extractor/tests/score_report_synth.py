"""Build a minimal synthetic "Score Details"-style PDF for testing
answer_extractor.score_report, without needing to commit a real (likely
copyrighted) score report.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Sequence, Tuple, Union

import fitz

HEADER_LINES = ["Question", "", "Section", "Correct Answer", "Your Answer", "", "Actions"]


def _as_lines(value: Union[str, Sequence[str]]) -> List[str]:
    return [value] if isinstance(value, str) else list(value)


def write_score_report_pdf(
    path: str | Path,
    rows: Iterable[
        Tuple[int, Union[str, Sequence[str]], Union[str, Sequence[str]], Union[str, Sequence[str]], str]
    ],
    page_height: float = 792.0,
    margin_bottom: float = 40.0,
    domain: str | None = None,
) -> None:
    """`rows` is an iterable of (question, section, correct_answer,
    your_answer_value, correctness). `section`, `correct_answer`, and
    `your_answer_value` may each be a single string or a sequence of
    strings for a cell that wraps across physical PDF lines -- e.g.
    section=("Reading and", "Writing"), or correct_answer=(".3928,
    .3929,", "11/28") for a grid-in question's own multi-value answer key.
    `correctness` is "Correct" or "Incorrect", or "Omitted" for a skipped
    question -- in which case `your_answer_value` is ignored and just the
    literal "Omitted" is written, matching a real report's layout for
    skipped questions. A *wrapped* `your_answer_value` puts the
    correctness status on its own trailing line instead of glued to the
    value's own last line with a semicolon -- confirmed real-world shape
    for a grid-in answer that wrapped: "11/28;" then "Correct" as two
    separate lines, not one "11/28; Correct" line. `domain` optionally
    adds a trailing "Domain" column line after each row's "Review" link,
    to test that its presence (real reports sometimes include it,
    sometimes don't) doesn't confuse parsing. Pagination is based on
    actual available vertical space (not a fixed row count), so rows
    never silently overflow the page -- MuPDF's text extraction only
    returns text within the page's mediabox."""
    rows = list(rows)
    doc = fitz.open()
    page = None
    y = 0.0
    line_height = 14
    max_y = page_height - margin_bottom

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=612, height=page_height)
        y = 40.0

    def add_line(text: str):
        nonlocal y
        page.insert_text((40, y), text, fontsize=10)
        y += line_height

    def ensure_room(num_lines: int):
        if page is None or y + num_lines * line_height > max_y:
            new_page()
            for line in HEADER_LINES:
                add_line(line)

    ensure_room(0)  # first page's header
    for question, section, correct_answer, your_answer_value, correctness in rows:
        section_lines = _as_lines(section)
        correct_lines = _as_lines(correct_answer)
        if correctness == "Omitted":
            your_answer_lines = ["Omitted"]
        elif isinstance(your_answer_value, str):
            your_answer_lines = [f"{your_answer_value}; {correctness}"]
        else:
            value_lines = list(your_answer_value)
            your_answer_lines = value_lines[:-1] + [f"{value_lines[-1]};", correctness]

        num_lines = (
            len(section_lines) + len(correct_lines) + len(your_answer_lines) + 1 + (1 if domain else 0)
        )
        ensure_room(num_lines)
        add_line(str(question))
        for line in section_lines:
            add_line(line)
        for line in correct_lines:
            add_line(line)
        for line in your_answer_lines:
            add_line(line)
        add_line("Review")
        if domain:
            add_line(domain)

    doc.save(str(path))
    doc.close()
