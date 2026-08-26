#!/bin/bash
# Wrapper invoked by the "Answer Extractor.app" Automator droplet (see
# README.md "macOS drag-and-drop app" section). Not meant to be run
# directly by hand, though it works fine that way too:
#
#   scripts/mac_droplet.sh sheet1.pdf sheet2.jpg score_details.pdf ...
#
# Takes the dropped file/folder paths as arguments and runs them through
# answer_extractor.auto_cli, which auto-detects whether each one is a
# scanned bubble sheet or a text-based score-report PDF and routes it
# accordingly -- both kinds can be dropped together in one go, landing as
# separate tabs in one spreadsheet. Bubble sheets also get their template
# (which sheet format it is) auto-detected individually, so different
# formats can be dropped together too -- set ANSWER_EXTRACTOR_TEMPLATE to
# force one fixed template instead, e.g. if a particular sheet's format
# doesn't auto-detect cleanly. A sheet/report auto_cli exports individually
# (see its own module docstring) lands as its own Sheets-report PDF instead
# of a tab here; anything else goes to a combined spreadsheet on the
# Desktop, named after whatever was dropped (single file:
# "<name>_answers.xlsx"; multiple: "<first name>_and_N_others_answers.xlsx",
# never overwriting an existing file of that name), which is opened
# automatically if -- and only if -- anything actually landed in it. GUI
# error/success feedback throughout, since an Automator app has no visible
# terminal to print to.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"  # `python -m answer_extractor.auto_cli` needs the repo root on sys.path
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

# Non-fatal but worth a click-through: e.g. a sheet fell back to the plain
# .xlsx instead of its own Sheets-report PDF. A passive notification is
# too easy to miss for something this consequential, and there's no
# terminal to print it to either.
warn_dialog() {
  local message="$1"
  osascript -e "display alert \"Answer Extractor\" message \"$message\"" >/dev/null 2>&1 || true
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

STDERR_FILE="$(mktemp)"
trap 'rm -f "$STDERR_FILE"' EXIT

set +e
# The ${arr[@]+"${arr[@]}"} form (not plain "${arr[@]}") is required for an
# empty array under `set -u` on macOS's stock bash 3.2, which otherwise
# raises "unbound variable" expanding an empty array.
STDOUT_OUTPUT="$("$VENV_PYTHON" -m answer_extractor.auto_cli --input "$@" ${TEMPLATE_ARGS[@]+"${TEMPLATE_ARGS[@]}"} --output "$OUTPUT" 2>"$STDERR_FILE")"
STATUS=$?
set -e
STDERR_OUTPUT="$(cat "$STDERR_FILE")"

if [ "$STATUS" -ne 0 ]; then
  fail "Scan failed:\n$STDERR_OUTPUT"
fi

# auto_cli prints warnings to stderr for anything non-fatal that still
# changed what you got -- most importantly, a sheet falling back to the
# plain .xlsx instead of its own Sheets-report PDF (Google auth not set
# up, no matching Drive template, a bad filename, ...), but also one line
# per bubble-sheet-shaped page in a batch that just isn't one (routine and
# expected for a multi-page scan bundle where only one page is the real
# answer grid). The run still "succeeded" (you get a valid .xlsx either
# way), so don't lose that explanation just because nothing failed
# outright -- but a real batch can produce dozens of these lines, and
# dumping all of them into a single AppleScript alert produces an
# oversized, effectively uncloseable dialog. So the full text always goes
# to $LOG_FILE, and only a short preview (plus a pointer to the rest, if
# there is more) goes in the dialog itself. $STDOUT_OUTPUT is auto_cli's
# own one-line summary (e.g. "1 score report(s) exported to
# /Users/you/Desktop."), which already says where everything actually
# went, so it's used directly rather than a generic "Wrote <name>".
LOG_FILE="$HOME/Desktop/Answer Extractor - Last Run Warnings.txt"
MAX_DIALOG_CHARS=600

if [ -n "$STDERR_OUTPUT" ]; then
  printf '%s\n' "$STDERR_OUTPUT" >"$LOG_FILE"
  if [ "${#STDERR_OUTPUT}" -gt "$MAX_DIALOG_CHARS" ]; then
    DIALOG_STDERR="$(printf '%s' "$STDERR_OUTPUT" | cut -c1-"$MAX_DIALOG_CHARS")...\n\n(truncated -- full details saved to \"$LOG_FILE\")"
  else
    DIALOG_STDERR="$STDERR_OUTPUT"
  fi
  warn_dialog "$STDOUT_OUTPUT\n\n$DIALOG_STDERR"
else
  notify "$STDOUT_OUTPUT"
fi

# $OUTPUT (the combined .xlsx) is only written when at least one sheet's
# answers actually landed in it -- a sheet that exported as its own
# individual Sheets-report PDF instead needs nothing added there, and if
# every sheet in this run did, $OUTPUT is never created at all. Opening a
# path that doesn't exist would fail the whole script here (and did,
# surfaced as a generic Automator crash dialog instead of the summary
# above) -- the summary already says where the actual report(s) went.
if [ -e "$OUTPUT" ]; then
  open "$OUTPUT"
fi
