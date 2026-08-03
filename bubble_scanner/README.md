# Bubble Sheet Scanner

Extracts multiple-choice answers into an Excel spreadsheet, from either:

- **scanned/photographed bubble sheets** (`bubble_scanner.cli`) — image
  processing against a geometry template, described below, or
- **text-based "Score Details" report PDFs** (`bubble_scanner.score_report_cli`)
  such as the College Board SAT/PSAT Suite report — text parsing of the
  "Questions Overview" table, no image processing involved. See "Score
  report PDFs" below; everything else in this README is about the
  bubble-sheet path.

`bubble_scanner.auto_cli` combines both: point it at a mix of scanned
sheets and score-report PDFs and it auto-detects each file's type and
routes it accordingly (see "macOS drag-and-drop app"), which is what the
Mac droplet app uses.

- By default, odd-numbered questions use choices **A, B, C, D** and
  even-numbered questions use **F, G, H, J** — the standard ACT convention
  (`bubble_scanner/template.py:Template.choices_for`). This is configurable
  per template; swap the `choices:` block if your sheet uses the opposite
  mapping.
- Supports sheets with multiple independently-numbered sections (e.g. an
  ACT-style sheet with separate English/Math/Reading/Science tests that
  each restart question numbering at 1), as well as simple single-section
  sheets.
- Detects when more than one bubble is filled in for a question and marks
  it `MULTIPLE` (with the candidates recorded in a cell comment) instead of
  silently picking one.
- Tolerant of imperfect bubbling: partial fills and light pencil marks still
  count as long as they clear a configurable darkness floor, and a
  configurable "closeness to the darkest mark" margin is what actually
  triggers the multiple-answer detection (see Tuning below).
- Ignores printed "dropout" accent-color ink (e.g. the coral bubble
  outlines/letters real ACT sheets print in) instead of misreading it as a
  mark — see Detection below.
- Accepts image files (JPG/PNG/TIFF/BMP), PDFs (each page = one sheet), or a
  directory containing a mix of both.

## Requirements

