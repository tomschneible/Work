"""Pure logic tests for google_sheets_export's Drive/Sheets API
call-shaping -- a mocked `drive`/`sheets` service throughout, checking
that each function builds the request Drive/Sheets actually needs
(Shared Drive support flags, correct body/ids) rather than exercising any
real network call."""
import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.google_sheets_export import (
    CellWrite,
    clear_cells,
    copy_template,
    delete_file,
    delete_rows,
    export_pdf,
    export_xlsx,
    format_date_for_sheets,
    hide_columns,
    hide_gridlines,
    list_folder,
    narrow_columns,
    replace_content,
    write_cells,
)


def test_list_folder_queries_by_parent_and_requests_shared_drive_support():
    drive = MagicMock()
    drive.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "f1", "name": "Template", "mimeType": "application/vnd.google-apps.spreadsheet"}]
    }

    result = list_folder(drive, "FOLDER123")

    assert result == [{"id": "f1", "name": "Template", "mimeType": "application/vnd.google-apps.spreadsheet"}]
    _, kwargs = drive.files.return_value.list.call_args
    assert kwargs["q"] == "'FOLDER123' in parents and trashed = false"
    assert kwargs["supportsAllDrives"] is True
    assert kwargs["includeItemsFromAllDrives"] is True
    assert kwargs["corpora"] == "allDrives"


def test_list_folder_follows_every_page():
    drive = MagicMock()
    drive.files.return_value.list.return_value.execute.side_effect = [
        {"files": [{"id": "a", "name": "A", "mimeType": "x"}], "nextPageToken": "page2"},
        {"files": [{"id": "b", "name": "B", "mimeType": "x"}]},
    ]

    result = list_folder(drive, "FOLDER123")

    assert [f["id"] for f in result] == ["a", "b"]
    calls = drive.files.return_value.list.call_args_list
    assert calls[0].kwargs["pageToken"] is None
    assert calls[1].kwargs["pageToken"] == "page2"


def test_copy_template_names_the_copy_and_requests_shared_drive_support():
    drive = MagicMock()
    drive.files.return_value.copy.return_value.execute.return_value = {"id": "NEW_ID"}

    result = copy_template(drive, "TEMPLATE_ID", "Jane Student - 2026-08-19")

    assert result == "NEW_ID"
    _, kwargs = drive.files.return_value.copy.call_args
    assert kwargs["fileId"] == "TEMPLATE_ID"
    assert kwargs["body"] == {"name": "Jane Student - 2026-08-19"}
    assert kwargs["supportsAllDrives"] is True


def test_copy_template_places_the_copy_in_a_parent_folder_when_given_one():
    drive = MagicMock()
    drive.files.return_value.copy.return_value.execute.return_value = {"id": "NEW_ID"}

    copy_template(drive, "TEMPLATE_ID", "Report", parent_folder_id="FOLDER123")

    _, kwargs = drive.files.return_value.copy.call_args
    assert kwargs["body"] == {"name": "Report", "parents": ["FOLDER123"]}


def test_copy_template_omits_parents_when_no_folder_is_given():
    drive = MagicMock()
    drive.files.return_value.copy.return_value.execute.return_value = {"id": "NEW_ID"}

    copy_template(drive, "TEMPLATE_ID", "Report")

    _, kwargs = drive.files.return_value.copy.call_args
    assert "parents" not in kwargs["body"]


def test_export_pdf_returns_the_downloaded_bytes():
    """Uses Sheets' own dedicated export URL (via an AuthorizedSession),
    not Drive's generic files.export -- see export_pdf's own docstring
    for why."""
    fake_response = MagicMock()
    fake_response.headers = {"Content-Type": "application/pdf"}
    fake_response.content = b"%PDF-fake-content"
    fake_session = MagicMock()
    fake_session.get.return_value = fake_response

    with patch("answer_extractor.google_sheets_export.get_credentials", return_value="CREDS"), \
         patch("answer_extractor.google_sheets_export.AuthorizedSession", return_value=fake_session) as session_cls:
        result = export_pdf("FILE_ID")

    assert result == b"%PDF-fake-content"
    session_cls.assert_called_once_with("CREDS")
    fake_response.raise_for_status.assert_called_once()
    _, kwargs = fake_session.get.call_args
    assert fake_session.get.call_args[0][0] == "https://docs.google.com/spreadsheets/d/FILE_ID/export"
    assert kwargs["params"] == {"format": "pdf"}


