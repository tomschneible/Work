import datetime as dt
from pathlib import Path

import openpyxl
import pytest
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from answer_extractor.google_sheets_export import CellWrite, FillResult
from answer_extractor.sat_score_report_writer import (
    blocks_to_clear,
    fill_sat_score_report,
    locate_sat_blocks,
)

_MISSING = object()  # distinguishes "never written" from an explicitly-written None (an omitted answer)


def _at(result: FillResult, a1: str, sheet: str = "Student Responses"):
    col_letter, row = coordinate_from_string(a1)
    col = column_index_from_string(col_letter)
    for w in result.cell_writes:
        if w.sheet == sheet and w.row == row and w.column == col:
            return w.value
    return _MISSING


def _block(ws, title_col: int, title: str, flagged: bool, answers, title_row: int = 4) -> None:
    """One SAT block: a title (`title_row`), header labels (the next row:
    Correct Answer/Your Answer/Domain/Skill), and answer-key rows starting
    two rows below the title -- the shape _write_template composes into a
    full subject's worth of blocks (see there), reused directly by tests
    that only need one or two standalone blocks. `title_row` defaults to
    4 (module1's own row); pass a later row to place a second subject's
    blocks below the first's, like a real template."""
    header_row = title_row + 1
    ws.cell(row=title_row, column=title_col, value=title)
    if flagged:
        ws.cell(row=header_row, column=title_col, value=False)
    ws.cell(row=header_row, column=title_col + 1, value="Correct Answer")
    ws.cell(row=header_row, column=title_col + 2, value="Your Answer")
    ws.cell(row=header_row, column=title_col + 4, value="Domain")
    ws.cell(row=header_row, column=title_col + 5, value="Skill")
    for i, (q, correct) in enumerate(answers, start=header_row + 1):
        ws.cell(row=i, column=title_col, value=q)
        ws.cell(row=i, column=title_col + 1, value=correct)


