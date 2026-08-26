"""Fill a per-test SAT/DSAT score-report template's "Student Responses"
tab with one student's name, test date, and answers.

Structurally different from the ACT templates (see score_report_writer.py
and scoresheet_grid.py) in three ways, all confirmed against a real blank
template and its filled counterpart:

  - Every block has its own title directly above it, rather than one
    shared title governing several -- e.g. "R & W Module 2 - Higher
    Difficulty" titles just that one block, not a run of blocks to its
    right the way ACT's "English" does. Handled here by exploiting a
    layout fact that made this simpler than porting scoresheet_grid's
    title-attribution logic: a title's own column always equals its
    block's question-number column, so a title unambiguously identifies
    exactly one block by column alone.
  - Module 1 and Module 2 both number their questions starting at 1, so a
    block can't be identified by (subject, question) the way ACT's
    (section, question) works -- answers are keyed by
    (subject, module_slot, question) instead, module_slot being
    "module1", "easier", or "harder".
  - Each subject has two *pairs* of Module-2 blocks -- two "Higher
    Difficulty" blocks, two "Lower Difficulty" ones -- not one of each.
    Confirmed against the blank template: within a pair, the correct-
    answer key is byte-identical, so which twin is used is arbitrary;
    this always uses the leftmost and never touches its duplicate.

    A boolean flag cell above the block's own question-number column
    tells the sheet's own score formulas whether that column-group
    counts -- but there's only ONE row of these flags on the whole
    sheet (confirmed against the blank template: every subject's score
    formulas reference the exact same handful of cells, e.g. Math's own
    "correct count" formula reads $O$8/$V$8/$AC$8/$AJ$8, not a
    Math-specific row), reused by column position across every subject
    stacked underneath it. Harmless by construction, not something this
    needs to special-case: an inactive block is always left with blank
    "Your Answer" cells, and a blank cell's mark formula produces neither
    "✔" nor "✘", so it never affects a count regardless of what its
    shared flag happens to be set to. Module 1 has no flag cell at all
    (everyone takes it, nothing to disambiguate). Located here via one
    sheet-wide scan for boolean-valued cells, keyed by column -- not by
    searching near any one block's own header row, which is what a
    later-appearing subject's blocks actually need.

Which difficulty (easier/harder) was actually administered isn't
something this module figures out -- that identification already exists
in answer_keys.annotate_rows, matching a score report's own "Correct
Answer" column against a reference key. Callers pass the result in via
`active_variants`.

Like score_report_writer.py's ACT counterpart, this scans a *local,
read-only* copy of the template purely to find where things go and
returns the list of individual CellWrite values a caller pushes into the
live Sheet via google_sheets_export.write_cells -- it no longer edits or
returns a Workbook to be saved and re-uploaded wholesale (see
google_sheets_export.py's own module docstring for why).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from .google_sheets_export import CellWrite, FillResult, format_date_for_sheets

SatKey = Tuple[str, str, int]  # (subject, module_slot, question)

_NAME_PLACEHOLDER_PREFIXES = ("enter name", "type name here")

_TITLE_PATTERN = re.compile(
    r"^(?P<subject>.+?)\s+Module\s+(?P<module_num>1|2)"
    r"(?:\s*-\s*(?P<difficulty>Higher|Lower)\s+Difficulty)?\s*$",
    re.IGNORECASE,
)
# A block's full column span for clearing purposes: question, correct-answer,
# your-answer, mark -- SatBlock's own four columns -- plus Domain and Skill,
# two more columns immediately after mark_col that SatBlock doesn't track
# (nothing here ever reads or writes them) but that a cleared block still
# needs cleared too, or its Domain/Skill columns are left behind looking
# like an orphaned, unlabeled leftover even though every actual answer cell
# is gone. See blocks_to_clear, the only place this is used.
_CLEAR_BLOCK_WIDTH = 6
_SUBJECT_ALIASES = {
    "r & w": "reading and writing",
    "r and w": "reading and writing",
    "reading & writing": "reading and writing",
    "reading and writing": "reading and writing",
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
    header_row: int
    question_col: int
    correct_col: int
    answer_col: int
    mark_col: int
    flag_cell: Optional[Tuple[int, int]]  # (row, col); None for module1, which needs no flag


def _scan_raw_titles(ws: Worksheet) -> List[Tuple[int, int, str, str]]:
    """Every block title found anywhere on `ws`, as (row, col, subject,
    module_slot) -- *not* deduplicated (see locate_sat_blocks, which keeps
    only the leftmost per (subject, module_slot); _scan_block_occurrences
    needs every occurrence, including duplicates/twins, to know what to
    clear). Raises ValueError if a Module 2 title is missing its
    Higher/Lower difficulty."""
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


def _find_header_row(ws: Worksheet, title_row: int, title_col: int) -> int:
    """The row within _HEADER_SEARCH_ROWS of `title_row` whose
    `title_col + 2` cell reads "Your Answer" -- shared by locate_sat_blocks
    and _scan_block_occurrences, both of which need to find one block
    occurrence's own header row. Raises ValueError if it can't be found."""
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

    # One sheet-wide scan for the flag cells, keyed by column -- not
    # searched relative to any one block's own header row, since a
    # later-appearing subject's blocks reuse an earlier subject's flags
    # rather than having their own (see module docstring). Keeps the
    # topmost boolean found per column, matching what every subject's
    # score formulas actually reference.
    flag_cell_by_col: Dict[int, Tuple[int, int]] = {}
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, bool) and cell.column not in flag_cell_by_col:
                flag_cell_by_col[cell.column] = (cell.row, cell.column)

    blocks_by_key: Dict[Tuple[str, str], SatBlock] = {}
    for title_row, title_col, subject, module_slot in raw_titles:
        key = (subject, module_slot)
        if key in blocks_by_key and title_col >= blocks_by_key[key].question_col:
            continue  # a duplicate block further right -- keep the leftmost one already found

        header_row = _find_header_row(ws, title_row, title_col)
        flag_cell = flag_cell_by_col.get(title_col) if module_slot != "module1" else None

        blocks_by_key[key] = SatBlock(
            subject=subject,
            module_slot=module_slot,
            header_row=header_row,
            question_col=title_col,
            correct_col=title_col + 1,
            answer_col=title_col + 2,
            mark_col=title_col + 3,
            flag_cell=flag_cell,
        )
    return list(blocks_by_key.values())


@dataclasses.dataclass(frozen=True)
class _BlockOccurrence:
    """One raw title occurrence's own full extent -- unlike SatBlock (one
    already-deduplicated block per (subject, module_slot), used to know
    *where to write*), this tracks every occurrence individually,
    including duplicates/twins, each with its own row range -- used to
    know *what to clear* (see blocks_to_clear)."""

    subject: str
    module_slot: str
    title_row: int
    title_col: int
    header_row: int  # also where the shared flag cell lives, at (header_row, title_col) -- see blocks_to_clear
    last_row: int  # the block's own last question row (inclusive)


def _scan_block_occurrences(ws: Worksheet) -> List[_BlockOccurrence]:
    occurrences = []
    for title_row, title_col, subject, module_slot in _scan_raw_titles(ws):
        header_row = _find_header_row(ws, title_row, title_col)
        r = header_row + 1
        while ws.cell(row=r, column=title_col).value is not None:
            r += 1
        occurrences.append(_BlockOccurrence(subject, module_slot, title_row, title_col, header_row, last_row=r - 1))
    return occurrences


def blocks_to_clear(ws: Worksheet, active_variants: Mapping[str, str]) -> List[Tuple[int, int, int, int]]:
    """0-indexed (start_row, end_row, start_col, end_col) rectangles (end
    exclusive -- the shape a Sheets API GridRange needs) covering every
    Module 2 block occurrence that isn't the one subject-active
    administered difficulty actually written to -- every duplicate/twin,
    plus a subject's own *other* difficulty's canonical block. Module 1 is
    never included -- every student takes it, and it's never duplicated.

    Deliberately *per occurrence*, not per column the way an earlier
    version of this (inactive_block_column_ranges, since removed) worked:
    a subject's block columns are reused by column *position* across
    every other subject stacked underneath it (see this module's own
    docstring on flag cells), so hiding a whole column would also hide
    another subject's own, different-row occurrence of that same column
    if that subject's active variant happened to live there -- confirmed
    live against a real filled report where Reading & Writing's active
    variant (Higher) and Math's (Lower) differed, which a column-wide hide
    can't represent at all (whichever column you hide, some subject's
    real data lives in it). Clearing each occurrence's own row range
    instead of its whole column sidesteps that entirely: Math's own
    occurrence of "Higher Difficulty" (which it didn't use) gets cleared
    within Math's own rows without touching Reading & Writing's separate
    occurrence of the same title elsewhere on the sheet.

    Each occurrence's own shared flag cell -- at (header_row, title_col),
    see SatBlock.flag_cell -- is deliberately carved out of its rectangle
    rather than cleared along with everything else: that one cell is the
    single sheet-wide boolean an *other* subject's active pick may depend
    on (the same fact that makes column-wide hiding unsafe in the first
    place), and it happens to fall inside this occurrence's own row range
    whenever this occurrence's rows span the sheet's shared flag row (true
    for whichever subject's block appears first on the sheet). Clearing it
    unconditionally would silently un-check a flag write_cells just made
    for a different subject earlier in the same run. Carving it out costs
    nothing when no other subject needs it -- an unused flag cell being
    left at whatever the template's default was is harmless (see this
    module's own docstring).

    Each rectangle spans the full 6-column block width (title, correct
    answer, your answer, mark, Domain, Skill -- see _CLEAR_BLOCK_WIDTH),
    since google_sheets_export.clear_cells (the only caller of this)
    clears both value and border formatting for whatever rectangle it's
    given -- leaving column slack would leave a stray Domain/Skill column,
    or a half-cleared answer.
    """
    canonical_cols = {
        (block.subject, block.module_slot): block.question_col
        for block in locate_sat_blocks(ws)
        if block.module_slot == "module1" or active_variants.get(block.subject) == block.module_slot
    }
    rectangles = []
    for occurrence in _scan_block_occurrences(ws):
        if occurrence.module_slot == "module1":
            continue
        key = (occurrence.subject, occurrence.module_slot)
        if canonical_cols.get(key) == occurrence.title_col:
            continue  # the one occurrence that's actually filled in and shown

        top, bottom = occurrence.title_row - 1, occurrence.last_row
        left, right = occurrence.title_col - 1, occurrence.title_col - 1 + _CLEAR_BLOCK_WIDTH
        flag_row = occurrence.header_row - 1  # 0-indexed
        # correct-answer/your-answer/mark/Domain/Skill (everything but the
        # title/flag column) for the full row range -- never shares a cell
        # with another subject, so no need to carve anything out of it.
        rectangles.append((top, bottom, left + 1, right))
        # The title/flag column, split around the one cell (header_row,
        # title_col) that might be a flag another subject still needs.
        if top < flag_row:
            rectangles.append((top, flag_row, left, left + 1))
        if flag_row + 1 < bottom:
            rectangles.append((flag_row + 1, bottom, left, left + 1))
    return rectangles


def _find_score_value_cells(ws: Worksheet) -> Dict[str, Tuple[int, int]]:
    """{subject: (row, col)} for every "<Subject> Score" label found (e.g.
    "Reading\n& Writing\nScore", "Math\nScore") -- these templates put the
    label a few rows *below* its own value cell (confirmed against a real
    template: "Total Score" is likewise labeled below the cell that sums
    it), so this searches upward from each label for the nearest cell in
    the same column that already holds a number -- the static placeholder
    value (e.g. 200) every blank template ships with in that slot."""
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


def _find_name_cell(ws: Worksheet) -> Tuple[int, int]:
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if isinstance(value, str) and value.strip().lower().startswith(_NAME_PLACEHOLDER_PREFIXES):
                return cell.row, cell.column
    raise ValueError(
        f"Could not find a name placeholder cell (looked for a value starting with "
        f"one of {_NAME_PLACEHOLDER_PREFIXES!r})"
    )


def fill_sat_score_report(
    template_path: str | Path,
    answers: Mapping[SatKey, str],
    active_variants: Mapping[str, str],
    student_name: str,
    test_date: dt.date | str,
    section_scores: Optional[Mapping[str, int]] = None,
    sheet_name: str = "Student Responses",
) -> FillResult:
    """Return every cell write needed to fill `template_path`'s
    `sheet_name` tab in with the student's name, test date, every answer
    in `answers`, and (if given) each subject's scaled section score --
    plus, in the same FillResult, the rectangles to clear for every
    Module 2 block occurrence that wasn't administered (see
    blocks_to_clear), so the exported report only shows the modules that
    were actually filled in rather than every duplicate/twin block the
    template ships with.

    `active_variants` maps subject -> "easier"/"harder", the Module 2
    difficulty actually administered for that subject (from
    answer_keys.annotate_rows) -- this is what decides which of each
    subject's two same-difficulty block-pairs gets its flag cell set and
    its answers written (and, in FillResult, stays visible); the other
    pair is never touched, and every other occurrence gets cleared.
    `answers` must only contain keys for "module1" or each subject's
    active variant -- an entry for the *inactive* variant raises, since
    writing it would silently go nowhere the sheet's own formulas count
    (its flag stays False). A template question with no entry in
    `answers` is left blank (a legitimate omitted bubble).

    `section_scores` maps subject -> scaled score (e.g. {"math": 620}) --
    unlike every other field here, this isn't something this pipeline can
    derive from the scan/report itself (see this module's own history in
    the repo for why: no formula computes it in this template, and
    nothing upstream currently extracts it either), so it's expected to
    come from wherever the caller sourced it -- e.g. a value a person
    typed into a prompt. Omit a subject (or the whole mapping) to leave
    its score cell at the template's own default, unchanged.
    """
    for subject, slot, _question in answers:
        if slot != "module1" and active_variants.get(subject) != slot:
            raise ValueError(
                f"Got an answer for {subject!r} {slot!r}, but the active variant for {subject!r} "
                f"is {active_variants.get(subject)!r}"
            )

    wb = openpyxl.load_workbook(template_path, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"No {sheet_name!r} tab in {template_path} (tabs: {wb.sheetnames})")
    ws = wb[sheet_name]

    name_row, name_col = _find_name_cell(ws)
    writes = [
        CellWrite(sheet_name, name_row, name_col, student_name),
        # date sits directly below name
        CellWrite(sheet_name, name_row + 1, name_col, format_date_for_sheets(test_date)),
    ]

    if section_scores:
        score_cells = _find_score_value_cells(ws)
        for subject, score in section_scores.items():
            if subject not in score_cells:
                raise ValueError(
                    f"No score cell found for subject {subject!r} in {template_path}!{sheet_name} "
                    f"(found score cells for: {sorted(score_cells)})"
                )
            row, col = score_cells[subject]
            writes.append(CellWrite(sheet_name, row, col, score))

    remaining = dict(answers)
    for block in locate_sat_blocks(ws):
        is_active = block.module_slot == "module1" or active_variants.get(block.subject) == block.module_slot
        if is_active and block.flag_cell is not None:
            frow, fcol = block.flag_cell
            writes.append(CellWrite(sheet_name, frow, fcol, True))

        if not is_active:
            continue  # the inactive twin of an active pair -- leave completely untouched
        r = block.header_row + 1
        while True:
            question = ws.cell(row=r, column=block.question_col).value
            if question is None:
                break
            key = (block.subject, block.module_slot, int(question))
            writes.append(CellWrite(sheet_name, r, block.answer_col, remaining.pop(key, None)))
            r += 1

    if remaining:
        unmatched = ", ".join(f"{subject} {slot} {question}" for subject, slot, question in sorted(remaining))
        raise ValueError(f"{template_path}!{sheet_name} has no answer block for: {unmatched}")

    cleared_ranges = [
        (sheet_name, top, bottom, left, right)
        for top, bottom, left, right in blocks_to_clear(ws, active_variants)
    ]
    return FillResult(cell_writes=writes, cleared_ranges=cleared_ranges)
