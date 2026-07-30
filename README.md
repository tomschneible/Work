# Bubble Sheet Scanner

Extracts multiple-choice answers from scanned bubble sheets into an Excel
spreadsheet.

- Even-numbered questions use choices **A, B, C, D**; odd-numbered questions
  use **F, G, H, J** (`bubble_scanner/template.py:Template.choices_for`).
- Detects when more than one bubble is filled in for a question and marks
  it `MULTIPLE` (with the candidates recorded in a cell comment) instead of
  silently picking one.
- Tolerant of imperfect bubbling: partial fills and light pencil marks still
  count as long as they clear a configurable darkness floor, and a
  configurable "closeness to the darkest mark" margin is what actually
  triggers the multiple-answer detection (see Tuning below).
- Accepts image files (JPG/PNG/TIFF/BMP), PDFs (each page = one sheet), or a
  directory containing a mix of both.

## Usage

```bash
pip install -r requirements.txt
python -m bubble_scanner.cli \
  --input scans/ \
  --template templates/default_template.yaml \
  --output results.xlsx
```

`--input` may be a single image, a single PDF, or a directory of scans.

## How it works

1. **Load** (`bubble_scanner/loading.py`) — reads images directly, or
   rasterizes PDF pages via PyMuPDF.
2. **Align** (`bubble_scanner/align.py`) — finds the sheet's outer rectangular
   border in the photo/scan and perspective-warps it to the template's
   reference page size, so skewed or off-center photos still line up with
   the template's bubble coordinates. If no clean border is found, the image
   is resized directly and the result is flagged in the output ("Alignment"
   column) so you know to double check that sheet.
3. **Template** (`bubble_scanner/template.py`) — a YAML file describing the
   sheet's geometry: page size, one or more question columns (start
   position + row spacing), bubble spacing/radius, and the two choice sets.
   Bubble pixel coordinates are derived from this, not hardcoded.
4. **Detect** (`bubble_scanner/detect.py`) — for each bubble, thresholds the
   image (Otsu, adaptive to scan brightness) and measures what fraction of
   the bubble's interior is dark. Per question, a bubble counts as "marked"
   if it clears an absolute floor *and* is within a relative margin of the
   darkest bubble in that question — this is what allows partial/light
   marks through while still catching genuine double-bubbling.
5. **Export** (`bubble_scanner/export.py`) — one row per sheet, one column
   per question. Blank answers are highlighted amber, `MULTIPLE` answers
   red (with the candidate letters in a cell comment), and low-confidence
   detections (marked but only marginally above the floor) are italicized
   for manual review. A "Needs Review" column flags any such row.

## Building your own template

There is no universal bubble sheet layout, so `templates/default_template.yaml`
is a *starting point* (50 questions, 2 columns) that you calibrate against
your actual sheet:

1. Scan/photograph your sheet at a reasonable resolution (200+ DPI).
2. Measure (e.g. in any image viewer/editor) the pixel coordinates of the
   first bubble in each column, the spacing between question rows, and the
   spacing between adjacent choice bubbles, all relative to an image resized
   to your chosen `page.width` x `page.height`.
3. Update `columns`, `bubble_spacing_x`, and `bubble_radius` in a copy of
   the template YAML.
4. Run the pipeline on a test sheet with known answers filled in and check
   `results.xlsx` matches. Adjust `thresholds.fill_ratio_min` /
   `relative_margin` if marks are being missed or over-flagged as multiple.

`Template.validate()` (run automatically by the CLI) will catch structural
mistakes: overlapping/duplicate question numbers, gaps in question
numbering, or bubble coordinates that fall outside the page.

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
blank, multiple, and sloppy/partial marks — so the detection and alignment
logic is verified without needing a real scanned sample.
