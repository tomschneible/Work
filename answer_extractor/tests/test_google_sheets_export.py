"""Pure logic tests for google_sheets_export's Drive API call-shaping --
a mocked `drive` service throughout, checking that each function builds
the request Drive actually needs (Shared Drive support flags, correct
body/ids) rather than exercising any real network call."""
from unittest.mock import MagicMock, patch

from answer_extractor.google_sheets_export import copy_template, delete_file, export_pdf, list_folder


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

    result = copy_template(drive, "TEMPLATE_ID", "Anne Studnicky - 2026-08-19")

    assert result == "NEW_ID"
    _, kwargs = drive.files.return_value.copy.call_args
    assert kwargs["fileId"] == "TEMPLATE_ID"
    assert kwargs["body"] == {"name": "Anne Studnicky - 2026-08-19"}
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
    drive = MagicMock()
    request = MagicMock()
    drive.files.return_value.export_media.return_value = request

    def _fake_downloader(buffer, req):
        assert req is request
        buffer.write(b"%PDF-fake-content")
        downloader = MagicMock()
        downloader.next_chunk.return_value = (None, True)
        return downloader

    with patch("answer_extractor.google_sheets_export.MediaIoBaseDownload", side_effect=_fake_downloader):
        result = export_pdf(drive, "FILE_ID")

    assert result == b"%PDF-fake-content"
    drive.files.return_value.export_media.assert_called_once_with(fileId="FILE_ID", mimeType="application/pdf")


def test_export_pdf_keeps_downloading_until_done():
    drive = MagicMock()
    drive.files.return_value.export_media.return_value = MagicMock()

    downloader = MagicMock()
    downloader.next_chunk.side_effect = [(None, False), (None, False), (None, True)]

    with patch("answer_extractor.google_sheets_export.MediaIoBaseDownload", return_value=downloader):
        export_pdf(drive, "FILE_ID")

    assert downloader.next_chunk.call_count == 3


def test_delete_file_requests_shared_drive_support():
    drive = MagicMock()

    delete_file(drive, "FILE_ID")

    drive.files.return_value.delete.assert_called_once_with(fileId="FILE_ID", supportsAllDrives=True)
