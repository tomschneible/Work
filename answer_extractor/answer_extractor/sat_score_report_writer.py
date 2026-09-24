"""Read a current-format SAT/DSAT score-report template's "Student
Responses" tab -- the per-test template ("DSAT 8", ...) every new test
code gets -- plus the few conventions the simplified template shares with
it. Reports themselves are filled on the simplified template (see
sat_simplified_score_report_writer.py); this is where that writer gets
each question's correct answer, Domain and Skill (read_reference_questions),
and how it finds the name and score cells both templates lay out the same
way.

The current-format layout, confirmed against a real blank template:

  - Every block has its own title directly above it (e.g. "R & W Module
    2 - Higher Difficulty"), and a title's own column always equals its
    block's question-number column, so a title identifies exactly one
    block by column alone.
  - Module 1 and Module 2 both number their questions from 1, so a block
    is identified by (subject, module_slot) -- module_slot being
    "module1", "easier", or "harder" -- and answers by
    (subject, module_slot, question) (SatKey).
  - Each subject has two same-difficulty Module 2 blocks (two "Higher
    Difficulty", two "Lower Difficulty"); within a pair the correct-answer
    key is byte-identical, so the leftmost one is always the one read.

Which difficulty was actually administered isn't worked out here -- that
comes from answer_keys.annotate_rows, matching a score report's own
"Correct Answer" column against a reference key.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Dict, List, Tuple

from openpyxl.worksheet.worksheet import Worksheet

SatKey = Tuple[str, str, int]  # (subject, module_slot, question)

_NAME_PLACEHOLDER_PREFIXES = ("enter name", "type name here")

_TITLE_PATTERN = re.compile(
    r"^(?P<subject>.+?)\s+Module\s+(?P<module_num>1|2)"
    r"(?:\s*-\s*(?P<difficulty>Higher|Lower)\s+Difficulty)?\s*$",
    re.IGNORECASE,
)
_SUBJECT_ALIASES = {
    "r & w": "reading and writing",
    "r and w": "reading and writing",
    "reading & writing": "reading and writing",
    "reading and writing": "reading and writing",
    # Confirmed on a real DSAT report: right at a question boundary where
    # both the previous and current question shared the same Domain label
    # ("Standard English Conventions"), PyMuPDF's own text extraction
    # (score_report.py's _extract_lines) emitted the two words of "Reading
    # and Writing" out of order -- "Reading and" ended up displaced before
    # the question number, leaving only "Writing" where the section name
    # is normally found. Not a section score_report.py can ever actually
    # mean on its own (Digital SAT has no standalone "Writing" module --
    # it's always the combined "Reading and Writing" one), so it's safe
    # to always treat it as that rather than a genuine third subject.
    "writing": "reading and writing",
    "math": "math",
}
_DIFFICULTY_TO_SLOT = {"higher": "harder", "lower": "easier"}
_HEADER_SEARCH_ROWS = 5  # how far below a title to look for its "Your Answer" header

_SCORE_LABEL_PATTERN = re.compile(r"^(?P<subject>.+?)\s*score\s*$", re.IGNORECASE)
_SCORE_VALUE_SEARCH_ROWS = 5  # how far above a "<Subject> Score" label to look for its value cell


def normalize_subject(raw: str) -> str:
    key = re.sub(r"\s+", " ", raw.strip().lower())
    if key not in _SUBJECT_ALIASES:
        raise ValueError(f"Unrecognized SAT subject title {raw!r}")
    return _SUBJECT_ALIASES[key]


@dataclasses.dataclass(frozen=True)
class SatBlock:
    subject: str
    module_slot: str  # "module1" | "easier" | "harder"
    title_row: int
    header_row: int
    question_col: int
    correct_col: int
    answer_col: int
    mark_col: int


def _scan_raw_titles(ws: Worksheet) -> List[Tuple[int, int, str, str]]:
    """Every block title found anywhere on `ws`, as (row, col, subject,
    module_slot) -- *not* deduplicated (see locate_sat_blocks, which keeps
    only the leftmost per (subject, module_slot)). Raises ValueError if a
    Module 2 title is missing its Higher/Lower difficulty."""
    raw_titles: List[Tuple[int, int, str, str]] = []
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if not isinstance(value, str):
                continue
            match = _TITLE_PATTERN.match(value.strip())
            if not match:
                continue
            subject = normalize_subject(match.group("subject"))
            if match.group("module_num") == "1":
                module_slot = "module1"
            else:
                difficulty = match.group("difficulty")
                if difficulty is None:
                    raise ValueError(f"Module 2 title missing a Higher/Lower difficulty: {value!r}")
                module_slot = _DIFFICULTY_TO_SLOT[difficulty.lower()]
            raw_titles.append((cell.row, cell.column, subject, module_slot))
    return raw_titles


def find_header_row(ws: Worksheet, title_row: int, title_col: int) -> int:
    """The row within _HEADER_SEARCH_ROWS of `title_row` whose
    `title_col + 2` cell reads "Your Answer" -- used by locate_sat_blocks
    to find one block's own header row. Raises ValueError if it can't be
    found.

    Public (not `_`-prefixed): this convention is layout-independent, so
    sat_simplified_score_report_writer.py's own block locator reuses it
    as-is rather than re-deriving it."""
    for candidate_row in range(title_row, title_row + _HEADER_SEARCH_ROWS + 1):
        if ws.cell(row=candidate_row, column=title_col + 2).value == "Your Answer":
            return candidate_row
    raise ValueError(f"Could not find a 'Your Answer' header below the title at row {title_row}, column {title_col}")


def locate_sat_blocks(ws: Worksheet) -> List[SatBlock]:
    """Scan `ws` for every SAT block, deduplicated to exactly one per
    (subject, module_slot) -- see module docstring on why a subject's two
    same-difficulty blocks are interchangeable, and why only the leftmost
    is kept. Raises ValueError if a title's own "Your Answer" header can't
    be found nearby, or a Module 2 title is missing its difficulty."""
    raw_titles = _scan_raw_titles(ws)

    blocks_by_key: Dict[Tuple[str, str], SatBlock] = {}
    for title_row, title_col, subject, module_slot in raw_titles:
        key = (subject, module_slot)
        if key in blocks_by_key and title_col >= blocks_by_key[key].question_col:
            continue  # a duplicate block further right -- keep the leftmost one already found

        header_row = find_header_row(ws, title_row, title_col)

        blocks_by_key[key] = SatBlock(
            subject=subject,
            module_slot=module_slot,
            title_row=title_row,
            header_row=header_row,
            question_col=title_col,
            correct_col=title_col + 1,
            answer_col=title_col + 2,
            mark_col=title_col + 3,
        )
    return list(blocks_by_key.values())


@dataclasses.dataclass(frozen=True)
class ReferenceQuestion:
    """One question's per-test, per-question facts, as read straight off
    a block on the *current-format* (checkbox/duplicate-block) template's
    own "Student Responses" tab -- everything the simplified template's
    own writer needs that isn't a student's actual answer: the correct-
    answer key, and the Domain/Skill labels College Board's own report
    assigns that question. See read_reference_questions for why these
    come from here rather than a separately-maintained source."""

    correct_answer: object
    domain: object
    skill: object


def read_reference_questions(ws: Worksheet, subject: str, module_slot: str) -> Dict[int, ReferenceQuestion]:
    """Every question's ReferenceQuestion for one (subject, module_slot)
    block on `ws`, keyed by question number -- `ws` is the *current-format*
    template's own "Student Responses" tab (a local, read-only copy, the
    same kind every other reader in this module scans), not a sheet being
    filled.

    Exists for the simplified (no checkboxes, one Module 2 slot per
    subject) template reports are filled on: it needs each question's
    correct answer and Domain/Skill labels, with nowhere on *itself* to
    read them from -- so it reads them from the current-format template
    for the same test code, which carries them on each of its difficulty
    blocks, instead of duplicating them into a new CSV that would need to
    be kept in sync with it by hand. This is a deliberate choice,
    not a stopgap: a current-format template is made for every new test
    code regardless (see README), so it's already the one real,
    hand-verified source for these facts -- referencing it directly means
    there's only ever one place a new test's Domain/Skill labels actually
    live.

    Raises ValueError if `ws` has no block matching (subject, module_slot)
    at all -- almost always the wrong template file, subject spelling, or
    module_slot ("module1"/"easier"/"harder") reached this call."""
    block = next(
        (b for b in locate_sat_blocks(ws) if b.subject == subject and b.module_slot == module_slot),
        None,
    )
    if block is None:
        raise ValueError(f"No {subject!r} {module_slot!r} block found on {ws.title!r}")

    questions: Dict[int, ReferenceQuestion] = {}
    r = block.header_row + 1
    while True:
        question = ws.cell(row=r, column=block.question_col).value
        if question is None:
            break
        questions[int(question)] = ReferenceQuestion(
            correct_answer=ws.cell(row=r, column=block.correct_col).value,
            domain=ws.cell(row=r, column=block.question_col + 4).value,
            skill=ws.cell(row=r, column=block.question_col + 5).value,
        )
        r += 1
    return questions


def find_score_value_cells(ws: Worksheet) -> Dict[str, Tuple[int, int]]:
    """{subject: (row, col)} for every "<Subject> Score" label found (e.g.
    "Reading\n& Writing\nScore", "Math\nScore") -- these templates put the
    label a few rows *below* its own value cell (confirmed against a real
    template: "Total Score" is likewise labeled below the cell that sums
    it), so this searches upward from each label for the nearest cell in
    the same column that already holds a number -- the static placeholder
    value (e.g. 200) every blank template ships with in that slot.

    Public (not `_`-prefixed) because this convention isn't specific to
    the current-format template's own checkbox/duplicate-block layout --
    sat_simplified_score_report_writer.py's own fill function reuses this
    as-is rather than re-deriving it."""
    result: Dict[str, Tuple[int, int]] = {}
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if not isinstance(value, str):
                continue
            normalized = re.sub(r"\s+", " ", value.strip())
            match = _SCORE_LABEL_PATTERN.match(normalized)
            if not match:
                continue
            try:
                subject = normalize_subject(match.group("subject"))
            except ValueError:
                continue  # e.g. "Total Score" -- not a subject this module knows
            for candidate_row in range(cell.row - 1, cell.row - _SCORE_VALUE_SEARCH_ROWS - 1, -1):
                candidate_value = ws.cell(row=candidate_row, column=cell.column).value
                # bool is technically an int subclass -- excluded explicitly
                # so a stray flag cell in the search window is never mistaken
                # for the score value.
                if isinstance(candidate_value, (int, float)) and not isinstance(candidate_value, bool):
                    result[subject] = (candidate_row, cell.column)
                    break
    return result


def find_name_cell(ws: Worksheet) -> Tuple[int, int]:
    """(row, col) of the name placeholder cell (e.g. "Type name here, date
    below") -- the test date always sits directly below it. Public (not
    `_`-prefixed) for the same reason find_score_value_cells is: this
    convention isn't specific to the current-format template, and
    sat_simplified_score_report_writer.py's own fill function reuses it
    as-is."""
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if isinstance(value, str) and value.strip().lower().startswith(_NAME_PLACEHOLDER_PREFIXES):
                return cell.row, cell.column
    raise ValueError(
        f"Could not find a name placeholder cell (looked for a value starting with "
        f"one of {_NAME_PLACEHOLDER_PREFIXES!r})"
    )


