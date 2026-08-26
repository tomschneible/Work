import os

import cv2
import openpyxl
import pytest
from openpyxl import load_workbook

from answer_extractor.auto_compare_cli import main
from answer_extractor.detect import QuestionResult
from answer_extractor.export import write_xlsx
from answer_extractor.pipeline import SheetResult
from answer_extractor.template import Template
from tests.scoresheet_pdf_synth import MARK_FONT_PATH, Block, Row, write_scoresheet_pdf
from tests.synth import render_sheet

pdf_pytestmark = pytest.mark.skipif(
    not os.path.exists(MARK_FONT_PATH),
    reason=f"synthetic PDF fixtures need a Unicode-capable font at {MARK_FONT_PATH} (fonts-dejavu-core)",
)

_TEMPLATE_YAML = """
page:
  width: 900
  height: 700
sections:
  - name: English
    columns:
      - {first_question: 1, last_question: 3, x_start: 150, y_start: 100, row_height: 80}
bubble_spacing_x: 60
bubble_radius: 18
choices:
  even: [A, B, C, D]
  odd: [F, G, H, J]
thresholds:
  fill_ratio_min: 0.35
  relative_margin: 0.15
"""


def make_template_yaml(tmp_path):
    path = tmp_path / "template.yaml"
    path.write_text(_TEMPLATE_YAML)
    return path


