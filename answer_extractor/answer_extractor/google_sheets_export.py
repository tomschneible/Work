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

replace_content itself is kept here only as a thin, generically correct
wrapper -- nothing in this codebase calls it any more. A template's own
gridlines get turned off via hide_gridlines instead (a direct Sheets API
metadata change, no file conversion involved at all): confirmed live the
hard way that pointing the xlsx round-trip at a *template* file directly
(via replace_content) is exactly as unsafe as it was for a per-report
copy -- it corrupted the org's own live "ACT 25MC1" template's Cover Page
the one time it was tried, recovered only via Sheets' own version
history. Don't reach for replace_content to fix a template's formatting;
extend hide_gridlines's approach (a targeted batchUpdate request) instead
of reintroducing an xlsx round-trip anywhere in this codebase.

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
from typing import Dict, List, Optional, Sequence, Tuple, Union

from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from openpyxl.utils import get_column_letter

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


@dataclasses.dataclass(frozen=True)
class FillResult:
    """What a fill_fn (score_report_writer.fill_score_report,
    sat_score_report_writer.fill_sat_score_report) returns to
    google_report_export_common.export_filled_report: the individual
    cell writes needed, plus any rectangles that should be cleared
    (value and border formatting both) from the exported PDF once those
    writes land. `cleared_ranges` is empty for every fill_fn except
    SAT's -- it uses this to clear whichever Module 2 block occurrence
    (a subject's other difficulty, or a duplicate/twin) wasn't actually
    administered, so the report only shows the modules that were
    actually filled in; see sat_score_report_writer.blocks_to_clear for
    why this has to be scoped to one occurrence's own row range rather
    than hiding a whole column. Each entry is (sheet_name, 0-indexed
    start row, 0-indexed end row, 0-indexed start column, 0-indexed end
    column) -- all ends exclusive, the shape clear_cells' Sheets API
    request needs."""

    cell_writes: List[CellWrite]
    cleared_ranges: Sequence[Tuple[str, int, int, int, int]] = ()


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
    does. Nothing in this codebase calls this any more (see this
    module's own docstring): confirmed live that pointing it at a
    template file directly doesn't reconstruct that file's own
    formatting with full fidelity, corrupting a real template the one
    time this was tried for template maintenance. Kept only as a thin,
    correct wrapper -- don't reach for this to fix a template's
    formatting; use a targeted Sheets API batchUpdate (see
    hide_gridlines) instead."""
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


def clear_cells(
    sheets: Resource, spreadsheet_id: str, ranges: Sequence[Tuple[str, int, int, int, int]]
) -> None:
    """Clear both the value and the border formatting of every cell in
    each of `ranges` -- (sheet_name, 0-indexed start row, 0-indexed end
    row, 0-indexed start column, 0-indexed end column), all ends
    exclusive, the shape FillResult.cleared_ranges carries -- via one
    Sheets API `batchUpdate` `repeatCell` request per range. A per-report,
    per-copy content change (unrelated to hide_gridlines' template-wide
    metadata fix above) -- used by sat_score_report_writer.
    fill_sat_score_report to remove a Module 2 block occurrence that
    wasn't administered. Deliberately row-scoped rather than a whole-
    column hide (an earlier version of this, hide_columns, worked that
    way and got replaced -- see blocks_to_clear's own docstring for why a
    whole-column hide isn't safe here): clearing only ever affects the
    exact rows given, so it can't remove another subject's own real
    answers sitting in the same columns but a different row range. One
    `get` call resolves every sheet name to its numeric sheetId first,
    since the batchUpdate request itself only accepts that, not a name.
    A no-op (no API call at all) if `ranges` is empty. Raises ValueError
    if a range names a sheet this spreadsheet doesn't have."""
    if not ranges:
        return
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    sheet_id_by_title = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    requests = []
    for sheet_name, start_row, end_row, start_col, end_col in ranges:
        if sheet_name not in sheet_id_by_title:
            raise ValueError(f"No sheet named {sheet_name!r} in spreadsheet {spreadsheet_id}")
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_by_title[sheet_name],
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col,
                        "endColumnIndex": end_col,
                    },
                    "cell": {},
                    "fields": "userEnteredValue,userEnteredFormat.borders",
                }
            }
        )
    sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def hide_gridlines(sheets: Resource, spreadsheet_id: str) -> None:
    """Turn off gridlines on every sheet of `spreadsheet_id` that doesn't
    already have them off, via one Sheets API `batchUpdate` setting each
    sheet's own `gridProperties.hideGridlines` directly -- a pure
    metadata change, no file conversion involved at all (see this
    module's own docstring on why an xlsx round-trip -- download, edit,
    re-upload -- was tried for this instead and confirmed live to
    corrupt a template it was pointed at directly, the same failure mode
    already ruled out for per-report generation).

    Meant for a deliberate one-time fix applied directly to a template
    file itself, via google_sheets_cli's `hide-gridlines` command -- not
    used by per-report generation. A no-op (no API call at all) if every
    sheet already has gridlines hidden.
    """
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    requests = []
    for sheet in meta.get("sheets", []):
        properties = sheet["properties"]
        if properties.get("gridProperties", {}).get("hideGridlines"):
            continue
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": properties["sheetId"],
                        "gridProperties": {"hideGridlines": True},
                    },
                    "fields": "gridProperties.hideGridlines",
                }
            }
        )
    if not requests:
        return
    sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
