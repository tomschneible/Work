"""Write sheet results to an .xlsx spreadsheet."""
from __future__ import annotations

from pathlib import Path
from typing import List

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .pipeline import SheetResult

BLANK_FILL = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
MULTIPLE_FILL = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
LOW_CONFIDENCE_FONT = Font(italic=True, color="808080")


def write_xlsx(results: List[SheetResult], output_path: str | Path) -> None:
    if not results:
        raise ValueError("No results to export")

    num_questions = max(q.question for r in results for q in r.questions)

    wb = Workbook()
    ws = wb.active
    ws.title = "Answers"

    header = ["Sheet", "Alignment", "Needs Review"] + [f"Q{i}" for i in range(1, num_questions + 1)]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for result in results:
        answers_by_question = {q.question: q for q in result.questions}
        row = [
            result.label,
            "contour" if result.used_contour_alignment else "resized (no border found)",
            "YES" if result.has_review_items else "",
        ]
        for i in range(1, num_questions + 1):
            q = answers_by_question.get(i)
            row.append(q.answer if q else "")
        ws.append(row)

        row_index = ws.max_row
        for i in range(1, num_questions + 1):
            q = answers_by_question.get(i)
            if q is None:
                continue
            cell = ws.cell(row=row_index, column=3 + i)
            if q.answer == "MULTIPLE":
                cell.fill = MULTIPLE_FILL
                cell.comment = Comment(", ".join(q.candidates), "bubble_scanner")
            elif q.answer == "":
                cell.fill = BLANK_FILL
            if q.low_confidence:
                cell.font = LOW_CONFIDENCE_FONT

    for col_index in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(col_index)].width = 12

    wb.save(str(output_path))
