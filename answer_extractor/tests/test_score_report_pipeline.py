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
    assert should_export_to_sheets(_result("x", [], template_name="act_j_form_answer_sheet"))


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
    prompt_fn = MagicMock(return_value="1/20/2026")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        outcome = export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    # Everything comes from the typed date -- the report, and every output's
    # name -- not the filename's own January 17.
    assert outcome.pdf_path == tmp_path / "Student, Jane 2027 ACT 25MC1 January 20 2026.pdf"
    assert outcome.pdf_path.read_bytes() == b"%PDF-fake"
    assert outcome.xlsx_path is None
    assert not (tmp_path / "Student, Jane 2027 ACT 25MC1 January 20 2026.xlsx").exists()

    kwargs = export_mock.call_args.kwargs
    assert kwargs["category_path"] == ["ACT", "Enhanced"]
    assert kwargs["test_code"] == "25MC1"
    assert kwargs["student_name"] == "Jane Student"
    assert kwargs["test_date"] == dt.date(2026, 1, 20)
    assert kwargs["output_name"] == "Student, Jane 2027 ACT 25MC1 January 20 2026"
    assert kwargs["copy_folder_id"] is None  # no report_folders given -- Drive's own default


@pytest.mark.parametrize("typed", ["2/17/2026", "1/17/2025"])
def test_export_sheet_report_rejects_a_date_in_another_month_than_the_file_is_named_for(tmp_path, typed):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 2026", questions)

    expected = r"but the file is named January 2026 -- are you sure the file is named correctly\?"
    with patch(f"{_MODULE}.export_score_report") as export_mock:
        with pytest.raises(ValueError, match=expected):
            export_sheet_report(
                MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=MagicMock(return_value=typed)
            )
    export_mock.assert_not_called()


def test_export_sheet_report_accepts_a_filename_with_an_initial_and_a_c(tmp_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane M. 2027C ACT 25MC1 January 17 2026", questions)

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        outcome = export_sheet_report(
            MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=MagicMock(return_value="1/17/2026")
        )

    assert export_mock.call_args.kwargs["student_name"] == "Jane M. Student"
    assert export_mock.call_args.kwargs["test_code"] == "25MC1"
    assert outcome.pdf_path.name == "Student, Jane M. 2027C ACT 25MC1 January 17 2026.pdf"


def test_export_sheet_report_uses_the_real_enhanced_category_path_for_the_j_form_template(tmp_path):
    """act_j_form_answer_sheet's own real-world Drive templates live in
    "Real Enhanced", a sibling of Enhanced/Legacy directly under ACT --
    not this pipeline's own naming, the org's actual Drive folder name."""
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT J01 January 17 2026", questions, template_name="act_j_form_answer_sheet")
    prompt_fn = MagicMock(return_value="1/17/2026")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    assert export_mock.call_args.kwargs["category_path"] == ["ACT", "Real Enhanced"]


def test_export_sheet_report_also_writes_the_flagged_xlsx_when_the_sheet_has_review_items(tmp_path):
    questions = [QuestionResult("English", 1, "", [], {}, low_confidence=False)]  # blank -> has_review_items
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    assert result.has_review_items
    prompt_fn = MagicMock(return_value="1/17/2026")

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
    prompt_fn = MagicMock(return_value="1/8/2026")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        outcome = export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    assert export_mock.call_args.kwargs["test_date"] == dt.date(2026, 1, 8)
    assert outcome.pdf_path.name == "Student, Jane 2027 ACT 25MC1 January 8 2026.pdf"  # the typed day, too


def test_export_sheet_report_test_mode_skips_the_date(tmp_path):
    """Typing "test" (any casing) at the date prompt leaves the date cell
    out entirely -- gui_prompt.SKIP, translated to plain None -- rather
    than failing the way a cancelled prompt does."""
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    prompt_fn = MagicMock(return_value="TEST")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn)

    assert export_mock.call_args.kwargs["test_date"] is None


