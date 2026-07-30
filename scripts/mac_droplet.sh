#!/bin/bash
# Wrapper invoked by the "Bubble Sheet Scanner.app" Automator droplet (see
# README.md "macOS drag-and-drop app" section). Not meant to be run
# directly by hand, though it works fine that way too:
#
#   scripts/mac_droplet.sh sheet1.pdf sheet2.jpg ...
#
# Takes the dropped file/folder paths as arguments, runs them through the
# CLI using this repo's venv, writes a timestamped spreadsheet to the
# Desktop, and opens it -- with GUI error/success feedback since an
# Automator app has no visible terminal to print to.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"  # `python -m bubble_scanner.cli` needs the repo root on sys.path
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
TEMPLATE="${BUBBLE_TEMPLATE:-$REPO_ROOT/templates/act_answer_sheet.yaml}"
OUTPUT="$HOME/Desktop/bubble_scan_results_$(date +%Y%m%d_%H%M%S).xlsx"

notify() {
  osascript -e "display notification \"$1\" with title \"Bubble Sheet Scanner\"" >/dev/null 2>&1 || true
}

fail() {
  local message="$1"
  osascript -e "display alert \"Bubble Sheet Scanner\" message \"$message\" as critical" >/dev/null 2>&1 || true
  exit 1
}

if [ "$#" -eq 0 ]; then
  fail "No files were dropped."
fi

if [ ! -x "$VENV_PYTHON" ]; then
  fail "Python environment not found at $VENV_PYTHON. Run the one-time setup in README.md (\"macOS drag-and-drop app\") first."
fi

if [ ! -f "$TEMPLATE" ]; then
  fail "Template not found: $TEMPLATE"
fi

set +e
ERROR_OUTPUT="$("$VENV_PYTHON" -m bubble_scanner.cli --input "$@" --template "$TEMPLATE" --output "$OUTPUT" 2>&1 >/dev/null)"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  fail "Scan failed:\n$ERROR_OUTPUT"
fi

notify "Wrote $(basename "$OUTPUT")"
open "$OUTPUT"
