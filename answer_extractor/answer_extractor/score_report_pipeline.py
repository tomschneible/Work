"""Bridge between the OMR pipeline's own SheetResult objects and the
Drive-backed score-report export (google_score_report_export.py):
translates a scanned sheet's own filename and QuestionResults into
exactly what export_score_report needs, and decides -- from
SheetResult.has_review_items -- whether to also produce the familiar,
color-coded local .xlsx alongside the report, with a "FLAG" marker on
every output filename produced for a review-worthy sheet, so a flagged
report never looks identical to a clean one in a folder listing.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from googleapiclient.discovery import Resource

from .export import write_xlsx
from .google_score_report_export import export_score_report
from .pipeline import SheetResult
from .scan_filename import ScanFilename, parse_scan_filename
from .scoresheet_grid import normalize_section

# SheetResult.template_name (the matched template YAML's filename stem --
# see template_detect/pipeline.process_path_auto) -> the Drive category
# path under the templates root to search for that test's template file.
# Only ACT's two known bubble-sheet formats are wired to Drive so far --
# see should_export_to_sheets.
_TEMPLATE_NAME_TO_CATEGORY_PATH: Dict[str, List[str]] = {
    "act_answer_sheet": ["ACT", "Enhanced"],
    "legacy_act_answer_sheet": ["ACT", "Legacy"],
}


def should_export_to_sheets(result: SheetResult) -> bool:
    """Whether this sheet's matched template is one of the ones wired to
    the Drive score-report path -- False for anything else (a SAT scan,
    an unrecognized/default template, ...), which callers should instead
    fall back to the plain combined-.xlsx path for, same as before this
    feature existed."""
    return result.template_name in _TEMPLATE_NAME_TO_CATEGORY_PATH


def answers_from_result(result: SheetResult) -> Dict[Tuple[str, int], str]:
    """{(normalized_section, question): answer} for export_score_report.
    Blank and MULTIPLE both come through as "", the same as a genuinely
    omitted bubble -- an ambiguous read must never show up on the report
    looking like a confident answer (this pipeline's standing rule that a
    silent wrong answer is worse than a flagged blank one)."""
    return {
        (normalize_section(q.section), q.question): ("" if q.answer == "MULTIPLE" else q.answer)
        for q in result.questions
    }


def output_base_name(scan: ScanFilename, flagged: bool) -> str:
    suffix = " FLAG" if flagged else ""
    return f"{scan.student_name} - {scan.formatted_test_date}{suffix}"


@dataclasses.dataclass(frozen=True)
class ExportOutcome:
    pdf_path: Path
    # Only set when the sheet had review items -- the familiar
    # color-coded .xlsx, for checking exactly which answers to verify
    # against the original scan before the report goes out.
    xlsx_path: Optional[Path]


def export_sheet_report(
    drive: Resource,
    templates_root_folder_id: str,
    result: SheetResult,
    output_dir: str | Path,
    temp_folder_id: Optional[str] = None,
) -> ExportOutcome:
    """Produce this one sheet's score-report PDF -- and, if it has review
    items, the color-coded .xlsx alongside it -- in `output_dir`.
    `temp_folder_id` is passed straight through to export_score_report
    (see google_report_export_common.export_filled_report).

    Raises ValueError (from scan_filename.parse_scan_filename or
    template_lookup, surfaced through export_score_report) if the sheet's
    own filename doesn't match the expected naming convention, or no
    matching Drive template can be found. Callers processing a batch
    should catch this per-sheet rather than letting one bad filename fail
    the whole run -- the same posture process_path_auto already takes
    toward a sheet whose template can't be identified.
    """
    output_dir = Path(output_dir)
    if result.template_name not in _TEMPLATE_NAME_TO_CATEGORY_PATH:
        raise ValueError(
            f"{result.label!r} matched template {result.template_name!r}, which isn't wired to "
            "the Drive score-report path -- check should_export_to_sheets before calling this."
        )
    scan = parse_scan_filename(result.label)
    category_path = _TEMPLATE_NAME_TO_CATEGORY_PATH[result.template_name]
    flagged = result.has_review_items
    base_name = output_base_name(scan, flagged)
    test_date = scan.test_date if scan.day_known else scan.formatted_test_date

    pdf_bytes = export_score_report(
        drive=drive,
        templates_root_folder_id=templates_root_folder_id,
        category_path=category_path,
        test_code=scan.test_code,
        answers=answers_from_result(result),
        student_name=scan.student_name,
        test_date=test_date,
        output_name=base_name,
        temp_folder_id=temp_folder_id,
    )
    pdf_path = output_dir / f"{base_name}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    xlsx_path = None
    if flagged:
        xlsx_path = output_dir / f"{base_name}.xlsx"
        write_xlsx([result], xlsx_path)

    return ExportOutcome(pdf_path=pdf_path, xlsx_path=xlsx_path)