def _write_template(path: Path) -> None:
    """A miniature version of the real DSAT templates: one subject
    (Reading and Writing), Module 1 plus two same-difficulty pairs of
    Module 2 blocks (Higher x2, Lower x2), and the single shared row of
    flag cells every subject's score formulas actually reference (see
    module docstring) -- confirmed against a real blank template where a
    *second* subject's blocks reuse the *first* subject's flag row rather
    than having their own; this fixture only needs one subject to prove
    the same mechanism, since a second subject would just repeat it.

    Each block is 6 columns wide (question, correct, your-answer, mark,
    Domain, Skill -- see _CLEAR_BLOCK_WIDTH) plus one blank spacer column
    before the next block's own title, matching a real template's spacing
    exactly (confirmed against a real filled report) -- narrower spacing
    would let blocks_to_clear's clear range bleed into the next block's
    own title column, a fixture-only collision that doesn't happen
    against the real layout this is modeling.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Responses"

    ws["A1"] = "Type name here, date below"
    ws["A2"] = dt.datetime(2024, 1, 1)  # dummy placeholder date, like the real templates'

    # Module 1: columns A-F (question=A, correct=B, your=C, mark=D, Domain=E,
    # Skill=F). No flag.
    _block(ws, 1, "Reading and Writing Module 1", flagged=False, answers=[(1, "A"), (2, "B")])
    # Module 2 Higher, canonical copy: columns H-M, flag at H5.
    _block(ws, 8, "R & W Module 2 - Higher Difficulty", flagged=True, answers=[(1, "C"), (2, "D")])
    # Module 2 Lower, canonical copy: columns O-T, flag at O5.
    _block(ws, 15, "R & W Module 2 - Lower Difficulty", flagged=True, answers=[(1, "F"), (2, "G")])
    # Module 2 Higher, duplicate copy (byte-identical key): columns V-AA, flag at V5.
    _block(ws, 22, "R & W Module 2 - Higher Difficulty", flagged=True, answers=[(1, "C"), (2, "D")])
    # Module 2 Lower, duplicate copy: columns AC-AH, flag at AC5.
    _block(ws, 29, "R & W Module 2 - Lower Difficulty", flagged=True, answers=[(1, "F"), (2, "G")])

    # Scaled section score: value cell sits a couple rows *above* its own
    # label, like the real templates' "Total Score"/section-score cells --
    # well clear of every block's own columns (through AI).
    ws["AN10"] = 200
    ws["AN12"] = "Reading\n& Writing\nScore"

    wb.save(str(path))


def test_locate_sat_blocks_dedupes_duplicate_pairs_to_the_leftmost(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    blocks = locate_sat_blocks(ws)
    by_key = {(b.subject, b.module_slot): b for b in blocks}

    assert set(by_key) == {
        ("reading and writing", "module1"),
        ("reading and writing", "harder"),
        ("reading and writing", "easier"),
    }
    assert by_key[("reading and writing", "harder")].question_col == 8  # H, not the V duplicate
    assert by_key[("reading and writing", "easier")].question_col == 15  # O, not the AC duplicate
    assert by_key[("reading and writing", "module1")].flag_cell is None
    assert by_key[("reading and writing", "harder")].flag_cell == (5, 8)
    assert by_key[("reading and writing", "easier")].flag_cell == (5, 15)


def test_fill_sat_score_report_writes_name_date_and_active_variant_only(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    writes = fill_sat_score_report(
        path,
        answers={
            ("reading and writing", "module1", 1): "A",
            ("reading and writing", "module1", 2): "B",
            ("reading and writing", "harder", 1): "C",
            ("reading and writing", "harder", 2): "D",
        },
        active_variants={"reading and writing": "harder"},
        student_name="Jane Student",
        test_date=dt.date(2026, 3, 8),
    )

    assert _at(writes, "A1") == "Jane Student"
    assert _at(writes, "A2") == "2026-03-08"  # ISO-formatted for the Sheets API
    # Module 1 (always active).
    assert _at(writes, "C6") == "A"
    assert _at(writes, "C7") == "B"
    # Harder, canonical (H) copy -- filled.
    assert _at(writes, "J6") == "C"
    assert _at(writes, "J7") == "D"
    assert _at(writes, "H5") is True
    # Harder, duplicate (V) copy -- left completely untouched (not even a blank write).
    assert _at(writes, "X6") is _MISSING
    assert _at(writes, "X7") is _MISSING
    assert _at(writes, "V5") is _MISSING
    # Easier -- not active, left untouched, flag never written (stays whatever the template had).
    assert _at(writes, "Q6") is _MISSING
    assert _at(writes, "O5") is _MISSING
    # The report should only show the filled-in module -- every other Module 2
    # block occurrence (Easier's canonical block, plus both duplicates), title
    # through Domain/Skill, cleared (see test_blocks_to_clear_hides_every_
    # module_2_block_but_the_active_one for these same rectangles' own
    # column-letter breakdown).
    assert writes.cleared_ranges == [
        ("Student Responses", 3, 7, 15, 20),
        ("Student Responses", 3, 4, 14, 15),
        ("Student Responses", 5, 7, 14, 15),
        ("Student Responses", 3, 7, 22, 27),
        ("Student Responses", 3, 4, 21, 22),
        ("Student Responses", 5, 7, 21, 22),
        ("Student Responses", 3, 7, 29, 34),
        ("Student Responses", 3, 4, 28, 29),
        ("Student Responses", 5, 7, 28, 29),
    ]


def test_blocks_to_clear_hides_every_module_2_block_but_the_active_one(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    # Easier is active -- its canonical (O) block stays untouched; Higher's
    # canonical (H) is cleared along with both duplicates (V, AC). Each
    # occurrence yields 3 rectangles: correct-through-skill for the full row
    # range, plus the title/flag column split around the one flag cell (row
    # 4, 0-indexed) another subject sharing these columns could still need.
    ranges = blocks_to_clear(ws, {"reading and writing": "easier"})

    assert ranges == [
        (3, 7, 8, 13), (3, 4, 7, 8), (5, 7, 7, 8),  # H -- Higher, canonical
        (3, 7, 22, 27), (3, 4, 21, 22), (5, 7, 21, 22),  # V -- Higher, duplicate
        (3, 7, 29, 34), (3, 4, 28, 29), (5, 7, 28, 29),  # AC -- Easier, duplicate
    ]


def test_blocks_to_clear_is_scoped_to_one_subjects_own_row_range(tmp_path):
    """Module 2 block columns are reused by position across every subject
    stacked underneath them (see module docstring): Reading & Writing's
    own occurrences sit at rows 4-7, Math's own occurrences of the exact
    same two columns sit at rows 24-27. When R&W's active variant is
    Higher and Math's is Lower, each subject's own *non-matching*
    occurrence gets cleared independently -- R&W's own Lower (rows 4-7)
    and Math's own Higher (rows 24-27) -- while each one's own *matching*
    occurrence is untouched, without either subject's clearing reaching
    into the other's row range even though they share the same columns.
    This is exactly the case a whole-column hide can't represent at all
    (there's no single column-visibility answer that's correct for both
    subjects at once) -- see blocks_to_clear's own docstring."""
    path = tmp_path / "template.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Responses"
    _block(ws, 8, "R & W Module 2 - Higher Difficulty", flagged=True, answers=[(1, "C"), (2, "D")])
    _block(ws, 15, "R & W Module 2 - Lower Difficulty", flagged=True, answers=[(1, "F"), (2, "G")])
    # Math's own occurrences of the same columns, well below R&W's -- no
    # flag cell of its own (flagged=False): it reuses R&W's shared row (see
    # module docstring), which R&W's own blocks above already established.
    _block(ws, 8, "Math Module 2 - Higher Difficulty", flagged=False, answers=[(1, "A"), (2, "B")], title_row=24)
    _block(ws, 15, "Math Module 2 - Lower Difficulty", flagged=False, answers=[(1, "D"), (2, "C")], title_row=24)
    wb.save(str(path))
    ws = openpyxl.load_workbook(path)["Student Responses"]

    ranges = blocks_to_clear(ws, {"reading and writing": "harder", "math": "easier"})

    # R&W's own Higher (rows 4-7) and Math's own Lower (rows 24-27) -- each
    # subject's real pick -- are absent entirely: never cleared.
    assert ranges == [
        (3, 7, 15, 20), (3, 4, 14, 15), (5, 7, 14, 15),  # R&W's own Lower (rows 4-7) -- not its pick
        (23, 27, 8, 13), (23, 24, 7, 8), (25, 27, 7, 8),  # Math's own Higher (rows 24-27) -- not its pick
    ]


