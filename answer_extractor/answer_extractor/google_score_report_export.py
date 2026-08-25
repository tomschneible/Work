"""Produce a filled-in score-report PDF for one student, end to end:
find the right template in Drive, duplicate it, fill it in, export the
result as a PDF, and clean up the working copy.

Ties together three modules that each stay independently testable:
template_lookup (find the right template file without hardcoding ids),
google_sheets_export (the individual Drive/Sheets API calls -- see its
module docstring for why filling in a live Sheet needs the
export-as-xlsx/edit-locally/push-back-in round trip this function drives),
and score_report_writer (the actual cell-filling logic, which only knows
about local .xlsx files and has no idea Drive exists).

Named google_score_report_export (not score_report_export) to avoid
colliding with the unrelated, pre-existing score_report_export.py, which
writes parsed SAT/PSAT "Score Details" PDF rows to a plain .xlsx -- a
different pipeline entirely (see score_report.py) that has nothing to do
with Drive or this org's Sheets templates.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
from typing import List, Mapping

from googleapiclient.discovery import Resource

from .google_sheets_export import copy_template, delete_file, export_pdf, export_xlsx, replace_content
from .score_report_writer import QuestionKey, fill_score_report
from .template_lookup import find_template_file, resolve_template_folder


def export_score_report(
    drive: Resource,
    templates_root_folder_id: str,
    category_path: List[str],
    test_code: str,
    answers: Mapping[QuestionKey, str],
    student_name: str,
    test_date: dt.date | str,
    output_name: str,
) -> bytes:
    """Return the filled report's PDF bytes. `category_path` is the
    sequence of Drive subfolder names to walk from the templates root to
    reach the right template file, e.g. ["ACT", "Enhanced"] or ["SAT"];
    `test_code` is matched against template filenames the same way
    template_lookup.find_template_file does (e.g. "25MC1").

    The working Sheet copy created in Drive along the way is always
    deleted before returning -- including when a later step raises --
    since it exists only to be exported as this PDF, never to be kept
    around. The local temp file used for the same purpose is likewise
    always cleaned up.
    """
    folder_id = resolve_template_folder(drive, templates_root_folder_id, category_path)
    template = find_template_file(drive, folder_id, test_code)
    copy_id = copy_template(drive, template["id"], output_name)

    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        try:
            with open(tmp_path, "wb") as f:
                f.write(export_xlsx(drive, copy_id))
            filled = fill_score_report(tmp_path, answers, student_name, test_date)
            filled.save(tmp_path)
            replace_content(drive, copy_id, tmp_path)
            return export_pdf(drive, copy_id)
        finally:
            delete_file(drive, copy_id)
    finally:
        os.unlink(tmp_path)
