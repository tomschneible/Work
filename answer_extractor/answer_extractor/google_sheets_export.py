"""Drive/Sheets operations behind the Google Sheets score-report export
path: duplicate a template, fill it in via direct Sheets API cell writes,
and export the copy as a PDF -- see README's "Google Sheets score
reports" section for the overall design and why each step exists.

Filling in a report used to round-trip the *entire* workbook through
.xlsx: export_xlsx pulled a copy down locally, a writer module
(score_report_writer.fill_score_report and its SAT counterpart) edited
it with openpyxl, and a Drive files.update pushed the whole thing back
in, letting Drive convert it back to native Sheets
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

A template's own gridlines get turned off via hide_gridlines instead (a
direct Sheets API metadata change, no file conversion involved at all):
confirmed live the hard way that pointing the xlsx round-trip at a
*template* file directly is exactly as unsafe as it was for a per-report
copy -- it corrupted the org's own live "ACT 25MC1" template's Cover Page
the one time it was tried, recovered only via Sheets' own version
history. To fix a template's formatting, extend hide_gridlines's
approach (a targeted batchUpdate request) rather than reintroducing an
xlsx round-trip anywhere in this codebase.

Deliberately thin wrappers around the official googleapiclient calls
rather than a bigger abstraction: there's no meaningful behavior to
capture beyond "make this one Drive/Sheets API call correctly," and
keeping each call in its own small function is what makes it possible to
unit-test the request-shaping (does this pass supportsAllDrives, the
right fields, ...) with a mocked service object instead of live network
access every test run needs. export_pdf is the one exception -- it calls
Sheets' own dedicated (undocumented, no googleapiclient wrapper) export
URL directly rather than a Drive/Sheets API method, since confirmed live
that Drive's generic files.export doesn't reliably apply a sheet's own
print scale settings; see its own docstring.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import io
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple, Union

import requests
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from openpyxl.utils import get_column_letter

from .google_auth import get_credentials

_XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

CellValue = Union[str, int, float, bool, None]

# Every write helper below funnels its actual mutating call through
# _execute_with_rate_limit_retry now, not just google_sheets_cli's own
# repair-simplified-calculations command, which is where this retry
# originally lived (see that module's own docstring, command 4, for the
# full history: confirmed live that enough Sheets API write calls in a
# short window -- there, one write_cells call per cell, before that
# command started chunking them -- reliably trips Sheets' own
# WriteRequestsPerMinutePerUser quota, 60/minute). The per-report export
# path (google_report_export_common.export_filled_report) chains up to
# eight of these write helpers per report with no chunking of its own (each
# already collapses its own work into one batchUpdate call), so before this
# it had no protection at all against that exact quota -- a burst of
# reports generated back-to-back (the ordinary way to use this pipeline at
# the end of a testing session, not a misuse) would surface as an
# unhandled 429 mid-report rather than a brief, automatic retry.
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_RETRY_SECONDS = 65  # a little over the 60-second window WriteRequestsPerMinutePerUser is measured over

# Drive calls lean on googleapiclient's own retry instead: `num_retries`
# re-sends a request that got a 429 or 5xx, with exponential backoff.
_DRIVE_RETRIES = 3
# Seconds to wait before each retry of export_pdf's request -- it goes to
# Sheets' own export URL, not a googleapiclient method, so it has no
# built-in retry of its own; that endpoint answers a burst of back-to-back
# reports with 429s.
_PDF_EXPORT_RETRY_WAITS = (10, 30, 60)


def is_rate_limit_error(exc: Exception) -> bool:
    """True for a Google API 429 ("RATE_LIMIT_EXCEEDED") -- checked via
    `exc.status_code`, a convenience property googleapiclient's own
    HttpError exposes as `self.resp.status` -- not by parsing
    `reason`/`quota_metric` out of the error body, since status is the
    stable part of this across client versions. Deliberately narrow: a 400
    "trying to edit a protected cell" (confirmed live, see
    google_sheets_cli's own docstring) is a different, *permanent* kind of
    failure that retrying would just reproduce identically every time --
    see _execute_with_rate_limit_retry for why that distinction matters.
    Public (not prefixed with `_`, unlike this module's other internal
    helpers) since google_sheets_cli's own repair-simplified-calculations
    command still needs to tell a still-rate-limited cell apart from a
    genuinely failed one in its own per-cell reporting, after this
    module's own retry has already been exhausted."""
    return isinstance(exc, HttpError) and exc.status_code == 429


def _execute_with_rate_limit_retry(request):
    """Call `request.execute()` (a not-yet-executed googleapiclient request
    object, e.g. `sheets.spreadsheets().batchUpdate(...)`), retrying up to
    _RATE_LIMIT_MAX_RETRIES times on a transient 429 (is_rate_limit_error),
    sleeping _RATE_LIMIT_RETRY_SECONDS between attempts. Anything else --
    a protected-cell 400, or a 429 that still hasn't cleared after every
    retry -- propagates unchanged, so a caller sees exactly the same
    exception either way; this only ever adds retries around a transient
    failure, never changes what a non-retryable or exhausted one looks
    like."""
    attempts = 0
    while True:
        try:
            return request.execute()
        except Exception as exc:
            attempts += 1
            if not is_rate_limit_error(exc) or attempts > _RATE_LIMIT_MAX_RETRIES:
                raise
            print(
                f"  Rate-limited by Sheets (60 writes/minute) -- waiting {_RATE_LIMIT_RETRY_SECONDS}s "
                f"before retrying (attempt {attempts}/{_RATE_LIMIT_MAX_RETRIES})...",
                file=sys.stderr,
            )
            time.sleep(_RATE_LIMIT_RETRY_SECONDS)


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
    sat_simplified_score_report_writer.fill_simple_sat_score_report)
    returns to google_report_export_common.export_filled_report: the
    individual cell writes needed, pushed into the live Sheet by
    write_cells."""

    cell_writes: List[CellWrite]


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
            .execute(num_retries=_DRIVE_RETRIES)
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
    result = drive.files().copy(fileId=template_file_id, body=body, supportsAllDrives=True).execute(
        num_retries=_DRIVE_RETRIES
    )
    return result["id"]


def get_file(drive: Resource, file_id: str) -> Dict[str, object]:
    """A file or folder's own id, name, and parents -- `parents` (its
    containing folder's id, in a one-item list) is left out entirely for a
    Drive's own root, and for anything whose containing folder this
    account can't see."""
    request = drive.files().get(fileId=file_id, fields="id, name, parents", supportsAllDrives=True)
    return request.execute(num_retries=_DRIVE_RETRIES)