def write_reference(path, rows):
    """rows: list of (question, correct, your_answer, mark) for an
    "English" block -- same minimal shape as the real vendor file."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ScoreSheet"
    ws["A1"] = "English"
    ws["B2"], ws["C2"] = "Correct Answer", "Your Answer"
    for i, (q, correct, yours, mark) in enumerate(rows, start=3):
        ws.cell(row=i, column=1, value=q)
        ws.cell(row=i, column=2, value=correct)
        ws.cell(row=i, column=3, value=yours)
        ws.cell(row=i, column=4, value=mark)
    wb.save(str(path))


def test_scan_plus_reference_adds_a_comparison_tab(tmp_path):
    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)

    image_path = tmp_path / "sheet.png"
    cv2.imwrite(str(image_path), render_sheet(template, {1: ["F"], 2: ["A"], 3: []}))

    reference_path = tmp_path / "reference.xlsx"
    write_reference(
        reference_path,
        [
            (1, "F", "F", "✔"),  # matches our F -> match
            (2, "A", "B", "✘"),  # we read A, reference says B -> silent miss
            (3, "C", None, "ø"),  # both blank -> match
        ],
    )

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        [
            "--input",
            str(image_path),
            str(reference_path),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert "Comparison" in wb.sheetnames
    assert "sheet" in wb.sheetnames  # the ordinary bubble-sheet tab is still there

    comparison_rows = list(wb["Comparison"].iter_rows(min_row=2, values_only=True))
    by_question = {row[1]: row for row in comparison_rows}
    assert by_question[1][4] == "✔"  # Match column
    assert by_question[2][4] == "✘"
    assert not by_question[2][5]  # no flag -- a silent miss
    assert by_question[3][4] == "✔"


def test_no_reference_dropped_behaves_like_a_plain_scan(tmp_path):
    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)
    image_path = tmp_path / "sheet.png"
    cv2.imwrite(str(image_path), render_sheet(template, {1: ["F"]}))

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        ["--input", str(image_path), "--template", str(template_path), "--output", str(output_path)]
    )

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert "Comparison" not in wb.sheetnames


def test_two_reference_candidates_is_an_error(tmp_path):
    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)
    image_path = tmp_path / "sheet.png"
    cv2.imwrite(str(image_path), render_sheet(template, {1: ["F"]}))

    ref1 = tmp_path / "reference1.xlsx"
    ref2 = tmp_path / "reference2.xlsx"
    write_reference(ref1, [(1, "F", "F", "✔")])
    write_reference(ref2, [(1, "F", "F", "✔")])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        [
            "--input",
            str(image_path),
            str(ref1),
            str(ref2),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    assert not output_path.exists()


def test_reference_without_a_matching_bubble_sheet_skips_comparison_but_still_writes(tmp_path):
    reference_path = tmp_path / "reference.xlsx"
    write_reference(reference_path, [(1, "F", "F", "✔")])

    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)
    image_path_1 = tmp_path / "sheet1.png"
    image_path_2 = tmp_path / "sheet2.png"
    cv2.imwrite(str(image_path_1), render_sheet(template, {1: ["F"]}))
    cv2.imwrite(str(image_path_2), render_sheet(template, {1: ["G"]}))

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        [
            "--input",
            str(image_path_1),
            str(image_path_2),
            str(reference_path),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert "Comparison" not in wb.sheetnames
    assert {"sheet1", "sheet2"} <= set(wb.sheetnames)


def _write_existing_output(path):
    """An already-exported results file, as if from an earlier run of the
    plain scan droplet -- what "compare only" mode is meant to take
    without re-scanning anything."""
    questions = [
        QuestionResult("English", 1, "F", ["F"], {}, low_confidence=False),
        QuestionResult("English", 2, "A", ["A"], {}, low_confidence=False),  # will mismatch, unflagged
        QuestionResult("English", 3, "", [], {}, low_confidence=False),
    ]
    result = SheetResult(label="prior_scan", source="test", used_contour_alignment=False, questions=questions)
    write_xlsx([result], path)


def test_existing_output_plus_reference_compares_without_rescanning(tmp_path):
    existing_path = tmp_path / "prior_scan_answers.xlsx"
    _write_existing_output(existing_path)

    reference_path = tmp_path / "reference.xlsx"
    write_reference(
        reference_path,
        [
            (1, "F", "F", "✔"),
            (2, "A", "B", "✘"),  # our stored answer was A, reference says B -> silent miss
            (3, "C", None, "ø"),
        ],
    )

    output_path = tmp_path / "out.xlsx"
    exit_code = main(["--input", str(existing_path), str(reference_path), "--output", str(output_path)])

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert "Comparison" in wb.sheetnames
    assert "prior_scan" in wb.sheetnames  # the original tab is carried over untouched

    comparison_rows = list(wb["Comparison"].iter_rows(min_row=2, values_only=True))
    by_question = {row[1]: row for row in comparison_rows}
    assert by_question[1][4] == "✔"
    assert by_question[2][4] == "✘"
    assert not by_question[2][5]  # silent miss -- no flag


def test_existing_output_plus_bubble_sheet_is_ambiguous(tmp_path):
    existing_path = tmp_path / "prior_scan_answers.xlsx"
    _write_existing_output(existing_path)

    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)
    image_path = tmp_path / "sheet.png"
    cv2.imwrite(str(image_path), render_sheet(template, {1: ["F"]}))

    reference_path = tmp_path / "reference.xlsx"
    write_reference(reference_path, [(1, "F", "F", "✔")])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        [
            "--input",
            str(existing_path),
            str(image_path),
            str(reference_path),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    assert not output_path.exists()


def test_existing_output_without_a_reference_is_an_error(tmp_path):
    existing_path = tmp_path / "prior_scan_answers.xlsx"
    _write_existing_output(existing_path)

    output_path = tmp_path / "out.xlsx"
    exit_code = main(["--input", str(existing_path), "--output", str(output_path)])

    assert exit_code == 1
    assert not output_path.exists()


def _write_scoresheet_pdf(path, english_rows):
    """english_rows: list of (question, correct, your_answer) tuples."""
    write_scoresheet_pdf(
        path,
        [
            Block("English", "Math", [
                [Row(q, correct, yours) for q, correct, yours in english_rows],
                [Row(1, "F", "F")],
            ]),
        ],
    )


@pdf_pytestmark
def test_two_scoresheet_pdfs_compare_directly_first_as_ours(tmp_path):
    """No spreadsheet at all -- the two PDFs pair up directly, first given
    on the command line as "ours", second as "reference"."""
    ours_path = tmp_path / "our_report.pdf"
    reference_path = tmp_path / "their_report.pdf"
    _write_scoresheet_pdf(ours_path, [(1, "F", "F"), (2, "A", "B")])  # Q2: silent miss if B differs
    _write_scoresheet_pdf(reference_path, [(1, "F", "F"), (2, "A", "A")])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(["--input", str(ours_path), str(reference_path), "--output", str(output_path)])

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert wb.sheetnames == ["Comparison"]  # no scan tab -- neither side was scanned
    by_key = {(row[0], row[1]): row for row in wb["Comparison"].iter_rows(min_row=2, values_only=True)}
    assert by_key[("English", 1)][4] == "✔"
    assert by_key[("English", 2)][4] == "✘"


@pdf_pytestmark
def test_existing_output_plus_scoresheet_pdf_compares_without_rescanning(tmp_path):
    """A pre-existing export (always "ours" -- same convention as the
    xlsx-vs-xlsx compare-only mode) paired with a lone ScoreSheet PDF,
    which then plays the reference role by elimination."""
    existing_path = tmp_path / "prior_scan_answers.xlsx"
    _write_existing_output(existing_path)  # English 1=F, 2=A, 3=blank

    reference_path = tmp_path / "reference.pdf"
    _write_scoresheet_pdf(reference_path, [(1, "F", "F"), (2, "A", "B"), (3, "C", None)])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(["--input", str(existing_path), str(reference_path), "--output", str(output_path)])

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert "Comparison" in wb.sheetnames
    assert "prior_scan" in wb.sheetnames  # the original tab is carried over untouched
    by_key = {(row[0], row[1]): row for row in wb["Comparison"].iter_rows(min_row=2, values_only=True)}
    assert by_key[("English", 1)][4] == "✔"
    assert by_key[("English", 2)][4] == "✘"


@pdf_pytestmark
def test_tagged_reference_xlsx_plus_scoresheet_pdf_uses_the_pdf_as_ours(tmp_path):
    """The tagged spreadsheet always plays reference; a lone PDF alongside
    it plays ours by elimination -- no scanning involved."""
    ours_path = tmp_path / "our_report.pdf"
    _write_scoresheet_pdf(ours_path, [(1, "F", "F"), (2, "A", "B")])

    reference_path = tmp_path / "reference.xlsx"
    write_reference(reference_path, [(1, "F", "F", "✔"), (2, "A", "A", "✔")])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(["--input", str(ours_path), str(reference_path), "--output", str(output_path)])

    assert exit_code == 0
    wb = load_workbook(output_path)
    by_key = {(row[0], row[1]): row for row in wb["Comparison"].iter_rows(min_row=2, values_only=True)}
    assert by_key[("English", 1)][4] == "✔"
    assert by_key[("English", 2)][4] == "✘"


@pdf_pytestmark
def test_scan_plus_scoresheet_pdf_reference_adds_a_comparison_tab(tmp_path):
    """A lone ScoreSheet-shaped PDF can serve as the reference for an
    actual scan too, same as a tagged spreadsheet always could."""
    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)
    image_path = tmp_path / "sheet.png"
    cv2.imwrite(str(image_path), render_sheet(template, {1: ["F"], 2: ["A"], 3: []}))

    reference_path = tmp_path / "reference.pdf"
    _write_scoresheet_pdf(reference_path, [(1, "F", "F"), (2, "A", "B"), (3, "C", None)])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        [
            "--input",
            str(image_path),
            str(reference_path),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    wb = load_workbook(output_path)
    assert "Comparison" in wb.sheetnames
    assert "sheet" in wb.sheetnames


@pdf_pytestmark
def test_too_many_comparable_candidates_is_an_error(tmp_path):
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    _write_scoresheet_pdf(pdf_a, [(1, "F", "F")])
    _write_scoresheet_pdf(pdf_b, [(1, "F", "F")])
    reference_path = tmp_path / "reference.xlsx"
    write_reference(reference_path, [(1, "F", "F", "✔")])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        ["--input", str(pdf_a), str(pdf_b), str(reference_path), "--output", str(output_path)]
    )

    assert exit_code == 1
    assert not output_path.exists()


@pdf_pytestmark
def test_lone_scoresheet_pdf_with_nothing_else_is_an_error(tmp_path):
    pdf_path = tmp_path / "reference.pdf"
    _write_scoresheet_pdf(pdf_path, [(1, "F", "F")])

    output_path = tmp_path / "out.xlsx"
    exit_code = main(["--input", str(pdf_path), "--output", str(output_path)])

    assert exit_code == 1
    assert not output_path.exists()


@pdf_pytestmark
def test_comparison_pair_plus_something_to_scan_is_ambiguous(tmp_path):
    ours_path = tmp_path / "our_report.pdf"
    reference_path = tmp_path / "their_report.pdf"
    _write_scoresheet_pdf(ours_path, [(1, "F", "F")])
    _write_scoresheet_pdf(reference_path, [(1, "F", "F")])

    template_path = make_template_yaml(tmp_path)
    template = Template.from_yaml(template_path)
    image_path = tmp_path / "sheet.png"
    cv2.imwrite(str(image_path), render_sheet(template, {1: ["F"]}))

    output_path = tmp_path / "out.xlsx"
    exit_code = main(
        [
            "--input",
            str(ours_path),
            str(reference_path),
            str(image_path),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    assert not output_path.exists()
