"""A native macOS text-input dialog, for the rare piece of data this
pipeline has no automatic source for (currently: a DSAT report's scaled
section scores -- see sat_score_report_writer.py's module docstring).

Uses osascript's `display dialog`, the same mechanism scripts/mac_droplet.sh
already relies on for GUI notifications/alerts (an Automator droplet has no
visible terminal to prompt at otherwise) -- no new dependency, and it
degrades the same way: silently unavailable on a non-Mac or headless test
run, which is exactly why every caller of this gets its prompt function
injected rather than calling osascript directly (see
sat_score_report_pipeline.py, not yet built, for the real caller; tests
pass a fake).
"""
from __future__ import annotations

import subprocess
from typing import Optional

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


def _applescript_string(value: str) -> str:
    """A Python string as an AppleScript string literal -- escapes
    backslashes and double quotes, the only two characters that would
    otherwise break out of the quoted literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
