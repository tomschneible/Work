import pytest

from bubble_scanner.template import Template


def make_template(**overrides) -> Template:
    data = {
        "page": {"width": 800, "height": 600},
        "columns": [
            {"first_question": 1, "last_question": 4, "x_start": 100, "y_start": 100, "row_height": 50},
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
    assert set(bubbles.keys()) == {1, 2, 3, 4}
    q1 = {b.choice: (b.x, b.y) for b in bubbles[1]}
    assert q1["F"] == (100, 100)
    assert q1["G"] == (140, 100)
    q2 = {b.choice: (b.x, b.y) for b in bubbles[2]}
    assert q2["A"] == (100, 150)


def test_validate_passes_for_well_formed_template():
    make_template().validate()


def test_validate_rejects_duplicate_question():
    data = {
        "page": {"width": 800, "height": 600},
        "columns": [
            {"first_question": 1, "last_question": 4, "x_start": 100, "y_start": 100, "row_height": 50},
            {"first_question": 3, "last_question": 6, "x_start": 400, "y_start": 100, "row_height": 50},
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="more than one column"):
        Template.from_dict(data).validate()


def test_validate_rejects_missing_question():
    data = {
        "page": {"width": 800, "height": 600},
        "columns": [
            {"first_question": 1, "last_question": 3, "x_start": 100, "y_start": 100, "row_height": 50},
            {"first_question": 5, "last_question": 6, "x_start": 400, "y_start": 100, "row_height": 50},
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="missing questions"):
        Template.from_dict(data).validate()


def test_validate_rejects_out_of_bounds_bubble():
    data = {
        "page": {"width": 200, "height": 200},
        "columns": [
            {"first_question": 1, "last_question": 1, "x_start": 190, "y_start": 100, "row_height": 50},
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    with pytest.raises(ValueError, match="outside the page"):
        Template.from_dict(data).validate()


def test_default_template_file_loads_and_validates():
    template = Template.from_yaml("templates/default_template.yaml")
    template.validate()
    assert template.num_questions == 50
