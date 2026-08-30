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

# Tried, and abandoned: overriding the "Your Question-Level Feedback"
# page's own bottom print margin via export_pdf's bottom_margin_in (see
# its own docstring for why a margin override, rather than another
# column-narrowing pass, looked like the right way to close this
# specific gap). Confirmed live this doesn't just get ignored if wrong --
# passing `bottom_margin=0.25` made Sheets' own export endpoint itself
# fail outright (500 Internal Server Error from the signed
# googleusercontent.com URL it redirects to), taking the whole report
# export down with it -- caught by auto_cli's own per-report fallback (it
# fell back to the combined .xlsx with a warning, exactly as designed),
# but strictly worse than the overflow it was meant to fix: that was a
# two-page PDF, this was no Sheets report at all. Not necessarily true of
# every value or every margin parameter this endpoint takes -- just this
# one, at this value, confirmed once -- but not worth another live
# attempt at guessing a working variant blind. The plumbing this added
# (export_pdf/export_filled_report's own bottom_margin_in) is left in
# place, unused (nothing passes it now) rather than ripped out, in case a
# safer way to drive it surfaces later; nothing calls it with a non-None
# value any more.


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
