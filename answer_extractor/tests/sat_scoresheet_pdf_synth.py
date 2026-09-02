"""Build a minimal synthetic SAT/DSAT "Your Question-Level Feedback" grid
PDF for testing answer_extractor.sat_score_report_pdf_reader, without
needing to commit a real (and privacy-sensitive) score report. Mirrors
scoresheet_pdf_synth.py's own approach (raw fitz.Page.insert_text calls at
chosen coordinates) and reuses its Row/_mark_for -- a question row means
the same thing on both PDF shapes -- but the block structure itself is
different: see sat_score_report_pdf_reader.py's own module docstring for
why (each column-group has its own multi-word title directly above it,
not one shared title governing a pair of groups the way ACT's does).

Column positions are made up (not the real template's exact numbers --
see sat_score_report_pdf_reader.py's own module docstring for how those
were reverse engineered from a real generated report) but reproduce the
same relative structure the parser actually depends on: a group's own
title sitting ~20pt above its header row and often wider than the
group's own answer columns (confirmed against a real report: "R & W
Module 2 - Higher Difficulty" overruns "Correct Answer"/"Your Answer"'s
own combined width), a fixed x-offset from "Correct" to "Your"/mark/
Domain/Skill, and each row's mark glyph rendered ~1.7pt above the rest of
that row's own baseline (score_report_pdf_reader.py's own docstring on
why that offset matters for row-clustering, same reasoning here).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, NamedTuple, Sequence

import fitz

from tests.scoresheet_pdf_synth import MARK_FONT_PATH, Row, _mark_for

GROUP_WIDTH = 190.0
LEFT_MARGIN = 40.0
YOUR_OFFSET = 33.0
DOMAIN_OFFSET = 76.0
SKILL_OFFSET = 112.0
MARK_OFFSET = 54.0
NUMBER_OFFSET = -20.0
TITLE_Y_OFFSET = -20.0
ANSWER_HEADER_Y_OFFSET = 6.6
FIRST_ROW_Y_OFFSET = 14.0
ROW_HEIGHT = 8.1
MARK_Y_OFFSET = -1.7
HEADER_ROW_GAP = 40.0


class SatGroup(NamedTuple):
    title: str  # e.g. "Reading & Writing Module 1", "R & W Module 2 - Higher Difficulty"
    rows: List[Row]


# One header row's worth of side-by-side groups -- typically a subject's
# Module 1 and Module 2, left to right, the real template's own pairing.
SatHeaderRow = List[SatGroup]


def write_sat_scoresheet_pdf(
    path: str | Path,
    header_rows: Sequence[SatHeaderRow],
    student_name: str = "Jane Student",
    test_date: str = "March 8, 2026",
    page_height: float = 792.0,
) -> None:
    max_groups = max((len(hr) for hr in header_rows), default=0)
    page_width = LEFT_MARGIN + max_groups * GROUP_WIDTH + 60.0

    doc = fitz.open()
    page = doc.new_page(width=page_width, height=page_height)
    page.insert_font(fontname="F0", fontfile=MARK_FONT_PATH)

    def text(x: float, y: float, s: str, size: float = 8.0) -> None:
        page.insert_text((x, y), s, fontsize=size, fontname="F0", fontfile=MARK_FONT_PATH)

    text(LEFT_MARGIN, 40.0, "Your Question-Level Feedback")
    text(LEFT_MARGIN, 52.0, f"{student_name}  {test_date}")

    header_y = 100.0
    for header_row in header_rows:
        group_xs = [LEFT_MARGIN + gi * GROUP_WIDTH for gi in range(len(header_row))]

        for gx, group in zip(group_xs, header_row):
            text(gx, header_y + TITLE_Y_OFFSET, group.title)
            text(gx, header_y, "Correct")
            text(gx + YOUR_OFFSET, header_y, "Your")
            text(gx + DOMAIN_OFFSET, header_y, "Domain")
            text(gx + SKILL_OFFSET, header_y, "Skill")
            text(gx, header_y + ANSWER_HEADER_Y_OFFSET, "Answer")
            text(gx + YOUR_OFFSET, header_y + ANSWER_HEADER_Y_OFFSET, "Answer")

            for ri, row in enumerate(group.rows):
                row_y = header_y + FIRST_ROW_Y_OFFSET + ri * ROW_HEIGHT
                text(gx + NUMBER_OFFSET, row_y, str(row.question))
                text(gx, row_y, row.correct)
                if row.your is not None:
                    text(gx + YOUR_OFFSET, row_y, row.your)
                text(gx + MARK_OFFSET, row_y + MARK_Y_OFFSET, _mark_for(row))
                text(gx + DOMAIN_OFFSET, row_y, "CS")
                text(gx + SKILL_OFFSET, row_y, "WIC")

        max_rows = max((len(g.rows) for g in header_row), default=0)
        header_y = header_y + FIRST_ROW_Y_OFFSET + max_rows * ROW_HEIGHT + HEADER_ROW_GAP

    doc.save(str(path))
    doc.close()
