"""Command-line entry point.

    python -m answer_extractor.cli --input scans/ --template templates/default_template.yaml --output results.xlsx

`--input` accepts one or more paths (files, PDFs, or directories); all
sheets found are combined into a single spreadsheet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .export import write_xlsx
from .pipeline import process_paths
from .template import Template


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan bubble sheets into a spreadsheet.")
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        help="One or more image files, PDF files, and/or directories of scans",
    )
    parser.add_argument(
        "--template", required=True, help="Path to the template YAML describing sheet geometry"
    )
    parser.add_argument("--output", required=True, help="Path to write the .xlsx results to")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    template = Template.from_yaml(args.template)
    template.validate()

    input_paths = [Path(p) for p in args.input]
    missing = [p for p in input_paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"Input path does not exist: {p}", file=sys.stderr)
        return 1

    results = process_paths(input_paths, template)
    if not results:
        print(f"No scans found at: {', '.join(str(p) for p in input_paths)}", file=sys.stderr)
        return 1

    write_xlsx(results, args.output)

    review_count = sum(1 for r in results if r.has_review_items)
    print(f"Processed {len(results)} sheet(s). Wrote {args.output}.")
    if review_count:
        print(f"{review_count} sheet(s) have blank/multiple/low-confidence answers to review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
