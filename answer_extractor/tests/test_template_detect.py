"""Coverage for auto-detecting which bubble-sheet template a scanned sheet
is (see answer_extractor.template_detect), including a real-templates
integration check that the two shipped formats -- act_answer_sheet.yaml
(5 columns/section, 4 choices) and legacy_act_answer_sheet.yaml (6
columns/section, Math has 5 choices) -- don't get confused for each other.
"""
from pathlib import Path

import cv2
import numpy as np

from answer_extractor.pipeline import process_path_auto
from answer_extractor.template import Template
from answer_extractor.template_detect import (
    DEFAULT_TEMPLATES_DIR,
    discover_template_paths,
    detect_template,
    score_template,
)
from tests.synth import make_blank_sheet, render_sheet


def _write_template(path: Path, data: str) -> Path:
    path.write_text(data)
    return path


def make_template_a(templates_dir: Path) -> Path:
    # Two columns, 6 rows each.
    return _write_template(
        templates_dir / "template_a.yaml",
        """
page:
  width: 900
  height: 900
sections:
  - name: Answers
    columns:
      - {first_question: 1, last_question: 6, x_start: 150, y_start: 100, row_height: 40}
      - {first_question: 7, last_question: 12, x_start: 450, y_start: 100, row_height: 40}
bubble_spacing_x: 30
bubble_radius: 11
choices:
  even: [A, B, C, D]
  odd: [F, G, H, J]
thresholds:
  fill_ratio_min: 0.35
  relative_margin: 0.15
""",
    )


def make_template_b(templates_dir: Path) -> Path:
    # Different geometry entirely: 3 columns, 4 rows each, further down the
    # page, wider bubble spacing -- shouldn't structurally match template A's
    # sheets or vice versa.
    return _write_template(
        templates_dir / "template_b.yaml",
        """
page:
  width: 900
  height: 900
sections:
  - name: Answers
    columns:
      - {first_question: 1, last_question: 4, x_start: 100, y_start: 500, row_height: 45}
      - {first_question: 5, last_question: 8, x_start: 400, y_start: 500, row_height: 45}
      - {first_question: 9, last_question: 12, x_start: 700, y_start: 500, row_height: 45}
bubble_spacing_x: 45
bubble_radius: 13
choices:
  even: [A, B, C, D]
  odd: [F, G, H, J]
thresholds:
  fill_ratio_min: 0.35
  relative_margin: 0.15
""",
    )


# -- discover_template_paths --------------------------------------------------


def test_discover_template_paths_excludes_the_starter_template(tmp_path):
    make_template_a(tmp_path)
    (tmp_path / "default_template.yaml").write_text("page: {width: 1, height: 1}\nsections: []\n")

    found = discover_template_paths(tmp_path)

    assert [p.name for p in found] == ["template_a.yaml"]


def test_discover_template_paths_finds_both_real_shipped_templates():
    found = {p.name for p in discover_template_paths(DEFAULT_TEMPLATES_DIR)}
    assert "act_answer_sheet.yaml" in found
    assert "legacy_act_answer_sheet.yaml" in found
    assert "default_template.yaml" not in found


# -- score_template -------------------------------------------------------


def test_score_template_is_a_full_match_against_its_own_sheet(tmp_path):
    template_a_path = make_template_a(tmp_path)
    template_a = Template.from_yaml(template_a_path)
    image = make_blank_sheet(template_a, letters=True)

    match = score_template(image, template_a_path, template_a)

    assert match.is_full_match
    assert match.unmatched_sections == []
    assert match.matched_sections == ["Answers"]


def test_score_template_fails_against_a_different_templates_sheet(tmp_path):
    template_a_path = make_template_a(tmp_path)
    template_a = Template.from_yaml(template_a_path)
    template_b_path = make_template_b(tmp_path)
    template_b = Template.from_yaml(template_b_path)

    image_a = make_blank_sheet(template_a, letters=True)

    match = score_template(image_a, template_b_path, template_b)

    assert not match.is_full_match


# -- detect_template --------------------------------------------------------


def test_detect_template_picks_the_matching_candidate(tmp_path):
    template_a_path = make_template_a(tmp_path)
    template_a = Template.from_yaml(template_a_path)
    make_template_b(tmp_path)

    image = make_blank_sheet(template_a, letters=True)

    result = detect_template(image, tmp_path)

    assert result.match is not None
    assert result.match.path == template_a_path
    assert len(result.attempts) == 2


def test_detect_template_is_ambiguous_when_nothing_matches(tmp_path):
    make_template_a(tmp_path)
    make_template_b(tmp_path)
    blank_white = 255 * np.ones((900, 900, 3), dtype=np.uint8)  # nothing printed at all

    result = detect_template(blank_white, tmp_path)

    assert result.match is None
    assert "didn't match any known template" in result.describe_failure()


def test_detect_template_is_ambiguous_when_more_than_one_candidate_fully_matches(tmp_path):
    template_a_path = make_template_a(tmp_path)
    template_a = Template.from_yaml(template_a_path)
    # A second copy of the same geometry under a different filename --
    # both should structurally match the same sheet.
    _write_template(tmp_path / "template_a_dup.yaml", template_a_path.read_text())

    image = make_blank_sheet(template_a, letters=True)

    result = detect_template(image, tmp_path)

    assert result.match is None
    assert "ambiguous" in result.describe_failure()


# -- pipeline.process_path_auto ----------------------------------------------


def test_process_path_auto_scores_each_sheet_against_its_own_detected_template(tmp_path):
    template_a_path = make_template_a(tmp_path)
    template_a = Template.from_yaml(template_a_path)
    template_b_path = make_template_b(tmp_path)
    template_b = Template.from_yaml(template_b_path)

    sheets_dir = tmp_path / "sheets"
    sheets_dir.mkdir()
    cv2.imwrite(str(sheets_dir / "a.png"), render_sheet(template_a, {1: ["F"]}, letters=True))
    cv2.imwrite(str(sheets_dir / "b.png"), render_sheet(template_b, {1: ["G"]}, letters=True))

    results, undetected = process_path_auto(sheets_dir, templates_dir=tmp_path)

    assert undetected == []
    by_label = {r.label: r for r in results}
    assert by_label["a"].template_name == "template_a"
    assert by_label["b"].template_name == "template_b"
    assert {(r.section, r.question): r.answer for r in by_label["a"].questions}[("Answers", 1)] == "F"
    assert {(r.section, r.question): r.answer for r in by_label["b"].questions}[("Answers", 1)] == "G"


def test_process_path_auto_reports_an_unmatched_sheet_without_failing_the_rest(tmp_path):
    template_a_path = make_template_a(tmp_path)
    template_a = Template.from_yaml(template_a_path)

    sheets_dir = tmp_path / "sheets"
    sheets_dir.mkdir()
    cv2.imwrite(str(sheets_dir / "good.png"), render_sheet(template_a, {1: ["F"]}, letters=True))
    blank_white = make_blank_sheet(template_a, with_border=False)
    blank_white[:] = 255
    cv2.imwrite(str(sheets_dir / "blank.png"), blank_white)

    results, undetected = process_path_auto(sheets_dir, templates_dir=tmp_path)

    assert [r.label for r in results] == ["good"]
    assert [u.label for u in undetected] == ["blank"]
    assert undetected[0].reason
