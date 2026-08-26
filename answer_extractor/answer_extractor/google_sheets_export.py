"""Drive/Sheets operations behind the Google Sheets score-report export
path: duplicate a template, fill it in via direct Sheets API cell writes,
and export the copy as a PDF -- see README's "Google Sheets score
reports" section for the overall design and why each step exists.

Filling in a report used to round-trip the *entire* workbook through
.xlsx: export_xlsx pulled a copy down locally, a writer module
(score_report_writer.fill_score_report / sat_score_report_writer's
counterpart) edited it with openpyxl, and replace_content pushed the
whole thing back in, letting Drive convert it back to native Sheets
format on upload. That turned out not to be safe: confirmed live,
re-importing an openpyxl-authored .xlsx doesn't reconstruct some of a
template's own formatting with full fidelity to how Google's own native
export/import round-trip would -- specifically, a merged, centered title
cell on a tab this pipeline never even touches (the org's own "Cover
Page") came back with its text no longer filling the cell, even though
the underlying value was intact. Since every tab this pipeline actually
needs to change (a report's own answer grid) works the same as any other
live-Sheet edit -- a value in a specific cell -- there was never a real
need to touch the rest of the workbook at all.

So now: export_xlsx is still used, but only to download a *read-only*
local copy a writer module scans to figure out *where* to write (find the
name/date placeholder cells, each question's answer cell, ...) --
score_report_writer.fill_score_report and its SAT counterpart now return
a plain list of CellWrite instead of an edited Workbook. write_cells then
pushes exactly those cells into the live Sheet via the Sheets API's
values().batchUpdate -- every other tab (Cover Page, Student Report,
Content, ...), most of which are populated by formulas referencing the
answer tab anyway, is never re-converted through .xlsx at all, so nothing
about their own formatting is ever at risk from this pipeline again.
replace_content is kept for the one thing that still needs a full-file
overwrite: fixing a template's own cover-tab print settings directly
(see google_sheets_cli's `tighten-print-area` command), a rare,
deliberate one-time maintenance operation on the template itself rather
than something done for every generated report.

Deliberately thin wrappers around the official googleapiclient calls
rather than a bigger abstraction: there's no meaningful behavior to
capture beyond "make this one Drive/Sheets API call correctly," and
keeping each call in its own small function is what makes it possible to
unit-test the request-shaping (does this pass supportsAllDrives, the
right fields, ...) with a mocked service object instead of live network
access every test run needs.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import io
from typing import Dict, List, Optional, Sequence, Union

from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook

from .google_auth import get_credentials

_XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CellValue = Union[str, int, float, bool, None]


@dataclasses.dataclass(frozen=True)
class CellWrite:
    """One cell to set in a live Sheet -- `row`/`column` are 1-indexed,
    matching openpyxl (and this pipeline's writer modules locate cells
    with openpyxl in the first place, against a read-only local download
    -- see this module's own docstring)."""

    sheet: str
    row: int
    column: int
    value: CellValue


def format_date_for_sheets(value: dt.date | str) -> str:
    """A `test_date` (score_report_writer.fill_score_report and its SAT
    counterpart both take either a real `date` or an already-formatted
    string -- see either one's own docstring) as a string suitable for a
    Sheets API cell write. ISO format (`date.isoformat()`) for a real
    `date`, since with `write_cells`'s USER_ENTERED input option it's
    reliably recognized as a date regardless of the Sheet's own locale
    settings, unlike an ambiguous M/D/Y string; an already-formatted
    string (no specific day known -- e.g. "January 2026") is passed
    through as plain text, same as before."""
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


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


def _export(drive: Resource, file_id: str, mime_type: str) -> bytes:
    request = drive.files().export_media(fileId=file_id, mimeType=mime_type)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def export_pdf(drive: Resource, file_id: str) -> bytes:
    """The Sheets file at `file_id`, rendered to PDF exactly as Google
    Sheets' own File > Download > PDF would -- that menu and this API
    call both honor whatever print setup (page range/"entire workbook",
    layout, scale) is already saved on the file itself, so a template
    with its print settings configured once produces the same PDF shape
    on every copy without this code needing to know or set them itself."""
    return _export(drive, file_id, "application/pdf")


def export_xlsx(drive: Resource, file_id: str) -> bytes:
    """The Sheets file at `file_id`, converted to .xlsx bytes -- used to
    pull a freshly-duplicated template down locally, *read-only*, so a
    writer module (score_report_writer.fill_score_report, which only
    knows how to scan a local .xlsx via openpyxl, not a live Sheet over
    the API) can figure out where its writes need to go -- see this
    module's own docstring for why the result is never edited or
    re-uploaded wholesale any more, only used to locate cells for
    write_cells."""
    return _export(drive, file_id, _XLSX_MIME_TYPE)


def replace_content(drive: Resource, file_id: str, local_xlsx_path: str) -> None:
    """Overwrite the Sheets file at `file_id` with the contents of a local
    .xlsx -- Drive converts it to native Sheets format on upload, the same
    conversion Google Sheets' own File > Import > Replace spreadsheet
    does. Not used for per-report generation any more (see this module's
    own docstring) -- kept for the rare, deliberate case of overwriting a
    template file itself (e.g. google_sheets_cli's `tighten-print-area`
    command)."""
    media = MediaFileUpload(local_xlsx_path, mimetype=_XLSX_MIME_TYPE)
    drive.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()


def delete_file(drive: Resource, file_id: str) -> None:
    """Permanently remove a file (used to clean up a working copy after a
    failed export attempt, or after a successful one when the caller
    asked not to keep it -- see google_report_export_common.
    export_filled_report's keep_working_copy)."""
    drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def _a1(row: int, column: int) -> str:
    return f"{get_column_letter(column)}{row}"


def write_cells(sheets: Resource, spreadsheet_id: str, cells: Sequence[CellWrite]) -> None:
    """Write `cells` into the live Sheet at `spreadsheet_id` in one
    `values().batchUpdate` call, each cell addressed by its own
    single-cell A1 range (e.g. `"'ScoreSheet'!C5"`) -- this pipeline's
    writes are scattered across many rows/columns, never one contiguous
    range, so one range per cell rather than trying to batch adjacent
    ones is both simpler and doesn't need write_cells to know or care
    about layout at all. `value_input_option="USER_ENTERED"` -- same as
    typing directly into a cell -- so e.g. a date string from
    format_date_for_sheets is still recognized and formatted as a date,
    matching what the previous xlsx-based writer relied on openpyxl's
    native date cell type for. A cell whose value is None becomes an
    empty string, clearing whatever the template otherwise has there
    (e.g. an omitted question's blank "Your Answer") -- the API has no
    separate "null" value. A no-op (no API call at all) if `cells` is
    empty."""
    if not cells:
        return
    data = [
        {
            "range": f"'{c.sheet}'!{_a1(c.row, c.column)}",
            "values": [["" if c.value is None else c.value]],
        }
        for c in cells
    ]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def tighten_print_areas(wb: Workbook) -> None:
    """Give every sheet in `wb` that doesn't already have one an explicit
    print area matching its own real content (openpyxl's own
    `dimensions`, unaffected by stray explicit row-height formatting some
    tabs carry out to row 1000 even though their actual content ends far
    short of that) and turn off its gridlines.

    A sheet with no print area set exports (both a manual Download-as-PDF
    and export_pdf go through the same underlying conversion) with
    everything up to its highest-numbered row that carries any
    formatting at all -- for a tab like this org's own "Cover Page",
    whose real content is a few dozen rows but whose row-height
    formatting extends to row 1000, that's most of a page of blank,
    gridline-covered space below the actual header block. Confirmed live
    against a real template.

    Not used for per-report generation (see this module's own docstring
    on why editing/re-uploading a whole workbook turned out to be unsafe)
    -- meant for a rare, deliberate one-time fix applied directly to a
    template file itself, via google_sheets_cli's `tighten-print-area`
    command: download the template as .xlsx, call this, then
    replace_content it back onto the *same* file id (never a copy). A
    sheet that already has its own print area is left alone, so a
    template that's been deliberately configured some other way isn't
    second-guessed.
    """
    for ws in wb.worksheets:
        if ws.print_area:
            continue
        ws.print_area = ws.dimensions
        ws.sheet_view.showGridLines = False