def test_export_pdf_raises_if_the_response_is_not_actually_a_pdf():
    """This endpoint can return a 200 with an HTML error/login page
    instead of a clean HTTP error for some failure modes -- confirm that
    gets caught rather than silently treated as a valid PDF."""
    fake_response = MagicMock()
    fake_response.headers = {"Content-Type": "text/html; charset=UTF-8"}
    fake_response.content = b"<html>not a pdf</html>"
    fake_session = MagicMock()
    fake_session.get.return_value = fake_response

    with patch("answer_extractor.google_sheets_export.get_credentials", return_value="CREDS"), \
         patch("answer_extractor.google_sheets_export.AuthorizedSession", return_value=fake_session):
        with pytest.raises(RuntimeError, match="text/html"):
            export_pdf("FILE_ID")


def test_export_xlsx_requests_the_xlsx_mime_type():
    drive = MagicMock()
    drive.files.return_value.export_media.return_value = MagicMock()

    downloader = MagicMock()
    downloader.next_chunk.return_value = (None, True)

    with patch("answer_extractor.google_sheets_export.MediaIoBaseDownload", return_value=downloader):
        export_xlsx(drive, "FILE_ID")

    drive.files.return_value.export_media.assert_called_once_with(
        fileId="FILE_ID",
        mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def test_replace_content_uploads_the_local_file_with_shared_drive_support(tmp_path):
    drive = MagicMock()
    local_path = tmp_path / "filled.xlsx"
    local_path.write_bytes(b"fake xlsx bytes")

    with patch("answer_extractor.google_sheets_export.MediaFileUpload") as media_cls:
        media_cls.return_value = "MEDIA"
        replace_content(drive, "FILE_ID", str(local_path))

    media_cls.assert_called_once_with(
        str(local_path), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    drive.files.return_value.update.assert_called_once_with(
        fileId="FILE_ID", media_body="MEDIA", supportsAllDrives=True
    )


def test_delete_file_requests_shared_drive_support():
    drive = MagicMock()

    delete_file(drive, "FILE_ID")

    drive.files.return_value.delete.assert_called_once_with(fileId="FILE_ID", supportsAllDrives=True)


def test_format_date_for_sheets_uses_iso_format_for_a_real_date():
    assert format_date_for_sheets(dt.date(2026, 1, 17)) == "2026-01-17"


def test_format_date_for_sheets_passes_a_string_through_unchanged():
    assert format_date_for_sheets("January 2026") == "January 2026"


def test_write_cells_batches_every_cell_as_its_own_single_cell_range():
    sheets = MagicMock()
    cells = [
        CellWrite(sheet="ScoreSheet", row=5, column=3, value="C"),
        CellWrite(sheet="ScoreSheet", row=6, column=1, value=42),
        CellWrite(sheet="Student Responses", row=2, column=2, value=True),
    ]

    write_cells(sheets, "SPREADSHEET_ID", cells)

    sheets.spreadsheets.return_value.values.return_value.batchUpdate.assert_called_once()
    _, kwargs = sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args
    assert kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    body = kwargs["body"]
    assert body["valueInputOption"] == "USER_ENTERED"
    assert body["data"] == [
        {"range": "'ScoreSheet'!C5", "values": [["C"]]},
        {"range": "'ScoreSheet'!A6", "values": [[42]]},
        {"range": "'Student Responses'!B2", "values": [[True]]},
    ]
    sheets.spreadsheets.return_value.values.return_value.batchUpdate.return_value.execute.assert_called_once()


def test_write_cells_turns_a_none_value_into_an_empty_string():
    sheets = MagicMock()
    write_cells(sheets, "SPREADSHEET_ID", [CellWrite(sheet="ScoreSheet", row=1, column=1, value=None)])

    _, kwargs = sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args
    assert kwargs["body"]["data"] == [{"range": "'ScoreSheet'!A1", "values": [[""]]}]


def test_write_cells_is_a_no_op_for_an_empty_list():
    sheets = MagicMock()
    write_cells(sheets, "SPREADSHEET_ID", [])

    sheets.spreadsheets.assert_not_called()


def test_hide_gridlines_updates_every_sheet_that_still_has_them_showing():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": 111, "title": "Cover Page", "gridProperties": {}}},
            {"properties": {"sheetId": 222, "title": "ScoreSheet", "gridProperties": {"hideGridlines": False}}},
        ]
    }

    hide_gridlines(sheets, "SPREADSHEET_ID")

    sheets.spreadsheets.return_value.get.assert_called_once_with(
        spreadsheetId="SPREADSHEET_ID", fields="sheets.properties"
    )
    _, kwargs = sheets.spreadsheets.return_value.batchUpdate.call_args
    assert kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    assert kwargs["body"]["requests"] == [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": 111, "gridProperties": {"hideGridlines": True}},
                "fields": "gridProperties.hideGridlines",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": 222, "gridProperties": {"hideGridlines": True}},
                "fields": "gridProperties.hideGridlines",
            }
        },
    ]
    sheets.spreadsheets.return_value.batchUpdate.return_value.execute.assert_called_once()


