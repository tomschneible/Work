"""Naming for the files a run writes into its output folder (the Desktop,
by default) -- never over a file that's already there, such as the very
scan a report was made from, dropped from the Desktop under the report's
own name."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def unused_base_name(directory: Path, base_name: str, extensions: Iterable[str]) -> str:
    """`base_name`, or -- when `directory` already has a file by that name
    with any of `extensions` -- the first of "base_name (2)",
    "base_name (3)", ... that's free for all of them: numbered the way
    Finder and the droplet's own combined .xlsx are. One number across
    every extension, so a report's PDF and flagged .xlsx stay a matching
    pair."""
    extensions = list(extensions)
    candidate, n = base_name, 1
    while any((directory / f"{candidate}{ext}").exists() for ext in extensions):
        n += 1
        candidate = f"{base_name} ({n})"
    return candidate
