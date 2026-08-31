# Answer Extractor

Extracts multiple-choice answers into an Excel spreadsheet, from either:

- **scanned/photographed bubble sheets** (`answer_extractor.cli`) — image
  processing against a geometry template, described below, or
- **text-based "Score Details" report PDFs** (`answer_extractor.score_report_cli`)
  such as the College Board SAT/PSAT Suite report — text parsing of the
  "Questions Overview" table, no image processing involved. See "Score
  report PDFs" below; everything else in this README is about the
  bubble-sheet path.

`answer_extractor.auto_cli` combines both: point it at a mix of scanned
sheets and score-report PDFs and it auto-detects each file's type and
routes it accordingly (see "macOS drag-and-drop app"), which is what the
Mac droplet app uses.

- By default, odd-numbered questions use choices **A, B, C, D** and
  even-numbered questions use **F, G, H, J** — the standard ACT convention
  (`answer_extractor/template.py:Template.choices_for`). This is configurable
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
python -m answer_extractor.cli \
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
python -m answer_extractor.score_report_cli \
  --input Score_Details.pdf \
  --output answers.xlsx
```

`--input` accepts one or more PDFs and/or directories of them, combined
into one spreadsheet with columns `Test | Section / Module | Question |
Your Answer`. Only the plain answer value is kept in "Your Answer" (e.g.
`D`, `18`, `11/28`) — the "; Correct"/"; Incorrect" suffix is dropped,
since only what the student answered matters there. "Section / Module"
reads e.g. "Reading and Writing - Module 1" -- the section always comes
first so it's clear at a glance which module you're looking at, and starts
as a plain "Module N" label (N = which occurrence of that section this is
-- these reports commonly have two same-named modules per section, e.g.
two "Reading and Writing" modules, each numbered 1..N, so rows stay
unambiguous even though the raw "Question" number repeats).

This path is implemented in `answer_extractor/score_report.py` (parsing) and
`answer_extractor/score_report_export.py` (spreadsheet export); tests build
synthetic report PDFs (`tests/score_report_synth.py`) rather than
committing a real (likely personal/copyrighted) score report.

### Identifying the test and the adaptive module 2 variant

The digital SAT's Reading & Writing and Math sections are each two-stage
adaptive: everyone gets the same Module 1, then Module 2 is one of two
different question sets (easier/harder) depending on Module 1 performance.
A score report never states in plain text which variant was administered
-- that's external knowledge, and grading against the wrong variant's key
would be wrong. `answer_extractor/answer_keys.py` figures this out using a
signal the report *does* give you: its own "Correct Answer" column (which
we parse but otherwise discard) is Bluebook's ground truth for whatever
was actually administered, so comparing it against a reference key for the
right test/variant should match ~100%, while a wrong test/variant only
agrees by chance (~25% on 4-option questions) -- a clean, high-confidence
signal rather than a fuzzy one.

- Reference keys live in `answer_extractor/answer_keys/sat_answer_keys.csv`
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

## Google Sheets score reports

**Status: wired into the main auto_cli/droplet pipeline for both ACT
(Enhanced and Legacy) scans and SAT/DSAT score-report PDFs.** This
section covers what's implemented today.

For an ACT scan whose auto-detected bubble-sheet template is one of the
wired formats, or a SAT/DSAT score-report PDF that answer-key
identification can confidently place, results get written into a copy of
the matching Google Sheets template found automatically in your Drive,
then exported as a PDF -- so the final artifact looks exactly like the
score reports you already produce by hand from that template, honoring
whatever print setup (page range, layout) is already saved on the
template (see "The template is filled in" below for how a template's own
gridlines get fixed once, directly, if it needs it). Anything not wired
to this path (an unidentified/unrecognized input, or any bubble sheet
when `--template` forces a fixed one) still goes into the combined
`.xlsx`, same as always.

### Identity: a dedicated org account, not a personal one

This runs as its own Google identity (e.g. a department account), not
whoever's logged into a lab laptop's browser at the time -- the account
this points at needs to already have Drive access to whatever templates
and destination folders it'll use, granted the ordinary way (Share a
folder with its email), same as sharing with a coworker.

### One-time Google Cloud setup

This needs an OAuth client of your own -- there's no shared one, since
whoever holds it can act as this app against your Google account:

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com/)
   (free -- no billing/credit card needed for this). It can live under any
   account that's part of your Workspace org -- doesn't have to be the
   dedicated account itself, since project ownership and "which account's
   Drive access gets used" are separate things (see step 5).
2. **APIs & Services -> Library**: enable the **Google Sheets API** and
   the **Google Drive API**.
3. **APIs & Services -> OAuth consent screen**: User Type **Internal**
   (only available because the project belongs to a Workspace org) --
   this skips Google's app-review process entirely regardless of scope,
   and avoids the 7-day refresh-token expiry that an External app stuck
   in "Testing" status would hit.
4. **APIs & Services -> Credentials -> Create Credentials -> OAuth client
   ID**, application type **Desktop app**. Download the resulting JSON.
5. Save that file as `~/.config/answer_extractor/client_secret.json` (or
   point `ANSWER_EXTRACTOR_GOOGLE_CLIENT_SECRET` at wherever you keep
   it). **Never commit this file** -- it's excluded by `.gitignore` as a
   safety net, but the real protection is just not putting it in the repo
   directory at all.

The first API call after that opens a browser for one-time consent and
caches a refresh token at `~/.cache/answer_extractor/google_token.json`
(or `ANSWER_EXTRACTOR_GOOGLE_TOKEN_CACHE`). **Complete that consent
screen logged in as the dedicated account**, not your own -- whichever
Google account approves it there is the one every future run acts as.
Every call after that is silent, with no browser needed, which is what
makes this workable on an unattended lab laptop. To switch which account
this points at, just delete that cached token file -- the next call
re-prompts for consent, with no changes to the Cloud project or OAuth
client needed.

### Finding a template's file id by hand (setup/debugging only)

```bash
python -m answer_extractor.google_sheets_cli list-folder --folder-id <folder id>
```

The folder id is the long token in a Drive folder link:
`https://drive.google.com/drive/folders/<this part>?usp=drive_link`. This
lists every file directly inside that folder (works for a folder in a
Shared Drive too, as long as the dedicated account has at least Viewer
access to it) with its own file id and name -- also doubles as a check
that the sharing step actually worked, since an unexpectedly empty result
almost always means the folder hasn't been shared with that account yet.
The real pipeline (below) never needs this run by hand -- it finds the
right template itself -- but it's the fastest way to confirm sharing
worked, or to look around the folder tree while debugging.

### How a scan becomes a report

1. **The template is found automatically**, not hardcoded anywhere.
   `template_lookup.py` walks category subfolders by name from one
   configured root (`Testmastergrids`, by default -- override with
   `--templates-root-folder-id` or `$ANSWER_EXTRACTOR_TEMPLATES_ROOT_FOLDER_ID`),
   e.g. `ACT/Enhanced` or `SAT`, then matches a template file by test
   code substring against real template names like `ACT 25MC1` or
   `DSAT 8`. Uploading a new template to the right subfolder is all a new
   test code needs -- no code change. An ambiguous or missing match
   raises, listing what it actually found, rather than guessing.
2. **The student's name, test date, and which category/test code to look
   for all come from the input's own filename** -- for an ACT scan, the
   image/PDF's filename; for SAT, the score-report PDF's. See
   `scan_filename.py` for the exact convention
   (`"LastName, FirstName GradYear ACT/SAT/DSAT TestCode Month [Day] Year"`,
   e.g. `Student, Jane 2027 ACT 25MC1 January 17 2026` or
   `Student, Jane 2027 DSAT 8 March 8 2026`). This is the *only* source
   for those fields; an input dropped in without being renamed to this
   convention first fails to export (with a clear error) and falls back
   to the combined `.xlsx` instead.
