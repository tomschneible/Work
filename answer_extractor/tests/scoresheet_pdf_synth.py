"""Build a minimal synthetic ACT-style "ScoreSheet" grid PDF for testing
answer_extractor.score_report_pdf_reader, without needing to commit a real
(and likely privacy-sensitive) score report. Mirrors score_report_synth.py's
approach (raw fitz.Page.insert_text calls at chosen coordinates) but for the
column-group grid layout instead of the linear Question/Section/... list.

Column positions are made up (not the real template's exact numbers -- see
score_report_pdf_reader.py's module docstring for how those were reverse
engineered from a real file) but reproduce the same relative structure the
parser actually depends on: a fixed x-offset from each group's "Correct"
header to its "Your"/mark/Category columns, a subject title ~16pt above its
header row, and each row's mark glyph rendered ~1.7pt above the rest of
that row's baseline -- the offset that (per score_report_pdf_reader's
docstring) originally broke naive row-clustering.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

import fitz

# The mark glyphs (✔/✘/ø) aren't in PyMuPDF's built-in base-14 fonts (a
# plain insert_text with the default font silently substitutes an
# unrelated character, which get_text then reads back instead of what was
# asked for) -- DejaVu Sans covers them and is present on essentially any
# Debian/Ubuntu box (fonts-dejavu-core), same family other Linux test
# environments already have. Tests using this module skip (rather than
# fail) if it's missing -- see MARK_FONT_PATH's docstring note below.
MARK_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

GROUP_WIDTH = 170.0
LEFT_MARGIN = 40.0
YOUR_OFFSET = 33.0
CATEGORY_OFFSET = 76.0
MARK_OFFSET = 54.0
NUMBER_OFFSET = -20.0
TITLE_Y_OFFSET = -16.0
CATEGORY_HEADER_Y_OFFSET = 3.2
ANSWER_HEADER_Y_OFFSET = 6.6
FIRST_ROW_Y_OFFSET = 14.0
ROW_HEIGHT = 8.1
MARK_Y_OFFSET = -1.7
BLOCK_GAP = 40.0


class Row(NamedTuple):
    question: int
    correct: str
    your: Optional[str]  # None means omitted -- no "Your Answer" rendered, mark is "ø"


# One column-group's rows.
Group = List[Row]


class Block(NamedTuple):
    subject1: str
    subject2: str
    # >=2 groups, evenly split: the first half governed by subject1, the
    # second half by subject2 -- same convention the real template uses.
    groups: List[Group]


def _mark_for(row: Row) -> str:
    if row.your is None:
        return "ø"
    return "✔" if row.your == row.correct else "✘"


def write_scoresheet_pdf(
    path: str | Path,
    blocks: Sequence[Block],
    student_name: str = "Jane Student",
    test_date: str = "January 2026",
    trailing_legend: bool = False,
    page_height: float = 792.0,
) -> None:
    """Write `blocks` as a ScoreSheet-style grid to a fresh single-page PDF
    at `path`. `trailing_legend` additionally writes a multi-line category
    legend block reusing the first group's own column position, several
    points below that group's last real row -- reproducing the real
    template's category-legend footer, to test that it doesn't get
    mistaken for more data rows (see score_report_pdf_reader.py's module
    docstring)."""
    max_groups = max((len(b.groups) for b in blocks), default=0)
    page_width = LEFT_MARGIN + max_groups * GROUP_WIDTH + 40.0

    doc = fitz.open()
    page = doc.new_page(width=page_width, height=page_height)
    page.insert_font(fontname="F0", fontfile=MARK_FONT_PATH)

    def text(x: float, y: float, s: str, size: float = 8.0) -> None:
        page.insert_text((x, y), s, fontsize=size, fontname="F0", fontfile=MARK_FONT_PATH)

    text(LEFT_MARGIN, 40.0, f"Name: {student_name}")
    text(LEFT_MARGIN, 52.0, f"Test Date: {test_date} Score Sheet")

    header_y = 100.0
    for block in blocks:
        half = len(block.groups) // 2
        group_xs = [LEFT_MARGIN + gi * GROUP_WIDTH for gi in range(len(block.groups))]

        text(group_xs[0], header_y + TITLE_Y_OFFSET, block.subject1)
        text(group_xs[half], header_y + TITLE_Y_OFFSET, block.subject2)

        for gx in group_xs:
            text(gx, header_y, "Correct")
            text(gx + YOUR_OFFSET, header_y, "Your")
            text(gx + CATEGORY_OFFSET, header_y + CATEGORY_HEADER_Y_OFFSET, "Category")
            text(gx, header_y + ANSWER_HEADER_Y_OFFSET, "Answer")
            text(gx + YOUR_OFFSET, header_y + ANSWER_HEADER_Y_OFFSET, "Answer")

        max_rows = max(len(g) for g in block.groups)
        for gx, group in zip(group_xs, block.groups):
            for ri, row in enumerate(group):
                row_y = header_y + FIRST_ROW_Y_OFFSET + ri * ROW_HEIGHT
                text(gx + NUMBER_OFFSET, row_y, str(row.question))
                text(gx, row_y, row.correct)
                if row.your is not None:
                    text(gx + YOUR_OFFSET, row_y, row.your)
                text(gx + MARK_OFFSET, row_y + MARK_Y_OFFSET, _mark_for(row))
                text(gx + CATEGORY_OFFSET, row_y, "CAT")

            if trailing_legend and group:
                legend_y = header_y + FIRST_ROW_Y_OFFSET + len(group) * ROW_HEIGHT + ROW_HEIGHT
                text(gx + CATEGORY_OFFSET, legend_y, f"{block.subject1} Categories")
                text(gx + CATEGORY_OFFSET, legend_y + ROW_HEIGHT, "CAT Some Category Name")

        header_y = header_y + FIRST_ROW_Y_OFFSET + max_rows * ROW_HEIGHT + BLOCK_GAP

    doc.save(str(path))
    doc.close()