- Python 3.9+ (macOS doesn't ship Python 3 -- install it via
  [python.org](https://www.python.org/downloads/macos/) or Homebrew).
- On macOS, the practical floor is **macOS 11 (Big Sur) on Apple Silicon /
  macOS 12 (Monterey) on Intel**, set by `requirements.txt`'s
  `opencv-python-headless` pin (`<4.11` -- newer OpenCV 4.x releases only
  ship wheels for macOS 13+/14+, so raising that cap raises this floor;
  see the comment in `requirements.txt`). PyMuPDF, openpyxl, and PyYAML are
  all more lenient or platform-independent, so OpenCV is the binding
  constraint. Linux/Windows aren't version-constrained this way.

## Usage

```bash
pip install -r requirements.txt
python -m bubble_scanner.cli \
  --input scans/ \
  --template templates/act_answer_sheet.yaml \
  --output results.xlsx
```

`--input` accepts one or more images, PDFs, and/or directories of scans in a
single run (`--input a.pdf b.pdf scans/`); everything found is combined into
one spreadsheet.
`templates/act_answer_sheet.yaml` is calibrated for the standard ACT-style
answer sheet (English/Math/Reading/Science); use
`templates/default_template.yaml` as a starting point for any other layout
(see "Building your own template").

## Score report PDFs

Some answers don't come from a scanned sheet at all — services like the
College Board SAT/PSAT Suite provide a "Score Details" PDF export with a
text-based "Questions Overview" table (Question | Section | Correct Answer
| Your Answer | Actions). For these, use the separate `score_report_cli`
instead of the bubble-sheet pipeline — no template or image processing
needed, just text parsing:

```bash
python -m bubble_scanner.score_report_cli \
  --input Score_Details.pdf \
  --output answers.xlsx
```

`--input` accepts one or more PDFs and/or directories of them, combined
into one spreadsheet with columns `Source | Test | Module | Question |
Section | Your Answer`. Only the plain answer value is kept in "Your
Answer" (e.g. `D`, `18`, `11/28`) — the "; Correct"/"; Incorrect" suffix is
dropped, since only what the student answered matters there. "Module"
starts as a plain "Module N" label (N = which occurrence of that section
this is -- these reports commonly have two same-named modules per section,
e.g. two "Reading and Writing" modules, each numbered 1..N, so rows stay
unambiguous even though the raw "Question" number repeats).

This path is implemented in `bubble_scanner/score_report.py` (parsing) and
`bubble_scanner/score_report_export.py` (spreadsheet export); tests build
synthetic report PDFs (`tests/score_report_synth.py`) rather than
committing a real (likely personal/copyrighted) score report.

### Identifying the test and the adaptive module 2 variant

The digital SAT's Reading & Writing and Math sections are each two-stage
adaptive: everyone gets the same Module 1, then Module 2 is one of two
different question sets (easier/harder) depending on Module 1 performance.
A score report never states in plain text which variant was administered
-- that's external knowledge, and grading against the wrong variant's key
would be wrong. `bubble_scanner/answer_keys.py` figures this out using a
signal the report *does* give you: its own "Correct Answer" column (which
we parse but otherwise discard) is Bluebook's ground truth for whatever
was actually administered, so comparing it against a reference key for the
right test/variant should match ~100%, while a wrong test/variant only
agrees by chance (~25% on 4-option questions) -- a clean, high-confidence
signal rather than a fuzzy one.

- Reference keys live in `bubble_scanner/answer_keys/sat_answer_keys.csv`
  (columns `Test, Section, Question, Module1, Module2Easy, Module2Hard`),
  one row per question per test. It's a plain CSV rather than a spreadsheet
  file specifically so it's editable directly in GitHub's web UI (a binary
  .xlsx can only be replaced wholesale there, not edited cell-by-cell).
- To avoid every machine needing a `git pull` whenever a new test's keys
  are added, both CLIs fetch the *latest* copy of that file straight from
  GitHub over HTTPS on each run, cache it locally, and fall back to the
  cache (then the copy bundled in your checkout) if offline -- so editing
  the file on GitHub reaches every machine automatically, and grading still
  works without a network connection using the last-known keys. Pass
  `--no-refresh-keys` to skip the network fetch and use the cache/bundled
  copy outright (e.g. for a fully offline run, or in tests).
- Identification runs automatically and degrades gracefully: with no
  confident match (unknown test, or no reference data at all), the "Test"
  column reads "Unknown" and "Module" stays a plain "Module N" -- it never
  blocks extracting the underlying answers.

To add a new test, append rows to `sat_answer_keys.csv` (via a PR, or
editing directly on GitHub) -- one row per question, with that question's
correct answer for Module 1, the easier Module 2, and the harder Module 2.

## macOS drag-and-drop app

You can turn this into a real `.app` icon on a Mac: drop scanned bubble
sheets, score-report PDFs, or a mix of both onto it, and it writes a
timestamped spreadsheet to your Desktop and opens it automatically. This
still requires a one-time Python setup (there's no way to produce a fully
standalone, dependency-free executable without building on macOS itself),
but after that it's just an icon.

`scripts/mac_droplet.sh` calls `bubble_scanner.auto_cli`, which
auto-detects each dropped file's type (images are always treated as
bubble sheets; a PDF is treated as a score report if it actually parses
as one, otherwise as a scanned bubble sheet) and routes it accordingly.
Each bubble sheet gets its own tab (see "How it works" below), opening on
the first one; any score-report PDFs land in a separate "Score Report
Answers" tab -- all in the same spreadsheet.

This program lives in the `bubble_scanner/` subdirectory of the
`tomschneible/Work` repo (a monorepo -- other unrelated programs may live
in sibling directories at the repo root). All commands and paths below
assume you're inside `bubble_scanner/`, not the repo root.

**One-time setup** (Terminal). Copy this block exactly -- do not leave in
any `<` `>` placeholder characters, since those are shell redirection
operators and will make `git clone` fail with a confusing "no such file or
directory" instead of actually cloning anything. If your repo lives
somewhere other than the URL below, grab the real one from GitHub's green
"Code" button on the repo page:

```bash
git clone https://github.com/tomschneible/Work.git ~/Work
cd ~/Work/bubble_scanner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x scripts/mac_droplet.sh
```

**Build the droplet app** (Automator, no coding required):

1. Open **Automator** (Spotlight → "Automator") → **New Document** → choose
   **Application** → Choose.
2. In the search box on the left, find **"Run Shell Script"** and drag it
   into the empty workflow area on the right.
3. At the top of that action, set **Shell** to `/bin/bash` and **Pass
   input** to **"as arguments"**.
4. Replace the placeholder script text with this one line (replace the path
   with wherever you actually cloned the repo in step 1):

   ```bash
   ~/Work/bubble_scanner/scripts/mac_droplet.sh "$@"
   ```
5. **File → Save**, name it `Bubble Sheet Scanner`, and save it as an
   **Application** (e.g. to your Desktop or Applications folder).

**To use it:** drag one or more scanned PDFs/images and/or score-report
PDFs onto the app's icon, in any mix. It writes
`~/Desktop/scanned_answers_<timestamp>.xlsx` and opens it. Errors (e.g.
missing setup, a page the pipeline couldn't read) show as a macOS alert
dialog instead of silently failing, since a droplet app has no visible
terminal.

By default any bubble-sheet inputs are scored against
`templates/act_answer_sheet.yaml` (score-report PDFs don't need a template
at all). To point bubble-sheet scoring at a different template, add an env
var line before the script call in the same Automator action:

```bash
export BUBBLE_TEMPLATE=~/Work/bubble_scanner/templates/default_template.yaml
~/Work/bubble_scanner/scripts/mac_droplet.sh "$@"
```

## How it works

1. **Load** (`bubble_scanner/loading.py`) — reads images directly, or
   rasterizes PDF pages via PyMuPDF.
2. **Align** (`bubble_scanner/align.py`) — finds the sheet's outer rectangular
   border (or page edge) in the photo/scan and perspective-warps it to the
   template's reference page size, so skewed or off-center photos still line
   up with the template's bubble coordinates. If no clean border is found,
   the image is resized directly and the result is flagged in the output
   ("Alignment" column) so you know to double check that sheet.
3. **Template** (`bubble_scanner/template.py`) — a YAML file describing the
   sheet's geometry: page size, one or more named **sections** (independently
   numbered question blocks — most sheets have just one), each with one or
   more question columns (start position + row spacing), plus bubble
   spacing/radius and the two choice sets. Bubble pixel coordinates are
   derived from this, not hardcoded.
4. **Locate bubbles** (`bubble_scanner/grid_detect.py`) — a template's
   coordinates are calibrated against one reference render, and real-world
   inputs drift from that by more than you'd expect: even a "born-digital"
   PDF at a page size differing by under 1% has been observed to shift
   answers by nearly a full row, enough to read every answer off the wrong
   bubble. So for each section, the actual printed bubbles are detected
   fresh on every sheet (glyph contours clustered into rows/column-groups)
   and matched to the template's known question layout by count and order,
   not absolute position; occasional individual misses (e.g. a heavy mark's
   ink forming an oversized contour) fall back to the nominal position
   corrected by that section's own median observed shift, so one bad
   contour doesn't sink the section. A section only falls back to raw,
   uncorrected template coordinates if detection can't establish the
   expected structure at all (rare, and only on a genuinely poor scan).
5. **Detect marks** (`bubble_scanner/detect.py`) — thresholds the image once
   per sheet using `max(B, G, R)` per pixel (equivalent to HSV "Value")
   rather than grayscale luminance, then measures what fraction of each
   bubble's interior is dark. Using the max channel instead of luminance is
   what lets marks be told apart from printed accent-color ink: a saturated
   color stays bright in whichever channel gives it its hue, while a
   genuine pencil/pen mark is dark in every channel. Per question, a bubble
   counts as "marked" if it clears an absolute floor *and* is within a
   relative margin of the darkest bubble in that question — this is what
   allows partial/light marks through while still catching genuine
   double-bubbling.
6. **Export** (`bubble_scanner/export.py`) — one tab per scanned sheet, with
   a Question column and one column per section (e.g. English, Mathematics,
   Reading, Science) — matching the sheet's own layout rather than one
   column per individual question. The output opens on the first sheet's
   tab. Blank answers are highlighted amber, `MULTIPLE` answers red (with
   the candidate letters in a cell comment), and low-confidence detections
   (marked but only marginally above the floor) are italicized for manual
   review.

## Building your own template

There is no universal bubble sheet layout, so `templates/default_template.yaml`
(single section, 50 questions, 2 columns) is a *starting point* you
calibrate against your actual sheet, and `templates/act_answer_sheet.yaml`
is a ready-to-use, precisely-measured template for the standard ACT-style
answer sheet (English/Math/Reading/Science, 4 sections). To build your own:

1. Scan/photograph your sheet at a reasonable resolution (200+ DPI).
2. Measure (e.g. in any image viewer/editor) the pixel coordinates of the
   first bubble in each column, the spacing between question rows, and the
   spacing between adjacent choice bubbles, all relative to an image resized
   to your chosen `page.width` x `page.height`. If your sheet has multiple
   independently-numbered tests, give each its own `sections` entry.
3. Update `sections`, `bubble_spacing_x`, and `bubble_radius` in a copy of
   the template YAML.
4. Run the pipeline on a test sheet with known answers filled in and check
   `results.xlsx` matches. Adjust `thresholds.fill_ratio_min` /
   `relative_margin` if marks are being missed or over-flagged as multiple.

`Template.validate()` (run automatically by the CLI) will catch structural
mistakes: overlapping/duplicate question numbers, gaps in question
numbering, duplicate section names, or bubble coordinates that fall outside
the page.

## Tuning detection sensitivity

Both knobs live under `thresholds:` in the template YAML:

- `fill_ratio_min` — minimum fraction of a bubble's interior that must be
  dark to count as marked at all. Lower it if light/partial marks are being
  missed (read as blank); raise it if scan noise/smudges are being read as
  answers.
- `relative_margin` — how close another bubble's darkness must be to the
  darkest bubble in the same question to *also* count as marked. This is
  what flags genuine multiple answers. Raise it to catch more borderline
  double-marks; lower it if single answers with slightly uneven bubbling
  are being flagged as `MULTIPLE`.

## Tests

```bash
pip install -r requirements.txt pytest
pytest
```

Tests generate synthetic bubble sheets (`tests/synth.py`) — including
blank, multiple, sloppy/partial marks, and sheets printed in a saturated
dropout accent color — so the detection and alignment logic is verified
without needing to commit a real (copyrighted) scanned sample.
