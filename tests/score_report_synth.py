"""Build a minimal synthetic "Score Details"-style PDF for testing
bubble_scanner.score_report, without needing to commit a real (likely
copyrighted) score report.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

import fitz

HEADER_LINES = ["Question", "", "Section", "Correct Answer", "Your Answer", "", "Actions"]


def write_score_report_pdf(
    path: str | Path,
    rows: Iterable[Tuple[int, str, str, str, str]],
    page_height: float = 792.0,
    margin_bottom: float = 40.0,
) -> None:
    """`rows` is an iterable of (question, section, correct_answer,
    your_answer_value, correctness) where correctness is "Correct" or
    "Incorrect". Pagination is based on actual available vertical space
    (not a fixed row count), so rows never silently overflow the page --
    MuPDF's text extraction only returns text within the page's mediabox."""
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
        ensure_room(5)
        add_line(str(question))
        add_line(section)
        add_line(correct_answer)
        add_line(f"{your_answer_value}; {correctness}")
        add_line("Review")

    doc.save(str(path))
    doc.close()
