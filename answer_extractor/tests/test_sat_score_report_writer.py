import datetime as dt
from pathlib import Path
from typing import List

import openpyxl
import pytest
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from answer_extractor.google_sheets_export import CellWrite
from answer_extractor.sat_score_report_writer import fill_sat_score_report, locate_sat_blocks

_MISSING = object()  # distinguishes "never written" from an explicitly-written None (an omitted answer)


def _at(writes: List[CellWrite], a1: str, sheet: str = "Student Responses"):
    col_letter, row = coordinate_from_string(a1)
    col = column_index_from_string(col_letter)
    for w in writes:
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
    the same mechanism, since a second subject would just repeat it."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Responses"

    ws["A1"] = "Type name here, date below"
    ws["A2"] = dt.datetime(2024, 1, 1)  # dummy placeholder date, like the real templates'

    # Module 1: columns A-D (question=A, correct=B, your=C, mark=D). No flag.
    ws["A4"] = "Reading and Writing Module 1"
    ws["B5"], ws["C5"] = "Correct Answer", "Your Answer"
    for i, (q, correct) in enumerate([(1, "A"), (2, "B")], start=6):
        ws.cell(row=i, column=1, value=q)
        ws.cell(row=i, column=2, value=correct)

    # Module 2 Higher, first (canonical) copy: columns F-I, flag at F5.
    ws["F4"] = "R & W Module 2 - Higher Difficulty"
    ws["F5"] = False
    ws["G5"], ws["H5"] = "Correct Answer", "Your Answer"
    for i, (q, correct) in enumerate([(1, "C"), (2, "D")], start=6):
        ws.cell(row=i, column=6, value=q)
        ws.cell(row=i, column=7, value=correct)

    # Module 2 Lower, first (canonical) copy: columns K-N, flag at K5.
    ws["K4"] = "R & W Module 2 - Lower Difficulty"
    ws["K5"] = False
    ws["L5"], ws["M5"] = "Correct Answer", "Your Answer"
    for i, (q, correct) in enumerate([(1, "F"), (2, "G")], start=6):
        ws.cell(row=i, column=11, value=q)
        ws.cell(row=i, column=12, value=correct)

    # Module 2 Higher, duplicate copy (byte-identical key): columns P-S, flag at P5.
    ws["P4"] = "R & W Module 2 - Higher Difficulty"
    ws["P5"] = False
    ws["Q5"], ws["R5"] = "Correct Answer", "Your Answer"
    for i, (q, correct) in enumerate([(1, "C"), (2, "D")], start=6):
        ws.cell(row=i, column=16, value=q)
        ws.cell(row=i, column=17, value=correct)

    # Module 2 Lower, duplicate copy: columns U-X, flag at U5.
    ws["U4"] = "R & W Module 2 - Lower Difficulty"
    ws["U5"] = False
    ws["V5"], ws["W5"] = "Correct Answer", "Your Answer"
    for i, (q, correct) in enumerate([(1, "F"), (2, "G")], start=6):
        ws.cell(row=i, column=21, value=q)
        ws.cell(row=i, column=22, value=correct)

    # Scaled section score: value cell sits a couple rows *above* its own
    # label, like the real templates' "Total Score"/section-score cells.
    ws["Z10"] = 200
    ws["Z12"] = "Reading\n& Writing\nScore"

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
    assert by_key[("reading and writing", "harder")].question_col == 6  # F, not the P duplicate
    assert by_key[("reading and writing", "easier")].question_col == 11  # K, not the U duplicate
    assert by_key[("reading and writing", "module1")].flag_cell is None
    assert by_key[("reading and writing", "harder")].flag_cell == (5, 6)
    assert by_key[("reading and writing", "easier")].flag_cell == (5, 11)


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
    # Harder, canonical (F) copy -- filled.
    assert _at(writes, "H6") == "C"
    assert _at(writes, "H7") == "D"
    assert _at(writes, "F5") is True
    # Harder, duplicate (P) copy -- left completely untouched (not even a blank write).
    assert _at(writes, "R6") is _MISSING
    assert _at(writes, "R7") is _MISSING
    assert _at(writes, "P5") is _MISSING
    # Easier -- not active, left untouched, flag never written (stays whatever the template had).
    assert _at(writes, "M6") is _MISSING
    assert _at(writes, "K5") is _MISSING


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

    assert _at(writes, "Z10") == 590


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

    assert _at(writes, "Z10") is _MISSING  # never written -- template's own default (200) stands


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
