import pytest

from answer_extractor.template import Template


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
    assert template.choices_for("Answers", 1) == ["F", "G", "H", "J"]
    assert template.choices_for("Answers", 2) == ["A", "B", "C", "D"]


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


def test_section_choices_override_takes_precedence_over_template_default():
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
                "name": "Mathematics",
                "choices": {"odd": ["A", "B", "C", "D", "E"], "even": ["F", "G", "H", "J", "K"]},
                "columns": [
                    {"first_question": 1, "last_question": 2, "x_start": 100, "y_start": 300, "row_height": 50},
                ],
            },
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
        "choices": {"odd": ["A", "B", "C", "D"], "even": ["F", "G", "H", "J"]},
    }
    template = Template.from_dict(data)
    template.validate()

    # Section without an override falls back to the template-level choices.
    assert template.choices_for("English", 1) == ["A", "B", "C", "D"]
    assert template.choices_for("English", 2) == ["F", "G", "H", "J"]

    # Section with an override uses its own 5-choice set instead.
    assert template.choices_for("Mathematics", 1) == ["A", "B", "C", "D", "E"]
    assert template.choices_for("Mathematics", 2) == ["F", "G", "H", "J", "K"]

    # The override also drives bubble geometry -- 5 bubbles, not 4.
    bubbles = template.bubbles()
    assert len(bubbles[("Mathematics", 1)]) == 5
    assert [b.choice for b in bubbles[("Mathematics", 1)]] == ["A", "B", "C", "D", "E"]
    assert len(bubbles[("English", 1)]) == 4


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
    assert template.choices_for("English", 1) == ["A", "B", "C", "D"]
    assert template.choices_for("English", 2) == ["F", "G", "H", "J"]


def test_legacy_act_answer_sheet_template_loads_and_validates():
    template = Template.from_yaml("templates/legacy_act_answer_sheet.yaml")
    template.validate()
    names = [s.name for s in template.sections]
    assert names == ["English", "Mathematics", "Reading", "Science"]
    by_name = {s.name: s for s in template.sections}
    assert by_name["English"].num_questions == 75
    assert by_name["Mathematics"].num_questions == 60
    assert by_name["Reading"].num_questions == 40
    assert by_name["Science"].num_questions == 40
    # Every section but Mathematics uses the sheet-wide 4-choice convention.
    assert template.choices_for("English", 1) == ["A", "B", "C", "D"]
    assert template.choices_for("English", 2) == ["F", "G", "H", "J"]
    assert template.choices_for("Reading", 1) == ["A", "B", "C", "D"]
    assert template.choices_for("Science", 2) == ["F", "G", "H", "J"]
    # Mathematics alone overrides to 5 choices per question.
    assert template.choices_for("Mathematics", 1) == ["A", "B", "C", "D", "E"]
    assert template.choices_for("Mathematics", 2) == ["F", "G", "H", "J", "K"]


def test_act_j_form_answer_sheet_template_loads_and_validates():
    template = Template.from_yaml("templates/act_j_form_answer_sheet.yaml")
    template.validate()
    names = [s.name for s in template.sections]
    assert names == ["English", "Mathematics", "Reading", "Science"]
    by_name = {s.name: s for s in template.sections}
    assert by_name["English"].num_questions == 40
    assert by_name["Mathematics"].num_questions == 41
    assert by_name["Reading"].num_questions == 27
    assert by_name["Science"].num_questions == 34
    # Unlike every other shipped template, this one doesn't trust odd/even
    # parity past question 1 -- see dynamic_choices below -- but question 1
    # itself still starts every section at the standard "odd" group.
    assert all(s.dynamic_choices for s in template.sections)
    assert template.choices_for("English", 1) == ["A", "B", "C", "D"]


# -- Section.dynamic_choices / Template.question_choices_override -----------


def test_dynamic_choices_defaults_to_false():
    template = make_template()
    assert template.sections[0].dynamic_choices is False


def test_dynamic_choices_parses_from_yaml():
    data = {
        "page": {"width": 800, "height": 600},
        "sections": [
            {
                "name": "Answers",
                "dynamic_choices": True,
                "columns": [
                    {"first_question": 1, "last_question": 2, "x_start": 100, "y_start": 100, "row_height": 50},
                ],
            }
        ],
        "bubble_spacing_x": 40,
        "bubble_radius": 10,
    }
    template = Template.from_dict(data)
    assert template.sections[0].dynamic_choices is True


def test_choices_for_ignores_parity_when_an_override_says_otherwise():
    """The whole point of question_choices_override: two *consecutive*
    questions (5 and 6, both normally opposite parities) can be forced to
    the same choice list, simulating the real "duplicate row" this
    project's own dynamic_choices templates exist for."""
    template = make_template()
    resolved = template.with_resolved_choices(
        {("Answers", 1): ["A", "B", "C", "D"], ("Answers", 2): ["A", "B", "C", "D"]}
    )
    assert resolved.choices_for("Answers", 1) == ["A", "B", "C", "D"]
    assert resolved.choices_for("Answers", 2) == ["A", "B", "C", "D"]  # would be even_choices without the override
    # A question with no override entry still falls back to ordinary parity.
    assert resolved.choices_for("Answers", 3) == ["F", "G", "H", "J"]


def test_with_resolved_choices_merges_rather_than_replaces():
    template = make_template()
    once = template.with_resolved_choices({("Answers", 1): ["X", "X", "X", "X"]})
    twice = once.with_resolved_choices({("Answers", 2): ["Y", "Y", "Y", "Y"]})
    # Both overrides survive -- the second call didn't discard the first's.
    assert twice.choices_for("Answers", 1) == ["X", "X", "X", "X"]
    assert twice.choices_for("Answers", 2) == ["Y", "Y", "Y", "Y"]


def test_with_resolved_choices_drives_bubbles_geometry_too():
    """bubbles() goes through choices_for internally, so an override is
    picked up there transparently -- no separate wiring needed."""
    template = make_template()
    resolved = template.with_resolved_choices({("Answers", 1): ["A", "B", "C", "D"]})
    bubbles = resolved.bubbles()
    assert [b.choice for b in bubbles[("Answers", 1)]] == ["A", "B", "C", "D"]
    # Positions are unaffected -- both groups are the same length, so a
    # slot's (x, y) doesn't depend on which one is used.
    unresolved_bubbles = template.bubbles()
    assert [(b.x, b.y) for b in bubbles[("Answers", 1)]] == [
        (b.x, b.y) for b in unresolved_bubbles[("Answers", 1)]
    ]