3. **The template is filled in via the Sheets API, not by re-uploading a
   whole workbook.** `copy_template` duplicates the live template --
   into the org's "Temporary Files" folder by default (override with
   `--temp-folder-id` or `$ANSWER_EXTRACTOR_TEMP_FOLDER_ID`), not the same
   folder as the real template, so a working copy never sits amid them --
   `export_xlsx` pulls that copy down locally, *read-only*, purely so the
   format-specific writer (`score_report_writer.fill_score_report` for
   ACT, `sat_score_report_writer.fill_sat_score_report` for SAT -- see
   either module's own docstring for what does and doesn't get touched,
   and why they're different enough not to share one implementation) can
   figure out where each field goes; it returns the list of individual
   cell writes needed rather than an edited workbook. Those writes are
   pushed straight into the live Sheet with one `write_cells` call (the
   Sheets API's `values().batchUpdate`) -- an earlier version of this
   round-tripped the *entire* workbook through `.xlsx` (download, edit
   locally, re-upload, letting Drive convert it back to native Sheets
   format), which turned out not to be safe: confirmed live, re-importing
   an openpyxl-authored `.xlsx` didn't reconstruct a merged, centered
   title cell on a tab this pipeline never even touches (the org's own
   "Cover Page") with full fidelity to how Google's own native
   export/import round-trip would -- its text stopped filling its cell,
   even though the underlying value was intact. Writing only the specific
   cells that actually need to change means nothing else on the file is
   ever re-converted through `.xlsx` at all any more, so nothing about a
   tab's own formatting is at risk from this pipeline, no matter what
   Drive's `.xlsx` import does or doesn't preserve faithfully.
   `export_pdf` then renders the final PDF -- via Sheets' own dedicated
   export URL (the same one "File > Download > PDF" in the Sheets UI
   itself uses), not Drive's generic file-export call: confirmed live
   that the generic Drive export doesn't reliably apply a sheet's own
   "fit to height"/"fit to page" print scale the same way the Sheets UI
   export does, even when the setting is genuinely saved correctly on the
   file -- the PDF came out undistorted and overflowing onto an extra
   page regardless. The filled working Sheet copy
   is *kept*, not deleted, by default (this org's own choice -- having
   the live Sheet behind each generated report is useful for
   review/editing and for debugging one that came out wrong); pass
   `keep_working_copy=False` to `export_filled_report` to restore the old
   delete-after-export behavior for a given call. The whole sequence
   lives once in `google_report_export_common.export_filled_report`,
   shared by both formats' own thin wrapper
   (`google_score_report_export.export_score_report`,
   `google_sat_score_report_export.export_sat_score_report`).

   A template's own gridlines showing up in its exported PDF is a
   property of the template file itself, not something a per-report fill
   introduces or can silently fix any more, now that per-report filling
   never re-uploads a workbook -- fix it directly, once, with:

   ```bash
   python -m answer_extractor.google_sheets_cli hide-gridlines --file-id <template file id>
   # or several at once, rather than editing and re-running this per file:
   python -m answer_extractor.google_sheets_cli hide-gridlines --file-id <id one> <id two> <id three>
   ```

   which turns off every sheet's gridlines via one Sheets API metadata
   change per file (`google_sheets_export.hide_gridlines`) -- no file
   conversion involved at all, and nothing else about the file is
   touched. An earlier version of this command instead downloaded the
   template as `.xlsx`, edited it locally, and re-uploaded the whole
   thing (the same round-trip step 3 above moved away from) -- confirmed
   live that doing this to a *template* file directly is exactly as
   unsafe as it was for a per-report copy: it corrupted the org's own
   live "ACT 25MC1" template the one time it was tried, recovered only
   via Sheets' own version history (File → Version history → See version
   history → restore the version from just before). `hide-gridlines`
   never touches `.xlsx` at all, so that failure mode doesn't apply to
   it. Run it once per already-duplicated template that needs it, and
   once more against a master template before duplicating it for a new
   test code so every future duplicate inherits the fix. Given several
   ids at once, one file's failure is reported and skipped rather than
   stopping the rest -- check the output for any "Warning: couldn't hide
   gridlines on ..." lines.
4. **What ACT flags, SAT prompts for.** A blank or MULTIPLE-marked bubble
   always comes through as blank on an ACT report -- never a guessed
   answer. If the sheet has any review items at all (blank/MULTIPLE/
   low-confidence/unreadable/pattern-inferred), the run also writes the
   familiar color-coded `.xlsx` for that one sheet alongside the PDF, and
   both filenames get a `" FLAG"` suffix, so a report that needs a human
   look never looks identical to a clean one in a folder listing (see
   `score_report_pipeline.py`: `should_export_to_sheets`,
   `answers_from_result`, `export_sheet_report`). SAT has no equivalent
   confidence signal to flag on (a parsed PDF's answers are just correct
   text extraction, not an OMR read), but it does need one piece of input
   nothing upstream can supply: each subject's scaled section score,
   which a native macOS dialog prompts for once per report, in the exam's
   own section order (Reading and Writing, then Math -- `_SUBJECT_PROMPT_
   ORDER`, not alphabetical, which would ask for Math first) (see
   `gui_prompt.py`, and `sat_score_report_pipeline.py`'s
   `answers_from_rows`/`active_variants_from_rows`/`export_sat_report` for
   the rest of that glue). Cancelling a prompt, or a Module 2 whose
   difficulty couldn't be confidently identified, falls that report back
   to the combined `.xlsx` the same as any other export failure.
5. **The test date is prompted for too, program-wide -- not read from the
   file's own name any more.** Used to come straight from
   `scan_filename.parse_scan_filename`'s own `test_date`/`day_known`
   (`ScanFilename`'s only source for anything, including this): confirmed
   this org's own filenames don't reliably carry the *real* test date as
   trustworthy data even when they parse cleanly (a scan/upload date, a
   placeholder, or just whatever the person renaming it remembered, not
   necessarily the actual test date) -- an inconsistency in what people
   put there, not a parsing failure. `gui_prompt.prompt_for_date` (same
   native-dialog mechanism as the section-score prompt, re-prompting on
   anything that isn't a real M/D/YYYY date, e.g. "3/8/2026") replaces it
   for both `score_report_pipeline.export_sheet_report` (ACT) and
   `sat_score_report_pipeline.export_sat_report` (SAT/DSAT) -- prompted
   once per report, before any section-score prompts on the SAT side.
   `ScanFilename.canonical_filename`'s own *output-file naming*
   convention is untouched by this and still reads its date from the
   input filename -- only what's actually written into the report as the
   Test Date moved off it.
6. **A SAT/DSAT report only shows the Module 2 variant actually
   administered -- and always in the same place.** The template ships
   with two same-difficulty pairs of Module 2 blocks per subject (Higher
   x2, Lower x2 -- see `sat_score_report_writer.py`'s own module
   docstring on why), but a given student only ever sat one difficulty
   per subject. `fill_sat_score_report` consolidates every subject's real
   answers -- title, correct-answer key, your answer, Domain, Skill --
   into one fixed column (`sat_score_report_writer._canonical_module2_col`,
   always Higher's own canonical block): if a subject's active variant
   already lives there they're written in place as always, otherwise
   those same values are copied in from wherever that subject's real
   block actually sits. Every other Module 2 occurrence -- a subject's
   own non-matching difficulty, and every duplicate/twin -- is left
   completely untouched and cleared and hidden instead (value, border
   formatting, and data validation cleared via
   `sat_score_report_writer.blocks_to_clear` +
   `google_sheets_export.clear_cells`; the same columns hidden outright
   via `sat_score_report_writer.columns_to_hide` +
   `google_sheets_export.hide_columns`), so the report only ever shows
   one Module 2 table per subject.

   Consolidating into one column, rather than just clearing whichever
   columns a subject didn't use in place (an earlier version of this
   worked that way, and got replaced): a subject's block columns are
   reused by column *position* across every other subject stacked
   underneath it (the same fact that makes the shared flag-cell row work
   at all) -- confirmed live against a real filled report where Reading &
   Writing's active variant (Higher) and Math's (Lower) differed, so each
   subject's real answers naturally sat in *different* columns, leaving
   their two Module 2 tables visibly offset from each other on the
   exported report (confirmed against the org's own reference example,
   where both sit at identical column positions instead). Clearing in
   place can't fix an offset like that -- it only hides or shows content
   where it already is, it never moves anything. Consolidating removes
   the offset entirely, and -- since every subject's score-total formulas
   read a fixed handful of cells by absolute address regardless of which
   subject or difficulty -- also means only one flag (the canonical
   column's own) ever needs to be true at a time, rather than a different
   subject-specific flag that formula was never actually built to
   distinguish between.

   `blocks_to_clear` clears each non-canonical column's *entire* height,
   not just the rows a block's own questions occupy: now that nothing
   real is ever left at a non-canonical column for any subject, there's
   nothing left to protect by scoping to rows the way an earlier version
   did. That mattered live: a non-canonical column still held things
   outside any block's own question rows -- a boolean flag cell (whose
   checkbox *widget* persisted even after `clear_cells` cleared its
   value, since a checkbox is a data-validation rule independent of the
   cell's own value -- `clear_cells` now clears data validation too, not
   just value and border) and a repeated "Page X of Y" footer label sitting
   well below the last question row -- both of which a row-scoped clear
   silently left behind, occupying enough of the sheet to force the
   exported PDF to scale down and overflow onto an extra page trying to
   fit them in.

   Clearing a non-canonical column's *values* alone still wasn't enough,
   though -- confirmed live against a fully manual "File > Download >
   PDF" export using "Fit to Page" scale (ruling out the export
   mechanism itself as the cause): a cleared-but-still-present column is
   blank, but it's still full width and still counted in the sheet's
   print area, so "fit to page" was scaling down to fit roughly four
   times the width the one Module 2 table per subject that's actually
   left with content needs -- squeezing the real tables into a small
   corner of the page with a large blank margin around them, both to the
   right (the extra column width) and below (the same scale factor
   applied uniformly to row height too). `columns_to_hide` hides the
   exact same non-canonical columns `blocks_to_clear` clears, via
   `google_sheets_export.hide_columns` (a Sheets API
   `updateDimensionProperties` request, the same metadata change Sheets'
   own right-click "Hide column" makes) -- removing them from the print
   area entirely so "fit to page" scales to what's actually left to
   show. This is safe unconditionally now for the same reason clearing
   is: an earlier version of this codebase tried hiding columns keyed
   only by which difficulty was "active," and abandoned it, because
   Reading & Writing and Math share the same four column positions --
   hiding one difficulty's columns for one subject could hide a
   *different* subject's real answers. Now that every subject's real
   answers always land in the same canonical column first, nothing real
   is ever left in a non-canonical column for anyone, so hiding it is
   never at risk of hiding real data.

   Fixing the width didn't fully fix the sizing, though -- confirmed
   live comparing a real export against the org's own reference example:
   the tables were noticeably wider now, but still confined to roughly
   the top 86% of the page's usable height instead of filling it, the
   leftover space stranded below rather than distributed proportionally.

   The first theory was that this had nothing to do with Module 2 at
   all: the "Student Responses" tab itself carries stray formatting
   (borders, fills -- no actual value) all the way out to row 996, even
   though its real content -- every block, every score cell, the footer
   -- ends at row 64 (confirmed against the real template file:
   `openpyxl`'s own `ws.max_row` reports 996 there), and with no print
   area explicitly set on the file, Sheets' PDF export falls back to the
   sheet's full used range -- so, the theory went, those ~930 empty rows
   were still being counted in the vertical "fit to page" scale
   calculation right alongside the real content. Two different fixes for
   this were tried in turn -- first hiding those rows
   (`updateDimensionProperties`, the row-dimension version of
   `hide_columns`), then, when that measured zero effect, deleting them
   outright (`deleteDimension`, actually shrinking the sheet's row
   count rather than just marking it invisible) -- and **both** measured
   zero effect on the exported PDF: three separate real exports (no row
   fix, rows hidden, rows deleted) came out pixel-for-pixel identical.
   Whatever's setting this sheet's print scale, it evidently isn't
   reading live row state the way it reads live column state (confirmed
   hiding a column *did* measurably change the export). The theory was
   wrong; neither fix stuck around in the code.

   The org then tested Google's own "Custom" print scale directly on the
   master template (bypassing "Fit to Page" entirely) to see whether a
   *static* percentage would be respected any differently. It was: font
   size measurably changed for the first time in this whole
   investigation. But no percentage threaded the needle -- every value
   small enough not to overflow read as too small to be worth printing,
   and nothing in between existed. That result, together with the
   "Fit to Page" numbers, pins down what's actually going on: Sheets'
   print scale is one uniform percentage applied to *both* width and
   height, and width was confirmed to be the tighter of the two (a real
   export's rendered table width already reached the page's full
   available width at "Fit to Page"'s own ~55% scale, while its rendered
   height fell well short of the page's available height at that same
   scale) -- so the scale needed to keep width inside the page was
   smaller than height alone would have required, and that same
   undersized scale then left height under-filled too. There is no
   percentage that fixes this, because the problem isn't the percentage
   -- it's that the *columns* are wide enough to need a smaller
   percentage than the *rows* do.

   Per the org's own instruction, this is fixed by reformatting the
   sheet, not by touching print settings at all:
   `sat_score_report_writer.visible_table_columns_to_narrow` finds every
   *visible* answer-table column (Module 1's own title column through
   the canonical Module 2 block's own last column -- the sidebar is
   deliberately excluded, to avoid clipping or misaligning a graphic
   element this never otherwise touches), and
   `google_sheets_export.narrow_columns` shrinks each one to
   `_TABLE_COLUMN_NARROW_FACTOR` times its own *actual current* pixel
   width -- read live from Sheets itself via a `get` call rather than
   converted from `openpyxl`'s character-unit column width locally,
   since a generic conversion formula wasn't confirmed to match what
   Sheets actually renders. Narrowing the columns that make width the
   tighter constraint lets "fit to page" recompute a larger uniform
   scale on its own (still guaranteed not to overflow -- "fit to page"
   always finds whatever scale fits) -- which, applied uniformly, also
   renders the *untouched* rows taller, filling more of the page's
   actual height as a side effect, without narrowing anything, adjusting
   a print setting, or resizing a single font.

   The first value tried, 0.75, overshot: confirmed live against a real
   export, it filled the page far better (font size measurably bigger,
   matching the reference example's own fill level) but pushed a
   subject's last couple of questions onto a nearly-blank extra page,
   and separately exposed a *different*, previously-latent bug (see next
   paragraph). 0.82 (a pull back toward the original, whole-print-area
   estimate) *also* overshot, once the print area's own centering was
   separately fixed (see the centering fix a few paragraphs down) --
   confirmed live, closing that gap raised the achievable scale enough
   that 0.82 pushed several of a subject's last questions onto a mostly-
   blank extra page, a bigger overflow than 0.75 caused on its own.
   0.90 pulled back further still, and got close: confirmed live, only
   the Math tables' last two rows (of 22 each -- the Reading & Writing
   tables, with more rows but no multi-line wrapped answer cells
   inflating a couple of their row heights, fit in full) spilled onto an
   otherwise-empty extra page, only ~26pt short of fitting. 0.95 pulled
   back again -- confirmed live, down to only the single tallest row (the
   multi-line wrapped answer cell) still spilling over.

   At that point, switched from eyeballing overflow amounts to a
   scale-independent signal instead: a real export's own dominant body
   font size (measured directly off the rendered PDF -- it scales
   linearly with the actual "fit to page" percentage regardless of how
   much content there is, unlike row/overflow counts) compared against
   the same measurement on a real, confirmed-good reference export of
   the same report that never overflows, its own Question-Level Feedback
   page filling all the way down to a bottom margin matching its top
   margin almost exactly -- as tightly filled as this page is meant to
   get. 0.90 measured 6.31pt, 0.95 measured 6.11pt: a consistent, linear
   -4.0pt of font size per +1.0 of factor across the only two live data
   points gathered since the centering fix (both above). The reference
   export measured 5.92pt. Solving that line for 5.92 gives `f =~
   0.9975` -- i.e. once the centering fix was in place, the columns
   barely need narrowing at all to reach the same fill level as the
   reference.

   Confirmed live at 1.0: font size landed at 5.93pt, matching the
   reference almost exactly -- the font-size-matching approach was
   right. Still not quite enough, though: not a table row this time,
   just the page's own trailing footer line (directly below one blank
   spacer row -- nothing structural) spilled onto its own near-empty
   extra page. Measured precisely why: the page's actual usable bottom
   edge sits at ~736pt (mirroring its own ~56pt top margin, and matching
   where the reference's own footer sits, right at 737pt); at 1.0, the
   last drawn content on the page ended at 729pt -- only ~7pt of slack,
   for a footer line that itself needs ~7pt. Matching font size means
   matching per-row height, so that ~7pt gap reads as accumulated
   rounding/measurement slop over ~65 rows of content, not a real
   structural difference from the reference. `_TABLE_COLUMN_NARROW_FACTOR`
   is now 1.09, a small *widen* rather than a narrow -- see below for
   why 0.98 stopped being the right value, and its own comment in
   `sat_score_report_writer.py` for the arithmetic behind every value
   tried, six that came before this and this one.

   0.98 turned out to depend on a bug elsewhere: `columns_to_hide`'s own
   contiguous hidden-column range started one column after the canonical
   block's own last column, not right at it -- leaving exactly one
   column (the spacer between the two) un-narrowed and un-hidden,
   quietly propping up every one of the six values above's own headroom
   without ever being counted in any of their derivations. Fixing that
   (starting the range at the canonical block's own end instead) let
   "fit to page" compute a *larger* scale at the same 0.98 -- confirmed
   live, font size measured 6.32pt there after the fix, not the pre-fix
   5.99pt, overshooting the reference upward and spilling *more* onto
   the extra page, not less. Re-deriving the same font-size-matching fit
   against that new baseline (same slope, since the narrowing mechanism
   itself didn't change; only its intercept shifts by removing a fixed
   width) gives `f =~ 1.085` for 5.92pt -- these columns now need
   widening slightly past their own natural width, not narrowing at all.
   Rounded to 1.09; confirmed live at that value, font size measured
   5.92pt, matching the reference essentially exactly.

   Matching font size that closely still didn't guarantee matching the
   reference's own total page fill, though: the same trailing row still
   spilled onto its own extra page at 1.09, and by an amount that didn't
   track the visible pixel slack the way matching font size (and so
   per-row height) implied it should. Sheets' own pagination evidently
   isn't decided against a continuous post-scale pixel budget the way
   that reasoning assumed -- this factor controls how closely the report
   matches the reference's own density, not reliably whether it fits on
   one page, and by this point it's doing the first job about as well as
   it can. A live test of Sheets' own PDF export endpoint accepting a
   `bottom_margin` query parameter directly (reclaiming page height
   without touching column width, and so without moving the font-size
   match at all) went a different way than hoped: passing
   `bottom_margin=0.25` didn't get ignored or clamped, it made the
   endpoint itself fail outright (a 500 from the signed
   `googleusercontent.com` URL it redirects to), taking the whole
   report's export down with it -- caught by `auto_cli`'s own per-report
   fallback (into the combined `.xlsx`, with a warning, exactly as
   designed), but strictly worse than the overflow it was meant to fix.
   Not necessarily true of every value or every margin parameter this
   endpoint takes, just this one at this value, confirmed once -- but not
   worth another live attempt at guessing a working variant blind, so
   this code path (`export_pdf`'s own `bottom_margin_in`) exists but
   nothing calls it with a non-`None` value any more.

   The recommended fix for this last gap is a one-time edit to the
   template file itself, not another per-export code path: reduce its
   own saved bottom print margin directly (File > Print > Margins >
   Custom in the Sheets UI, e.g. from its current ~0.75-0.78in down to
   around 0.25in) and save it. `export_pdf` already defers to whatever
   print setup is saved on the file for everything it doesn't explicitly
   override (that's how the "fit to page" scale setting itself has
   always worked here) -- a template-level margin change reaches every
   future export automatically, the same way the one-time
   `hide-gridlines` fix above does, with no code involved and none of
   the live-export-breaking risk the query-parameter attempt carried.
   Not yet confirmed live.

   Narrowing far enough also genuinely truncated a block's own title in
   that same real export -- not just visually overlapped by a
   neighboring cell's background, but missing characters outright in
   the exported PDF's own text layer. Confirmed live it wasn't
   `narrow_columns` cutting the text off directly: a *different*
   subject's own canonical Module 2 title, at the exact same narrowed
   column width, rendered completely intact. Since both are literally
   the same physical columns (just different rows), the only
   explanation is that these two title cells never shared the same
   `wrapStrategy` in the template to begin with -- one already tolerated
   overflowing into its blank neighbors, the other didn't -- and only
   narrowing a full-width column that used to paper over the gap made
   that latent inconsistency visible. `google_sheets_export.
   allow_text_overflow` forces `OVERFLOW_CELL` wrap strategy onto every
   active block's own title cell (Module 1's, and whichever cell ends up
   holding each subject's canonical Module 2 title after any
   repositioning) regardless of what the template's own cell already
   had, closing off the dependency on that inconsistency entirely rather
   than hoping a wider column reliably works around it.

   Narrowing had two more side effects, both confirmed live against the
   same real export and both fixed the same way -- by patching up
   whatever the narrowing affected, not by narrowing less:

   - Each active block's own `mark_col` (the ✔/✘ column) can lose its
     own header cell's left border entirely in the exported PDF: that
     header cell is blank (there's no label over the mark column, unlike
     every neighboring one), while the *data* rows below it (never
     blank, always holding a ✔ or ✘) never lose theirs. This one took
     two wrong turns before landing on the actual fix. First,
     `visible_table_columns_to_narrow` simply *excluded* `mark_col` from
     narrowing, leaving it at its own original width -- confirmed live
     this wasn't enough; the border stayed broken. Second, a
     `mark_columns_to_widen` function actively widened the canonical
     block's own `mark_col` to 1.5x its real width instead (reusing
     `narrow_columns` above 1) -- confirmed live *this* wasn't enough
     either, and the same bug even showed up on Module 1's own
     `mark_col` at that point, a column no narrowing fix here had ever
     touched. Column width, in either direction, was never reliably the
     variable. What actually fixed it: `fill_sat_score_report` now
     writes a real, zero-width character (`_MARK_HEADER_NON_BLANK`, a
     U+200B ZERO WIDTH SPACE) into every active block's own `mark_col`
     header cell, matching the one confirmed, consistent distinguishing
     factor between cells that keep their border and cells that don't --
     not blank vs. narrow, just blank vs. not.
   - This sheet's `printOptions horizontalCentered="1"` (confirmed
     against the real template file -- this pipeline never sets it, so
     it was always meant to center the print area horizontally) doesn't
     seem to treat a *hidden* column as having zero width for centering
     purposes, only for rendering: the visible tables came out pushed
     left of center, with a blank gap on the right roughly the width of
     the hidden, non-canonical Module 2 columns -- the same gap between
     "hidden" and "actually gone" already confirmed for trailing rows,
     just showing up in a different computation this time.
     `hidden_columns_to_shrink` narrows those same hidden columns to
     `_HIDDEN_COLUMN_SHRINK_FACTOR` (near-zero) via `narrow_columns`
     too, in addition to `hide_columns` marking them hidden. That alone
     still wasn't quite enough, though: confirmed live the blank *spacer*
     column standing between each pair of non-canonical occurrences (and
     the sheet's own last one) was never included in either fix, since
     both were built from one range *per occurrence*, each exactly
     `_CLEAR_BLOCK_WIDTH` columns wide -- leaving those spacers at their
     own full width, still counted toward centering. `columns_to_hide`
     now returns a single range spanning from the leftmost non-canonical
     occurrence's own title column all the way through the rightmost
     occurrence's own last column, sweeping the spacers up too --
     `hidden_columns_to_shrink` (built from the same function) inherits
     the fix automatically. Confirmed live this actually fixed it --
     the org's own next real export centered correctly.

     One spacer still slipped through even this fix, though: the one
     between the *canonical* block's own last column and the first
     non-canonical occurrence, since the range above started *at* that
     occurrence's own title column, one column later than where the
     canonical block's own end (and `visible_table_columns_to_narrow`'s
     own range) actually stops. `columns_to_hide` now starts its range
     right at the canonical block's own end instead, closing that last
     gap too -- see the font-size-matching narrative below for how this
     one was actually found (a real export's own rendered geometry, not
     a live A/B comparison) and what it changed about
     `_TABLE_COLUMN_NARROW_FACTOR`.
   - Separately, this sheet's own decorative accent bar under "Your
     Question-Level Feedback" (a solid fill spanning a fixed range of
     columns on row 1, confirmed against the real template file to run
     from column A through N) is exactly as wide as the sum of those
     columns' own widths -- so narrowing the table columns inside that
     same range (Module 1's own, from column H on) shrank the bar right
     along with them, leaving it visibly short of where it used to
     reach. `header_bar_extension` (paired with
     `google_sheets_export.extend_fill`) re-applies that same fill color
     -- read live from the sheet itself, not hardcoded -- across the
     rest of the narrowed table's own width, so the bar spans the same
     width as the content sitting below it again. Extracting the
     exported PDF's own raw drawing commands (not just its text) showed
     this fix is doing exactly what it's supposed to -- the bar's blue
     fill genuinely does extend through the canonical block's own last
     column now, immediately followed by a separate, correctly-bounded
     white rectangle covering everything past it. What still reads as
     "cut off short" is really the *centering* gap above -- the bar (tied
     to the same columns as the table it sits above) is left of center
     for the exact same reason the table is, and should move right along
     with it once that's fixed, without needing a fix of its own beyond
     what's already here.
7. **Where files land, and how they're named.** PDFs (and any flagged
   `.xlsx`) are written to the Desktop by default -- override with
   `--report-output-dir` or `$ANSWER_EXTRACTOR_REPORT_OUTPUT_DIR`. Each
   report's own filename (and the kept Google Sheet working copy behind
   it) is the same "LastName, FirstName GradYear TestFamily TestCode
   Month [Day] Year" shape the scan's own input filename was parsed from
   in the first place (`ScanFilename.canonical_filename`, point 2 above)
   -- plus a trailing `" FLAG"` for a flagged ACT sheet (point 4). The
   family token is always exactly whatever the input carried (`ACT`,
   `SAT`, or `DSAT`) -- never a separately-chosen label layered on top, so
   a DSAT report is never redundantly double-labeled ("SAT DSAT ...").

