"""Where each report's filled-in Google Sheet is kept in Drive: the folder
for the day the test was given, in the org's own "Student Tracking" tree --

    Student Tracking/Practice Tests 2026/09 September/12 September/

-- a "Practice Tests YYYY" folder per year, "MM Month" folders inside it,
and a "DD Month" folder inside those for each day a test was given. The
date is the one typed in at the test-date prompt (see
gui_prompt.prompt_for_date), not the input's filename, and any level of
that path that doesn't exist yet -- a new year, a month nobody has made a
folder for, the day itself -- is created on the spot.

"Student Tracking" isn't configured anywhere by id: it's found by walking
up from the "Temporary Files" folder (which sits in "Answer Extractor",
which sits in "Student Tracking") to the nearest folder with that name, so
year/month/day folders only ever get made inside a folder actually called
that. auto_cli's --student-tracking-folder-id skips the search.

A report's Sheet goes to "Temporary Files" instead when there's no date to
file it under (a test-mode run -- the date prompt answered "test", see
gui_prompt.SKIP), or when filing it fails for any reason (no access to
Student Tracking, two folders with the same name, ...): the report itself
still gets made, with a warning saying why its Sheet is there.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from typing import Dict, List, Optional, Tuple

from googleapiclient.discovery import Resource

from .google_sheets_export import FOLDER_MIME_TYPE, create_folder, get_file, list_folder

STUDENT_TRACKING_FOLDER_NAME = "Student Tracking"
# Spelled out rather than taken from calendar.month_name or strftime("%B"),
# which follow the machine's own language settings.
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# How far up to look for Student Tracking before giving up -- it's two
# levels above Temporary Files today.
_MAX_ANCESTOR_LEVELS = 10


def date_folder_names(test_date: dt.date) -> List[str]:
    """The year/month/day folder names for `test_date`, outermost first --
    e.g. ["Practice Tests 2026", "09 September", "12 September"] for
    September 12, 2026."""
    month = _MONTH_NAMES[test_date.month - 1]
    return [f"Practice Tests {test_date.year}", f"{test_date.month:02d} {month}", f"{test_date.day:02d} {month}"]


def _match_key(name: str) -> str:
    """Case, extra spaces, and a number's leading zeros don't make two
    folder names different -- so a hand-made "9 September" gets reused
    rather than joined by a second, "09 September", folder."""
    return re.sub(r"\b0+(?=\d)", "", " ".join(name.split())).lower()


def find_ancestor_folder(drive: Resource, folder_id: str, name: str) -> str:
    """The id of the nearest folder above `folder_id` named `name`. Raises
    ValueError if there isn't one -- or this account can't see far enough
    up to find it, which is what an unshared Student Tracking looks like."""
    passed: List[str] = []
    current = get_file(drive, folder_id)
    for _ in range(_MAX_ANCESTOR_LEVELS):
        parents = current.get("parents") or []
        if not parents:
            break
        current = get_file(drive, parents[0])
        if _match_key(current["name"]) == _match_key(name):
            return current["id"]
        passed.append(current["name"])
    seen = ", ".join(repr(n) for n in passed) or "none visible"
    raise ValueError(
        f"No {name!r} folder above Drive folder {folder_id} (folders above it: {seen}) -- "
        f"this account needs access to {name!r} itself, not just a folder inside it"
    )


def find_or_create_subfolder(drive: Resource, parent_folder_id: str, name: str) -> str:
    """The id of `parent_folder_id`'s subfolder named `name` (see
    _match_key for what counts as the same name), creating it if there
    isn't one. When several match, the one spelled exactly `name` wins;
    raises ValueError if that still doesn't settle it, rather than
    guessing which one the org actually uses."""
    matches = [
        f
        for f in list_folder(drive, parent_folder_id)
        if f["mimeType"] == FOLDER_MIME_TYPE and _match_key(f["name"]) == _match_key(name)
    ]
    if len(matches) > 1:
        exact = [f for f in matches if f["name"] == name]
        if len(exact) != 1:
            names = ", ".join(repr(f["name"]) for f in matches)
            raise ValueError(f"More than one {name!r} folder in Drive folder {parent_folder_id} ({names}) -- ambiguous")
        matches = exact
    if matches:
        return matches[0]["id"]
    return create_folder(drive, parent_folder_id, name)


class ReportFolders:
    """Picks the Drive folder for each report's Sheet (see this module's
    own docstring). Meant to be made once per run: every report from the
    same day shares one lookup, and a folder this run created is
    remembered rather than looked up again -- Drive's own folder listing
    can lag a moment behind a brand-new folder, and looking it up again
    too soon would make a second one."""

    def __init__(self, drive: Resource, fallback_folder_id: str, student_tracking_folder_id: Optional[str] = None):
        self._drive = drive
        self._fallback_folder_id = fallback_folder_id
        self._student_tracking_folder_id = student_tracking_folder_id
        self._folder_ids: Dict[Tuple[str, ...], str] = {}

    def folder_for(self, test_date: Optional[dt.date], student_name: str) -> str:
        """The id of `test_date`'s day folder, made (along with any missing
        folder above it) if needed. The fallback folder instead when
        `test_date` is None (test mode), or -- with a warning on stderr
        naming `student_name` -- when filing fails."""
        if test_date is None:
            return self._fallback_folder_id
        try:
            return self._day_folder(test_date)
        except Exception as exc:
            print(
                f"Warning: couldn't file {student_name}'s Google Sheet under {STUDENT_TRACKING_FOLDER_NAME} "
                f"({exc}); it's in Temporary Files instead.",
                file=sys.stderr,
            )
            return self._fallback_folder_id

    def _day_folder(self, test_date: dt.date) -> str:
        if self._student_tracking_folder_id is None:
            self._student_tracking_folder_id = find_ancestor_folder(
                self._drive, self._fallback_folder_id, STUDENT_TRACKING_FOLDER_NAME
            )
        folder_id = self._student_tracking_folder_id
        path: Tuple[str, ...] = ()
        for name in date_folder_names(test_date):
            path += (name,)
            if path not in self._folder_ids:
                self._folder_ids[path] = find_or_create_subfolder(self._drive, folder_id, name)
            folder_id = self._folder_ids[path]
        return folder_id
