"""Combined entry point: auto-detects whether each dropped file is a
scanned bubble sheet or a text-based score-report PDF, and routes it to
the matching pipeline. Both kinds can be mixed in the same run; results
land as separate tabs in one spreadsheet. This is what the macOS droplet
(scripts/mac_droplet.sh) calls -- use answer_extractor.cli or
answer_extractor.score_report_cli directly if you only ever have one kind
of input and want a plain, single-purpose CLI.

Detection: images are always treated as bubble sheets (no text layer to
inspect). PDFs are routed by whether they actually parse as a score
report -- a real score-report PDF always yields at least one answer row
via answer_extractor.score_report.parse_score_report; a scanned/vector-print
bubble sheet PDF has no matching text table and yields none.

Bubble sheets also get their *template* auto-detected per sheet (see
template_detect), rather than assuming every dropped sheet is the same
format -- pass --template to force one fixed template for all of them
instead (e.g. if a sheet's format is genuinely ambiguous to auto-detect,
or for a quick one-off test).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

from openpyxl import Workbook

from .answer_keys import annotate_rows, load_answer_keys
from .export import add_bubble_sheet_answers_sheet
from .loading import IMAGE_SUFFIXES, PDF_SUFFIXES
from .pipeline import SheetResult, UndetectedSheet, process_paths, process_paths_auto
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


def scan_bubble_sheets(
    bubble_paths: List[Path], template_path: str | None
) -> List[SheetResult]:
    """Scan every bubble-sheet input, either against one fixed template
    (if `template_path` is given) or auto-detecting each sheet's template
    individually (see template_detect). Prints a warning to stderr for any
    sheet auto-detection couldn't confidently match rather than failing
    the whole batch over it -- the rest still get scanned and included."""
    if template_path is not None:
        template = Template.from_yaml(template_path)
        template.validate()
        return process_paths(bubble_paths, template)

    results, undetected = process_paths_auto(bubble_paths)
    for sheet in undetected:
        print(f"Warning: couldn't identify {sheet.label}'s template ({sheet.reason})", file=sys.stderr)
    return results


def template_breakdown(results: List[SheetResult]) -> str:
    """" [name: count, ...]" summarizing which auto-detected template each
    sheet matched, or "" when there's nothing to show (a fixed template
    was used, or there's only one sheet)."""
    counts: dict[str, int] = {}
    for r in results:
        if r.template_name:
            counts[r.template_name] = counts.get(r.template_name, 0) + 1
    if len(counts) < 2:
        return ""
    return " [" + ", ".join(f"{name}: {n}" for name, n in counts.items()) + "]"


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
        default=None,
        help=(
            "Template YAML to use for every bubble-sheet input found, skipping auto-detection "
            "(by default, each sheet's template is auto-detected individually -- see "
            "answer_extractor.template_detect -- so a batch can mix sheet formats freely; pass "
            "this to force one fixed template instead)"
        ),
    )
    parser.add_argument("--output", required=True, help="Path to write the combined .xlsx to")
    parser.add_argument(
        "--no-refresh-keys",
        action="store_true",
        help=(
            "Skip fetching the latest answer key reference data over the network; use the "
            "last cached copy (or the one bundled in this checkout) instead"
        ),
    )
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
        results = scan_bubble_sheets(bubble_paths, args.template)
        if results:
            add_bubble_sheet_answers_sheet(wb, results)
            summary_parts.append(f"{len(results)} bubble sheet(s){template_breakdown(results)}")

    if score_rows:
        try:
            library = load_answer_keys(refresh=not args.no_refresh_keys)
            score_rows = annotate_rows(score_rows, library)
        except Exception as exc:  # answer-key identification is a bonus, not required for extraction
            print(f"Warning: answer key identification skipped ({exc})", file=sys.stderr)
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
