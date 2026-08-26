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
# question/correct-answer/your-answer/mark-column -- every SAT block's own
# width, used both to locate a block's individual columns (SatBlock) and to
# size a hidden-block's column range (inactive_block_column_ranges).
_BLOCK_WIDTH = 4
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
    only the leftmost per (subject, module_slot); inactive_block_column_ranges
    needs every occurrence, including duplicates/twins, to know what to
    hide). Raises ValueError if a Module 2 title is missing its
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

        header_row = None
        for candidate_row in range(title_row, title_row + _HEADER_SEARCH_ROWS + 1):
            if ws.cell(row=candidate_row, column=title_col + 2).value == "Your Answer":
                header_row = candidate_row
                break
        if header_row is None:
            raise ValueError(
                f"Could not find a 'Your Answer' header below {subject!r} {module_slot!r}'s title "
                f"at row {title_row}, column {title_col}"
            )

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


def inactive_block_column_ranges(ws: Worksheet, active_variants: Mapping[str, str]) -> List[Tuple[int, int]]:
    """0-indexed [start, end) column ranges (the shape a Sheets API
    dimension range needs) for every Module 2 block's own
    _BLOCK_WIDTH columns that a filled report should hide -- every block
    except the one subject-active administered difficulty actually
    written to. Module 1's own column is never included -- every student
    takes it, and it's never duplicated.

    This is a whole-sheet decision keyed by column, not evaluated
    per-subject: a subject's block columns are reused by column
    *position* across every other subject stacked underneath it (see this
    module's own docstring on flag cells) -- so a column stays visible if
    *any* subject's active variant uses it, even though it's inactive
    (and therefore blank) for another subject sharing those same columns.
    Building the "keep" set from every block returned by locate_sat_blocks
    -- one already-deduplicated leftmost block per (subject, module_slot)
    -- and then hiding every *other* raw title occurrence (including
    duplicates/twins) handles that correctly by construction, without
    needing to reason about which subject owns which column explicitly.
    """
    keep_cols = {
        block.question_col
        for block in locate_sat_blocks(ws)
        if block.module_slot == "module1" or active_variants.get(block.subject) == block.module_slot
    }
    all_module2_cols = {col for _row, col, _subject, module_slot in _scan_raw_titles(ws) if module_slot != "module1"}
    return [(col - 1, col - 1 + _BLOCK_WIDTH) for col in sorted(all_module2_cols - keep_cols)]


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
    plus, in the same FillResult, the column ranges for every Module 2
    block variant that wasn't administered (see
    inactive_block_column_ranges), so the exported report only shows the
    modules that were actually filled in rather than every duplicate/twin
    block the template ships with.

    `active_variants` maps subject -> "easier"/"harder", the Module 2
    difficulty actually administered for that subject (from
    answer_keys.annotate_rows) -- this is what decides which of each
    subject's two same-difficulty block-pairs gets its flag cell set and
    its answers written (and, in FillResult, stays visible); the other
    pair is never touched, and every other block gets hidden. `answers`
    must only contain keys for "module1" or each subject's active variant
    -- an entry for the *inactive* variant raises, since writing it would
    silently go nowhere the sheet's own formulas count (its flag stays
    False). A template question with no entry in `answers` is left blank
    (a legitimate omitted bubble).

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

    hidden_ranges = [
        (sheet_name, start, end) for start, end in inactive_block_column_ranges(ws, active_variants)
    ]
    return FillResult(cell_writes=writes, hidden_column_ranges=hidden_ranges)
