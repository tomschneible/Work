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
