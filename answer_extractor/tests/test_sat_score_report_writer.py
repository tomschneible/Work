import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from answer_extractor.sat_score_report_writer import fill_sat_score_report, locate_sat_blocks


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

    wb = fill_sat_score_report(
        path,
        answers={
            ("reading and writing", "module1", 1): "A",
            ("reading and writing", "module1", 2): "B",
            ("reading and writing", "harder", 1): "C",
            ("reading and writing", "harder", 2): "D",
        },
        active_variants={"reading and writing": "harder"},
        student_name="Jane Student",
        test_date=dt.datetime(2026, 3, 8),
    )
    ws = wb["Student Responses"]

    assert ws["A1"].value == "Jane Student"
    assert ws["A2"].value == dt.datetime(2026, 3, 8)
    # Module 1 (always active).
    assert ws["C6"].value == "A"
    assert ws["C7"].value == "B"
    # Harder, canonical (F) copy -- filled.
    assert ws["H6"].value == "C"
    assert ws["H7"].value == "D"
    assert ws["F5"].value is True
    # Harder, duplicate (P) copy -- left completely untouched.
    assert ws["R6"].value is None
    assert ws["R7"].value is None
    assert ws["P5"].value is False
    # Easier -- not active, left untouched, flag stays False.
    assert ws["M6"].value is None
    assert ws["K5"].value is False


def test_fill_sat_score_report_leaves_a_missing_answer_blank(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    wb = fill_sat_score_report(
        path,
        answers={("reading and writing", "module1", 1): "A"},  # question 2 omitted
        active_variants={},
        student_name="Jane Student",
        test_date=dt.datetime(2026, 3, 8),
    )
    ws = wb["Student Responses"]

    assert ws["C6"].value == "A"
    assert ws["C7"].value is None


def test_fill_sat_score_report_raises_for_an_answer_in_the_inactive_variant(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    with pytest.raises(ValueError, match="active variant"):
        fill_sat_score_report(
            path,
            answers={("reading and writing", "easier", 1): "F"},  # but harder is active
            active_variants={"reading and writing": "harder"},
            student_name="Jane Student",
            test_date=dt.datetime(2026, 3, 8),
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
            test_date=dt.datetime(2026, 3, 8),
        )
