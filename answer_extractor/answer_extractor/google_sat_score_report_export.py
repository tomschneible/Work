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

# How far the "Your Question-Level Feedback" page's own bottom print
# margin is pulled back from the template's saved default -- see
# google_sheets_export.export_pdf's own docstring for why a margin
# override, not another column-narrowing pass, is what closes this
# specific gap. The template's own saved bottom margin was never read
# directly (no live Sheets access when this was picked); measured off a
# real export's own rendered PDF instead, its top and bottom margins
# both landed close to ~56pt (~0.78in) -- near Sheets' own out-of-the-box
# 0.75in default, consistent with an unmodified template. 0.25in reclaims
# roughly half an inch of extra usable page height, well past the single
# trailing row (and the blank spacer row above it) that kept spilling
# onto its own extra page even once _TABLE_COLUMN_NARROW_FACTOR was
# re-derived to match the reference export's own dominant font size
# almost exactly -- generous rather than tight, since Sheets' own
# pagination didn't track that pixel-level match closely enough to trust
# cutting this any closer. Not yet confirmed live at this specific value,
# same as that factor. SAT-only: ACT's own export path
# (google_score_report_export.py) never passes this, so its reports keep
# whatever margin their own templates already have saved.
_BOTTOM_MARGIN_IN = 0.25


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
        bottom_margin_in=_BOTTOM_MARGIN_IN,
    )
