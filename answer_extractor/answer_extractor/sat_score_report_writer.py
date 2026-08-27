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
    stacked underneath it. This is exactly why fill_sat_score_report
    consolidates *every* subject's real answers into one single column
    (_canonical_module2_col) rather than leaving each subject's own
    difficulty wherever it naturally sits: confirmed live that when
    Reading & Writing and Math administer *different* difficulties, their
    real answers land in different columns, leaving the two subjects'
    Module 2 tables visibly offset on the exported report -- and since
    every subject's score-count formula reads the *same* four flag cells
    by fixed address regardless of which subject or difficulty, there's
    no way to set a *different* subject-specific flag safely anyway.
    Consolidating means only the canonical column's own flag is ever set
    True; every other Module 2 occurrence is left untouched here and
    cleared and hidden instead (see blocks_to_clear and columns_to_hide
    -- both are needed, not just clearing: a cleared-but-not-hidden
    occurrence's blank columns still count toward the exported PDF's
    print area, forcing its "fit to page" scale down far more than the
    content actually left needs). Module 1 has no flag cell at
    all (everyone takes it, nothing to disambiguate). Flag cells are
    located here via one sheet-wide scan for boolean-valued cells, keyed
    by column -- not by searching near any one block's own header row,
    which is what a later-appearing subject's blocks actually need.

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
    title_row: int
    header_row: int
    question_col: int
    correct_col: int
    answer_col: int
    mark_col: int
    flag_cell: Optional[Tuple[int, int]]  # (row, col); None for module1, which needs no flag


def _scan_raw_titles(ws: Worksheet) -> List[Tuple[int, int, str, str]]:
    """Every block title found anywhere on `ws`, as (row, col, subject,
    module_slot) -- *not* deduplicated (see locate_sat_blocks, which keeps
    only the leftmost per (subject, module_slot); blocks_to_clear needs
    every occurrence's own column, including duplicates/twins, to know
    what to clear). Raises ValueError if a Module 2 title is missing its
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
    `title_col + 2` cell reads "Your Answer" -- used by locate_sat_blocks
    to find one block's own header row. Raises ValueError if it can't be
    found."""
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
            title_row=title_row,
            header_row=header_row,
            question_col=title_col,
            correct_col=title_col + 1,
            answer_col=title_col + 2,
            mark_col=title_col + 3,
            flag_cell=flag_cell,
        )
    return list(blocks_by_key.values())


def _canonical_module2_col(ws: Worksheet) -> Optional[int]:
    """The single column every subject's real Module 2 answers get
    consolidated into, regardless of which difficulty was actually
    administered -- always the leftmost Module 2 title column found (this
    template's own layout puts a subject's Higher-difficulty canonical
    block first; see fill_sat_score_report's own docstring for why every
    subject's real pick has to land in the same column position). None if
    the template has no Module 2 blocks at all."""
    cols = [block.question_col for block in locate_sat_blocks(ws) if block.module_slot != "module1"]
    return min(cols) if cols else None


def _non_canonical_module2_cols(ws: Worksheet) -> List[int]:
    """Every Module 2 title column found anywhere on `ws` (1-indexed)
    except _canonical_module2_col -- shared by blocks_to_clear and
    columns_to_hide, since both need to act on exactly the same set of
    occurrences (see columns_to_hide's own docstring for why a cleared
    occurrence's columns also need hiding, not just clearing)."""
    canonical_col = _canonical_module2_col(ws)
    return sorted(
        {col for _row, col, _subject, module_slot in _scan_raw_titles(ws) if module_slot != "module1"}
        - {canonical_col}
    )


def blocks_to_clear(ws: Worksheet) -> List[Tuple[int, int, int, int]]:
    """0-indexed (start_row, end_row, start_col, end_col) rectangles (end
    exclusive -- the shape a Sheets API GridRange needs) covering every
    Module 2 column position that isn't _canonical_module2_col -- every
    subject's own non-canonical difficulty, and every duplicate/twin --
    for the sheet's *entire* height (row 0 through `ws.max_row`), not just
    the rows a block occurrence's own questions happen to occupy.

    Whole-column, not per-occurrence, deliberately: an earlier version of
    this scoped each rectangle to one occurrence's own row range, because
    at the time a non-canonical column could still legitimately hold a
    *different* subject's real answers (this was before
    fill_sat_score_report consolidated every subject's real data into one
    column -- see its own docstring). Now that nothing real is ever left
    at a non-canonical column for *any* subject, there's nothing left to
    protect by scoping to rows -- confirmed live against a real template:
    a non-canonical column still held a handful of things outside any
    block occurrence's own question rows entirely (a boolean-valued flag
    cell, whose checkbox *widget* persisted even after its value was
    cleared -- see google_sheets_export.clear_cells's own docstring on why
    that needed a data-validation clear too; and a repeated "Page X of Y"
    footer label sitting well below the last question row), both of which
    a row-scoped rectangle silently left behind, still occupying enough
    of the sheet to force the exported PDF to scale down to fit them.
    Clearing the whole column removes all of it in one pass, whatever it
    turns out to be, without needing to enumerate each kind by hand.

    Each rectangle spans the full 6-column block width (title, correct
    answer, your answer, mark, Domain, Skill -- see _CLEAR_BLOCK_WIDTH).
    Module 1 is never included -- every student takes it, and it's never
    duplicated, so its own column is always canonical-equivalent (nothing
    else there needs to be canonicalized in the first place).
    """
    non_canonical_cols = _non_canonical_module2_cols(ws)
    return [(0, ws.max_row, col - 1, col - 1 + _CLEAR_BLOCK_WIDTH) for col in non_canonical_cols]