def test_hide_gridlines_skips_a_sheet_that_already_has_them_hidden():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"sheetId": 111, "gridProperties": {"hideGridlines": True}}}]
    }

    hide_gridlines(sheets, "SPREADSHEET_ID")

    sheets.spreadsheets.return_value.batchUpdate.assert_not_called()


def test_hide_gridlines_makes_no_batch_update_call_when_nothing_needs_changing():
    """Every sheet already has gridlines hidden -- a no-op, not even an
    empty batchUpdate request."""
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": 111, "gridProperties": {"hideGridlines": True}}},
            {"properties": {"sheetId": 222, "gridProperties": {"hideGridlines": True}}},
        ]
    }

    hide_gridlines(sheets, "SPREADSHEET_ID")

    assert not sheets.spreadsheets.return_value.batchUpdate.called


def test_clear_cells_resolves_sheet_names_and_sends_one_batch_update():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": 111, "title": "Student Responses"}},
            {"properties": {"sheetId": 222, "title": "Cover Page"}},
        ]
    }

    clear_cells(
        sheets,
        "SPREADSHEET_ID",
        [("Student Responses", 5, 30, 10, 14), ("Cover Page", 0, 1, 0, 4)],
    )

    sheets.spreadsheets.return_value.get.assert_called_once_with(
        spreadsheetId="SPREADSHEET_ID", fields="sheets.properties"
    )
    _, kwargs = sheets.spreadsheets.return_value.batchUpdate.call_args
    assert kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    assert kwargs["body"]["requests"] == [
        {
            "repeatCell": {
                "range": {
                    "sheetId": 111,
                    "startRowIndex": 5,
                    "endRowIndex": 30,
                    "startColumnIndex": 10,
                    "endColumnIndex": 14,
                },
                "cell": {},
                "fields": "userEnteredValue,userEnteredFormat.borders,dataValidation",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": 222,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "cell": {},
                "fields": "userEnteredValue,userEnteredFormat.borders,dataValidation",
            }
        },
    ]
    sheets.spreadsheets.return_value.batchUpdate.return_value.execute.assert_called_once()


def test_clear_cells_is_a_no_op_for_an_empty_list():
    sheets = MagicMock()

    clear_cells(sheets, "SPREADSHEET_ID", [])

    sheets.spreadsheets.assert_not_called()


def test_clear_cells_raises_for_an_unknown_sheet_name():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"sheetId": 111, "title": "Student Responses"}}]
    }

    with pytest.raises(ValueError, match="Cover Page"):
        clear_cells(sheets, "SPREADSHEET_ID", [("Cover Page", 0, 1, 0, 4)])


def test_hide_columns_resolves_sheet_names_and_sends_one_batch_update():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": 111, "title": "Student Responses"}},
            {"properties": {"sheetId": 222, "title": "Cover Page"}},
        ]
    }

    hide_columns(
        sheets,
        "SPREADSHEET_ID",
        [("Student Responses", 14, 20), ("Cover Page", 0, 4)],
    )

    sheets.spreadsheets.return_value.get.assert_called_once_with(
        spreadsheetId="SPREADSHEET_ID", fields="sheets.properties"
    )
    _, kwargs = sheets.spreadsheets.return_value.batchUpdate.call_args
    assert kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    assert kwargs["body"]["requests"] == [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": 111,
                    "dimension": "COLUMNS",
                    "startIndex": 14,
                    "endIndex": 20,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": 222,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 4,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
    ]
    sheets.spreadsheets.return_value.batchUpdate.return_value.execute.assert_called_once()


def test_hide_columns_is_a_no_op_for_an_empty_list():
    sheets = MagicMock()

    hide_columns(sheets, "SPREADSHEET_ID", [])

    sheets.spreadsheets.assert_not_called()


def test_hide_columns_raises_for_an_unknown_sheet_name():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"sheetId": 111, "title": "Student Responses"}}]
    }

    with pytest.raises(ValueError, match="Cover Page"):
        hide_columns(sheets, "SPREADSHEET_ID", [("Cover Page", 0, 4)])