`answer_extractor/auto_cli.py` (what the macOS droplet calls) is where
this is wired in: each auto-detected bubble sheet, and each identified
score-report PDF (grouped by source file -- one PDF, one student -- via
`score_report.group_by_source`), either goes through this path or into
the combined `.xlsx`, per the rules in its own module docstring. If
Google auth isn't set up at all, answer-key identification itself fails,
or one particular sheet/report fails to export on its own, that one falls
back into the combined `.xlsx` with a warning -- never fails the whole
batch.

### The simplified SAT/DSAT template

Everything in "How a scan becomes a report" above about Module 2 --
`blocks_to_clear`, `columns_to_hide`, `hidden_columns_to_shrink`,
`header_bar_extension`, and the whole `_TABLE_COLUMN_NARROW_FACTOR`
saga -- exists for one reason: the current-format template has to
physically hold every Module 2 difficulty pair (Higher x2, Lower x2)
and hide whichever three weren't administered, since which one *was*
isn't known until a specific student's report is being filled. A
template with a single Module 2 slot per subject, filled in directly
once the active variant is known, never needs any of that -- not a
smaller version of the same machinery, none of it, by construction.

This is the live path now: `sat_score_report_pipeline.py`'s own
`export_sat_report` calls `google_sat_simplified_score_report_export.
export_simple_sat_score_report`, not the current-format
`google_sat_score_report_export.export_sat_score_report` any more.
That older function (and `sat_score_report_writer.fill_sat_score_report`
behind it) is still there, still tested, just no longer called from
this pipeline -- kept rather than deleted in case the simplified path
needs a fallback while its real template gets shaken out.

