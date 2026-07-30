# Bubble Sheet Scanner

Extracts multiple-choice answers from scanned bubble sheets into an Excel
spreadsheet.

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

## macOS drag-and-drop app

You can turn this into a real `.app` icon on a Mac: drop one or more PDFs/
images onto it and it writes a timestamped spreadsheet to your Desktop and
opens it automatically. This still requires a one-time Python setup (there's
no way to produce a fully standalone, dependency-free executable without
building on macOS itself), but after that it's just an icon.

**One-time setup** (Terminal):

```bash
git clone <this repo's URL> ~/bubble-sheet-scanner   # or wherever you keep it
cd ~/bubble-sheet-scanner
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
   ~/bubble-sheet-scanner/scripts/mac_droplet.sh "$@"
   ```
5. **File → Save**, name it `Bubble Sheet Scanner`, and save it as an
   **Application** (e.g. to your Desktop or Applications folder).

**To use it:** drag one or more scanned PDFs/images onto the app's icon. It
writes `~/Desktop/bubble_scan_results_<timestamp>.xlsx` and opens it. Errors
(e.g. missing setup, a page the pipeline couldn't read) show as a macOS
alert dialog instead of silently failing, since a droplet app has no
visible terminal.

By default the droplet scores against `templates/act_answer_sheet.yaml`. To
point it at a different template, add an env var line before the script
call in the same Automator action:

```bash
export BUBBLE_TEMPLATE=~/bubble-sheet-scanner/templates/default_template.yaml
~/bubble-sheet-scanner/scripts/mac_droplet.sh "$@"
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
4. **Detect** (`bubble_scanner/detect.py`) — thresholds the image once per
   sheet using `max(B, G, R)` per pixel (equivalent to HSV "Value") rather
   than grayscale luminance, then measures what fraction of each bubble's
   interior is dark. Using the max channel instead of luminance is what lets
   marks be told apart from printed accent-color ink: a saturated color
   stays bright in whichever channel gives it its hue, while a genuine
   pencil/pen mark is dark in every channel. Per question, a bubble counts
   as "marked" if it clears an absolute floor *and* is within a relative
   margin of the darkest bubble in that question — this is what allows
   partial/light marks through while still catching genuine double-bubbling.
5. **Export** (`bubble_scanner/export.py`) — one row per sheet, one column
   per question (named `<Section>_Q<n>`, e.g. `English_Q1`). Blank answers
   are highlighted amber, `MULTIPLE` answers red (with the candidate letters
   in a cell comment), and low-confidence detections (marked but only
   marginally above the floor) are italicized for manual review. A "Needs
   Review" column flags any such row.

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
