"""Command-line entry point for checking this tool's own output against an
independently-scored reference spreadsheet (see scoresheet_check.py).

    python -m answer_extractor.compare_cli \
        --ours results.xlsx --reference Vinca_Dion_....xlsx \
        --output vinca_comparison.xlsx

`--ours` is one tab of this tool's own exported .xlsx (defaults to that
workbook's first tab -- pass --ours-tab to pick a different one out of a
multi-sheet batch export). `--reference` is the vendor spreadsheet
containing the scored "ScoreSheet" tab (pass --reference-tab if it's named
something else).
"""
from __future__ import annotations

import argparse
import sys

from .scoresheet_check import (
    compare,
    parse_program_output,
    parse_reference_scoresheet,
    summarize,
    write_comparison_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare this tool's extracted answers against a reference ScoreSheet."
    )
    parser.add_argument("--ours", required=True, help="This tool's own exported .xlsx")
    parser.add_argument("--ours-tab", default=None, help="Tab name within --ours (default: first tab)")
    parser.add_argument("--reference", required=True, help="Vendor spreadsheet with a scored ScoreSheet tab")
    parser.add_argument(
        "--reference-tab", default="ScoreSheet", help="Tab name within --reference (default: ScoreSheet)"
    )
    parser.add_argument("--output", required=True, help="Path to write the comparison report .xlsx to")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    reference = parse_reference_scoresheet(args.reference, sheet_name=args.reference_tab)
    ours = parse_program_output(args.ours, tab_name=args.ours_tab)
    rows = compare(reference, ours)
    write_comparison_report(rows, args.output)

    print(f"Wrote {args.output}.")
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
