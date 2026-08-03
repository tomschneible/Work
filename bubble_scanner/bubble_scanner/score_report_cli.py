"""Command-line entry point for extracting answers from text-based score
report PDFs (e.g. College Board SAT/PSAT Suite "Score Details" reports) --
as opposed to scanned bubble sheets, see bubble_scanner.cli for those.

    python -m bubble_scanner.score_report_cli --input Score_Details.pdf --output answers.xlsx

`--input` accepts one or more PDFs and/or directories of them; everything
found is combined into a single spreadsheet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .answer_keys import annotate_rows, load_answer_keys
from .score_report import parse_score_reports
from .score_report_export import write_score_report_xlsx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract 'Your Answer' values from score report PDFs into a spreadsheet."
    )
    parser.add_argument(
        "--input", required=True, nargs="+", help="One or more score-report PDFs and/or directories"
    )
    parser.add_argument("--output", required=True, help="Path to write the .xlsx results to")
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

    rows = parse_score_reports(input_paths)
    if not rows:
        print(f"No answer rows found in: {', '.join(str(p) for p in input_paths)}", file=sys.stderr)
        return 1

    try:
        library = load_answer_keys(refresh=not args.no_refresh_keys)
        rows = annotate_rows(rows, library)
    except Exception as exc:  # answer-key identification is a bonus, not required for extraction
        print(f"Warning: answer key identification skipped ({exc})", file=sys.stderr)

    write_score_report_xlsx(rows, args.output)

    print(f"Processed {len(rows)} question(s) from {len(input_paths)} input(s). Wrote {args.output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