@pytest.mark.parametrize("typed, folder_date", [("1/17/2026", dt.date(2026, 1, 17)), ("test", None)])
def test_export_sheet_report_files_the_sheet_by_the_prompted_date(tmp_path, typed, folder_date):
    # The prompted date picks the folder, not the filename's own January 17;
    # test mode's missing date is ReportFolders' cue to use Temporary Files.
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    report_folders = MagicMock()
    report_folders.folder_for.return_value = "DAY_FOLDER_ID"

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake") as export_mock:
        export_sheet_report(
            MagicMock(), MagicMock(), "ROOT", result, tmp_path,
            prompt_fn=MagicMock(return_value=typed), report_folders=report_folders,
        )

    report_folders.folder_for.assert_called_once_with(folder_date, "Jane Student")
    assert export_mock.call_args.kwargs["copy_folder_id"] == "DAY_FOLDER_ID"
    assert report_folders.save_copies.called == (folder_date is not None)  # a test run uploads no copies


def _scanned_result(scan_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    return SheetResult(
        label=scan_path.stem, source=str(scan_path), used_contour_alignment=False, questions=questions,
        template_name="act_answer_sheet",
    )


def test_export_sheet_report_saves_copies_of_the_scan_and_report(tmp_path):
    scan_path = tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026 Test Scan & Bubble.pdf"
    report_folders = MagicMock()
    report_folders.folder_for.return_value = "DAY_FOLDER_ID"

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake"):
        export_sheet_report(
            MagicMock(), MagicMock(), "ROOT", _scanned_result(scan_path), tmp_path,
            prompt_fn=MagicMock(return_value="1/17/2026"), report_folders=report_folders,
        )

    report_folders.save_copies.assert_called_once_with(
        "DAY_FOLDER_ID", scan_path, "Student, Jane 2027 ACT 25MC1 January 17 2026.pdf", b"%PDF-fake", "Jane Student"
    )


def test_export_sheet_report_numbers_the_pdf_instead_of_overwriting_the_scan_it_came_from(tmp_path):
    # A scan dropped from the Desktop under exactly its report's name.
    scan_path = tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026.pdf"
    scan_path.write_bytes(b"scan bytes")

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake"):
        outcome = export_sheet_report(
            MagicMock(), MagicMock(), "ROOT", _scanned_result(scan_path), tmp_path,
            prompt_fn=MagicMock(return_value="1/17/2026"),
        )

    assert scan_path.read_bytes() == b"scan bytes"
    assert outcome.pdf_path == tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026 (2).pdf"
    assert outcome.pdf_path.read_bytes() == b"%PDF-fake"


def test_export_sheet_report_numbers_a_flagged_reports_pdf_and_xlsx_as_a_pair(tmp_path):
    # Only the .xlsx is left over from an earlier run -- the new PDF still
    # takes the same number as its .xlsx, not the free unnumbered name.
    (tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG.xlsx").write_bytes(b"earlier run")
    questions = [QuestionResult("English", 1, "", [], {}, low_confidence=False)]  # blank -> flagged
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake"):
        outcome = export_sheet_report(
            MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=MagicMock(return_value="1/17/2026")
        )

    assert outcome.pdf_path.name == "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG (2).pdf"
    assert outcome.xlsx_path.name == "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG (2).xlsx"
    assert (tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG.xlsx").read_bytes() == b"earlier run"


def test_export_sheet_report_raises_when_the_date_prompt_is_cancelled(tmp_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 17 2026", questions)
    prompt_fn = MagicMock(return_value=None)  # Cancel button, or osascript unavailable
    report_folders = MagicMock()

    with patch(f"{_MODULE}.export_score_report", return_value=b"%PDF-fake"):
        with pytest.raises(ValueError, match="[Nn]o test date"):
            export_sheet_report(
                MagicMock(), MagicMock(), "ROOT", result, tmp_path, prompt_fn=prompt_fn, report_folders=report_folders
            )
    report_folders.folder_for.assert_not_called()  # no folder made for a report that was never produced


def test_export_sheet_report_raises_a_clear_error_for_an_unrecognized_template(tmp_path):
    questions = [QuestionResult("English", 1, "A", ["A"], {}, low_confidence=False)]
    result = _result("Student, Jane 2027 ACT 25MC1 January 2026", questions, template_name="default_template")

    with pytest.raises(ValueError, match="isn't wired to"):
        export_sheet_report(MagicMock(), MagicMock(), "ROOT", result, tmp_path)
