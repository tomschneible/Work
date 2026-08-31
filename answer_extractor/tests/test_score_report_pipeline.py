import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.detect import QuestionResult
from answer_extractor.pipeline import SheetResult
from answer_extractor.score_report_pipeline import (
    answers_from_result,
    export_sheet_report,
    output_base_name,
    should_export_to_sheets,
)
from answer_extractor.scan_filename import parse_scan_filename

_MODULE = "answer_extractor.score_report_pipeline"


def _result(label, questions, template_name="act_answer_sheet"):
    return SheetResult(
        label=label, source="test", used_contour_alignment=False, questions=questions, template_name=template_name
    )


def test_should_export_to_sheets_true_for_known_act_templates():
    assert should_export_to_sheets(_result("x", [], template_name="act_answer_sheet"))
    assert should_export_to_sheets(_result("x", [], template_name="legacy_act_answer_sheet"))


def test_should_export_to_sheets_false_for_unrecognized_template():
    assert not should_export_to_sheets(_result("x", [], template_name="default_template"))
    assert not should_export_to_sheets(_result("x", [], template_name=""))


def test_answers_from_result_blanks_multiple_but_keeps_a_low_confidence_answer():
    questions = [
        QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False),
        QuestionResult("English", 2, "MULTIPLE", ["A", "B"], {}, low_confidence=True),
        QuestionResult("English", 3, "", [], {}, low_confidence=False),
        QuestionResult("Mathematics", 1, "F", ["F"], {}, low_confidence=True),  # a real guess, kept
    ]
    result = _result("x", questions)

    answers = answers_from_result(result)

    assert answers[("english", 1)] == "A"
    assert answers[("english", 2)] == ""  # MULTIPLE -> blank
    assert answers[("english", 3)] == ""  # already blank
    assert answers[("mathematics", 1)] == "F"  # low-confidence, but a real answer -- not blanked


def test_output_base_name_appends_flag_suffix_only_when_flagged():
    scan = parse_scan_filename("Student, Jane 2027 ACT 25MC1 January 17 2026")
    assert output_base_name(scan, flagged=False) == "Student, Jane 2027 ACT 25MC1 January 17 2026"
    assert output_base_name(scan, flagged=True) == "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG"


def test_export_sheet_report_writes_only_the_pdf_when_not_flagged(tmp_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    prompt_fn = MagicMock(return_value="3/8/2026")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        outcome = export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    assert outcome.pdf_path == tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026.pdf"
    assert outcome.pdf_path.read_bytes() == b"%PDF-fake"
    assert outcome.xlsx_path is None
    assert not (tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026.xlsx").exists()

    kwargs = export_mock.call_args.kwargs
    assert kwargs["category_path"] == ["ACT", "Enhanced"]
    assert kwargs["test_code"] == "25MC1"
    assert kwargs["student_name"] == "Jane Student"
    # The prompted date, not the filename's own (parse_scan_filename(result.label).test_date
    # would be January 17 2026 here) -- see gui_prompt.py's own module docstring for why.
    assert kwargs["test_date"] == dt.date(2026, 3, 8)
    assert kwargs["output_name"] == "Student, Jane 2027 ACT 25MC1 January 17 2026"
    # output_name/canonical_filename still reads its own date from the
    # input filename, unchanged -- confirmed by the pdf_path assertion
    # above still naming the file after "January 17 2026", not "3/8/2026".


def test_export_sheet_report_also_writes_the_flagged_xlsx_when_the_sheet_has_review_items(tmp_path):
    questions = [QuestionResult("English", 1, "", [], {}, low_confidence=False)]  # blank -> has_review_items
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    assert result.has_review_items
    prompt_fn = MagicMock(return_value="3/8/2026")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake"):
        outcome = export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    assert outcome.pdf_path.name == "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG.pdf"
    assert outcome.xlsx_path is not None
    assert outcome.xlsx_path.name == "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG.xlsx"
    assert outcome.xlsx_path.exists()  # write_xlsx actually ran, not mocked


def test_export_sheet_report_uses_the_prompted_date_even_when_the_filename_has_no_day(tmp_path):
    """Confirmed this org's own filenames don't reliably carry a
    trustworthy test date even when they parse cleanly (see gui_prompt.py's
    own module docstring) -- the prompted date is what's written into the
    report regardless of whether the filename's own day is known at all;
    a missing day no longer falls back to a "Month Year" string the way
    scan.formatted_test_date used to produce."""
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 2026", questions)  # no day
    prompt_fn = MagicMock(return_value="3/8/2026")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    assert export_mock.call_args.kwargs["test_date"] == dt.date(2026, 3, 8)


def test_export_sheet_report_raises_when_the_date_prompt_is_cancelled(tmp_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    prompt_fn = MagicMock(return_value=None)  # Cancel button, or osascript unavailable

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake"):
        with pytest.raises(ValueError, match="[Nn]o test date"):
            export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)


def test_export_sheet_report_raises_a_clear_error_for_an_unrecognized_template(tmp_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 2026", questions, template_name="default_template")

    with pytest.raises(ValueError, match="isn't wired to"):
        export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path)
