"""A native macOS text-input dialog, for the rare pieces of data this
pipeline has no automatic source for: a DSAT report's scaled section
scores (see sat_score_report_writer.py's module docstring), and -- both
ACT and SAT/DSAT alike -- the actual date a test was taken.

That date used to come straight from the scanned/exported file's own
name (scan_filename.parse_scan_filename's own test_date/day_known) --
confirmed live this isn't reliable as *data*, even when it parses
cleanly: nothing enforces that whoever named a file actually put the
real test date in it rather than a scan/upload date, a placeholder, or
just whatever they remembered at the time, and this org's own naming
conventions drifted enough in practice that trusting it silently
produced wrong dates on real reports. Prompting for it directly --
same category of "no automatic source, ask a person" as a section
score -- replaces that guess with an answer someone's actually
confirming in the moment; see prompt_for_date. The filename is still
this pipeline's only source for a scan's student name, test code, and
which Drive template to use (see scan_filename.py's own docstring) --
only the *date* moved off it, and only for what's actually written into
a report -- ScanFilename.canonical_filename's own output-file naming
convention still reads its date from the input filename, unchanged.

Uses osascript's `display dialog`, the same mechanism scripts/mac_droplet.sh
already relies on for GUI notifications/alerts (an Automator droplet has no
visible terminal to prompt at otherwise) -- no new dependency, and it
degrades the same way: silently unavailable on a non-Mac or headless test
run, which is exactly why every caller of this gets its prompt function
injected rather than calling osascript directly (see
sat_score_report_pipeline.py and score_report_pipeline.py for the real
callers; tests pass a fake).
"""
from __future__ import annotations

import datetime as dt
import subprocess
from typing import Callable, Optional

_APP_TITLE = "Answer Extractor"


def prompt_for_text(message: str, default_answer: str = "") -> Optional[str]:
    """Show a native modal text-input dialog and return what was typed,
    or None if the dialog was cancelled (or osascript itself isn't
    available, e.g. not running on macOS -- treated the same as a
    cancel, since there's no dialog to show either way).
    """
    script = (
        f'display dialog {_applescript_string(message)} '
        f'default answer {_applescript_string(default_answer)} '
        f'with title {_applescript_string(_APP_TITLE)}'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None  # not on macOS -- osascript doesn't exist

    if result.returncode != 0:
        return None  # Cancel button, or Esc

    # osascript prints "button returned:OK, text returned:<what they typed>"
    marker = "text returned:"
    idx = result.stdout.find(marker)
    if idx == -1:
        return None
    return result.stdout[idx + len(marker):].splitlines()[0]


def prompt_for_date(
    prompt_fn: Callable[[str, str], Optional[str]], base_message: str, default_answer: str = ""
) -> Optional[dt.date]:
    """Like `prompt_fn` (e.g. prompt_for_text) itself, but only returns
    once a real calendar date in M/D/YYYY form (e.g. "3/8/2026" -- the
    way this org's own staff write a date by hand, not the zero-padded
    or ISO forms) has actually been entered: re-prompts on anything else
    the same way sat_score_report_pipeline._prompt_for_section_score
    re-prompts on a non-numeric or out-of-range score, and returns None
    if the dialog is ever cancelled (same as `prompt_fn`'s own None --
    never raised here; see this module's own docstring for why a
    cancelled prompt is a per-report ValueError instead, raised by the
    caller, not here).

    `base_message` is the question asked every time -- a bad answer's
    own retry prompt is built fresh from it each time (prefixed with
    what was wrong), never by prefixing the *previous* retry's own
    message, so retrying repeatedly doesn't grow the dialog's own text
    unboundedly."""
    message = base_message
    default = default_answer
    while True:
        raw = prompt_fn(message, default)
        if raw is None:
            return None
        raw = raw.strip()
        try:
            return dt.datetime.strptime(raw, "%m/%d/%Y").date()
        except ValueError:
            default = raw
            message = f"{raw!r} isn't a date in M/D/YYYY form (e.g. 3/8/2026) -- {base_message}"


def _applescript_string(value: str) -> str:
    """A Python string as an AppleScript string literal -- escapes
    backslashes and double quotes, the only two characters that would
    otherwise break out of the quoted literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
