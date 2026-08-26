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
     needs its own run.

    python -m answer_extractor.google_sheets_cli hide-gridlines --file-id 1BqZHAVfpbHMW-g0HB5sn10GV3g3eCihK

The folder/file id is the long token in a Drive URL:
https://drive.google.com/drive/folders/<this part>?usp=drive_link
https://docs.google.com/spreadsheets/d/<this part>/edit
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .google_sheets_export import build_services, hide_gridlines, list_folder


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
        "--file-id", required=True, help="Drive file id of the template to fix directly (not a copy)"
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
        hide_gridlines(sheets, args.file_id)
        print(f"Hid gridlines on every sheet of {args.file_id}.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
