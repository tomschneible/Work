from pathlib import Path

import openpyxl
import pytest

from answer_extractor.detect import QuestionResult
from answer_extractor.export import write_xlsx
from answer_extractor.pipeline import SheetResult
from answer_extractor.scoresheet_check import (
    compare,
    parse_program_output,
    parse_reference_scoresheet,
)


def _write_reference_scoresheet(path: Path) -> None:
    """A miniature version of the real vendor layout: two sections
    (English, Math), each split across two side-by-side column-groups, one
    title row governing both groups to its right -- same shape as the real
    file, just far fewer questions."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ScoreSheet"

    # Row 1: section titles. "English" governs columns A-F, "Math" columns H-M.
    ws["A1"] = "English"
    ws["H1"] = "Math"
    # Row 2: header pairs for each of the two column-groups.
    ws["B2"], ws["C2"] = "Correct Answer", "Your Answer"
    ws["I2"], ws["J2"] = "Correct Answer", "Your Answer"

    # English block: Q1-3, one correct/wrong/omitted.
    english_rows = [
        (1, "A", "A", "✔"),
        (2, "B", "C", "✘"),
        (3, "C", None, "ø"),
    ]
    for i, (q, correct, yours, mark) in enumerate(english_rows, start=3):
        ws.cell(row=i, column=1, value=q)
        ws.cell(row=i, column=2, value=correct)
        ws.cell(row=i, column=3, value=yours)
        ws.cell(row=i, column=4, value=mark)

    # Math block: Q1-2.
    math_rows = [
        (1, "F", "F", "✔"),
        (2, "G", "H", "✘"),
    ]
    for i, (q, correct, yours, mark) in enumerate(math_rows, start=3):
        ws.cell(row=i, column=8, value=q)
        ws.cell(row=i, column=9, value=correct)
        ws.cell(row=i, column=10, value=yours)
        ws.cell(row=i, column=11, value=mark)

    wb.save(str(path))


def test_parse_reference_scoresheet_reads_both_sections(tmp_path):
    path = tmp_path / "reference.xlsx"
    _write_reference_scoresheet(path)

    result = parse_reference_scoresheet(path)

    # "Math" in the source is normalized to "mathematics" to match our
    # template's section naming.
    assert result == {
        ("english", 1): "A",
        ("english", 2): "C",
        ("english", 3): "",  # omitted
        ("mathematics", 1): "F",
        ("mathematics", 2): "H",
    }


def test_parse_reference_scoresheet_missing_tab_raises(tmp_path):
    path = tmp_path / "reference.xlsx"
    _write_reference_scoresheet(path)

    with pytest.raises(ValueError, match="No 'Nope'"):
        parse_reference_scoresheet(path, sheet_name="Nope")


def _write_our_output(path: Path) -> None:
    questions = [
        QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False),
        QuestionResult("English", 2, "B", ["B"], {}, low_confidence=False),  # will mismatch, unflagged
        QuestionResult("English", 3, "", [], {}, low_confidence=False),  # ordinary blank
        QuestionResult("Mathematics", 1, "F", ["F"], {}, low_confidence=False),
        QuestionResult(
            "Mathematics", 2, "", [], {}, low_confidence=False, unreadable=True
        ),  # flagged blank
    ]
    result = SheetResult(label="sheet1", source="test", used_contour_alignment=False, questions=questions)
    write_xlsx([result], path)


def test_parse_program_output_reads_answers_and_flags_back_off_the_cell_styling(tmp_path):
    path = tmp_path / "ours.xlsx"
    _write_our_output(path)

    result = parse_program_output(path)

    assert result[("english", 1)].answer == "A"
    assert result[("english", 1)].flag is None
    assert result[("english", 3)].answer == ""
    assert result[("english", 3)].flag == "blank"
    assert result[("mathematics", 2)].answer == ""
    assert result[("mathematics", 2)].flag == "unreadable"


def test_compare_categorizes_by_severity(tmp_path):
    ref_path = tmp_path / "reference.xlsx"
    ours_path = tmp_path / "ours.xlsx"
    _write_reference_scoresheet(ref_path)
    _write_our_output(ours_path)

    reference = parse_reference_scoresheet(ref_path)
    ours = parse_program_output(ours_path)
    rows = compare(reference, ours)
    by_key = {(r.section, r.question): r for r in rows}

    assert by_key[("english", 1)].severity == "match"
    # We read "B" where the reference says "C" -- wrong, and we never
    # flagged it, so this is the worst case.
    assert by_key[("english", 2)].severity == "silent_miss"
    # Reference also has this one omitted -- matches our ordinary blank.
    assert by_key[("english", 3)].severity == "match"
    assert by_key[("mathematics", 1)].severity == "match"
    # Reference says "H" was marked, but we flagged this one unreadable --
    # still wrong, but not a *silent* miss since it was already surfaced.
    assert by_key[("mathematics", 2)].severity == "flagged"
