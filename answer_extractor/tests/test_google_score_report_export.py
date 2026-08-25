"""export_score_report just binds the ACT-specific fill step onto
export_filled_report -- the orchestration mechanics (call order, cleanup
on failure, ...) are export_filled_report's own concern and tested in
test_google_report_export_common.py; this only checks the delegation
itself: the right top-level args passed through, and fill_score_report
called correctly when the resulting fill_fn is invoked."""
import datetime as dt
from unittest.mock import MagicMock, patch

from answer_extractor.google_score_report_export import export_score_report

_MODULE = "answer_extractor.google_score_report_export"


def test_export_score_report_delegates_to_export_filled_report():
    with patch(f"{_MODULE}.export_filled_report", return_value=b"%PDF-final") as export_mock:
        result = export_score_report(
            drive=MagicMock(),
            templates_root_folder_id="ROOT",
            category_path=["ACT", "Enhanced"],
            test_code="25MC1",
            answers={("english", 1): "A"},
            student_name="Jane Student",
            test_date=dt.datetime(2026, 3, 4),
            output_name="Jane Student - 2026-03-04",
        )

    assert result == b"%PDF-final"
    kwargs = export_mock.call_args
    assert kwargs[0][1] == "ROOT"
    assert kwargs[0][2] == ["ACT", "Enhanced"]
    assert kwargs[0][3] == "25MC1"
    assert kwargs[0][4] == "Jane Student - 2026-03-04"


def test_export_score_report_fill_fn_calls_fill_score_report_with_its_bound_arguments():
    with patch(f"{_MODULE}.export_filled_report") as export_mock:
        export_score_report(
            drive=MagicMock(),
            templates_root_folder_id="ROOT",
            category_path=["ACT", "Enhanced"],
            test_code="25MC1",
            answers={("english", 1): "A"},
            student_name="Jane Student",
            test_date=dt.datetime(2026, 3, 4),
            output_name="Jane Student - 2026-03-04",
        )
        fill_fn = export_mock.call_args.kwargs["fill_fn"]

    with patch(f"{_MODULE}.fill_score_report", return_value="FILLED") as fill_mock:
        result = fill_fn("/tmp/local.xlsx")

    assert result == "FILLED"
    fill_mock.assert_called_once_with("/tmp/local.xlsx", {("english", 1): "A"}, "Jane Student", dt.datetime(2026, 3, 4))
