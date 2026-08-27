"""The Drive-orchestration sequence shared by every score-report export
path (currently ACT's google_score_report_export.py and SAT's
google_sat_score_report_export.py): find the right template, duplicate
it, fill it in via direct Sheets API cell writes, and export the result
as a PDF. The only thing that differs between formats is *how* a local
copy gets scanned to figure out what to write -- everything else
(finding the template, downloading a read-only local copy to locate
cells, writing them into the live Sheet, exporting the PDF -- see
google_sheets_export.py's own module docstring for why this no longer
edits/re-uploads the whole workbook) is identical, so that difference is
the one thing callers supply, via `fill_fn`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from .google_sheets_export import (
    FillResult,
    clear_cells,
    copy_template,
    delete_file,
    export_pdf,
    export_xlsx,
    hide_columns,
    hide_rows,
    write_cells,
)
from .template_lookup import find_template_file, resolve_template_folder


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
    sheets: Resource,
    templates_root_folder_id: str,
    category_path: List[str],
    test_code: str,
    output_name: str,
    fill_fn: Callable[[str | Path], FillResult],
    temp_folder_id: Optional[str] = None,
    keep_working_copy: bool = True,
) -> bytes:
    """Return the filled report's PDF bytes. `category_path` is the
    sequence of Drive subfolder names to walk from the templates root to
    reach the right template file, e.g. ["ACT", "Enhanced"] or ["SAT"];
    `test_code` is matched against template filenames the same way
    template_lookup.find_template_file does (e.g. "25MC1"). `fill_fn`
    receives a local, read-only path to the duplicated template (already
    downloaded as .xlsx, purely so `fill_fn` can figure out where things
    go) and must return a FillResult -- the caller-specific part of this
    (score_report_writer.fill_score_report or
    sat_score_report_writer.fill_sat_score_report, each pre-bound with
    the rest of their own arguments via e.g. functools.partial). Its
    `cell_writes` are pushed directly into the live Sheet via the Sheets
    API (google_sheets_export.write_cells) -- nothing else about the
    workbook is ever touched or re-converted through .xlsx (see
    google_sheets_export.py's own module docstring for why that
    matters). Its `cleared_ranges`, `hidden_column_ranges`, and
    `hidden_row_ranges`, if any, are then applied via
    google_sheets_export.clear_cells, .hide_columns, and .hide_rows (in
    that order) before the PDF is exported -- SAT's fill_fn uses the
    first two so the report only shows the Module 2 blocks that were
    actually administered, both in content and in the exported PDF's own
    print-area sizing (see sat_score_report_writer.blocks_to_clear for
    why both are needed, not just one), and the third to hide a sheet's
    own trailing blank rows that would otherwise inflate that same print
    area regardless of Module 2 at all (see
    sat_score_report_writer.rows_to_hide); ACT's fill_fn leaves all three
    empty and these steps are skipped entirely.

    `temp_folder_id`, if given, is where the working Sheet copy is placed
    (e.g. the org's "Temporary Files" folder, alongside the real
    templates root) instead of Drive's copy default (the same folder as
    the template it was copied from) -- keeps a working copy from ever
    sitting amid the real templates.

    `keep_working_copy` (default True, this org's own choice): whether
    that working Sheet copy is left in place once its PDF has been
    exported, rather than deleted -- kept by default since having the
    actual live Sheet behind each generated report is useful both for
    manual review/editing and for debugging one that came out wrong. A
    *failed* attempt is always cleaned up regardless of this flag -- it
    didn't produce a report worth keeping evidence of, and leaving every
    failed/retried attempt behind would just accumulate clutter in
    `temp_folder_id`. That cleanup is best-effort and can never mask or
    be mistaken for the actual failure: the fill/export sequence's own
    exception is always what propagates, even if the best-effort delete
    that follows it *also* fails (a plain `finally: delete_file(...)`
    doesn't have this property -- a delete failure there replaces
    whatever real exception was already propagating, hiding it). If
    `keep_working_copy` is False and the sequence succeeded, a delete
    failure is logged to stderr rather than thrown away the PDF this
    already-successful call obtained -- unless it's a 404, which means
    the file's already gone (by something else -- a retention policy on
    `temp_folder_id`, or occasional Shared Drive eventual consistency)
    and there's nothing left to warn about (see
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
            result = fill_fn(tmp_path)
            write_cells(sheets, copy_id, result.cell_writes)
            clear_cells(sheets, copy_id, result.cleared_ranges)
            hide_columns(sheets, copy_id, result.hidden_column_ranges)
            hide_rows(sheets, copy_id, result.hidden_row_ranges)
            pdf_bytes = export_pdf(copy_id)
        except Exception:
            try:
                delete_file(drive, copy_id)
            except Exception:
                pass  # the original exception below is the one that matters
            raise
        if keep_working_copy:
            return pdf_bytes
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
