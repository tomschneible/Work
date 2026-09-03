#!/bin/bash
# Wrapper invoked by the "Answer Extractor - Compare.app" Automator droplet
# (see README.md "macOS drag-and-drop app" section). Not meant to be run
# directly by hand, though it works fine that way too:
#
#   scripts/mac_droplet_compare.sh sheet.pdf reference.xlsx
#
# Takes the dropped file/folder paths as arguments and runs them through
# answer_extractor.auto_compare_cli: scanned bubble sheets and score-report
# PDFs are extracted as usual (each bubble sheet's format auto-detected
# individually -- set ANSWER_EXTRACTOR_TEMPLATE to force one fixed template
# instead), and whichever dropped spreadsheet has a "ScoreSheet" tab (an
# independently-scored reference, e.g. from a test-prep vendor) is compared
# against the scanned answers, adding a color-coded "Comparison" tab to the
# same output workbook. Writes the spreadsheet to the Desktop, named after
# whatever scan was dropped (same convention as mac_droplet.sh), and opens
# it, with GUI error/success feedback since an Automator app has no visible
# terminal to print to.
#
# On a Mac where dropping two files together still launches this app once
# per file instead of once with both (confirmed live -- a real,
# reproducible Finder/Automator behavior, not anything wrong with this
# script; see auto_compare_cli.py's own module docstring), a run can exit
# successfully having written nothing at all yet -- see the $OUTPUT
# existence check below.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"  # `python -m answer_extractor.auto_compare_cli` needs the repo root on sys.path
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
TEMPLATE_ARGS=()
if [ -n "${ANSWER_EXTRACTOR_TEMPLATE:-}" ]; then
  TEMPLATE_ARGS=(--template "$ANSWER_EXTRACTOR_TEMPLATE")
fi

notify() {
  osascript -e "display notification \"$1\" with title \"Answer Extractor\"" >/dev/null 2>&1 || true
}

fail() {
  local message="$1"
  osascript -e "display alert \"Answer Extractor\" message \"$message\" as critical" >/dev/null 2>&1 || true
  exit 1
}

if [ "$#" -eq 0 ]; then
  fail "No files were dropped."
fi

# Name the output after whatever was actually scanned, not the reference
# spreadsheet -- pick the first dropped path that isn't a .xlsx/.xlsm, so
# it doesn't matter which order the two got dropped/selected in.
NAME_SOURCE="$1"
for arg in "$@"; do
  case "$arg" in
    *.xlsx|*.xlsm|*.XLSX|*.XLSM) ;;
    *) NAME_SOURCE="$arg"; break ;;
  esac
done
FIRST_NAME="$(basename "$NAME_SOURCE")"
FIRST_STEM="${FIRST_NAME%.*}"
if [ "$#" -eq 1 ]; then
  BASE_NAME="${FIRST_STEM}_comparison"
else
  OTHERS=$(( $# - 1 ))
  BASE_NAME="${FIRST_STEM}_and_${OTHERS}_others_comparison"
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
# The ${arr[@]+"${arr[@]}"} form (not plain "${arr[@]}") is required for an
# empty array under `set -u` on macOS's stock bash 3.2, which otherwise
# raises "unbound variable" expanding an empty array.
RUN_OUTPUT="$("$VENV_PYTHON" -m answer_extractor.auto_compare_cli --input "$@" ${TEMPLATE_ARGS[@]+"${TEMPLATE_ARGS[@]}"} --output "$OUTPUT" 2>&1)"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  fail "Scan failed:\n$RUN_OUTPUT"
fi

# A successful run doesn't always mean $OUTPUT was actually written --
# auto_compare_cli's own mode 3 can exit 0 having only recorded one half
# of a two-file drop that Finder/Automator split into separate launches
# (see its own module docstring, _PENDING_COMPARE_MARKER) and written
# nothing at all yet, waiting for the second file. Only try to open (and
# only claim to have written) an $OUTPUT that's actually there -- opening
# a path that doesn't exist would fail the whole script here, the same
# reasoning mac_droplet.sh's own combined-.xlsx handling already applies.
if [ -e "$OUTPUT" ]; then
  # auto_compare_cli prints "Wrote <path>: ..." on one line, then (only if
  # a reference was found and matched to exactly one scanned sheet) a
  # comparison summary line -- surface that in the notification when
  # present so you know at a glance whether anything needs a second look,
  # without opening the file first.
  SUMMARY_LINE="$(echo "$RUN_OUTPUT" | sed -n '2p')"
  if [ -n "$SUMMARY_LINE" ]; then
    notify "$(basename "$OUTPUT") -- $SUMMARY_LINE"
  else
    notify "Wrote $(basename "$OUTPUT")"
  fi
  open "$OUTPUT"
else
  notify "$RUN_OUTPUT"
fi
