"""See test_google_sat_score_report_export.py's module docstring -- same
shape, plus coverage for the extra reference-template download this
path needs that the current-format one doesn't."""
import datetime as dt
import io
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

from answer_extractor.google_sat_simplified_score_report_export import (
    SIMPLIFIED_TEMPLATE_CATEGORY_PATH,
    SIMPLIFIED_TEMPLATE_NAME,
    _load_reference_worksheet,
    export_simple_sat_score_report,
)

_MODULE = "answer_extractor.google_sat_simplified_score_report_export"


def _xlsx_bytes(sheet_names) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.title = sheet_names[0]
    for name in sheet_names[1:]:
        wb.create_sheet(name)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_load_reference_worksheet_downloads_the_current_format_template_read_only():
    with patch(f"{_MODULE}.resolve_template_folder", return_value="SAT_FOLDER") as resolve_mock, \
         patch(f"{_MODULE}.find_template_file", return_value={"id": "REF_ID", "name": "DSAT 8"}) as find_mock, \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes(["Student Responses"])) as export_mock:
        ws = _load_reference_worksheet(MagicMock(), "ROOT", "8", "Student Responses")

    assert resolve_mock.call_args[0][1] == "ROOT"
    assert resolve_mock.call_args[0][2] == ["SAT"]
    assert find_mock.call_args[0][2] == "8"
    assert export_mock.call_args[0][1] == "REF_ID"
    assert ws.title == "Student Responses"


def test_load_reference_worksheet_raises_when_sheet_name_is_missing():
    with patch(f"{_MODULE}.resolve_template_folder", return_value="SAT_FOLDER"), \
         patch(f"{_MODULE}.find_template_file", return_value={"id": "REF_ID", "name": "DSAT 8"}), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes(["Some Other Tab"])):
        with pytest.raises(ValueError, match="Student Responses"):
            _load_reference_worksheet(MagicMock(), "ROOT", "8", "Student Responses")


def test_export_simple_sat_score_report_finds_the_one_simplified_template_by_exact_name():
    fake_ws = object()
    with patch(f"{_MODULE}._load_reference_worksheet", return_value=fake_ws) as load_ref_mock, \
         patch(f"{_MODULE}.resolve_template_folder", return_value="SIMPLIFIED_FOLDER") as resolve_mock, \
         patch(f"{_MODULE}.find_file_by_exact_name", return_value={"id": "SIMPLE_ID", "name": SIMPLIFIED_TEMPLATE_NAME}) as find_mock, \
         patch(f"{_MODULE}.export_filled_report", return_value=b"%PDF-final") as export_mock:
        result = export_simple_sat_score_report(
            drive=MagicMock(),
            sheets=MagicMock(),
            templates_root_folder_id="ROOT",
            test_code="8",
            answers={("math", "module1", 1): "A"},
            active_variants={"math": "harder"},
            student_name="Jane Student",
            test_date=dt.datetime(2026, 3, 8),
            section_scores={"math": 620},
            output_name="Jane Student - 2026-03-08",
        )

    assert result == b"%PDF-final"
    assert load_ref_mock.call_args[0][1] == "ROOT"
    assert load_ref_mock.call_args[0][2] == "8"
    assert load_ref_mock.call_args[0][3] == "Student Responses"

    # The simplified template is found by exact name in its own
    # subfolder -- never by test_code substring matching.
    assert resolve_mock.call_args[0][1] == "ROOT"
    assert resolve_mock.call_args[0][2] == SIMPLIFIED_TEMPLATE_CATEGORY_PATH
    assert find_mock.call_args[0][2] == SIMPLIFIED_TEMPLATE_NAME

    # export_filled_report gets template_id directly, not a lookup triple.
    export_kwargs = export_mock.call_args.kwargs
    assert export_kwargs["templates_root_folder_id"] is None
    assert export_kwargs["category_path"] is None
    assert export_kwargs["test_code"] is None
    assert export_kwargs["template_id"] == "SIMPLE_ID"
    assert export_kwargs["output_name"] == "Jane Student - 2026-03-08"


def test_export_simple_sat_score_report_fill_fn_calls_fill_simple_sat_score_report_with_its_bound_arguments():
    fake_ws = object()
    with patch(f"{_MODULE}._load_reference_worksheet", return_value=fake_ws), \
         patch(f"{_MODULE}.resolve_template_folder", return_value="SIMPLIFIED_FOLDER"), \
         patch(f"{_MODULE}.find_file_by_exact_name", return_value={"id": "SIMPLE_ID", "name": SIMPLIFIED_TEMPLATE_NAME}), \
         patch(f"{_MODULE}.export_filled_report") as export_mock:
        export_simple_sat_score_report(
            drive=MagicMock(),
            sheets=MagicMock(),
            templates_root_folder_id="ROOT",
            test_code="8",
            answers={("math", "module1", 1): "A"},
            active_variants={"math": "harder"},
            student_name="Jane Student",
            test_date=dt.datetime(2026, 3, 8),
            section_scores={"math": 620},
            output_name="Jane Student - 2026-03-08",
        )
        fill_fn = export_mock.call_args.kwargs["fill_fn"]

    with patch(f"{_MODULE}.fill_simple_sat_score_report", return_value="FILLED") as fill_mock:
        result = fill_fn("/tmp/local.xlsx")

    assert result == "FILLED"
    fill_mock.assert_called_once_with(
        "/tmp/local.xlsx",
        fake_ws,
        {("math", "module1", 1): "A"},
        {"math": "harder"},
        "Jane Student",
        dt.datetime(2026, 3, 8),
        section_scores={"math": 620},
        sheet_name="Student Responses",
    )
