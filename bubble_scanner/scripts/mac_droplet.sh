#!/bin/bash
# Wrapper invoked by the "Bubble Sheet Scanner.app" Automator droplet (see
# README.md "macOS drag-and-drop app" section). Not meant to be run
# directly by hand, though it works fine that way too:
#
#   scripts/mac_droplet.sh sheet1.pdf sheet2.jpg score_details.pdf ...
#
# Takes the dropped file/folder paths as arguments and runs them through
# bubble_scanner.auto_cli, which auto-detects whether each one is a
# scanned bubble sheet or a text-based score-report PDF and routes it
# accordingly -- both kinds can be dropped together in one go, landing as
# separate tabs in one spreadsheet. Writes the spreadsheet to the Desktop
# named after whatever was dropped (single file: "<name>_answers.xlsx";
# multiple: "<first name>_and_N_others_answers.xlsx", never overwriting an
# existing file of that name) and opens it, with GUI error/success
# feedback since an Automator app has no visible terminal to print to.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"  # `python -m bubble_scanner.auto_cli` needs the repo root on sys.path
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
TEMPLATE="${BUBBLE_TEMPLATE:-$REPO_ROOT/templates/act_answer_sheet.yaml}"

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

FIRST_NAME="$(basename "$1")"
FIRST_STEM="${FIRST_NAME%.*}"
if [ "$#" -eq 1 ]; then
  BASE_NAME="${FIRST_STEM}_answers"
else
  OTHERS=$(( $# - 1 ))
  BASE_NAME="${FIRST_STEM}_and_${OTHERS}_others_answers"
fi

OUTPUT="$HOME/Desktop/${BASE_NAME}.xlsx"
# Don't clobber a previous run's output for the same file(s); number like
# Finder/Chrome downloads do ("name (2).xlsx", "name (3).xlsx", ...).
if [ -e "$OUTPUT" ]; then
  n=2
  while [ -e "$HOME/Desktop/${BASE_NAME} (${n}).xlsx" ]; do
    n=$(( n + 1 ))
  done
  OUTPUT="$HOME/Desktop/${BASE_NAME} (${n}).xlsx"
fi

if [ ! -x "$VENV_PYTHON" ]; then
  fail "Python environment not found at $VENV_PYTHON. Run the one-time setup in README.md (\"macOS drag-and-drop app\") first."
fi

set +e
ERROR_OUTPUT="$("$VENV_PYTHON" -m bubble_scanner.auto_cli --input "$@" --template "$TEMPLATE" --output "$OUTPUT" 2>&1 >/dev/null)"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  fail "Scan failed:\n$ERROR_OUTPUT"
fi

notify "Wrote $(basename "$OUTPUT")"
open "$OUTPUT"