Confirmed live against a real template and a real student, in two
rounds. First round: the title match failed for every Module 2 block --
confirmed the real template's own title cells aren't reliably blank the
way building it from a blank slate might suggest (a placeholder like
"R & W Module 2 - (Enter Difficulty)", or even a stale, pre-baked
difficulty like "Math Module 2 - Higher Difficulty" from however the
template was built) -- `_TITLE_PATTERN` now matches the
"<subject> Module <N>" prefix only, ignoring whatever trails it, and a
Module 2 title is always regenerated from its own subject text (e.g.
"R & W", preserving the template author's own abbreviation) plus the
identified difficulty, never read-and-appended to what was already
there. Second round: the answers landed correctly (confirmed on the
exported PDF's own question-level page), but every score summary and
Domain/Skill breakdown came out blank -- see "Repairing the simplified
template's own formulas" below for why and the fix; that's a one-time
fix to the template file itself, not a code change.

**The current-format template's own role changes, but it doesn't go
away.** It's still made once per test and still used for hand-grading,
same as always -- and it *also* becomes the one real source for a
question's Domain/Skill labels and correct answer, since the simplified
template never carries that content itself (nothing here duplicates it
into a second, separately-maintained source -- see
`sat_score_report_writer.read_reference_questions`'s own docstring).
Reading it back out of a template already made for another reason, via
the exact same block-locating logic (`locate_sat_blocks`) that already
finds these values for consolidation, cost nothing new to build.

**The simplified template itself is not made per test.** Unlike the
current-format one, it carries no per-test content at all -- no
Domain/Skill values, no correct answers, nothing that would differ
between two different DSAT administrations sharing the same module/
question-count shape. Its layout is fixed by the exam *format*, not by
which specific test it's grading, so there's exactly one of it, reused
for every student regardless of test code -- not duplicated the way the
current-format templates are.
`google_sat_simplified_score_report_export.SIMPLIFIED_TEMPLATE_NAME`
(currently `"DSAT TEMPLATE"`) is found by exact name, in its own `SAT
Template` folder -- a sibling of `SAT`, directly under the templates
root, not a subfolder of it -- so `find_template_file`'s own
substring-against-test-code matching inside `SAT/` itself is never at
risk of also matching it. If a differently-shaped exam ever needs its
own version (PSAT 10 and PSAT 8/9 run shorter modules than the full
digital SAT) that becomes a small, fixed set of named templates and a
lookup keyed off whatever already distinguishes them -- still nowhere
near one per test code.

**Building the template itself:** same general shape as the
current-format one's own blocks (a title, then a header row with
"Correct Answer"/"Your Answer" two and three columns to its right and
"Domain"/"Skill" four and five columns to its right, then one row per
question, pre-numbered in the question column same as today), but:

- One Module 2 block per subject, not a Higher/Lower pair -- no flag
  checkbox above it either (nothing to disambiguate any more).
- A block's title doesn't need to be blank -- it isn't trusted either
  way (see the "confirmed live" paragraph above): `fill_simple_sat_score_
  report` always regenerates it from its own subject text (whatever's
  written before "Module 2" -- e.g. "R & W", preserving the template
  author's own abbreviation) plus the identified difficulty, so the
  exported report still reads the same way the current-format one's own
  titles do regardless of what the cell held beforehand.
- Correct Answer/Domain/Skill cells stay blank on the template itself --
  they're filled in from the current-format template's own matching
  block at export time, never present here beforehand.
- correct_col's own font size is filled in from the reference too, the
  same trip: confirmed against a real template pair, the current-format
  template's own correct_col explicitly overrides its font size (12pt),
  but the simplified template's matching cells never got that override
  when it was built, so they silently fell back to the workbook's own
  smaller shared default (10pt, both templates') instead. Copied
  per-question (ReferenceQuestion.correct_answer_font_size ->
  FillResult.font_size_cells -> google_sheets_export.set_font_sizes),
  and only when the reference's own cell actually has an explicit size
  to copy -- nowhere else on the row, since nothing else (the student's
  own answer, Domain, Skill) was confirmed to have this same gap.
- "Score Report"'s own "Digital SAT (test number)" placeholder gets
  overwritten wholesale with the real test number substituted in (e.g.
  "Digital SAT #4"), the same "search by text, regenerate the whole
  cell, don't trust or append to what's there" approach as a Module 2
  block's own title -- a current-format template's own copy has this
  hand-typed in for its one test code; the simplified template, being
  singular across every test code, can't.

### Repairing the simplified template's own formulas

The current-format template's score summaries (a subject's total
correct/incorrect count on "Student Responses") and its per-Domain/
per-Skill breakdown table (on "Calculations") are built for four Module
2 occurrences per subject: one column always counted, three more each
gated behind a boolean flag cell (`if($O$8=TRUE, ..., 0)` and its
`$V$8`/`$AC$8`/`$AJ$8` counterparts). Deleting the three non-canonical
occurrences' own columns to build the simplified template breaks every
formula that referenced them -- confirmed against a real template, the
deleted columns read `#REF!`, and since a spreadsheet error propagates
through addition, every summary and Domain/Skill count built on top
comes out blank. This is exactly what "the answers are right but none
of the calculations got pulled over" looks like from the filled
report's own side: the exported PDF's question-level page reads
"Student Responses" directly and is unaffected; the score-report page's
own totals and per-Domain/Skill breakdown read through these broken
formulas and show nothing.

Fixed with a new one-time, Sheets-API-only repair
(`sat_simplified_template_repair.py`, wired into
`google_sheets_cli.py`'s `repair-simplified-calculations` command --
same category as `hide-gridlines` above, a different problem) rather
than hand-editing each broken cell: it reads every formula worth
restoring off a real, working *current-format* template
(`--reference-file-id` -- this formula scheme is the same across every
current-format template, not test-specific, so any working one will
do), drops the three dead branches and unwraps the one remaining flag
check wherever a formula has one (the simplified template's Module 2
slot has no flag cell to gate on -- it's always the one administered),
and writes the repaired version onto the simplified template
(`--target-file-id`) at the same cell position -- confirmed the two
templates' own Domain/Skill label rows line up exactly, so a positional
copy is safe.

Confirmed live in two rounds against a real template pair. First round
(the flag-gated cells only) found and cleanly repaired 92 formulas (4
on "Student Responses", 88 on "Calculations") -- confirmed live this
landed correctly (all 92 succeeded once a protected range blocking the
first attempt was lifted -- see below), and the exported report's
question-level counts came out right. But its own summary percentages
("% of Section," e.g. "0% of section, 10 out of 14 questions correct")
were still wrong: `Calculations!E2` (`=C2/54`) never referenced a flag
cell at all, so that first pass never found it, even though it was
blanked in the very same pass that broke the flag-gated cells -- true
of the whole "% Correct"/"% of Section" columns on both the Domain and
Skill tables (confirmed nothing there is test-specific data, so
restoring all of it unconditionally is safe). The repair now restores
every formula on "Calculations" -- confirmed against the real reference
file this is 216 formulas total (4 + 212, i.e. every formula cell that
sheet has), not just the 92 that happen to reference the deleted
columns directly.

Also confirmed live: `write_cells`' own batched `values().batchUpdate()`
fails *entirely* if even one cell in the batch hits a protected range
(a 400, "You are trying to edit a protected cell or object"), which
silently hides whether anything else in the same batch would also be
blocked. Writing one cell at a time instead fixed that -- but then hit a
*second* problem, also confirmed live: 216 individual write requests
with nothing pacing them reliably exceeds Sheets' own 60-writes-per-
minute-per-user quota (`WriteRequestsPerMinutePerUser`) well before
finishing -- a real run got 61/216 done before every remaining write
started coming back `429 RATE_LIMIT_EXCEEDED`.

Fixed by writing in chunks of 20 cells per `batchUpdate` call instead of
either extreme: one call for everything (216 cells, at most 20% larger
than what already failed once to a single protected cell) or one call
per cell (216 requests, well over the per-minute quota). Chunking keeps
the common case (nothing protected) to about a dozen requests total --
comfortably under that quota with no pacing needed at all -- while still
falling back to one write per cell, but *only* for whichever chunk
itself failed, to isolate exactly which cell(s) in it are the problem --
the same guarantee the all-individual approach existed to provide, at a
fraction of the request count. A `429` hit at either level (a chunk, or
an individual fallback write) is retried automatically with a cooldown
a little over Sheets' own 60-second quota window, up to a few times,
before being reported as still rate-limited -- kept distinct in the
final summary from a genuine protected-cell report, since the fix for
each is different: just re-run the command for the former (already-
repaired cells are harmless to write again), change protection in
Sheets (Data -> Protected sheets and ranges) for the latter.

```bash
python -m answer_extractor.google_sheets_cli repair-simplified-calculations \
  --reference-file-id <a working current-format template's file id> \
  --target-file-id <the simplified template's own file id>
```

### Clearing the simplified template's leftover Notes

"Student Responses"' own I/J columns (Correct Answer/Your Answer, around
the Math section) carry real Sheets *Notes* -- the plain, single,
non-threaded annotation from right-click "Insert note" (not Sheets'
newer threaded "Comment" feature, which isn't part of the Sheets API and
isn't representable in xlsx at all): reminders like "Remember to put the
= sign in front of fractions," meant for a person typing an answer in by
hand so Sheets doesn't auto-reformat a fraction. Once the program fills
those cells directly via the API, the notes are meaningless, but Sheets'
PDF export still appends every note in the exported range as its own
extra "Notes" page if the print setup's own Formatting > Notes option is
on -- confirmed live, this is exactly what a real export's own unwanted
extra page turned out to be.

Fixed the same way as the two template-level repairs above -- a new
one-time, Sheets-API-only command
(`sat_simplified_template_repair.find_note_cells` +
`google_sheets_export.clear_notes`, wired into `google_sheets_cli.py`'s
`clear-notes` command) rather than turning off that print option or
deleting the notes by hand: it downloads the target itself read-only
(no separate reference file needed here, unlike
`repair-simplified-calculations` -- this only needs to know where the
target's *own* notes already are) to find every commented cell, then
clears them live via the Sheets API. Confirmed against the real
simplified template: 8 such cells, all on "Student Responses" (I46/J46,
I59/J59, I60/J60, I62/J62). Run once against the master template so
every future per-student duplicate inherits having none to print at all
-- the current-format template's own copy is untouched either way, since
a human might still type into it by hand.

```bash
python -m answer_extractor.google_sheets_cli clear-notes --file-id <the simplified template's own file id>
```

### The cover page splitting across two PDF pages

Long predates the simplified template: confirmed byte-for-byte identical
between the simplified template and a real current-format one's own
"Cover Page" tab -- nothing in this whole redesign has ever touched it,
and this export's own is exactly the same shape a current-format
export's always has been. Its own layout: real content (name/date/test)
in rows 24-46, then nothing until a footer/disclaimer at row 60 -- a
~13-row gap that reads as intentional (a footer anchored near a standard
page's own bottom margin) rather than a structural bug, but it is what
pushes the footer onto its own mostly-blank second page.

The user's own manual File > Print of the same (already-filled) Sheet --
rather than through `export_pdf` -- was reported to show only 4 pages, no
Cover Page split, for the identical content. That ruled out a
content/layout problem (the current-format template's "Page X of 4"
footer text, still present verbatim on the simplified template's own
Cover Page, already assumes a 4-page report) in favor of a mismatch
between `export_pdf`'s dedicated-but-undocumented export endpoint and
Sheets' own interactive print rendering -- confirmed via a local read of
the real simplified template: "Fit to page" is *already* Cover Page's own
saved scale setting (also true of Score Report and Content), so this
isn't a case of the wrong setting being saved, just of `export_pdf` not
applying it the way the interactive UI does -- the same *category* of
gap already documented on this endpoint (see its own docstring: Drive's
generic export, which this replaced, had an analogous "fit to page"
mismatch; even this dedicated endpoint's own pagination "isn't decided
against a continuous post-scale pixel budget" the way `bottom_margin_in`
assumed).

Fix in progress, not yet confirmed live: `export_pdf` gained a
`fit_to_page` parameter (see its own docstring) that adds this endpoint's
own `scale=4` ("Fit to Page," per outside reverse-engineering of this
endpoint's parameters -- there's no official spec) to force that scale
explicitly rather than deferring to whatever's saved, threaded through
`export_filled_report` the same way `bottom_margin_in` already was, and
passed as `True` only by the simplified SAT export path
(`google_sat_simplified_score_report_export.export_simple_sat_score_report`).

This is a workbook-wide override, not a Cover-Page-specific one -- there's
no per-sheet `scale` when, as here, no `gid` narrows the export to one
sheet -- and confirmed via the same local read: "Student Responses" (the
Question-Level Feedback page) is deliberately saved at a fixed, hand-set
54% scale instead of "Fit to page" (this is almost certainly what all of
this project's own narrow-factor history above was tuning towards in the
first place). Turning `fit_to_page` on overrides that page's own scale
too, not just Cover Page's, so verifying this fix means checking *both*
pages in the same real export -- if Question-Level Feedback regresses,
the next step is exporting Cover Page (and Score Report/Content) as their
own separate `gid`-scoped `export_pdf` call with `fit_to_page=True`,
leaving Student Responses' own call untouched, and merging the results
into one PDF, rather than a workbook-wide override.

## macOS drag-and-drop app

You can turn this into a real `.app` icon on a Mac: drop scanned bubble
sheets, score-report PDFs, or a mix of both onto it, and it writes a
timestamped spreadsheet to your Desktop and opens it automatically. This
still requires a one-time Python setup (there's no way to produce a fully
standalone, dependency-free executable without building on macOS itself),
but after that it's just an icon.

`scripts/mac_droplet.sh` calls `answer_extractor.auto_cli`, which
auto-detects each dropped file's type (images are always treated as
bubble sheets; a PDF is treated as a score report if it actually parses
as one, otherwise as a scanned bubble sheet) and routes it accordingly.
Each bubble sheet gets its own tab (see "How it works" below), opening on
the first one; any score-report PDFs land in a separate "Score Report
Answers" tab -- all in the same spreadsheet.

This program lives in the `answer_extractor/` subdirectory of the
`tomschneible/Work` repo (a monorepo -- other unrelated programs may live
in sibling directories at the repo root). All commands and paths below
assume you're inside `answer_extractor/`, not the repo root.

**One-time setup** (Terminal). Copy this block exactly -- do not leave in
any `<` `>` placeholder characters, since those are shell redirection
operators and will make `git clone` fail with a confusing "no such file or
directory" instead of actually cloning anything. If your repo lives
somewhere other than the URL below, grab the real one from GitHub's green
"Code" button on the repo page:

```bash
git clone https://github.com/tomschneible/Work.git ~/Work
cd ~/Work/answer_extractor
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
   ~/Work/answer_extractor/scripts/mac_droplet.sh "$@"
   ```
5. **File → Save**, name it `Answer Extractor`, and save it as an
   **Application** (e.g. to your Desktop or Applications folder).

**To use it:** drag one or more scanned PDFs/images and/or score-report
PDFs onto the app's icon, in any mix. It writes the spreadsheet to your
Desktop named after whatever you dropped -- `<name>_answers.xlsx` for a
single file, `<first name>_and_N_others_answers.xlsx` for multiple -- and
opens it. Dropping the same file(s) again doesn't overwrite the previous
result; it numbers the new one `<name>_answers (2).xlsx` and so on, the
same way Finder/Chrome handle duplicate downloads. Errors (e.g. missing
setup, a page the pipeline couldn't read) show as a macOS alert dialog
instead of silently failing, since a droplet app has no visible terminal.

By default, each bubble-sheet input has its template (which sheet format it
is) auto-detected individually -- see "Auto-detecting the template" below --
so you can drop different sheet formats in the same batch and each one is
scored correctly (score-report PDFs don't need a template at all). To force
one fixed template for every bubble sheet instead -- e.g. to sidestep a
sheet whose format doesn't auto-detect cleanly, or for a quick one-off test
-- add an env var line before the script call in the same Automator action:

```bash
export ANSWER_EXTRACTOR_TEMPLATE=~/Work/answer_extractor/templates/default_template.yaml
~/Work/answer_extractor/scripts/mac_droplet.sh "$@"
```

## macOS drag-and-drop app (with a reference check)

A second droplet for when you already have an independently-scored
reference report for a student (a vendor's scoring spreadsheet, or a
rendered ScoreSheet-style PDF -- including this pipeline's own PDF export)
and want to check this tool's read against it. It auto-detects which of
three things you're doing from what you drop:

- **Scan + compare**: drop the scanned bubble sheet *and* a reference
  (spreadsheet or PDF) together. Scans as normal, then adds a color-coded
  "Comparison" tab.
- **Compare only**: drop a spreadsheet this tool *already* exported
  (e.g. from an earlier run of the plain droplet above) together with a
  reference. No re-scanning -- it just appends the "Comparison" tab to a
  copy of that file's existing answers. Use this when you already ran the
  plain scan and only got the reference afterward.
- **Direct comparison**: drop two already-finished score reports and
  nothing else -- e.g. this pipeline's own generated PDF report and a
  report you already had for that student -- to check them against each
  other with no scanning at all. Works with any mix of PDF/PDF,
  PDF/spreadsheet, or (as in compare-only above) an existing export
  alongside either. With two PDFs and nothing else to break the tie, the
  first one dropped is treated as "ours" and the second as "reference" --
  the success notification always names which file played which role.

A reference **spreadsheet** must have a tab named `ScoreSheet` (the vendor
export this was built against uses that name) containing repeated
`Question | Correct Answer | Your Answer | (mark) | Category` column
blocks, one section title (`English`/`Math`/`Reading`/`Science`) above
each group of blocks -- see `answer_extractor/scoresheet_check.py`'s
module docstring for the exact shape expected. If your vendor's tab is
named something else, add `--reference-tab "Whatever It's Called"` to the
script invocation in the Automator step below. A reference **PDF** needs
no special tab or filename -- any rendered report using that same
column-group layout (this pipeline's own export included) is recognized
automatically; see `answer_extractor/score_report_pdf_reader.py`.

This droplet is for the common case of **one scan (or one pre-existing/
finished report) plus one reference at a time** -- drop more comparable
candidates than that, or mix a ready-to-compare pair with something to
actually scan, and it either skips the comparison (writing the scan(s)
anyway, with a clear note as to why) or fails outright with a GUI alert
explaining the ambiguity, rather than guessing which files go together.

**Setup** is the same one-time Python environment as the plain droplet
above (skip it if you already did that), plus:

```bash
chmod +x ~/Work/answer_extractor/scripts/mac_droplet_compare.sh
```

**Build the droplet app** (same Automator steps as before, with a
different script and name):

1. Open **Automator** → **New Document** → **Application** → Choose.
2. Drag in **"Run Shell Script"**, set **Shell** to `/bin/bash` and **Pass
   input** to **"as arguments"**.
3. Script text (adjust the path if you cloned elsewhere):

   ```bash
   ~/Work/answer_extractor/scripts/mac_droplet_compare.sh "$@"
   ```
4. **File → Save**, name it `Answer Extractor - Compare`, save as an
   **Application**.

**To use it:** drag any pair from the three modes above -- (a) the scanned
bubble sheet and a reference, (b) a spreadsheet this tool already exported
and a reference, or (c) two already-finished reports (PDF and/or
spreadsheet) to compare directly -- onto the app's icon together. The
output is named after whichever dropped file isn't a spreadsheet with the
reference tab (so for two PDFs, it's named after whichever one you dropped
first). It writes and opens the spreadsheet the same way the plain droplet
does, and the success notification includes a one-line summary (e.g. "171
questions compared: 171 match, 0 flagged mismatch, 0 silent miss") so you
know at a glance whether anything needs a second look before you even open
the file.

## Comparing two score reports directly (`compare_cli`)

The two droplets above both scan first. For just checking one already-
finished score report against another -- e.g. this pipeline's own
generated report against a report you already had for that student, to
spot-check the program's output -- use `compare_cli` directly instead:

```bash
python -m answer_extractor.compare_cli \
    --ours "Jane Student - March 2026.pdf" \
    --reference "Jane Student (vendor).pdf" \
    --output comparison.xlsx
```

`--ours` and `--reference` each accept **either a `.pdf` or a `.xlsx`**,
in any combination, picked automatically by file extension:

- **`.pdf`**: a rendered ScoreSheet-style report -- this pipeline's own
  PDF export (`google_score_report_export.py`), or any other report using
  the same repeated `Question | Correct Answer | Your Answer | (mark) |
  Category` column-group layout, one section title (`English`/`Math`/
  `Reading`/`Science`) governing each group of blocks. Parsed straight off
  the rendered text/positions (`answer_extractor/score_report_pdf_reader.py`)
  -- no `.xlsx` needed. A PDF carries no flag/low-confidence data (nothing
  in a finished report says which answers the pipeline itself was unsure
  of), so on the `--ours` side any mismatch against it always comes out as
  an unflagged "silent miss".
- **`.xlsx`**: same as before -- `--ours` is one tab of this tool's own
  exported spreadsheet (`--ours-tab` to pick a non-default tab out of a
  multi-sheet batch export), `--reference` is a vendor spreadsheet with a
  `ScoreSheet` tab (`--reference-tab` if it's named something else).

The output is the same color-coded "Comparison" tab and match/flagged/
silent-miss/unmatched summary the droplets produce, written to
`--output` and printed to the terminal.

## How it works

1. **Load** (`answer_extractor/loading.py`) — reads images directly, or
   rasterizes PDF pages via PyMuPDF.
2. **Align** (`answer_extractor/align.py`) — finds the sheet's outer rectangular
   border (or page edge) in the photo/scan and perspective-warps it to the
   template's reference page size, so skewed or off-center photos still line
   up with the template's bubble coordinates. If no clean border is found,
   the image is resized directly and the result is flagged in the output
   ("Alignment" column) so you know to double check that sheet.
3. **Template** (`answer_extractor/template.py`) — a YAML file describing the
   sheet's geometry: page size, one or more named **sections** (independently
   numbered question blocks — most sheets have just one), each with one or
   more question columns (start position + row spacing), plus bubble
   spacing/radius and the two choice sets. Bubble pixel coordinates are
   derived from this, not hardcoded. When the template isn't fixed in
   advance (the droplet, `auto_cli`, `auto_compare_cli`), it's auto-detected
   per sheet instead — see "Auto-detecting the template" below.
4. **Locate bubbles** (`answer_extractor/grid_detect.py`) — a template's
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
5. **Detect marks** (`answer_extractor/detect.py`) — thresholds the image once
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
6. **Export** (`answer_extractor/export.py`) — one tab per scanned sheet, with
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
the page. Once your new template is added under `templates/` (and isn't
named `default_template.yaml`, which is never a real, calibrated format),
it's automatically picked up by auto-detection too -- see below.

## Auto-detecting the template

`answer_extractor.cli` still takes `--template` as a required, fixed
argument -- for the droplet and `auto_cli`/`auto_compare_cli`, though,
you don't have to know in advance which sheet format was dropped, or keep
a batch to one format: each sheet's template is detected individually
(`answer_extractor/template_detect.py`), so a batch can freely mix e.g.
`act_answer_sheet.yaml` and `legacy_act_answer_sheet.yaml` sheets and each
one is still scored correctly.

Detection works by structure, not by reading any printed text on the
sheet (a test code, a form name, ...) -- that path was tried for a related
feature and found unreliable across real sheets (some printed/OCR-able,
one handwritten, one entirely absent). Instead, every template under
`templates/` (except the generic `default_template.yaml` starting point)
is tried against the sheet using the same glyph-contour detection that
locates bubbles in the first place (see "Locate bubbles" above): if a
template's expected row/column layout is actually found, printed, at the
position that template predicts, for *every* section, it's a match. A
wrong template's sections essentially never all agree by coincidence, so
this is a reliable, ink-independent fingerprint of which physical sheet
it is.

If a sheet matches no template, or matches more than one, it's **not**
guessed at -- it's left out of the output and reported as a warning
instead (in the CLI's stderr output, and in the droplet's failure alert if
every input in the batch is ambiguous), the same "flag it for a human
rather than risk a confident wrong answer" rule this project applies to
individual bubbles. Pass `--template` (or set `ANSWER_EXTRACTOR_TEMPLATE`
for the droplet) to force one fixed template instead when a sheet's format
doesn't auto-detect cleanly.

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
