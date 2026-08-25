"""Command-line entry point for checking this tool's own output against an
independently-scored reference score report (see scoresheet_check.py).

    python -m answer_extractor.compare_cli \\
        --ours results.xlsx --reference reference_scores.xlsx \\
        --output comparison.xlsx

`--ours` and `--reference` each accept either shape of score report, picked
by file extension:

  - a .xlsx: `--ours` is one tab of this tool's own exported .xlsx
    (defaults to that workbook's first tab -- pass --ours-tab to pick a
    different one out of a multi-sheet batch export); `--reference` is a
    vendor spreadsheet containing a scored "ScoreSheet" tab (pass
    --reference-tab if it's named something else).
  - a .pdf: a rendered ScoreSheet report -- this pipeline's own PDF
    export, or a report already produced some other way. Carries no
    flag/low-confidence data, so on the --ours side any mismatch against
    it is reported as an unflagged "silent miss" (see
    scoresheet_check.py's module docstring); --ours-tab/--reference-tab
    are ignored for a .pdf.

Both sides can be any mix of the two -- e.g. --ours a PDF this pipeline
just generated and --reference a PDF you already had, to check this run's
output against a report you already trust.
"""
from __future__ import annotations

import argparse
import sys

from .scoresheet_check import (
    compare,
    load_our_answers,
    load_reference_answers,
    summarize,
    write_comparison_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare this tool's extracted answers against a reference score report (.xlsx or .pdf)."
    )
    parser.add_argument("--ours", required=True, help="This tool's own exported .xlsx or .pdf score report")
    parser.add_argument(
        "--ours-tab", default=None, help="Tab name within --ours (default: first tab; ignored for a .pdf)"
    )
    parser.add_argument("--reference", required=True, help="Reference score report to compare against (.xlsx or .pdf)")
    parser.add_argument(
        "--reference-tab",
        default="ScoreSheet",
        help="Tab name within --reference (default: ScoreSheet; ignored for a .pdf)",
    )
    parser.add_argument("--output", required=True, help="Path to write the comparison report .xlsx to")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    reference = load_reference_answers(args.reference, sheet_name=args.reference_tab)
    ours = load_our_answers(args.ours, tab_name=args.ours_tab)
    rows = compare(reference, ours)
    write_comparison_report(rows, args.output)

    print(f"Wrote {args.output}.")
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
