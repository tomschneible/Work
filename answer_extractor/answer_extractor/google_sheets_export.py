"""Drive/Sheets operations behind the Google Sheets score-report export
path: duplicate a template, (eventually) fill it in, export the copy as a
PDF, and clean up the working copy -- see README's "Google Sheets score
reports" section for the overall design and why each step exists.

Deliberately thin wrappers around the official googleapiclient calls
rather than a bigger abstraction: there's no meaningful behavior to
capture beyond "make this one Drive/Sheets API call correctly," and
keeping each call in its own small function is what makes it possible to
unit-test the request-shaping (does this pass supportsAllDrives, the
right fields, ...) with a mocked service object instead of live network
access every test run needs.
"""
from __future__ import annotations

import io
from typing import Dict, List, Optional

from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaIoBaseDownload

from .google_auth import get_credentials


def build_services() -> tuple[Resource, Resource]:
    """(drive_service, sheets_service), both authenticated the same way --
    see google_auth.get_credentials for what triggers the interactive
    consent flow vs. a silent token refresh."""
    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets


def list_folder(drive: Resource, folder_id: str) -> List[Dict[str, str]]:
    """Every non-trashed file directly inside a Drive folder -- id, name,
    mimeType -- including one that lives in a Shared Drive (see
    supportsAllDrives/includeItemsFromAllDrives/corpora below; omitting
    any of the three silently drops Shared Drive results rather than
    erroring, so all three are always passed together). Used to identify
    a template file's id from a human-readable folder link before it's
    hardcoded anywhere -- see google_sheets_cli's `list-folder` command."""
    files: List[Dict[str, str]] = []
    page_token = None
    while True:
        response = (
            drive.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
                pageToken=page_token,
            )
            .execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def copy_template(
    drive: Resource, template_file_id: str, new_name: str, parent_folder_id: Optional[str] = None
) -> str:
    """Duplicate `template_file_id` (a Sheets file) as `new_name`, returning
    the new copy's own file id. `parent_folder_id`, if given, places the
    copy there instead of Drive's default (the copier's own My Drive
    root) -- also needs supportsAllDrives when that folder is a Shared
    Drive folder."""
    body = {"name": new_name}
    if parent_folder_id:
        body["parents"] = [parent_folder_id]
    result = drive.files().copy(fileId=template_file_id, body=body, supportsAllDrives=True).execute()
    return result["id"]


def export_pdf(drive: Resource, file_id: str) -> bytes:
    """The Sheets file at `file_id`, rendered to PDF exactly as Google
    Sheets' own File > Download > PDF would -- that menu and this API
    call both honor whatever print setup (page range/"entire workbook",
    layout, scale) is already saved on the file itself, so a template
    with its print settings configured once produces the same PDF shape
    on every copy without this code needing to know or set them itself."""
    request = drive.files().export_media(fileId=file_id, mimeType="application/pdf")
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def delete_file(drive: Resource, file_id: str) -> None:
    """Permanently remove a file (used to clean up the working copy after
    its PDF has been exported -- see the README for why the copy isn't
    kept around)."""
    drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()
