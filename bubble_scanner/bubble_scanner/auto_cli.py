"""Combined entry point: auto-detects whether each dropped file is a
scanned bubble sheet or a text-based score-report PDF, and routes it to
the matching pipeline. Both kinds can be mixed in the same run; results
land as separate tabs in one spreadsheet. This is what the macOS droplet
(scripts/mac_droplet.sh) calls -- use bubble_scanner.cli or
bubble_scanner.score_report_cli directly if you only ever have one kind
of input and want a plain, single-purpose CLI.

Detection: images are always treated as bubble sheets (no text layer to
inspect). PDFs are routed by whether they actually parse as a score
report -- a real score-report PDF always yields at least one answer row
via bubble_scanner.score_report.parse_score_report; a scanned/vector-print
bubble sheet PDF has no matching text table and yields none.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

from openpyxl import Workbook

from .export import add_bubble_sheet_answers_sheet
from .loading import IMAGE_SUFFIXES, PDF_SUFFIXES
from .pipeline import process_paths
from .score_report import ScoreReportRow, parse_score_report
from .score_report_export import add_score_report_answers_sheet
from .template import Template


def _iter_files(path: Path) -> List[Path]:
    if path.is_dir():
        files: List[Path] = []
        for child in sorted(path.iterdir()):
            files.extend(_iter_files(child))
        return files
    if path.suffix.lower() in IMAGE_SUFFIXES or path.suffix.lower() in PDF_SUFFIXES:
        return [path]
    return []


def classify_inputs(paths: List[Path]) -> Tuple[List[Path], List[ScoreReportRow]]:
    """Split resolved files into (bubble_sheet_paths, score_report_rows).
    Score-report PDFs are fully parsed here (not just flagged) so callers
    don't need to parse them a second time."""
    bubble_paths: List[Path] = []
    score_rows: List[ScoreReportRow] = []
    for path in paths:
        for file in _iter_files(path):
            if file.suffix.lower() in IMAGE_SUFFIXES:
                bubble_paths.append(file)
                continue
            try:
                rows = parse_score_report(file)
            except Exception:
                rows = []
            if rows:
                score_rows.extend(rows)
            else:
                bubble_paths.append(file)
    return bubble_paths, score_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract answers from a mix of scanned bubble sheets and "
            "text-based score-report PDFs, auto-detecting each input's type."
        )
    )
    parser.add_argument(
        "--input", required=True, nargs="+", help="One or more images, PDFs, and/or directories"
    )
    parser.add_argument(
        "--template",
        default="templates/act_answer_sheet.yaml",
        help=(
            "Template YAML used for any bubble-sheet inputs found "
            "(default: templates/act_answer_sheet.yaml; unused if no bubble sheets are present)"
        ),
    )
    parser.add_argument("--output", required=True, help="Path to write the combined .xlsx to")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    input_paths = [Path(p) for p in args.input]
    missing = [p for p in input_paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"Input path does not exist: {p}", file=sys.stderr)
        return 1

    bubble_paths, score_rows = classify_inputs(input_paths)

    if not bubble_paths and not score_rows:
        print(
            f"No images or PDFs found in: {', '.join(str(p) for p in input_paths)}",
            file=sys.stderr,
        )
        return 1

    wb = Workbook()
    del wb["Sheet"]
    summary_parts = []

    if bubble_paths:
        template = Template.from_yaml(args.template)
        template.validate()
        results = process_paths(bubble_paths, template)
        if results:
            add_bubble_sheet_answers_sheet(wb, results)
            summary_parts.append(f"{len(results)} bubble sheet(s)")

    if score_rows:
        add_score_report_answers_sheet(wb, score_rows)
        num_reports = len({row.source for row in score_rows})
        summary_parts.append(f"{len(score_rows)} score-report question(s) from {num_reports} file(s)")

    if not wb.sheetnames:
        print("No answers could be extracted from the given input.", file=sys.stderr)
        return 1

    wb.save(args.output)
    print(f"Wrote {args.output}: {'; '.join(summary_parts)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
