"""One-off setup/maintenance utility for the Google Sheets score-report
export path -- not part of the main scan-to-spreadsheet pipeline. Three
jobs, deliberately combined into one command you run locally:

  1. Complete the interactive Google OAuth consent (see google_auth) --
     this has to happen wherever a real browser is available, which is
     never the environment this code gets written/reviewed in. Run it
     logged into the browser as the org's dedicated account for this
     program, not your own -- whichever Google account approves the
     consent screen is the one every future run acts as.
  2. List a Drive folder's contents by id, so a template file's own id
     can be identified (and hardcoded into config, once that config
     exists) from nothing more than the folder link you already have.
     Also doubles as a check that the account actually has access to the
     folder (an empty result usually means it hasn't been shared yet).

    python -m answer_extractor.google_sheets_cli list-folder --folder-id 1BqZHAVfpbHMW-g0HB5sn10GV3g3eCihK

  3. Turn off a template file's own gridlines directly, via
     google_sheets_export.hide_gridlines -- a pure Sheets API metadata
     change (no file conversion, nothing else on the file touched at
     all) -- a rare, deliberate one-time maintenance operation on the
     template itself, not something the per-report export pipeline does
     any more. An earlier version of this command instead downloaded the
     template as .xlsx, edited it locally, and re-uploaded the whole
     thing -- confirmed live that this corrupted the one real template it
     was tried against (recovered only via Sheets' own version history)
     -- so this never touches .xlsx at all any more. Run this once per
     template that needs it -- and once more against a master template
     before duplicating it for a new test code, so every future
     duplicate inherits the fix; an already-duplicated template still
     needs its own run. Takes one or more --file-id values so a whole
     batch of templates can be fixed in one command instead of editing
     and re-running this once per file; one file's failure (e.g. a typo'd
     id) is reported and skipped rather than stopping the rest.

    python -m answer_extractor.google_sheets_cli hide-gridlines --file-id 1BqZHAVfpbHMW-g0HB5sn10GV3g3eCihK
    python -m answer_extractor.google_sheets_cli hide-gridlines --file-id ID_ONE ID_TWO ID_THREE

  4. Repair the simplified SAT template's own "Student Responses"/
     "Calculations" formulas, via sat_simplified_template_repair.py --
     the same category of one-time, Sheets-API-only template fix as
     hide-gridlines, for a different problem (see that module's own
     docstring for the full why: the simplified template's Module 2
     column deletions broke every score-summary/Domain/Skill-breakdown
     formula that used to also count the three difficulty pairs that
     template no longer has). `--reference-file-id` is any real,
     working *current-format* template (its formula scheme is the same
     across every one, not test-specific) -- read-only, never modified;
     `--target-file-id` is the simplified template actually being fixed.
     Writes one cell at a time, not one big batch: confirmed live against
     a real template with a protected range on it, a single blocked cell
     fails a batched values().batchUpdate() call *entirely* (a 400,
     "trying to edit a protected cell"), silently hiding whether anything
     else in the same batch would also be blocked -- writing individually
     means one blocked cell is reported and skipped the same way
     hide-gridlines already handles one bad file id, so a single run
     surfaces every protected cell needing attention in Sheets (Data ->
     Protected sheets and ranges) at once, not one discovered per retry.

    python -m answer_extractor.google_sheets_cli repair-simplified-calculations \\
      --reference-file-id 1BqZHAVfpbHMW-g0HB5sn10GV3g3eCihK --target-file-id 1AbCdEfGhIjKlMnOpQrS

The folder/file id is the long token in a Drive URL:
https://drive.google.com/drive/folders/<this part>?usp=drive_link
https://docs.google.com/spreadsheets/d/<this part>/edit
"""
from __future__ import annotations

import argparse
import io
import sys
from typing import List, Optional

import openpyxl
from openpyxl.utils import get_column_letter

from .google_sheets_export import build_services, export_xlsx, hide_gridlines, list_folder, write_cells
from .sat_simplified_template_repair import repair_calculations_writes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-folder", help="List every file in a Drive folder")
    list_parser.add_argument("--folder-id", required=True, help="Drive folder id, from its URL")

    hide_gridlines_parser = subparsers.add_parser(
        "hide-gridlines",
        help="Turn off a template file's gridlines directly, via a Sheets API metadata change only",
    )
    hide_gridlines_parser.add_argument(
        "--file-id",
        required=True,
        nargs="+",
        help="One or more Drive file ids of the templates to fix directly (not copies)",
    )

    repair_parser = subparsers.add_parser(
        "repair-simplified-calculations",
        help="Repair the simplified SAT template's own score-summary/Domain/Skill formulas",
    )
    repair_parser.add_argument(
        "--reference-file-id",
        required=True,
        help="A real, working current-format SAT template -- read-only, never modified",
    )
    repair_parser.add_argument(
        "--target-file-id",
        required=True,
        help="The simplified SAT template to fix directly (not a copy)",
    )

    args = parser.parse_args(argv)

    if args.command == "list-folder":
        drive, _sheets = build_services()
        files = list_folder(drive, args.folder_id)
        if not files:
            print("No files found -- either the folder is empty, or this Google account doesn't have access to it.")
            return 0
        for f in files:
            print(f"{f['id']}  {f['mimeType']:<45}  {f['name']}")
        return 0

    if args.command == "hide-gridlines":
        _drive, sheets = build_services()
        failures = 0
        for file_id in args.file_id:
            try:
                hide_gridlines(sheets, file_id)
            except Exception as exc:
                failures += 1
                print(f"Warning: couldn't hide gridlines on {file_id}: {exc}", file=sys.stderr)
            else:
                print(f"Hid gridlines on every sheet of {file_id}.")
        if len(args.file_id) > 1:
            print(f"{len(args.file_id) - failures}/{len(args.file_id)} succeeded.")
        return 1 if failures else 0

    if args.command == "repair-simplified-calculations":
        drive, sheets = build_services()
        reference_wb = openpyxl.load_workbook(
            io.BytesIO(export_xlsx(drive, args.reference_file_id)), data_only=False
        )
        writes = repair_calculations_writes(reference_wb)
        if not writes:
            print(
                f"No matching formulas found in {args.reference_file_id} -- "
                "double check it's a real current-format template, not the simplified one."
            )
            return 1
        failures = 0
        by_sheet: dict[str, int] = {}
        for w in writes:
            coord = f"{w.sheet}!{get_column_letter(w.column)}{w.row}"
            try:
                write_cells(sheets, args.target_file_id, [w])
            except Exception as exc:
                failures += 1
                print(f"Warning: couldn't repair {coord}: {exc}", file=sys.stderr)
            else:
                by_sheet[w.sheet] = by_sheet.get(w.sheet, 0) + 1
        succeeded = len(writes) - failures
        breakdown = ", ".join(f"{n} on {sheet!r}" for sheet, n in by_sheet.items()) or "none"
        print(f"Repaired {succeeded}/{len(writes)} formulas on {args.target_file_id} ({breakdown}).")
        if failures:
            print(
                f"{failures} cell(s) couldn't be written -- likely a protected range on the target "
                "(Data -> Protected sheets and ranges in Sheets). Fix protection for the cells listed "
                "above and re-run; already-repaired cells are harmless to write again.",
                file=sys.stderr,
            )
        return 1 if failures else 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
