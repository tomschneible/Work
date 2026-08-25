"""See test_google_score_report_export.py's module docstring -- same
shape, SAT's counterpart."""
import datetime as dt
from unittest.mock import MagicMock, patch

from answer_extractor.google_sat_score_report_export import export_sat_score_report

_MODULE = "answer_extractor.google_sat_score_report_export"


def test_export_sat_score_report_delegates_to_export_filled_report_with_the_sat_category():
    with patch(f"{_MODULE}.export_filled_report", return_value=b"%PDF-final") as export_mock:
        result = export_sat_score_report(
            drive=MagicMock(),
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
    kwargs = export_mock.call_args
    assert kwargs[0][1] == "ROOT"
    assert kwargs[0][2] == ["SAT"]
    assert kwargs[0][3] == "8"
    assert kwargs[0][4] == "Jane Student - 2026-03-08"


def test_export_sat_score_report_fill_fn_calls_fill_sat_score_report_with_its_bound_arguments():
    with patch(f"{_MODULE}.export_filled_report") as export_mock:
        export_sat_score_report(
            drive=MagicMock(),
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

    with patch(f"{_MODULE}.fill_sat_score_report", return_value="FILLED") as fill_mock:
        result = fill_fn("/tmp/local.xlsx")

    assert result == "FILLED"
    fill_mock.assert_called_once_with(
        "/tmp/local.xlsx",
        {("math", "module1", 1): "A"},
        {"math": "harder"},
        "Jane Student",
        dt.datetime(2026, 3, 8),
        section_scores={"math": 620},
    )
