"""Each Drive template's .xlsx download, kept on this Mac between runs.

Every report downloads its template as an .xlsx to work out which cells
to fill in (see google_sheets_export.py's own module docstring), and a
SAT report downloads a second one for each question's domain and skill.
A download takes a few seconds; asking Drive for a template's version
number takes a fraction of one. So each download is kept here and used
again for as long as that number stays the same. Any change to the
template, by anyone, changes it, and the next report downloads the
template again.

The copies live in ~/.cache/answer_extractor/templates (the
ANSWER_EXTRACTOR_TEMPLATE_CACHE_DIR environment variable moves them). They're
the templates as they sit in Drive, with no student data in them, and
deleting the folder only means each template gets downloaded again the
next time a report needs it. A copy that can't be saved is simply not
kept -- that never stops a report.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from googleapiclient.discovery import Resource

from .google_sheets_export import export_xlsx, file_version

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "answer_extractor" / "templates"


def template_xlsx(drive: Resource, file_id: str) -> bytes:
    """`file_id` as .xlsx bytes, the same as export_xlsx gives -- read from
    the copy kept here when Drive's version number for the file hasn't
    changed since that copy was downloaded, and downloaded (and kept)
    otherwise."""
    version = file_version(drive, file_id)
    if version is None:
        return export_xlsx(drive, file_id)
    cache_dir = Path(os.environ.get("ANSWER_EXTRACTOR_TEMPLATE_CACHE_DIR", _DEFAULT_CACHE_DIR))
    path = cache_dir / file_id / f"{version}.xlsx"
    try:
        return path.read_bytes()
    except OSError:
        pass  # not downloaded yet at this version
    content = export_xlsx(drive, file_id)
    try:
        _keep(path, content)
    except OSError:
        pass  # not kept -- the next report downloads it again
    return content


def _keep(path: Path, content: bytes) -> None:
    """Save `content` at `path` -- whole or not at all, since a half-written
    copy would be read back later as a broken template -- then delete the
    copies of the file's older versions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, partial = tempfile.mkstemp(dir=path.parent, suffix=".partial")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(partial, path)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    for older in path.parent.glob("*.xlsx"):
        if older != path:
            older.unlink(missing_ok=True)
