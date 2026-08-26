"""Produce a filled-in DSAT score-report PDF for one student, end to end
-- the SAT counterpart to google_score_report_export.py, sharing the same
Drive-orchestration sequence (google_report_export_common.export_filled_report)
and supplying the SAT-specific fill step instead
(sat_score_report_writer.fill_sat_score_report).

Always searches the "SAT" Drive category -- unlike ACT, there's no
Enhanced/Legacy split (see README).
"""
from __future__ import annotations

import datetime as dt
from typing import Mapping, Optional

from googleapiclient.discovery import Resource

from .google_report_export_common import export_filled_report
from .sat_score_report_writer import SatKey, fill_sat_score_report


def export_sat_score_report(
    drive: Resource,
    sheets: Resource,
    templates_root_folder_id: str,
    test_code: str,
    answers: Mapping[SatKey, str],
    active_variants: Mapping[str, str],
    student_name: str,
    test_date: dt.date | str,
    section_scores: Mapping[str, int],
    output_name: str,
    temp_folder_id: Optional[str] = None,
) -> bytes:
    """Return the filled report's PDF bytes -- see
    google_report_export_common.export_filled_report for `test_code`/
    `temp_folder_id`/cleanup semantics, and
    sat_score_report_writer.fill_sat_score_report for what every other
    argument means; this just binds the SAT-specific fill step to the
    shared orchestration and fixes category_path to ["SAT"]."""
    return export_filled_report(
        drive,
        sheets,
        templates_root_folder_id,
        ["SAT"],
        test_code,
        output_name,
        fill_fn=lambda tmp_path: fill_sat_score_report(
            tmp_path, answers, active_variants, student_name, test_date, section_scores=section_scores
        ),
        temp_folder_id=temp_folder_id,
    )
