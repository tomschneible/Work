"""One-off setup utility for the Google Sheets score-report export path --
not part of the main scan-to-spreadsheet pipeline. Two jobs, deliberately
combined into one command you run locally:

  1. Complete the interactive Google OAuth consent (see google_auth) --
     this has to happen wherever a real browser is available, which is
     never the environment this code gets written/reviewed in.
  2. List a Drive folder's contents by id, so a template file's own id
     can be identified (and hardcoded into config, once that config
     exists) from nothing more than the folder link you already have.

    python -m answer_extractor.google_sheets_cli list-folder --folder-id 1hzDrOzqBymstYHdTqjdLxKOmdlbKqSSt

The folder id is the long token in a Drive folder URL:
https://drive.google.com/drive/folders/<this part>?usp=drive_link
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .google_sheets_export import build_services, list_folder


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-folder", help="List every file in a Drive folder")
    list_parser.add_argument("--folder-id", required=True, help="Drive folder id, from its URL")

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

    return 1


if __name__ == "__main__":
    sys.exit(main())
