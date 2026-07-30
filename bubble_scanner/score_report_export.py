"""Write parsed score-report rows to an .xlsx spreadsheet."""
from __future__ import annotations

from pathlib import Path
from typing import List

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .score_report import ScoreReportRow


def write_score_report_xlsx(rows: List[ScoreReportRow], output_path: str | Path) -> None:
    if not rows:
        raise ValueError("No rows to export")

    wb = Workbook()
    ws = wb.active
    ws.title = "Answers"

    header = ["Source", "Module", "Question", "Section", "Your Answer"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in rows:
        ws.append([row.source, row.module, row.question, row.section, row.your_answer])

    for col_index in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(col_index)].width = 18

    wb.save(str(output_path))
