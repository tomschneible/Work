"""Produce a filled-in ACT score-report PDF for one student, end to end
-- see google_report_export_common.export_filled_report for the shared
Drive-orchestration sequence this just supplies the ACT-specific fill
step to (score_report_writer.fill_score_report, which only knows about
local .xlsx files and has no idea Drive exists).

Named google_score_report_export (not score_report_export) to avoid
colliding with the unrelated, pre-existing score_report_export.py, which
writes parsed SAT/PSAT "Score Details" PDF rows to a plain .xlsx -- a
different pipeline entirely (see score_report.py) that has nothing to do
with Drive or this org's Sheets templates.
"""
from __future__ import annotations

import datetime as dt
from typing import List, Mapping, Optional

from googleapiclient.discovery import Resource

from .google_report_export_common import export_filled_report
from .score_report_writer import QuestionKey, fill_score_report


def export_score_report(
    drive: Resource,
    templates_root_folder_id: str,
    category_path: List[str],
    test_code: str,
    answers: Mapping[QuestionKey, str],
    student_name: str,
    test_date: dt.date | str,
    output_name: str,
    temp_folder_id: Optional[str] = None,
) -> bytes:
    """Return the filled report's PDF bytes -- see
    google_report_export_common.export_filled_report for what
    `category_path`/`test_code`/`temp_folder_id`/cleanup semantics mean;
    this just binds the ACT-specific fill step to it."""
    return export_filled_report(
        drive,
        templates_root_folder_id,
        category_path,
        test_code,
        output_name,
        fill_fn=lambda tmp_path: fill_score_report(tmp_path, answers, student_name, test_date),
        temp_folder_id=temp_folder_id,
    )
