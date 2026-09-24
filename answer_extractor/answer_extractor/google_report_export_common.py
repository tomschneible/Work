"""The Drive-orchestration sequence shared by every score-report export
path (currently ACT's google_score_report_export.py and SAT's
google_sat_simplified_score_report_export.py): find the right template, duplicate
it, fill it in via direct Sheets API cell writes, and export the result
as a PDF. The only thing that differs between formats is *how* a local
copy gets scanned to figure out what to write -- everything else
(finding the template, getting a read-only local copy of it to locate
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

from .google_sheets_export import FillResult, copy_template, delete_file, export_pdf, write_cells
from .template_cache import template_xlsx
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
    templates_root_folder_id: Optional[str],
    category_path: Optional[List[str]],
    test_code: Optional[str],
    output_name: str,
    fill_fn: Callable[[str | Path], FillResult],
    copy_folder_id: Optional[str] = None,
    keep_working_copy: bool = True,
    fit_to_page: bool = False,
    template_id: Optional[str] = None,
) -> bytes:
    """Return the filled report's PDF bytes. Two ways to say which
    template to duplicate -- give exactly one, not a mix:

    - `templates_root_folder_id`/`category_path`/`test_code` (all three,
      the original and still the common case): looked up the usual way
      -- `category_path` is the sequence of Drive subfolder names to
      walk from the templates root to reach the right template file,
      e.g. ["ACT", "Enhanced"] or ["SAT"]; `test_code` is matched against
      template filenames the same way template_lookup.find_template_file
      does (e.g. "25MC1"). Right for a template made once per test, since
      it carries real per-test content of its own.
    - `template_id` alone: the file to duplicate is already known,
      resolved by the caller however it sees fit -- e.g. the simplified
      SAT template (sat_simplified_score_report_writer.py), which is
      found once by a fixed name rather than by test code, since it
      carries no per-test content of its own to make a new copy of for
      every test (see that module's own docstring). Raises ValueError if
      neither form -- or a mix of both -- is given.

    `fill_fn` receives a local, read-only path to the template as an .xlsx
    (the same content as the copy just made -- from
    template_cache.template_xlsx, which reuses an earlier report's
    download while the template is unchanged), purely so `fill_fn` can
    figure out where things go, and must return a FillResult -- the
    caller-specific part of this (score_report_writer.fill_score_report or
    sat_simplified_score_report_writer.fill_simple_sat_score_report, each
    pre-bound with the rest of their own arguments). Its `cell_writes` are
    pushed directly into the live Sheet via the Sheets API
    (google_sheets_export.write_cells) -- nothing else about the workbook
    is ever touched or re-converted through .xlsx (see
    google_sheets_export.py's own module docstring for why that matters).

    `copy_folder_id`, if given, is where the working Sheet copy is placed
    -- the test day's own folder under Student Tracking, or "Temporary
    Files" for a test-mode run (see report_folders.py) -- instead of
    Drive's copy default (the same folder as the template it was copied
    from), which would leave a working copy sitting amid the real
    templates.

    `fit_to_page` is passed straight through to export_pdf's
    own `fit_to_page` -- see its docstring for what it overrides, why,
    and the risk it carries (a workbook-wide override, not scoped to
    whichever sheet motivated it). `False` (the default, used by every
    caller except the simplified SAT template's own) leaves every
    sheet's own saved scale untouched.

    `keep_working_copy` (default True, this org's own choice): whether
    that working Sheet copy is left in place once its PDF has been
    exported, rather than deleted -- kept by default since having the
    actual live Sheet behind each generated report is useful both for
    manual review/editing and for debugging one that came out wrong. A
    *failed* attempt is always cleaned up regardless of this flag -- it
    didn't produce a report worth keeping evidence of, and leaving every
    failed/retried attempt behind would just accumulate clutter in
    `copy_folder_id`. That cleanup is best-effort and can never mask or
    be mistaken for the actual failure: the fill/export sequence's own
    exception is always what propagates, even if the best-effort delete
    that follows it *also* fails (a plain `finally: delete_file(...)`
    doesn't have this property -- a delete failure there replaces
    whatever real exception was already propagating, hiding it). If
    `keep_working_copy` is False and the sequence succeeded, a delete
    failure is logged to stderr rather than thrown away the PDF this
    already-successful call obtained -- unless it's a 404, which means
    the file's already gone (by something else -- a retention policy on
    `copy_folder_id`, or occasional Shared Drive eventual consistency)
    and there's nothing left to warn about (see
    _cleanup_delete_is_actionable). The local temp file used for the same
    purpose is likewise always cleaned up (and can't mask anything the
    same way, since nothing downstream of it depends on its content).
    """
    by_lookup = templates_root_folder_id is not None or category_path is not None or test_code is not None
    if template_id is not None and by_lookup:
        raise ValueError("Pass either template_id or templates_root_folder_id/category_path/test_code, not both")
    if template_id is None:
        if templates_root_folder_id is None or category_path is None or test_code is None:
            raise ValueError(
                "Need either template_id, or all three of templates_root_folder_id/category_path/test_code"
            )
        folder_id = resolve_template_folder(drive, templates_root_folder_id, category_path)
        template_id = find_template_file(drive, folder_id, test_code)["id"]
    copy_id = copy_template(drive, template_id, output_name, parent_folder_id=copy_folder_id)

    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        try:
            with open(tmp_path, "wb") as f:
                f.write(template_xlsx(drive, template_id))
            result = fill_fn(tmp_path)
            write_cells(sheets, copy_id, result.cell_writes)
            pdf_bytes = export_pdf(copy_id, fit_to_page=fit_to_page)
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
