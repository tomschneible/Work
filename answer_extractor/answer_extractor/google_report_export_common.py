"""The Drive-orchestration sequence shared by every score-report export
path (currently ACT's google_score_report_export.py and SAT's
google_sat_score_report_export.py): find the right template, duplicate
it, fill it in, export the result as a PDF, and clean up the working
copy. The only thing that differs between formats is *how* a local copy
gets filled in -- everything else (finding the template, the
export-as-xlsx/edit-locally/push-back-in round trip google_sheets_export's
module docstring explains, cleanup) is identical, so that difference is
the one thing callers supply, via `fill_fn`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

from openpyxl.workbook import Workbook
from googleapiclient.discovery import Resource

from googleapiclient.errors import HttpError

from .google_sheets_export import copy_template, delete_file, export_pdf, export_xlsx, replace_content
from .template_lookup import find_template_file, resolve_template_folder


def _tighten_print_areas(wb: Workbook) -> None:
    """Give every sheet in `wb` that doesn't already have one an explicit
    print area matching its own real content (openpyxl's own
    `dimensions`, unaffected by stray explicit row-height formatting some
    tabs carry out to row 1000 even though their actual content ends far
    short of that) and turn off its gridlines.

    A sheet with no print area set exports (both a manual Download-as-PDF
    and this pipeline's own export_pdf go through the same underlying
    conversion) with everything up to its highest-numbered row that
    carries any formatting at all -- for a tab like this org's own "Cover
    Page", whose real content is a few dozen rows but whose row-height
    formatting extends to row 1000, that's most of a page of blank,
    gridline-covered space below the actual header block. Confirmed live
    against a real template.

    This is applied every export rather than relying on the template's
    own saved print settings, which turned out not to reliably persist
    through Google Sheets' own Print-settings UI -- baking the fix into
    every generated report is more robust than depending on that. A sheet
    that already has its own print area is left alone, so a template
    that's been deliberately configured some other way isn't
    second-guessed.
    """
    for ws in wb.worksheets:
        if ws.print_area:
            continue
        ws.print_area = ws.dimensions
        ws.sheet_view.showGridLines = False


def _cleanup_delete_is_actionable(exc: Exception) -> bool:
    """False for a delete failure that means there's nothing left to warn
    about: a 404 says Drive no longer has this file at all (already
    deleted -- by something else, a retention policy on the org's
    "Temporary Files" folder, or occasional Shared Drive eventual
    consistency -- not "still there and this call couldn't remove it").
    True for anything else (e.g. a permission error), where the working
    copy plausibly *is* still sitting there and a person should know."""
    return not (isinstance(exc, HttpError) and exc.status_code == 404)


def export_filled_report(
    drive: Resource,
    templates_root_folder_id: str,
    category_path: List[str],
    test_code: str,
    output_name: str,
    fill_fn: Callable[[str | Path], Workbook],
    temp_folder_id: Optional[str] = None,
) -> bytes:
    """Return the filled report's PDF bytes. `category_path` is the
    sequence of Drive subfolder names to walk from the templates root to
    reach the right template file, e.g. ["ACT", "Enhanced"] or ["SAT"];
    `test_code` is matched against template filenames the same way
    template_lookup.find_template_file does (e.g. "25MC1"). `fill_fn`
    receives a local path to the duplicated template (already downloaded
    as .xlsx) and must return the filled-in Workbook -- the caller-specific
    part of this (score_report_writer.fill_score_report or
    sat_score_report_writer.fill_sat_score_report, each pre-bound with
    the rest of their own arguments via e.g. functools.partial). Every
    sheet in the result then gets its print area tightened (see
    _tighten_print_areas) before it's pushed back to Drive and exported.

    `temp_folder_id`, if given, is where the working Sheet copy is placed
    (e.g. the org's "Temporary Files" folder, alongside the real
    templates root) instead of Drive's copy default (the same folder as
    the template it was copied from) -- keeps a working copy from ever
    sitting amid the real templates, even for the moment before it's
    deleted. Cleanup is always attempted before this returns (or raises)
    -- but a cleanup failure never masks or is mistaken for the actual
    result: if the fill/export sequence itself raised, that original
    exception is always what propagates, even if the best-effort delete
    attempted afterward *also* fails (a plain `finally: delete_file(...)`
    doesn't have this property -- a delete failure there replaces
    whatever real exception was already propagating, hiding it); if the
    sequence succeeded, a delete failure is logged to stderr rather than
    thrown away the PDF this already-successful call obtained -- unless
    it's a 404, which means the file's already gone (by something else --
    a retention policy on `temp_folder_id`, or occasional Shared Drive
    eventual consistency) and there's nothing left to warn about (see
    _cleanup_delete_is_actionable). The local temp file used for the same
    purpose is likewise always cleaned up (and can't mask anything the
    same way, since nothing downstream of it depends on its content).
    """
    folder_id = resolve_template_folder(drive, templates_root_folder_id, category_path)
    template = find_template_file(drive, folder_id, test_code)
    copy_id = copy_template(drive, template["id"], output_name, parent_folder_id=temp_folder_id)

    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        try:
            with open(tmp_path, "wb") as f:
                f.write(export_xlsx(drive, copy_id))
            filled = fill_fn(tmp_path)
            _tighten_print_areas(filled)
            filled.save(tmp_path)
            replace_content(drive, copy_id, tmp_path)
            pdf_bytes = export_pdf(drive, copy_id)
        except Exception:
            try:
                delete_file(drive, copy_id)
            except Exception:
                pass  # the original exception below is the one that matters
            raise
        try:
            delete_file(drive, copy_id)
        except Exception as exc:
            if _cleanup_delete_is_actionable(exc):
                print(
                    f"Warning: {output_name}'s report exported fine, but couldn't clean up its "
                    f"working Drive copy (id {copy_id}): {exc}",
                    file=sys.stderr,
                )
            # else: already gone -- nothing left to clean up, nothing to warn about.
        return pdf_bytes
    finally:
        os.unlink(tmp_path)
