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
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

from openpyxl.workbook import Workbook
from googleapiclient.discovery import Resource

from .google_sheets_export import copy_template, delete_file, export_pdf, export_xlsx, replace_content
from .template_lookup import find_template_file, resolve_template_folder


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
    the rest of their own arguments via e.g. functools.partial).

    `temp_folder_id`, if given, is where the working Sheet copy is placed
    (e.g. the org's "Temporary Files" folder, alongside the real
    templates root) instead of Drive's copy default (the same folder as
    the template it was copied from) -- keeps a working copy from ever
    sitting amid the real templates, even for the moment before it's
    deleted. That deletion always happens before this returns -- including
    when a later step raises -- since the copy exists only to be exported
    as this PDF, never to be kept around. The local temp file used for the
    same purpose is likewise always cleaned up.
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
            filled.save(tmp_path)
            replace_content(drive, copy_id, tmp_path)
            return export_pdf(drive, copy_id)
        finally:
            delete_file(drive, copy_id)
    finally:
        os.unlink(tmp_path)
