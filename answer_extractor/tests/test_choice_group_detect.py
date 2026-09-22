"""Regression coverage for the real problem choice_group_detect exists to
solve: a `dynamic_choices` sheet where two blank copies of the same
physical form broke odd/even alternation at *different* rows (see this
module's own module docstring for the full evidence) -- so
resolve_section_choices has to read each row's actual choice group off
the image itself, not trust the template's static choices past question
1. These tests build a synthetic sheet with a deliberately-placed
"duplicate row" (two adjacent questions both printing the same group)
using Template.with_resolved_choices + tests.synth's own letters=True
rendering, then confirm resolve_section_choices reconstructs that exact
pattern from nothing but the rendered pixels.
"""
from __future__ import annotations

import cv2

from answer_extractor.choice_group_detect import resolve_section_choices
from answer_extractor.grid_detect import locate_section_bubbles
from answer_extractor.template import Template
from tests.synth import fill_bubble, make_blank_sheet

# tests.synth.make_blank_sheet's own *default* letters=True rendering
# (tuned for its actual usual callers -- exercising glyph *position*
# detection, not glyph *shape* distinguishability) draws each choice
# letter too small/thin relative to the bubble ring to cleanly separate
# two different letters by shape at this module's own real thresholds.
# On a real scanned sheet the printed letter fills most of the bubble's
# own interior (confirmed directly against the real sheets
# choice_group_detect.py's own module docstring describes), so these
# tests ask for a bolder, better-proportioned glyph instead -- empirically
# checked (at the bubble_radius/bubble_spacing_x make_template uses below)
# to clear resolve_section_choices' own real-scan-calibrated thresholds
# with a comfortable margin both ways (same-letter similarity 1.0,
# different-letter similarity ~0.62, vs. thresholds of 0.85/0.70) *and*
# still pass _find_glyph_boxes' own contour size filter, which
# test_grid_detect.py's own smaller-scale make_template's default
# (radius 11) letter size does not -- rather than loosening either of
# those against a thinner/smaller synthetic font.
_FONT_SCALE = 1.0
_FONT_THICKNESS = 2


def make_template(num_questions: int = 9, second_column: bool = False) -> Template:
    """One `dynamic_choices` section. Larger scale (radius 16, spacing 45)
    than test_grid_detect.py's own make_template -- needed so a letter
    bold enough for resolve_section_choices' own shape comparison (see
    _FONT_SCALE above) still fits _find_glyph_boxes' contour size filter
    instead of being rejected as an oversized glyph."""
    columns = [{"first_question": 1, "last_question": num_questions, "x_start": 150, "y_start": 100, "row_height": 60}]
    if second_column:
        columns.append(
            {"first_question": num_questions + 1, "last_question": num_questions * 2, "x_start": 450, "y_start": 100, "row_height": 60}
        )
    data = {
        "page": {"width": 900, "height": 900},
        "sections": [{"name": "Answers", "dynamic_choices": True, "columns": columns}],
        "bubble_spacing_x": 45,
        "bubble_radius": 16,
        "choices": {"even": ["A", "B", "C", "D"], "odd": ["F", "G", "H", "J"]},
        "thresholds": {"fill_ratio_min": 0.35, "relative_margin": 0.15},
    }
    return Template.from_dict(data)


def render_and_detect(template: Template, groups_by_question: dict, extra_ops=None):
    """Render a blank sheet whose questions print exactly `groups_by_question`
    (question -> ["A","B","C","D"] or ["F","G","H","J"]) instead of the
    template's own ordinary odd/even alternation, via with_resolved_choices
    -- then run the real locate_section_bubbles against it, the same
    detected positions resolve_section_choices always actually receives
    (real positions, not yet relabeled). `extra_ops(image, resolved)`, if
    given, can draw further marks (e.g. filling a bubble) before
    detection runs."""
    overrides = {("Answers", q): group for q, group in groups_by_question.items()}
    resolved = template.with_resolved_choices(overrides)
    image = make_blank_sheet(
        resolved, with_border=False, letters=True, letter_font_scale=_FONT_SCALE, letter_thickness=_FONT_THICKNESS
    )
    if extra_ops is not None:
        extra_ops(image, resolved)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    section = template.sections[0]
    detected = locate_section_bubbles(gray, template, section)
    assert detected is not None, "structural detection itself failed -- not what this test is checking"
    return gray, section, detected


def _sequence_with_duplicates(num_questions: int, duplicate_at=frozenset()) -> dict:
    """Build {question: choice_group} for `num_questions`, starting question
    1 at the "odd" group (this project's own standard convention, and
    dynamic resolution's own seed) and flipping group every subsequent
    question -- UNLESS that question's number is in `duplicate_at`, in
    which case it keeps the *same* group as the question before it
    instead (the real alternation-break this whole feature exists to
    handle). Built iteratively -- like resolve_section_choices' own chain,
    and unlike a plain `question % 2` formula -- because a real duplicate
    shifts every *later* question's own parity relative to naive
    odd/even, not just the duplicated row itself; a formula-based
    sequence would silently go back out of sync with itself the moment a
    duplicate is spliced in by hand."""
    odd_group = ["F", "G", "H", "J"]
    even_group = ["A", "B", "C", "D"]
    groups = {}
    current = odd_group
    for q in range(1, num_questions + 1):
        if q > 1 and q not in duplicate_at:
            current = even_group if current is odd_group else odd_group
        groups[q] = current
    return groups


