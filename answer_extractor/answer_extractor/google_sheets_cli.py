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

  3. Fix a template file's own print settings directly (gridlines
     showing, and/or a blank/gridline-covered page below a tab's real
     content when exported to PDF -- see
     google_sheets_export.tighten_print_areas) -- a rare, deliberate
     one-time maintenance operation on the template itself, not
     something the per-report export pipeline does any more (see
     google_sheets_export.py's own module docstring for why editing and
     re-uploading a whole workbook turned out to be unsafe for per-report
     generation). Run this once per template that needs it -- and once
     more against a master template before duplicating it for a new test
     code, so every future duplicate inherits the fix; an
     already-duplicated template still needs its own run.

    python -m answer_extractor.google_sheets_cli tighten-print-area --file-id 1BqZHAVfpbHMW-g0HB5sn10GV3g3eCihK

The folder/file id is the long token in a Drive URL:
https://drive.google.com/drive/folders/<this part>?usp=drive_link
https://docs.google.com/spreadsheets/d/<this part>/edit
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import List, Optional

import openpyxl

from .google_sheets_export import build_services, export_xlsx, list_folder, replace_content, tighten_print_areas


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-folder", help="List every file in a Drive folder")
    list_parser.add_argument("--folder-id", required=True, help="Drive folder id, from its URL")

    tighten_parser = subparsers.add_parser(
        "tighten-print-area",
        help="Fix a template file's own print area/gridlines directly (see tighten_print_areas)",
    )
    tighten_parser.add_argument(
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

    if args.command == "tighten-print-area":
        drive, _sheets = build_services()
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(export_xlsx(drive, args.file_id))
            wb = openpyxl.load_workbook(tmp_path)
            tighten_print_areas(wb)
            wb.save(tmp_path)
            replace_content(drive, args.file_id, tmp_path)
        finally:
            os.unlink(tmp_path)
        print(f"Tightened print areas on {args.file_id} and pushed the fix back.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
