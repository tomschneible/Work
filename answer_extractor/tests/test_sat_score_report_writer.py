import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from answer_extractor.sat_score_report_writer import (
    find_score_value_cells,
    locate_sat_blocks,
    normalize_subject,
    read_reference_questions,
)


def test_normalize_subject_treats_a_bare_writing_as_reading_and_writing():
    """Confirmed on a real DSAT report: at a question boundary where the
    previous and current question shared the same Domain label, PyMuPDF's
    own text extraction (score_report.py) emitted "Reading and Writing"
    out of order, leaving only "Writing" where the section name is
    normally found. Digital SAT has no standalone "Writing" module, so
    this is always the combined section, not a genuine third subject."""
    assert normalize_subject("Writing") == "reading and writing"


def test_normalize_subject_still_recognizes_the_full_name_and_rejects_unknown_ones():
    assert normalize_subject("Reading and Writing") == "reading and writing"
    assert normalize_subject("Math") == "math"
    with pytest.raises(ValueError, match="Unrecognized SAT subject title"):
        normalize_subject("Science")


def _block(ws, title_col: int, title: str, answers, title_row: int = 4) -> None:
    """One SAT block: a title (`title_row`), header labels (the next row:
    Correct Answer/Your Answer/Domain/Skill), and answer-key rows starting
    two rows below the title."""
    header_row = title_row + 1
    ws.cell(row=title_row, column=title_col, value=title)
    ws.cell(row=header_row, column=title_col + 1, value="Correct Answer")
    ws.cell(row=header_row, column=title_col + 2, value="Your Answer")
    ws.cell(row=header_row, column=title_col + 4, value="Domain")
    ws.cell(row=header_row, column=title_col + 5, value="Skill")
    for i, (q, correct) in enumerate(answers, start=header_row + 1):
        ws.cell(row=i, column=title_col, value=q)
        ws.cell(row=i, column=title_col + 1, value=correct)


def _write_template(path: Path) -> None:
    """A miniature version of the real current-format DSAT templates: one
    subject (Reading and Writing), Module 1 plus two same-difficulty pairs
    of Module 2 blocks (Higher x2, Lower x2), each block 6 columns wide
    (question, correct, your-answer, mark, Domain, Skill) plus a blank
    spacer column, like the real layout."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Responses"

    ws["A1"] = "Type name here, date below"
    ws["A2"] = dt.datetime(2024, 1, 1)  # dummy placeholder date, like the real templates'

    _block(ws, 1, "Reading and Writing Module 1", answers=[(1, "A"), (2, "B")])  # columns A-F
    _block(ws, 8, "R & W Module 2 - Higher Difficulty", answers=[(1, "C"), (2, "D")])  # H-M
    _block(ws, 15, "R & W Module 2 - Lower Difficulty", answers=[(1, "F"), (2, "G")])  # O-T
    _block(ws, 22, "R & W Module 2 - Higher Difficulty", answers=[(1, "C"), (2, "D")])  # V-AA, duplicate
    _block(ws, 29, "R & W Module 2 - Lower Difficulty", answers=[(1, "F"), (2, "G")])  # AC-AH, duplicate

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


def test_read_reference_questions_reads_correct_answer_domain_and_skill(tmp_path):
    """_block only ever writes the Domain/Skill *header* labels, not
    per-question values (no test needed them before this) -- add a
    couple directly here rather than changing the shared fixture."""
    path = tmp_path / "template.xlsx"
    _write_template(path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Student Responses"]
    ws["E6"], ws["F6"] = "Craft and Structure", "Words in Context"  # Module 1 Q1
    ws["E7"], ws["F7"] = "Information and Ideas", "Central Ideas"  # Module 1 Q2
    ws["L6"], ws["M6"] = "Expression of Ideas", "Rhetorical Synthesis"  # Harder canonical Q1
    wb.save(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    module1 = read_reference_questions(ws, "reading and writing", "module1")
    assert (module1[1].correct_answer, module1[1].domain, module1[1].skill) == (
        "A",
        "Craft and Structure",
        "Words in Context",
    )
    assert (module1[2].correct_answer, module1[2].domain, module1[2].skill) == (
        "B",
        "Information and Ideas",
        "Central Ideas",
    )

    harder = read_reference_questions(ws, "reading and writing", "harder")
    assert (harder[1].correct_answer, harder[1].domain, harder[1].skill) == (
        "C",
        "Expression of Ideas",
        "Rhetorical Synthesis",
    )
    # Q2 never had Domain/Skill poked above -- still reads cleanly as None,
    # not an error; only a missing *block* raises (see below).
    assert (harder[2].correct_answer, harder[2].domain, harder[2].skill) == ("D", None, None)


def test_read_reference_questions_raises_for_a_block_that_does_not_exist(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    ws = openpyxl.load_workbook(path)["Student Responses"]

    with pytest.raises(ValueError, match="math.*module1"):
        read_reference_questions(ws, "math", "module1")


def test_find_score_value_cells_locates_the_value_above_its_label():
    path_ws = openpyxl.Workbook()
    ws = path_ws.active
    ws.title = "Student Responses"
    ws["A1"] = "Type name here, date below"
    ws["A2"] = dt.datetime(2024, 1, 1)
    ws["Z10"] = 200
    ws["Z12"] = "Reading\n& Writing\nScore"

    assert find_score_value_cells(ws) == {"reading and writing": (10, 26)}
