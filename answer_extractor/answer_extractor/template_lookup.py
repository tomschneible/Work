"""Find the right score-report template in Drive for a given scan,
without hardcoding any folder or file id anywhere.

The org's templates live under one root folder (see README), organized
as category subfolders by name -- SAT/, ACT/Enhanced/, ACT/Legacy/ -- with
one template file per specific test administration inside each, named
like "ACT 25MC1" (a literal "ACT "/"DSAT " prefix plus the same test code
that appears in the scanned sheet's own filename). Both the category
folders and the individual template files are looked up by name at call
time, not by id, so uploading a new template -- or even reorganizing next
year's test codes into it -- never requires a code change here, only that
the naming convention holds.
"""
from __future__ import annotations

from typing import Dict, List

from googleapiclient.discovery import Resource

from .google_sheets_export import list_folder

_SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"


def find_subfolder(drive: Resource, parent_folder_id: str, name: str) -> str:
    """The id of the single subfolder of `parent_folder_id` named `name`
    (case-insensitive). Raises ValueError if none or more than one match
    -- either is a real problem worth failing loudly over, not something
    to guess through."""
    folder_type = "application/vnd.google-apps.folder"
    matches = [
        f
        for f in list_folder(drive, parent_folder_id)
        if f["mimeType"] == folder_type and f["name"].strip().lower() == name.strip().lower()
    ]
    if not matches:
        available = ", ".join(f["name"] for f in list_folder(drive, parent_folder_id)) or "(empty)"
        raise ValueError(f"No {name!r} subfolder in Drive folder {parent_folder_id} (found: {available})")
    if len(matches) > 1:
        raise ValueError(f"More than one {name!r} subfolder in Drive folder {parent_folder_id} -- ambiguous")
    return matches[0]["id"]


def resolve_template_folder(drive: Resource, templates_root_folder_id: str, path: List[str]) -> str:
    """Walk `path` (category names, e.g. ["ACT", "Enhanced"] or ["SAT"])
    down from the templates root, one find_subfolder call per level,
    returning the final folder's id."""
    folder_id = templates_root_folder_id
    for name in path:
        folder_id = find_subfolder(drive, folder_id, name)
    return folder_id


def find_template_file(drive: Resource, folder_id: str, test_code: str) -> Dict[str, str]:
    """The single spreadsheet file in `folder_id` whose name contains
    `test_code` (case-insensitive substring match -- template files are
    named like "ACT 25MC1" or "DSAT 1234", so this matches "25MC1"/"1234"
    parsed from the scanned sheet's own filename against them). Raises
    ValueError if no file matches or more than one does -- an ambiguous
    or missing match almost always means either the wrong category
    folder was searched, or two templates for the same test code exist
    and need cleaning up in Drive, not something this should guess past."""
    candidates = [
        f
        for f in list_folder(drive, folder_id)
        if f["mimeType"] == _SPREADSHEET_MIME_TYPE and test_code.strip().lower() in f["name"].strip().lower()
    ]
    if not candidates:
        available = ", ".join(f["name"] for f in list_folder(drive, folder_id)) or "(empty)"
        raise ValueError(f"No template matching test code {test_code!r} in Drive folder {folder_id} (found: {available})")
    if len(candidates) > 1:
        names = ", ".join(f["name"] for f in candidates)
        raise ValueError(f"More than one template matches test code {test_code!r} in Drive folder {folder_id}: {names}")
    return candidates[0]


def find_file_by_exact_name(drive: Resource, folder_id: str, name: str) -> Dict[str, str]:
    """The single spreadsheet file in `folder_id` named exactly `name`
    (case-insensitive) -- unlike find_template_file's substring match
    against a test code, for a template that isn't duplicated per test
    code at all (the simplified SAT template, which carries no per-test
    content of its own -- see sat_simplified_score_report_writer.py's own
    module docstring for why). Raises ValueError if none or more than one
    match -- either means the file's own name, or which folder was
    searched, needs fixing in Drive, not something to guess past."""
    candidates = [
        f
        for f in list_folder(drive, folder_id)
        if f["mimeType"] == _SPREADSHEET_MIME_TYPE and f["name"].strip().lower() == name.strip().lower()
    ]
    if not candidates:
        available = ", ".join(f["name"] for f in list_folder(drive, folder_id)) or "(empty)"
        raise ValueError(f"No file named {name!r} in Drive folder {folder_id} (found: {available})")
    if len(candidates) > 1:
        raise ValueError(f"More than one file named {name!r} in Drive folder {folder_id} -- ambiguous")
    return candidates[0]