def columns_to_hide(ws: Worksheet) -> List[Tuple[int, int]]:
    """0-indexed (start_col, end_col) column ranges (end exclusive --
    the shape a Sheets API dimension range needs) covering the exact
    same non-canonical Module 2 columns blocks_to_clear clears -- one
    range per occurrence, each _CLEAR_BLOCK_WIDTH columns wide.

    Needed *in addition to* blocks_to_clear, not instead of it:
    clearing removes an occurrence's own cell values, but its columns
    are still fully present -- and still full width -- in the sheet's
    print area, since clearing never touches column width or
    visibility. Confirmed live against a real export: with every
    non-canonical occurrence cleared but not hidden, the exported PDF's
    "fit to page" scale was still being computed against a print area
    almost four times as wide as the one Module 2 block per subject
    that's actually left with content, forcing that scale down far more
    than the real content needed and leaving it squeezed into a small
    corner of the page with a large blank margin around it. Hiding
    these same columns removes them from the print area entirely, so
    "fit to page" scales to what's actually left to show.

    Safe to do unconditionally (every non-canonical column, for every
    subject) for the same reason blocks_to_clear now is: since
    fill_sat_score_report consolidates every subject's real answers
    into _canonical_module2_col, nothing real is ever left in any other
    Module 2 column position for any subject -- see hide_columns' own
    docstring for why this wasn't true, and this approach wasn't safe,
    before that consolidation existed.
    """
    return [(col - 1, col - 1 + _CLEAR_BLOCK_WIDTH) for col in _non_canonical_module2_cols(ws)]


