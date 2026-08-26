"""Tests for google_sheets_cli's `hide-gridlines` command -- `list-folder`
and the OAuth-consent completion are thin, interactive/setup-only
wrappers not covered here (see the module's own docstring)."""
from unittest.mock import MagicMock, patch

from answer_extractor.google_sheets_cli import main

_MODULE = "answer_extractor.google_sheets_cli"


def test_hide_gridlines_command_calls_through_with_the_given_file_id():
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines") as hide_mock:
        exit_code = main(["hide-gridlines", "--file-id", "TEMPLATE_ID"])

    assert exit_code == 0
    assert hide_mock.call_args[0][1] == "TEMPLATE_ID"


def test_hide_gridlines_command_passes_the_sheets_service_not_drive():
    sheets_service = MagicMock(name="sheets-service")
    drive_service = MagicMock(name="drive-service")
    with patch(f"{_MODULE}.build_services", return_value=(drive_service, sheets_service)), \
         patch(f"{_MODULE}.hide_gridlines") as hide_mock:
        main(["hide-gridlines", "--file-id", "TEMPLATE_ID"])

    assert hide_mock.call_args[0][0] is sheets_service


def test_hide_gridlines_command_accepts_multiple_file_ids_in_one_call():
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines") as hide_mock:
        exit_code = main(["hide-gridlines", "--file-id", "ID_ONE", "ID_TWO", "ID_THREE"])

    assert exit_code == 0
    assert [call.args[1] for call in hide_mock.call_args_list] == ["ID_ONE", "ID_TWO", "ID_THREE"]


def test_hide_gridlines_command_keeps_going_past_one_files_failure_and_reports_it(capsys):
    def _fake_hide(sheets, file_id):
        if file_id == "BAD_ID":
            raise ValueError("nope")

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines", side_effect=_fake_hide) as hide_mock:
        exit_code = main(["hide-gridlines", "--file-id", "GOOD_ID", "BAD_ID"])

    assert exit_code == 1  # at least one failure -- worth a non-zero exit
    assert [call.args[1] for call in hide_mock.call_args_list] == ["GOOD_ID", "BAD_ID"]
    err = capsys.readouterr().err
    assert "BAD_ID" in err and "nope" in err


def test_hide_gridlines_command_prints_no_summary_line_for_a_single_file(capsys):
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines"):
        main(["hide-gridlines", "--file-id", "TEMPLATE_ID"])

    assert "succeeded" not in capsys.readouterr().out


def test_hide_gridlines_command_prints_a_summary_line_for_multiple_files(capsys):
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines"):
        main(["hide-gridlines", "--file-id", "ID_ONE", "ID_TWO"])

    assert "2/2 succeeded." in capsys.readouterr().out
