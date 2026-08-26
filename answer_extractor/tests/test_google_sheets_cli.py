"""Tests for google_sheets_cli's `tighten-print-area` command --
`list-folder` and the OAuth-consent completion are thin, interactive/setup-
only wrappers not covered here (see the module's own docstring)."""
from unittest.mock import MagicMock, patch

import openpyxl

from answer_extractor.google_sheets_cli import main

_MODULE = "answer_extractor.google_sheets_cli"


def _template_xlsx_bytes() -> bytes:
    """A tiny workbook shaped like the real "Cover Page" bug: real content
    bounded to a small range, but row-height formatting (and therefore,
    per tighten_print_areas, an implicit "print everything up to here")
    extending well past it."""
    import io

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cover Page"
    ws["B7"] = "NAME:"
    for row in range(1, 50):
        ws.row_dimensions[row].height = 15.75
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_tighten_print_area_downloads_edits_and_pushes_back_onto_the_same_file_id():
    captured = {}

    def _capture_replace_content(drive, file_id, local_path):
        captured["file_id"] = file_id
        loaded = openpyxl.load_workbook(local_path)
        captured["print_area"] = loaded["Cover Page"].print_area
        captured["gridlines"] = loaded["Cover Page"].sheet_view.showGridLines

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_template_xlsx_bytes()) as export_mock, \
         patch(f"{_MODULE}.replace_content", side_effect=_capture_replace_content):
        exit_code = main(["tighten-print-area", "--file-id", "TEMPLATE_ID"])

    assert exit_code == 0
    assert export_mock.call_args[0][1] == "TEMPLATE_ID"

    # replace_content pushed the fix back onto the *same* file id -- never
    # a copy -- and the local file it was handed actually has the fix.
    assert captured["file_id"] == "TEMPLATE_ID"
    assert captured["print_area"] == "'Cover Page'!$B$7"
    assert captured["gridlines"] is False


def test_tighten_print_area_cleans_up_its_local_temp_file():
    written_paths = []
    real_open = open

    def _tracking_open(path, mode="r", *args, **kwargs):
        if "xlsx" in str(path) and "b" in mode:
            written_paths.append(str(path))
        return real_open(path, mode, *args, **kwargs)

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_template_xlsx_bytes()), \
         patch(f"{_MODULE}.replace_content"), \
         patch("builtins.open", side_effect=_tracking_open):
        main(["tighten-print-area", "--file-id", "TEMPLATE_ID"])

    assert len(written_paths) == 1
    import os

    assert not os.path.exists(written_paths[0])
