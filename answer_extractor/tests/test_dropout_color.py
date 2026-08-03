"""Regression coverage for sheets printed in a saturated "dropout" accent
color (e.g. the coral ink real ACT answer sheets use) instead of plain
black outlines.

Grayscale luminance alone can't tell such printed ink apart from a genuine
mark: across a realistically dense page (many bubbles, like a real answer
sheet), a vivid coral bubble outline+letter clusters distinctly darker than
white paper by luminance, so a global Otsu threshold on grayscale ends up
classifying most bubbles as "marked" even when nothing was filled in --
this is exactly the false-positive this project hit against a real ACT
sample sheet. detect.binarize uses max(B, G, R) (HSV Value) instead, which
stays bright for saturated print colors and only drops for true dark,
neutral pencil/pen marks. Using the bundled act_answer_sheet.yaml template
(171 questions) reproduces the page density needed for the old grayscale
bug to actually manifest -- a handful of bubbles isn't enough ink for Otsu
to misbehave the same way.
"""
from answer_extractor.detect import evaluate_sheet
from answer_extractor.template import Template
from tests.synth import fill_bubble, make_blank_sheet

CORAL = (100, 110, 230)  # BGR: saturated coral/red, similar to real ACT sheets


def load_template() -> Template:
    return Template.from_yaml("templates/act_answer_sheet.yaml")


def test_blank_dropout_color_sheet_is_not_read_as_all_marked():
    template = load_template()
    image = make_blank_sheet(template, ink_color=CORAL, letters=True)

    results, _ = evaluate_sheet(image, template)
    non_blank = [(r.section, r.question, r.answer) for r in results if r.answer != ""]
    assert non_blank == [], (
        f"expected every question blank, got {len(non_blank)} non-blank "
        f"(first few: {non_blank[:5]}) -- printed dropout-color ink is "
        "being misread as a mark"
    )


def test_dropout_color_sheet_still_detects_real_marks():
    template = load_template()
    image = make_blank_sheet(template, ink_color=CORAL, letters=True)
    bubbles_by_q = template.bubbles()

    b = [x for x in bubbles_by_q[("English", 1)] if x.choice == "A"][0]
    fill_bubble(image, b.x, b.y, template.bubble_radius, coverage=1.0, darkness=25)

    results = {(r.section, r.question): r for r in evaluate_sheet(image, template)[0]}
    assert results[("English", 1)].answer == "A"
    assert results[("English", 2)].answer == ""
    assert results[("Mathematics", 1)].answer == ""


def test_dropout_color_sheet_detects_multiple_marks():
    template = load_template()
    image = make_blank_sheet(template, ink_color=CORAL, letters=True)
    bubbles_by_q = template.bubbles()

    for choice in ("F", "H"):
        b = [x for x in bubbles_by_q[("Science", 2)] if x.choice == choice][0]
        fill_bubble(image, b.x, b.y, template.bubble_radius, coverage=1.0, darkness=25)

    results = {(r.section, r.question): r for r in evaluate_sheet(image, template)[0]}
    result = results[("Science", 2)]
    assert result.answer == "MULTIPLE"
    assert set(result.candidates) == {"F", "H"}