def test_resolve_section_choices_confirms_ordinary_alternation():
    """Baseline sanity check: no duplicate anywhere -- every question's
    group is confidently confirmed, nothing flagged."""
    template = make_template(9)
    groups = _sequence_with_duplicates(9)
    gray, section, detected = render_and_detect(template, groups)

    relabeled, low_confidence = resolve_section_choices(gray, template, section, detected)

    for q, expected_group in groups.items():
        assert relabeled[q][0][0] == expected_group[0], f"Q{q} expected group starting {expected_group[0]!r}"
    assert low_confidence == set()


def test_resolve_section_choices_reads_a_duplicate_row_correctly():
    """The actual bug this feature exists to fix: questions 5 and 6 both
    print F/G/H/J (breaking alternation) instead of 6 flipping to
    A/B/C/D. A naive odd/even reader would get every question from 6
    onward wrong; resolve_section_choices must not."""
    template = make_template(9)
    groups = _sequence_with_duplicates(9, duplicate_at={6})
    gray, section, detected = render_and_detect(template, groups)

    relabeled, low_confidence = resolve_section_choices(gray, template, section, detected)

    for q, expected_group in groups.items():
        assert relabeled[q][0][0] == expected_group[0], f"Q{q} expected group starting {expected_group[0]!r}"
    assert low_confidence == set()
    # Spell out the specific claim in letters, not just the first slot:
    # question 6 really did resolve to the same four choices as question
    # 5, not the "ordinary" flip a parity-only reader would have assumed,
    # and question 7 correctly flips relative to 6's own (repeated) group.
    assert [c for c, _, _ in relabeled[5]] == ["F", "G", "H", "J"]
    assert [c for c, _, _ in relabeled[6]] == ["F", "G", "H", "J"]
    assert [c for c, _, _ in relabeled[7]] == ["A", "B", "C", "D"]


def test_resolve_section_choices_duplicate_continues_across_a_column_boundary():
    """A duplicate's own phase shift carries into the *next* column
    seamlessly -- confirmed against the real sheets this module's own
    docstring describes: a column's first question is never independently
    reset to the "odd" group, it just continues the running alternation
    from wherever the previous column's own last question left off."""
    template = make_template(5, second_column=True)  # columns: Q1-5, Q6-10
    groups = _sequence_with_duplicates(10, duplicate_at={3})  # duplicate inside column 1
    gray, section, detected = render_and_detect(template, groups)

    relabeled, low_confidence = resolve_section_choices(gray, template, section, detected)

    for q, expected_group in groups.items():
        assert relabeled[q][0][0] == expected_group[0], f"Q{q} expected group starting {expected_group[0]!r}"
    assert low_confidence == set()


def test_resolve_section_choices_reads_around_a_filled_bubble():
    """A student's own mark obscures whichever bubble it's on, but the
    other (normally 3) unmarked bubbles in the same row are still real
    printed letters -- resolution should still succeed using those,
    without even needing to fall back/flag."""
    template = make_template(9)
    groups = _sequence_with_duplicates(9, duplicate_at={6})

    def mark_first_slot_of_row_6(image, resolved):
        bubble = resolved.bubbles()[("Answers", 6)][0]  # slot 0 of the duplicate row
        fill_bubble(image, bubble.x, bubble.y, resolved.bubble_radius, coverage=1.0, darkness=10)

    gray, section, detected = render_and_detect(template, groups, extra_ops=mark_first_slot_of_row_6)

    relabeled, low_confidence = resolve_section_choices(gray, template, section, detected)

    assert relabeled[6][0][0] == "F"  # still resolved correctly despite slot 0 being marked
    assert 6 not in low_confidence


def test_resolve_section_choices_falls_back_and_flags_when_a_row_is_unreadable():
    """Every slot of question 6 marked (an extreme, deliberately
    unrealistic case -- a student can't validly fill 4 choices at once,
    but this is the only way to force zero readable glyphs anywhere in a
    row for a controlled test) leaves nothing to compare -- resolution
    should carry the previous anchor's alternation forward (the ordinary-
    case guess) rather than raising, and flag the guess as low_confidence
    rather than presenting it as a confirmed read."""
    template = make_template(9)
    groups = _sequence_with_duplicates(9)  # no duplicate here -- question 6 ordinarily flips from 5

    def mark_every_slot_of_row_6(image, resolved):
        for bubble in resolved.bubbles()[("Answers", 6)]:
            fill_bubble(image, bubble.x, bubble.y, resolved.bubble_radius, coverage=1.0, darkness=10)

    gray, section, detected = render_and_detect(template, groups, extra_ops=mark_every_slot_of_row_6)

    relabeled, low_confidence = resolve_section_choices(gray, template, section, detected)

    assert 6 in low_confidence
    # The guess still has to be *something* sensible -- the ordinary flip
    # from question 5's own resolved group, same as ever other
    # (readable) question got.
    assert relabeled[6][0][0] == groups[6][0]
    # And the chain still recovers cleanly once readable rows resume --
    # question 6 being carried-forward/guessed doesn't poison 7 onward.
    for q in (7, 8, 9):
        assert relabeled[q][0][0] == groups[q][0]
    assert low_confidence == {6}
