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
    allow_text_overflow,
    clear_cells,
    copy_template,
    delete_file,
    delete_rows,
    export_pdf,
    export_xlsx,
    extend_fill,
    hide_columns,
    narrow_columns,
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
    templates_root_folder_id: Optional[str],
    category_path: Optional[List[str]],
    test_code: Optional[str],
    output_name: str,
    fill_fn: Callable[[str | Path], FillResult],
    temp_folder_id: Optional[str] = None,
    keep_working_copy: bool = True,
    bottom_margin_in: Optional[float] = None,
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

    `fill_fn`
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
    matters). Its `cleared_ranges`, `hidden_column_ranges`,
    `narrowed_column_ranges`, `header_bar_extension`,
    `overflow_title_cells`, and `deleted_row_ranges`, if any, are then
    applied via google_sheets_export.clear_cells, .hide_columns,
    .narrow_columns, .extend_fill, .allow_text_overflow, and
    .delete_rows (in that order) before the PDF is exported -- SAT's
    fill_fn uses the first two so the report only shows the Module 2
    blocks that were actually administered, both in content and in the
    exported PDF's own print-area sizing (see
    sat_score_report_writer.blocks_to_clear for why both are needed, not
    just one); the third to shrink the answer tables' own column widths
    (and the already-hidden Module 2 columns further still) so "fit to
    page" doesn't have to shrink the whole page's scale as far to keep
    them within one page's width -- which was leaving height under-
    filled, and the print area off-center, even though both had room to
    spare (see sat_score_report_writer.visible_table_columns_to_narrow
    and .hidden_columns_to_shrink); the fourth and fifth to patch up two
    side effects narrowing those columns exposed -- a decorative fill
    that happened to span some of the same columns shrinking right along
    with them (see sat_score_report_writer.header_bar_extension), and a
    latent template inconsistency that let one block's own title
    genuinely truncate once its column got that narrow (see
    google_sheets_export.allow_text_overflow); and the sixth to remove a
    sheet's own trailing blank rows that would otherwise inflate that
    same print area regardless of Module 2 at all (see
    sat_score_report_writer.trailing_rows_to_delete); ACT's fill_fn
    leaves all six empty and these steps are skipped entirely.

    `temp_folder_id`, if given, is where the working Sheet copy is placed
    (e.g. the org's "Temporary Files" folder, alongside the real
    templates root) instead of Drive's copy default (the same folder as
    the template it was copied from) -- keeps a working copy from ever
    sitting amid the real templates.

    `bottom_margin_in`, if given, is passed straight through to
    export_pdf's own `bottom_margin_in` -- see its docstring for what it
    overrides and why; `None` (the default, used by every caller except
    SAT's own) leaves the file's own saved bottom margin untouched.

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
    copy_id = copy_template(drive, template_id, output_name, parent_folder_id=temp_folder_id)

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
            narrow_columns(sheets, copy_id, result.narrowed_column_ranges)
            extend_fill(sheets, copy_id, result.header_bar_extension)
            allow_text_overflow(sheets, copy_id, result.overflow_title_cells)
            delete_rows(sheets, copy_id, result.deleted_row_ranges)
            pdf_bytes = export_pdf(copy_id, bottom_margin_in=bottom_margin_in)
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