def test_delete_rows_resolves_sheet_names_and_sends_one_batch_update():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": 111, "title": "Student Responses"}},
            {"properties": {"sheetId": 222, "title": "Cover Page"}},
        ]
    }

    delete_rows(
        sheets,
        "SPREADSHEET_ID",
        [("Student Responses", 64, 996), ("Cover Page", 60, 61)],
    )

    sheets.spreadsheets.return_value.get.assert_called_once_with(
        spreadsheetId="SPREADSHEET_ID", fields="sheets.properties"
    )
    _, kwargs = sheets.spreadsheets.return_value.batchUpdate.call_args
    assert kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    assert kwargs["body"]["requests"] == [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": 111,
                    "dimension": "ROWS",
                    "startIndex": 64,
                    "endIndex": 996,
                },
            }
        },
        {
            "deleteDimension": {
                "range": {
                    "sheetId": 222,
                    "dimension": "ROWS",
                    "startIndex": 60,
                    "endIndex": 61,
                },
            }
        },
    ]
    sheets.spreadsheets.return_value.batchUpdate.return_value.execute.assert_called_once()


def test_delete_rows_is_a_no_op_for_an_empty_list():
    sheets = MagicMock()

    delete_rows(sheets, "SPREADSHEET_ID", [])

    sheets.spreadsheets.assert_not_called()


def test_delete_rows_raises_for_an_unknown_sheet_name():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"sheetId": 111, "title": "Student Responses"}}]
    }

    with pytest.raises(ValueError, match="Cover Page"):
        delete_rows(sheets, "SPREADSHEET_ID", [("Cover Page", 0, 4)])


def test_narrow_columns_reads_current_widths_and_shrinks_each_by_its_own_factor():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.side_effect = [
        # first call: resolve sheet name -> sheetId
        {"sheets": [{"properties": {"sheetId": 111, "title": "Student Responses"}}]},
        # second call: current pixel widths for H1:J1
        {
            "sheets": [
                {
                    "properties": {"sheetId": 111},
                    "data": [{"columnMetadata": [{"pixelSize": 100}, {"pixelSize": 80}, {"pixelSize": 60}]}],
                }
            ]
        },
    ]

    narrow_columns(sheets, "SPREADSHEET_ID", [("Student Responses", 7, 10, 0.75)])

    get_calls = sheets.spreadsheets.return_value.get.call_args_list
    assert get_calls[0].kwargs == {"spreadsheetId": "SPREADSHEET_ID", "fields": "sheets.properties"}
    assert get_calls[1].kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    assert get_calls[1].kwargs["ranges"] == ["'Student Responses'!H1:J1"]
    assert get_calls[1].kwargs["fields"] == "sheets.properties.sheetId,sheets.data.columnMetadata.pixelSize"

    _, kwargs = sheets.spreadsheets.return_value.batchUpdate.call_args
    assert kwargs["spreadsheetId"] == "SPREADSHEET_ID"
    assert kwargs["body"]["requests"] == [
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 111, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 8},
                "properties": {"pixelSize": 75},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 111, "dimension": "COLUMNS", "startIndex": 8, "endIndex": 9},
                "properties": {"pixelSize": 60},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 111, "dimension": "COLUMNS", "startIndex": 9, "endIndex": 10},
                "properties": {"pixelSize": 45},
                "fields": "pixelSize",
            }
        },
    ]
    sheets.spreadsheets.return_value.batchUpdate.return_value.execute.assert_called_once()


def test_narrow_columns_skips_a_column_with_no_reported_pixel_size():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.side_effect = [
        {"sheets": [{"properties": {"sheetId": 111, "title": "Student Responses"}}]},
        {
            "sheets": [
                {
                    "properties": {"sheetId": 111},
                    "data": [{"columnMetadata": [{"pixelSize": 100}, {}]}],
                }
            ]
        },
    ]

    narrow_columns(sheets, "SPREADSHEET_ID", [("Student Responses", 7, 9, 0.75)])

    _, kwargs = sheets.spreadsheets.return_value.batchUpdate.call_args
    assert kwargs["body"]["requests"] == [
        {
            "updateDimensionProperties": {
                "range": {"sheetId": 111, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 8},
                "properties": {"pixelSize": 75},
                "fields": "pixelSize",
            }
        },
    ]


def test_narrow_columns_is_a_no_op_for_an_empty_list():
    sheets = MagicMock()

    narrow_columns(sheets, "SPREADSHEET_ID", [])

    sheets.spreadsheets.assert_not_called()


def test_narrow_columns_raises_for_an_unknown_sheet_name():
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"sheetId": 111, "title": "Student Responses"}}]
    }

    with pytest.raises(ValueError, match="Cover Page"):
        narrow_columns(sheets, "SPREADSHEET_ID", [("Cover Page", 0, 4, 0.75)])
