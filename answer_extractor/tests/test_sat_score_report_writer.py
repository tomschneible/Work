import datetime as dt
from pathlib import Path

import openpyxl
import pytest
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from answer_extractor.google_sheets_export import CellWrite, FillResult
from answer_extractor.sat_score_report_writer import (
    fill_sat_score_report,
    inactive_block_column_ranges,
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
    Domain, Skill -- see _HIDDEN_BLOCK_WIDTH) plus one blank spacer column
    before the next block's own title, matching a real template's spacing
    exactly (confirmed against a real filled report) -- narrower spacing
    would let inactive_block_column_ranges's hide range bleed into the
    next block's own title column, a fixture-only collision that doesn't
    happen against the real layout this is modeling.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Responses"

    ws["A1"] = "Type name here, date below"
    ws["A2"] = dt.datetime(2024, 1, 1)  # dummy placeholder date, like the real templates'

    def _block(title_col: int, title: str, flagged: bool, answers) -> None:
        ws.cell(row=4, column=title_col, value=title)
        if flagged:
            ws.cell(row=5, column=title_col, value=False)
        ws.cell(row=5, column=title_col + 1, value="Correct Answer")
        ws.cell(row=5, column=title_col + 2, value="Your Answer")
        ws.cell(row=5, column=title_col + 4, value="Domain")
        ws.cell(row=5, column=title_col + 5, value="Skill")
        for i, (q, correct) in enumerate(answers, start=6):
            ws.cell(row=i, column=title_col, value=q)
            ws.cell(row=i, column=title_col + 1, value=correct)

    # Module 1: columns A-F (question=A, correct=B, your=C, mark=D, Domain=E,
    # Skill=F). No flag.
    _block(1, "Reading and Writing Module 1", flagged=False, answers=[(1, "A"), (2, "B")])
    # Module 2 Higher, canonical copy: columns H-M, flag at H5.
    _block(8, "R & W Module 2 - Higher Difficulty", flagged=True, answers=[(1, "C"), (2, "D")])
    # Module 2 Lower, canonical copy: columns O-T, flag at O5.
    _block(15, "R & W Module 2 - Lower Difficulty", flagged=True, answers=[(1, "F"), (2, "G")])
    # Module 2 Higher, duplicate copy (byte-identical key): columns V-AA, flag at V5.
    _block(22, "R & W Module 2 - Higher Difficulty", flagged=True, answers=[(1, "C"), (2, "D")])
    # Module 2 Lower, duplicate copy: columns AC-AH, flag at AC5.
    _block(29, "R & W Module 2 - Lower Difficulty", flagged=True, answers=[(1, "F"), (2, "G")])

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
    # block (both the inactive Easier pair and the Harder duplicate), including
    # each one's own Domain/Skill columns, hidden.
    assert writes.hidden_column_ranges == [
        ("Student Responses", 14, 20),  # O -- Easier, canonical
        ("Student Responses", 21, 27),  # V -- Harder, duplicate
        ("Student Responses", 28, 34),  # AC -- Easier, duplicate
    ]


def test_inactive_block_column_ranges_hides_every_module_2_block_but_the_active_one(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    # Easier is active -- its canonical (O) column stays visible; Higher's
    # canonical (H) is hidden along with both duplicates (V, AC).
    ranges = inactive_block_column_ranges(ws, {"reading and writing": "easier"})

    assert ranges == [(7, 13), (21, 27), (28, 34)]  # H, V, AC


def test_inactive_block_column_ranges_keeps_a_column_visible_if_any_subject_uses_it(tmp_path):
    """Module 2 block columns are reused by position across every subject
    stacked underneath them (see module docstring): if Reading & Writing's
    active variant is Higher (column F) and Math's is Lower (column K),
    both columns must stay visible even though each is 'inactive' for the
    other subject sharing those same columns."""
    path = tmp_path / "template.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Responses"
    ws["F4"] = "R & W Module 2 - Higher Difficulty"
    ws["F5"] = False
    ws["H5"] = "Your Answer"
    ws["K4"] = "R & W Module 2 - Lower Difficulty"
    ws["K5"] = False
    ws["M5"] = "Your Answer"
    ws["F20"] = "Math Module 2 - Higher Difficulty"
    ws["H21"] = "Your Answer"
    ws["K20"] = "Math Module 2 - Lower Difficulty"
    ws["M21"] = "Your Answer"
    wb.save(str(path))
    ws = openpyxl.load_workbook(path)["Student Responses"]

    ranges = inactive_block_column_ranges(ws, {"reading and writing": "harder", "math": "easier"})

    assert ranges == []  # both F (R&W's pick) and K (Math's pick) stay visible


def test_inactive_block_column_ranges_hides_every_module_2_block_when_nothing_is_active(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    ranges = inactive_block_column_ranges(ws, {})

    assert ranges == [(7, 13), (14, 20), (21, 27), (28, 34)]  # H, O, V, AC -- nothing administered


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
