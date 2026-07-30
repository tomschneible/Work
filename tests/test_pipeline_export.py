import cv2

from bubble_scanner.export import write_xlsx
from bubble_scanner.pipeline import SheetResult, process_path
from bubble_scanner.template import Template
from tests.synth import render_sheet


def make_template() -> Template:
    data = {
        "page": {"width": 900, "height": 700},
        "columns": [
            {"first_question": 1, "last_question": 4, "x_start": 150, "y_start": 100, "row_height": 80},
        ],
        "bubble_spacing_x": 60,
        "bubble_radius": 18,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
    }
    return Template.from_dict(data)


def test_process_path_reads_image_files(tmp_path):
    template = make_template()
    image = render_sheet(template, {1: ["F"], 2: ["B"], 3: [], 4: ["A", "D"]})
    image_path = tmp_path / "sheet_001.png"
    cv2.imwrite(str(image_path), image)

    results = process_path(image_path, template)
    assert len(results) == 1
    result = results[0]
    assert result.label == "sheet_001"
    answers = {q.question: q.answer for q in result.questions}
    assert answers == {1: "F", 2: "B", 3: "", 4: "MULTIPLE"}
    assert result.has_review_items


def test_process_path_reads_directory_of_images(tmp_path):
    template = make_template()
    for i, marks in enumerate([{1: ["F"]}, {1: ["G"]}], start=1):
        image = render_sheet(template, marks)
        cv2.imwrite(str(tmp_path / f"sheet_{i}.png"), image)

    results = process_path(tmp_path, template)
    assert len(results) == 2
    assert {r.label for r in results} == {"sheet_1", "sheet_2"}


def test_write_xlsx_produces_file(tmp_path):
    template = make_template()
    image = render_sheet(template, {1: ["F"], 2: ["B"], 3: [], 4: ["A", "D"]})
    image_path = tmp_path / "sheet_001.png"
    cv2.imwrite(str(image_path), image)

    results = process_path(image_path, template)
    out_path = tmp_path / "results.xlsx"
    write_xlsx(results, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
