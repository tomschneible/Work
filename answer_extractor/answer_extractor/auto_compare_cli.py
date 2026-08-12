"""Combined entry point for the "scan + compare" macOS droplet
(scripts/mac_droplet_compare.sh). Two ways to use it, auto-detected from
whatever you drop:

1. Scan + compare: a scanned bubble sheet (and/or a text-based
   score-report PDF) together with a reference spreadsheet that has an
   independently-scored "ScoreSheet" tab. Scans the sheet as normal, then
   adds a "Comparison" tab checking the answers against the reference.

2. Compare only: a spreadsheet this tool *already* exported (e.g. from an
   earlier run of the plain scan droplet) together with a reference
   spreadsheet -- no re-scanning, since the answers are already sitting in
   that file. Just appends the "Comparison" tab to a copy of it.

    python -m answer_extractor.auto_compare_cli \
        --input sheet.pdf reference.xlsx --template ... --output out.xlsx
    python -m answer_extractor.auto_compare_cli \
        --input previous_answers.xlsx reference.xlsx --output out.xlsx

Whichever dropped .xlsx/.xlsm file contains the reference tab (default
"ScoreSheet") is treated as the reference; any *other* .xlsx/.xlsm is
treated as a pre-existing output to compare without scanning; everything
else is routed the same way answer_extractor.auto_cli routes it
(images/PDFs auto-detected as bubble sheets vs. score reports). This is
meant for the common one-student-at-a-time case, not batch comparison --
more than one candidate reference, more than one pre-existing output, or
mixing a pre-existing output with something to actually scan, is a clear
error rather than a guess at which files pair up.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import openpyxl
from openpyxl import Workbook

from .answer_keys import annotate_rows, load_answer_keys
from .auto_cli import classify_inputs
from .export import add_bubble_sheet_answers_sheet
from .pipeline import SheetResult, process_paths
from .score_report_export import add_score_report_answers_sheet
from .scoresheet_check import (
    add_comparison_sheet,
    compare,
    ours_from_results,
    parse_program_output,
    parse_reference_scoresheet,
    summarize,
)
from .template import Template

_XLSX_SUFFIXES = {".xlsx", ".xlsm"}


def _classify_xlsx(
    paths: List[Path], reference_tab: str
) -> Tuple[Optional[Path], List[Path], List[Path]]:
    """Split `paths` into (the one file with `reference_tab` -- the
    reference to compare against, or None if none of them have it, every
    *other* .xlsx/.xlsm -- a pre-existing output of this tool's own, to be
    compared without re-scanning, and everything else (images/PDFs/etc.,
    handled by auto_cli.classify_inputs downstream)."""
    references: List[Path] = []
    existing_outputs: List[Path] = []
    rest: List[Path] = []
    for p in paths:
        if p.suffix.lower() not in _XLSX_SUFFIXES:
            rest.append(p)
            continue
        try:
            wb = openpyxl.load_workbook(p, read_only=True)
        except Exception:
            rest.append(p)  # not a spreadsheet we can actually read -- let it fall through unchanged
            continue
        if reference_tab in wb.sheetnames:
            references.append(p)
        else:
            existing_outputs.append(p)

    if len(references) > 1:
        raise ValueError(
            f"Found {len(references)} spreadsheets with a {reference_tab!r} tab "
            f"({', '.join(p.name for p in references)}) -- drop one reference at a time."
        )
    reference = references[0] if references else None
    return reference, existing_outputs, rest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract answers from a scanned bubble sheet and/or score-report PDFs (or reuse a "
            "spreadsheet this tool already exported), and if a reference spreadsheet was dropped "
            "too, compare the bubble-sheet answers against it."
        )
    )
    parser.add_argument(
        "--input", required=True, nargs="+", help="One or more images, PDFs, spreadsheets, and/or directories"
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
    parser.add_argument(
        "--reference-tab",
        default="ScoreSheet",
        help="Tab name that marks a dropped spreadsheet as the reference to compare against (default: ScoreSheet)",
    )
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

    try:
        reference_path, existing_output_paths, remaining_paths = _classify_xlsx(
            input_paths, args.reference_tab
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    bubble_paths, score_rows = classify_inputs(remaining_paths)

    if existing_output_paths and (bubble_paths or score_rows):
        print(
            "Found both something to scan and an existing results spreadsheet "
            f"({', '.join(p.name for p in existing_output_paths)}) among the dropped files -- "
            "drop one or the other, not both, so it's clear whether to scan or just compare.",
            file=sys.stderr,
        )
        return 1

    if existing_output_paths:
        if len(existing_output_paths) > 1:
            print(
                f"Found {len(existing_output_paths)} spreadsheets that aren't the reference "
                f"({', '.join(p.name for p in existing_output_paths)}) -- drop just one previously "
                "exported results file at a time.",
                file=sys.stderr,
            )
            return 1
        if reference_path is None:
            print(
                f"Found {existing_output_paths[0].name} but no reference spreadsheet (a "
                f"{args.reference_tab!r} tab) to compare it against.",
                file=sys.stderr,
            )
            return 1

        # Compare-only mode: the answers are already sitting in this file
        # from an earlier scan -- reuse its own tab(s) as-is (no
        # re-scanning) and just append a Comparison tab to a copy of it.
        existing_path = existing_output_paths[0]
        wb = openpyxl.load_workbook(existing_path)
        ours = parse_program_output(existing_path)
        reference = parse_reference_scoresheet(reference_path, sheet_name=args.reference_tab)
        rows = compare(reference, ours)
        add_comparison_sheet(wb, rows)
        wb.save(args.output)
        print(f"Wrote {args.output}: compared {existing_path.name} against {reference_path.name}.")
        print(summarize(rows))
        return 0

    if not bubble_paths and not score_rows:
        print(
            f"No images or PDFs found in: {', '.join(str(p) for p in input_paths)}",
            file=sys.stderr,
        )
        return 1

    wb = Workbook()
    del wb["Sheet"]
    summary_parts = []
    bubble_results: List[SheetResult] = []

    if bubble_paths:
        template = Template.from_yaml(args.template)
        template.validate()
        bubble_results = process_paths(bubble_paths, template)
        if bubble_results:
            add_bubble_sheet_answers_sheet(wb, bubble_results)
            summary_parts.append(f"{len(bubble_results)} bubble sheet(s)")

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

    comparison_summary = None
    if reference_path is not None:
        if len(bubble_results) != 1:
            print(
                f"Found a reference spreadsheet ({reference_path.name}) but "
                f"{len(bubble_results)} scanned bubble sheet(s) -- drop exactly one bubble "
                "sheet alongside a reference so the pairing isn't ambiguous. Wrote the scan(s) "
                "without a comparison.",
                file=sys.stderr,
            )
        else:
            reference = parse_reference_scoresheet(reference_path, sheet_name=args.reference_tab)
            ours = ours_from_results(bubble_results[0].questions)
            rows = compare(reference, ours)
            add_comparison_sheet(wb, rows)
            comparison_summary = summarize(rows)
            summary_parts.append(f"compared against {reference_path.name}")

    wb.save(args.output)
    print(f"Wrote {args.output}: {'; '.join(summary_parts)}.")
    if comparison_summary:
        print(comparison_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