def create_folder(drive: Resource, parent_folder_id: str, name: str) -> str:
    """Create a folder named `name` inside `parent_folder_id`, returning
    the new folder's own id."""
    body = {"name": name, "mimeType": FOLDER_MIME_TYPE, "parents": [parent_folder_id]}
    request = drive.files().create(body=body, fields="id", supportsAllDrives=True)
    return request.execute(num_retries=_DRIVE_RETRIES)["id"]


def upload_bytes(drive: Resource, parent_folder_id: str, name: str, content: bytes, mime_type: str) -> str:
    """Upload `content` into `parent_folder_id` as a file named `name`,
    returning its id. Stored exactly as given -- a PDF stays a PDF, never
    converted to a Google Docs format -- and sent as a resumable upload,
    since a scanned PDF can easily outgrow the 5 MB a single-request
    upload allows."""
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
    body = {"name": name, "parents": [parent_folder_id]}
    request = drive.files().create(body=body, media_body=media, fields="id", supportsAllDrives=True)
    return request.execute(num_retries=_DRIVE_RETRIES)["id"]


def _export(drive: Resource, file_id: str, mime_type: str) -> bytes:
    request = drive.files().export_media(fileId=file_id, mimeType=mime_type)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk(num_retries=_DRIVE_RETRIES)
    return buffer.getvalue()


def export_pdf(spreadsheet_id: str, fit_to_page: bool = False) -> bytes:
    """The Sheets file at `spreadsheet_id`, rendered to PDF via Sheets'
    own dedicated export endpoint (`docs.google.com/spreadsheets/d/{id}/
    export?format=pdf`) -- the same URL "File > Download > PDF" in the
    Sheets UI itself navigates to, not Drive's generic `files.export`
    (used by every other `_export`-based function in this module, and
    what this used to call too). Switched deliberately: confirmed live
    that Drive's generic export doesn't reliably apply a sheet's own
    "fit to height"/"fit to page" print scale the same way the Sheets UI
    export does, even though the setting itself is genuinely saved on the
    file correctly -- the exported PDF came out at undistorted, un-shrunk
    size and overflowed onto an extra page regardless. This endpoint,
    called with no scale/gid overrides of its own, otherwise defers to
    whatever print setup (page range, layout, scale) is already saved on
    the file, the same as Drive's export was meant to -- just apparently
    more faithfully. No `gid` is passed, so this exports the *entire*
    workbook (every visible sheet), matching the Drive-based export it
    replaces.

    `fit_to_page`, if true, adds this same endpoint's own `scale=4`
    ("Fit to Page" -- values 1-4 are Normal/Fit-Width/Fit-Height/Fit-Page,
    per outside reverse-engineering of this endpoint's own parameters;
    there's no official spec for any of them) to force that scale explicitly rather than deferring to
    whatever's already saved. Exists for the simplified SAT template's
    own Cover Page: confirmed via a local read of the real template,
    "Fit to page" is *already* the saved setting there (and on Score
    Report and Content) -- yet a real export with no scale override still
    split it across two PDF pages. That gap between "already configured
    to fit" and "still doesn't, via this endpoint" is the same category
    of mismatch this whole function exists to route around in the first
    place (see this function's own docstring above on why Drive's generic
    export was replaced with this dedicated one) -- just not fully closed
    by switching endpoints alone, apparently. Passing `scale=4` explicitly
    is a next attempt at making this endpoint actually apply what's
    already configured, rather than something newly invented here.

    Not yet confirmed live. Also not free of risk: `scale` applies to the
    *entire* export (there's no per-sheet override when, as here, no
    `gid` narrows the call to one sheet), and at least one sheet in this
    same workbook -- "Student Responses" (the Question-Level Feedback
    page) -- is deliberately saved at a fixed, hand-set 54% scale instead
    of "Fit to page". Passing `fit_to_page=True` overrides that page's own
    saved scale too, not just Cover Page's -- a caller turning this on
    needs to verify *both* pages in the same real export, not just the
    one this was written to fix.

    Undocumented as a formal Google API (there's no googleapiclient
    wrapper for it) -- authenticated the same way as every other call in
    this module (`google_auth.get_credentials`), just via a raw
    `AuthorizedSession` GET instead of a `Resource` method, since this
    isn't a `sheets`/`drive` API call. Raises RuntimeError if the
    response isn't actually a PDF (e.g. an HTML error/login page, which
    this endpoint can return with a 200 status instead of a clean HTTP
    error for some failure modes)."""
    creds = get_credentials()
    session = AuthorizedSession(creds)
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"
    params = {"format": "pdf"}
    if fit_to_page:
        params["scale"] = "4"
    response = _get_with_retry(session, url, params)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("application/pdf"):
        raise RuntimeError(
            f"Expected a PDF from Sheets' export endpoint for {spreadsheet_id}, got "
            f"content-type {content_type!r} instead (first 200 bytes: {response.content[:200]!r})"
        )
    return response.content


