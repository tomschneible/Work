import cv2
import openpyxl
from openpyxl import load_workbook

from answer_extractor.auto_compare_cli import main
from answer_extractor.template import Template
from tests.synth import render_sheet

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
