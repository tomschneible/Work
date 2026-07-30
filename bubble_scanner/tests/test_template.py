import pytest

from bubble_scanner.template import Template


def make_template(**overrides) -> Template:
    data = {
        "page": {"width": 800, "height": 600},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 4, "x_start": 100, "y_start": 100, "row_height": 50},
                ],
            }
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.3, "relative_margin": 0.1},
    }
    data.update(overrides)
    return Template.from_dict(data)


def test_choices_for_parity():
    template = make_template()
    assert template.choices_for(1) == ["F", "G", "H", "J"]
    assert template.choices_for(2) == ["A", "B", "C", "D"]


def test_bubbles_geometry():
    template = make_template()
    bubbles = template.bubbles()
    assert set(bubbles.keys()) == {("Answers", 1), ("Answers", 2), ("Answers", 3), ("Answers", 4)}
    q1 = {b.choice: (b.x, b.y) for b in bubbles[("Answers", 1)]}
    assert q1["F"] == (100, 100)
    assert q1["G"] == (140, 100)
    q2 = {b.choice: (b.x, b.y) for b in bubbles[("Answers", 2)]}
    assert q2["A"] == (100, 150)


def test_validate_passes_for_well_formed_template():
    make_template().validate()


def test_validate_rejects_duplicate_question():
    data = {
        "page": {"width": 800, "height": 600},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 4, "x_start": 100, "y_start": 100, "row_height": 50},
                    {"first_question": 3, "last_question": 6, "x_start": 400, "y_start": 100, "row_height": 50},
                ],
            }
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="more than one column"):
        Template.from_dict(data).validate()


def test_validate_rejects_missing_question():
    data = {
        "page": {"width": 800, "height": 600},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 3, "x_start": 100, "y_start": 100, "row_height": 50},
                    {"first_question": 5, "last_question": 6, "x_start": 400, "y_start": 100, "row_height": 50},
                ],
            }
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="missing questions"):
        Template.from_dict(data).validate()


def test_validate_rejects_out_of_bounds_bubble():
    data = {
        "page": {"width": 200, "height": 200},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 1, "x_start": 190, "y_start": 100, "row_height": 50},
                ],
            }
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="outside the page"):
        Template.from_dict(data).validate()


def test_validate_rejects_duplicate_section_names():
    data = {
        "page": {"width": 800, "height": 600},
        "sections": [
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 2, "x_start": 100, "y_start": 100, "row_height": 50},
                ],
            },
            {
                "name": "Answers",
                "columns": [
                    {"first_question": 1, "last_question": 2, "x_start": 400, "y_start": 100, "row_height": 50},
                ],
            },
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="Duplicate section names"):
        Template.from_dict(data).validate()


def test_multi_section_questions_restart_numbering():
    data = {
        "page": {"width": 800, "height": 600},
        "sections": [
            {
                "name": "English",
                "columns": [
                    {"first_question": 1, "last_question": 2, "x_start": 100, "y_start": 100, "row_height": 50},
                ],
            },
            {
                "name": "Math",
                "columns": [
                    {"first_question": 1, "last_question": 2, "x_start": 100, "y_start": 300, "row_height": 50},
                ],
            },
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    template = Template.from_dict(data)
    template.validate()
    bubbles = template.bubbles()
    assert ("English", 1) in bubbles
    assert ("Math", 1) in bubbles
    assert bubbles[("English", 1)][0].y != bubbles[("Math", 1)][0].y


def test_default_template_file_loads_and_validates():
    template = Template.from_yaml("templates/default_template.yaml")
    template.validate()


def test_act_answer_sheet_template_loads_and_validates():
    template = Template.from_yaml("templates/act_answer_sheet.yaml")
    template.validate()
    names = [s.name for s in template.sections]
    assert names == ["English", "Mathematics", "Reading", "Science"]
    by_name = {s.name: s for s in template.sections}
    assert by_name["English"].num_questions == 50
    assert by_name["Mathematics"].num_questions == 45
    assert by_name["Reading"].num_questions == 36
    assert by_name["Science"].num_questions == 40
    # Real sheet convention: odd -> A/B/C/D, even -> F/G/H/J.
    assert template.choices_for(1) == ["A", "B", "C", "D"]
    assert template.choices_for(2) == ["F", "G", "H", "J"]