def _get_with_retry(session: AuthorizedSession, url: str, params: Dict[str, str]):
    """`session.get`, retried after each of _PDF_EXPORT_RETRY_WAITS on a 429,
    a 5xx, or a dropped connection. The last response comes back as-is
    (the caller's raise_for_status turns it into an error); a connection
    that keeps failing raises."""
    for wait in (*_PDF_EXPORT_RETRY_WAITS, None):
        try:
            response = session.get(url, params=params)
        except (requests.ConnectionError, requests.Timeout):
            if wait is None:
                raise
        else:
            if wait is None or (response.status_code != 429 and response.status_code < 500):
                return response
        print(f"  Google's PDF export didn't answer properly -- trying again in {wait}s...", file=sys.stderr)
        time.sleep(wait)


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


def delete_file(drive: Resource, file_id: str) -> None:
    """Permanently remove a file (used to clean up a working copy after a
    failed export attempt, or after a successful one when the caller
    asked not to keep it -- see google_report_export_common.
    export_filled_report's keep_working_copy)."""
    drive.files().delete(fileId=file_id, supportsAllDrives=True).execute(num_retries=_DRIVE_RETRIES)


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
    _execute_with_rate_limit_retry(
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        )
    )


def clear_notes(sheets: Resource, spreadsheet_id: str, cells: Sequence[Tuple[str, int, int]]) -> None:
    """Remove the Sheets *Note* (right-click "Insert note" -- the plain,
    single, non-threaded annotation `CellData.note`; not the newer
    threaded "Comment" feature, which is a Drive-level concept and isn't
    part of the Sheets API at all) from each of `cells` -- (sheet_name,
    0-indexed row, 0-indexed column) -- via one Sheets API `batchUpdate`
    `repeatCell` request per cell, setting `note` to an empty string. A
    no-op (no API call at all) if `cells` is empty.

    Exists for the simplified SAT template's own "Student Responses" tab:
    confirmed against the real template, 8 cells there still carry
    hand-grading reminder notes (e.g. "Remember to put the = sign in
    front of fractions") meant for a person typing an answer in by hand
    -- meaningless once the program fills those cells directly via the
    API, but Sheets' own PDF export still appends every note in the
    printed range as its own extra "Notes" page if the print setup's own
    Formatting > Notes option is on -- confirmed live, this is what a
    real export's own unwanted extra page turned out to be (see
    sat_simplified_template_repair.find_note_cells' own docstring for the
    real coordinates this was confirmed against). Clearing the notes
    outright fixes this regardless of whatever the target file's own
    print setup has saved, and -- run once against the master simplified
    template, same as hide_gridlines -- every future per-student
    duplicate inherits having no notes to print at all, rather than
    needing this run again per copy.

    One `get` call resolves every sheet name to its numeric sheetId
    first, since the batchUpdate request itself only accepts that, not a
    name. Raises ValueError if a cell names a sheet this spreadsheet
    doesn't have."""
    if not cells:
        return
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    sheet_id_by_title = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    requests = []
    for sheet_name, row, col in cells:
        if sheet_name not in sheet_id_by_title:
            raise ValueError(f"No sheet named {sheet_name!r} in spreadsheet {spreadsheet_id}")
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_by_title[sheet_name],
                        "startRowIndex": row,
                        "endRowIndex": row + 1,
                        "startColumnIndex": col,
                        "endColumnIndex": col + 1,
                    },
                    "cell": {"note": ""},
                    "fields": "note",
                }
            }
        )
    _execute_with_rate_limit_retry(
        sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
    )


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
    _execute_with_rate_limit_retry(
        sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
    )