def trailing_rows_to_delete(ws: Worksheet) -> List[Tuple[int, int]]:
    """0-indexed (start_row, end_row) row range (end exclusive -- the
    shape a Sheets API dimension range needs) covering every row below
    `ws`'s own last real content -- empty if there's no such gap.

    A completely different problem than blocks_to_clear/columns_to_hide:
    not a Module 2 occurrence that wasn't administered, but the sheet's
    own trailing rows that were never real content in the first place.
    Confirmed against the real template: "Student Responses" carries
    formatting (row heights, borders) all the way out to row 996, even
    though its actual content -- every block, every score cell, the
    footer -- ends at row 64. With no print area explicitly set on the
    file (see delete_rows' own docstring on why this pipeline doesn't set
    one directly), Sheets' PDF export falls back to the sheet's full used
    range for that tab, so those ~930 empty rows were still being
    counted when "fit to page" computed its scale -- confirmed live: the
    real tables were confined to the top ~86% of the page's usable
    height instead of filling it, the same class of problem
    columns_to_hide fixes for width, just for height instead and
    unrelated to Module 2 at all (this still matters even for a subject
    combination that never needed a single non-canonical column hidden).

    These rows get *deleted* outright, not hidden -- a first version of
    this fix hid them the same way columns_to_hide hides a column
    (google_sheets_export.hide_rows, since removed), and confirmed live
    that doing so had *no effect whatsoever* on the exported PDF: a
    before/after export came out pixel-for-pixel identical, unlike hiding
    a column, which measurably fixed the analogous width problem. Only
    actually removing the rows shrinks what Sheets' print-area
    computation still counts.

    "Last real content" is found by scanning every cell on the sheet for
    a non-None value, deliberately not using `ws.max_row`/`ws.dimensions`
    the way an earlier, now-abandoned approach did (_tighten_print_areas,
    see this repo's history) -- those reflect exactly the stray
    formatting this needs to look *past*, not the real content boundary
    itself.
    """
    last_content_row = 0
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                last_content_row = max(last_content_row, cell.row)
    if ws.max_row <= last_content_row:
        return []
    return [(last_content_row, ws.max_row)]


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
    plus, in the same FillResult, the rectangles to clear and the whole
    columns to hide for every Module 2 block occurrence that isn't the
    one column every subject's real answers get consolidated into (see
    blocks_to_clear and columns_to_hide), so the exported report only
    shows the modules that were actually filled in -- both in content
    and in the exported PDF's own sizing -- rather than every
    duplicate/twin block the template ships with; plus the whole rows
    below `sheet_name`'s own real content to delete too (see
    trailing_rows_to_delete), an unrelated problem (the sheet's own
    trailing blank-but-formatted rows, nothing to do with Module 2) that
    inflates the same exported PDF's print area regardless.

    `active_variants` maps subject -> "easier"/"harder", the Module 2
    difficulty actually administered for that subject (from
    answer_keys.annotate_rows) -- this is what decides which of each
    subject's own two same-difficulty block-pairs its answers get read
    from. Regardless of which difficulty that turns out to be, every
    subject's real answers -- title, correct-answer key, your answer,
    Domain, Skill -- are written into the *same* column position on the
    sheet (_canonical_module2_col, always Higher's own canonical block):
    if a subject's active variant already lives there, its cells are
    written in place exactly as before; otherwise those same values are
    copied in from wherever that subject's real block actually sits, and
    that column's own single flag cell is the only one this ever sets
    True. Every other Module 2 occurrence -- a subject's own non-matching
    difficulty, and every duplicate/twin -- is left completely untouched
    here and cleared and hidden instead (see FillResult.cleared_ranges
    and .hidden_column_ranges).

    This exists because Reading & Writing and Math share the exact same
    four column positions for their own Module 2 blocks (see this
    module's own docstring on flag cells) -- when they administer
    *different* difficulties (confirmed live: R&W Higher, Math Lower),
    each subject's real answers naturally sit in different columns, which
    left their two Module 2 tables visibly offset from each other on the
    exported report (confirmed against the org's own reference example,
    where both sit at identical column positions instead) -- a whole-
    column hide/clear keyed only by which difficulty is "active" can
    never fix that, since it never moves anything, only hides/shows it in
    place. Consolidating into one column removes the offset entirely, and
    -- as a side effect -- also means the sheet's own score-total
    formulas (which read a fixed handful of cells by absolute address,
    not by which subject or difficulty) only ever need to see one flag
    true at a time.

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

    canonical_col = _canonical_module2_col(ws)
    canonical_flag_cell = next(
        (b.flag_cell for b in locate_sat_blocks(ws) if b.question_col == canonical_col), None
    )

    remaining = dict(answers)
    for block in locate_sat_blocks(ws):
        is_active = block.module_slot == "module1" or active_variants.get(block.subject) == block.module_slot
        if not is_active:
            continue  # a non-administered block -- leave completely untouched, cleared instead

        if block.module_slot == "module1":
            r = block.header_row + 1
            while True:
                question = ws.cell(row=r, column=block.question_col).value
                if question is None:
                    break
                key = (block.subject, block.module_slot, int(question))
                writes.append(CellWrite(sheet_name, r, block.answer_col, remaining.pop(key, None)))
                r += 1
            continue

        # Module 2: consolidate into canonical_col regardless of which
        # difficulty this subject actually administered (see this
        # function's own docstring for why).
        if canonical_flag_cell is not None:
            frow, fcol = canonical_flag_cell
            writes.append(CellWrite(sheet_name, frow, fcol, True))

        repositioning = canonical_col is not None and block.question_col != canonical_col
        target_col = canonical_col if repositioning else block.question_col
        if repositioning:
            title_text = ws.cell(row=block.title_row, column=block.question_col).value
            writes.append(CellWrite(sheet_name, block.title_row, target_col, title_text))

        r = block.header_row + 1
        while True:
            question = ws.cell(row=r, column=block.question_col).value
            if question is None:
                break
            key = (block.subject, block.module_slot, int(question))
            answer_value = remaining.pop(key, None)
            if repositioning:
                # Every other cell the report shows for this question --
                # the correct-answer key and Domain/Skill -- has to move
                # too, or it'd still read as the wrong (or no) difficulty
                # once the title above it changes. mark_col needs no
                # write of its own: it's a formula already sitting at
                # target_col comparing correct-answer against your-answer
                # at that same position, so it recomputes on its own once
                # both of those land there.
                correct_value = ws.cell(row=r, column=block.correct_col).value
                domain_value = ws.cell(row=r, column=block.question_col + 4).value
                skill_value = ws.cell(row=r, column=block.question_col + 5).value
                writes.append(CellWrite(sheet_name, r, target_col + 1, correct_value))
                writes.append(CellWrite(sheet_name, r, target_col + 2, answer_value))
                writes.append(CellWrite(sheet_name, r, target_col + 4, domain_value))
                writes.append(CellWrite(sheet_name, r, target_col + 5, skill_value))
            else:
                writes.append(CellWrite(sheet_name, r, block.answer_col, answer_value))
            r += 1

    if remaining:
        unmatched = ", ".join(f"{subject} {slot} {question}" for subject, slot, question in sorted(remaining))
        raise ValueError(f"{template_path}!{sheet_name} has no answer block for: {unmatched}")

    cleared_ranges = [(sheet_name, top, bottom, left, right) for top, bottom, left, right in blocks_to_clear(ws)]
    hidden_column_ranges = [(sheet_name, left, right) for left, right in columns_to_hide(ws)]
    deleted_row_ranges = [(sheet_name, top, bottom) for top, bottom in trailing_rows_to_delete(ws)]
    return FillResult(
        cell_writes=writes,
        cleared_ranges=cleared_ranges,
        hidden_column_ranges=hidden_column_ranges,
        deleted_row_ranges=deleted_row_ranges,
    )