def test_blocks_to_clear_hides_every_module_2_block_when_nothing_is_active(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    ranges = blocks_to_clear(ws, {})

    assert ranges == [
        (3, 7, 8, 13), (3, 4, 7, 8), (5, 7, 7, 8),  # H -- Higher, canonical
        (3, 7, 15, 20), (3, 4, 14, 15), (5, 7, 14, 15),  # O -- Lower, canonical
        (3, 7, 22, 27), (3, 4, 21, 22), (5, 7, 21, 22),  # V -- Higher, duplicate
        (3, 7, 29, 34), (3, 4, 28, 29), (5, 7, 28, 29),  # AC -- Lower, duplicate
    ]


def test_fill_sat_score_report_leaves_a_missing_answer_blank(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    writes = fill_sat_score_report(
        path,
        answers={("reading and writing", "module1", 1): "A"},  # question 2 omitted
        active_variants={},
        student_name="Jane Student",
        test_date=dt.date(2026, 3, 8),
    )

    assert _at(writes, "C6") == "A"
    assert _at(writes, "C7") is None  # explicitly blanked, not just absent


def test_fill_sat_score_report_raises_for_an_answer_in_the_inactive_variant(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    with pytest.raises(ValueError, match="active variant"):
        fill_sat_score_report(
            path,
            answers={("reading and writing", "easier", 1): "F"},  # but harder is active
            active_variants={"reading and writing": "harder"},
            student_name="Jane Student",
            test_date=dt.date(2026, 3, 8),
        )


def test_fill_sat_score_report_raises_on_unmatched_answer_key(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    with pytest.raises(ValueError, match="math"):
        fill_sat_score_report(
            path,
            answers={("math", "module1", 1): "A"},  # no Math block in this fixture
            active_variants={},
            student_name="Jane Student",
            test_date=dt.date(2026, 3, 8),
        )


def test_find_score_value_cells_locates_the_value_above_its_label():
    path_ws = openpyxl.Workbook()
    ws = path_ws.active
    ws.title = "Student Responses"
    ws["A1"] = "Type name here, date below"
    ws["A2"] = dt.datetime(2024, 1, 1)
    ws["Z10"] = 200
    ws["Z12"] = "Reading\n& Writing\nScore"

    from answer_extractor.sat_score_report_writer import _find_score_value_cells

    assert _find_score_value_cells(ws) == {"reading and writing": (10, 26)}


def test_fill_sat_score_report_writes_a_given_section_score(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    writes = fill_sat_score_report(
        path,
        answers={},
        active_variants={},
        student_name="Jane Student",
        test_date=dt.date(2026, 3, 8),
        section_scores={"reading and writing": 590},
    )

    assert _at(writes, "AN10") == 590


def test_fill_sat_score_report_leaves_score_cell_alone_when_not_given(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    writes = fill_sat_score_report(
        path,
        answers={},
        active_variants={},
        student_name="Jane Student",
        test_date=dt.date(2026, 3, 8),
        # section_scores omitted entirely
    )

    assert _at(writes, "AN10") is _MISSING  # never written -- template's own default (200) stands


def test_fill_sat_score_report_raises_for_an_unrecognized_score_subject(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    with pytest.raises(ValueError, match="science"):
        fill_sat_score_report(
            path,
            answers={},
            active_variants={},
            student_name="Jane Student",
            test_date=dt.date(2026, 3, 8),
            section_scores={"science": 500},
        )
