"""Shared parsing for the "ScoreSheet" tab layout used both by vendor
reference spreadsheets (see scoresheet_check.py) and by this org's own
Google Sheets score-report templates (see score_report_writer.py): a grid
of repeated (Question, Correct Answer, Your Answer, mark, Category)
column-groups, several per section, with the section name as a small
standalone title cell a couple of rows above the leftmost block's header
row -- one title covers every block to its right up to the next title.
Block sizes differ by section and even by template (an org can and does
have several differently-sized templates per test format), so this locates
blocks generically by scanning for "Your Answer" header cells and walking
down beneath them, rather than hardcoding row/column numbers anywhere.

Kept as its own module (rather than living inside scoresheet_check.py)
because two different callers need it for two different things: reading
existing "Your Answer" values back out (scoresheet_check's comparison
tool) and writing new ones into a fresh template copy (score_report_writer)
-- both need the exact same idea of where a block starts and which
section it belongs to, and duplicating that scan would risk the two
silently disagreeing about it.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Tuple

from openpyxl.worksheet.worksheet import Worksheet

QuestionKey = Tuple[str, int]

_SECTION_ALIASES = {"math": "mathematics"}
_KNOWN_SECTION_TITLES = {"english", "math", "mathematics", "reading", "science"}


def normalize_section(name: str) -> str:
    key = str(name).strip().lower()
    return _SECTION_ALIASES.get(key, key)


@dataclasses.dataclass(frozen=True)
class AnswerBlock:
    """One "Correct Answer"/"Your Answer" column-group: `question_col` and
    `answer_col` are where per-row question numbers and answers live,
    starting at `header_row + 1` and continuing until a blank question
    cell (see iter_block_questions)."""

    section: str
    header_row: int
    question_col: int
    correct_col: int
    answer_col: int
    mark_col: int


def _section_for_header(
    titles: List[Tuple[int, int, str]], header_row: int, header_col: int
) -> Optional[str]:
    """The section title that governs this "Your Answer" column: the
    nearest title row above the header, and within that row, the
    rightmost title at or left of the header's column (one title covers
    every block-group to its right, up to the next title -- see module
    docstring)."""
    above = [(row, col, name) for row, col, name in titles if row < header_row]
    if not above:
        return None
    nearest_row = max(row for row, _, _ in above)
    same_row = [(col, name) for row, col, name in above if row == nearest_row]
    left_or_equal = [(col, name) for col, name in same_row if col <= header_col]
    pool = left_or_equal or same_row
    return max(pool, key=lambda item: item[0])[1]


def locate_answer_blocks(ws: Worksheet) -> List[AnswerBlock]:
    """Scan `ws` for every "Your Answer" column-group and the section
    title that governs it. Raises ValueError if a header's section can't
    be determined (no title cell anywhere above it)."""
    titles: List[Tuple[int, int, str]] = []
    headers: List[Tuple[int, int]] = []
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if not isinstance(value, str):
                continue
            text = value.strip()
            if text.lower() in _KNOWN_SECTION_TITLES:
                titles.append((cell.row, cell.column, normalize_section(text)))
            elif text == "Your Answer":
                headers.append((cell.row, cell.column))

    blocks: List[AnswerBlock] = []
    for header_row, header_col in headers:
        section = _section_for_header(titles, header_row, header_col)
        if section is None:
            raise ValueError(
                f"Could not find a section title above the 'Your Answer' column at "
                f"R{header_row}C{header_col}"
            )
        blocks.append(
            AnswerBlock(
                section=section,
                header_row=header_row,
                question_col=header_col - 2,
                correct_col=header_col - 1,
                answer_col=header_col,
                mark_col=header_col + 1,
            )
        )
    return blocks


def iter_block_questions(question_values_ws: Worksheet, block: AnswerBlock):
    """Yield (row, question_number) for each row in `block`, stopping at
    the first blank question cell. `question_values_ws` should come from a
    data_only=True load of the same workbook (some question-number cells
    are formulas like '=A64+1', continuing the previous block-group's
    numbering -- data_only=True resolves those to the cached computed
    number instead of the formula string; a data_only=False load would
    hand back the literal formula text here)."""
    r = block.header_row + 1
    while True:
        question = question_values_ws.cell(row=r, column=block.question_col).value
        if question is None:
            return
        yield r, int(question)
        r += 1
