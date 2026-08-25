import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from answer_extractor.score_report_writer import fill_score_report


def _write_template(path: Path) -> None:
    """A miniature version of the real per-test score-report templates:
    name/date placeholders, two sections (English, Math) each with their
    own question/correct-answer/your-answer block, and an unrelated
    scoring formula elsewhere on the sheet that filling in answers must
    never touch.

    The real templates' later blocks number their questions with a
    formula continuing the previous row's (e.g. '=A64+1', see
    scoresheet_grid.iter_block_questions) -- not reproduced here, since
    openpyxl never computes or caches a formula's result on save (proven
    against a real template+filled-example pair in this feature's manual
    validation instead, where those cached values already existed)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ScoreSheet"

    ws["A1"] = "Name:"
    ws["D1"] = "Enter Name on 'ScoreSheet' Tab"
    ws["A2"] = "Test Date:"
    ws["D2"] = "Enter Date on 'ScoreSheet' Tab"

    # English block: header at C5 ("Your Answer") -> question_col=A,
    # correct_col=B, answer_col=C. Rows 6-8, questions 1-3.
    ws["A4"] = "English"
    ws["B5"], ws["C5"] = "Correct Answer", "Your Answer"
    ws["A6"], ws["B6"] = 1, "A"
    ws["A7"], ws["B7"] = 2, "B"
    ws["A8"], ws["B8"] = 3, "C"
    ws["D8"] = '=IF(B8=C8,"match","no")'  # untouched by the writer either way

    # Math block: header at J5 ("Your Answer") -> question_col=H,
    # correct_col=I, answer_col=J. Rows 6-7, questions 1-2.
    ws["H4"] = "Math"
    ws["I5"], ws["J5"] = "Correct Answer", "Your Answer"
    ws["H6"], ws["I6"] = 1, "F"
    ws["H7"], ws["I7"] = 2, "G"

    wb.save(str(path))


def test_fill_score_report_writes_name_date_and_answers(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    wb = fill_score_report(
        path,
        answers={
            ("english", 1): "A",
            ("english", 2): "C",
            # english 3 intentionally omitted -- left blank below.
            ("mathematics", 1): "F",
            ("mathematics", 2): "H",
        },
        student_name="Jane Student",
        test_date=dt.datetime(2026, 3, 4),
    )
    ws = wb["ScoreSheet"]

    assert ws["D1"].value == "Jane Student"
    assert ws["D2"].value == dt.datetime(2026, 3, 4)
    assert ws["C6"].value == "A"
    assert ws["C7"].value == "C"
    assert ws["C8"].value is None  # omitted
    assert ws["J6"].value == "F"
    assert ws["J7"].value == "H"


def test_fill_score_report_preserves_formulas_and_correct_answers(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    wb = fill_score_report(
        path,
        answers={("english", 1): "A", ("english", 2): "B", ("english", 3): "C",
                  ("mathematics", 1): "F", ("mathematics", 2): "G"},
        student_name="Jane Student",
        test_date=dt.datetime(2026, 3, 4),
    )
    ws = wb["ScoreSheet"]

    # The unrelated match formula is untouched -- the writer only ever
    # sets answer_col cells.
    assert ws["D8"].value == '=IF(B8=C8,"match","no")'
    # Pre-baked correct answers (the answer key) are untouched too.
    assert ws["B6"].value == "A"
    assert ws["B7"].value == "B"


def test_fill_score_report_raises_on_unmatched_answer_key(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)

    with pytest.raises(ValueError, match="reading 1"):
        fill_score_report(
            path,
            answers={("english", 1): "A", ("reading", 1): "A"},  # no Reading block in this template
            student_name="Jane Student",
            test_date=dt.datetime(2026, 3, 4),
        )


def test_fill_score_report_raises_when_name_placeholder_missing(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    wb = openpyxl.load_workbook(path)
    wb["ScoreSheet"]["D1"] = None  # simulate a template missing its marker
    wb.save(path)

    with pytest.raises(ValueError, match="enter name"):
        fill_score_report(path, answers={}, student_name="Jane Student", test_date=dt.datetime(2026, 3, 4))


def test_fill_score_report_does_not_mutate_the_template_file(tmp_path):
    path = tmp_path / "template.xlsx"
    _write_template(path)
    before = path.read_bytes()

    fill_score_report(
        path,
        answers={("english", 1): "A", ("english", 2): "B", ("english", 3): "C",
                  ("mathematics", 1): "F", ("mathematics", 2): "G"},
        student_name="Jane Student",
        test_date=dt.datetime(2026, 3, 4),
    )

    assert path.read_bytes() == before
