"""Tests for sat_score_report_pdf_reader.parse_sat_score_report_pdf, using
synthetic PDFs built by sat_scoresheet_pdf_synth.py rather than a real
(and privacy-sensitive) score report. See scoresheet_pdf_synth.py's own
docstring for why these need a specific system font, and MARK_FONT_PATH
below for why these tests skip rather than fail when it's missing.
"""
from __future__ import annotations

import os

import pytest

from answer_extractor.sat_score_report_pdf_reader import parse_sat_score_report_pdf
from tests.scoresheet_pdf_synth import MARK_FONT_PATH, Row
from tests.sat_scoresheet_pdf_synth import SatGroup, write_sat_scoresheet_pdf

pytestmark = pytest.mark.skipif(
    not os.path.exists(MARK_FONT_PATH),
    reason=f"synthetic PDF fixtures need a Unicode-capable font at {MARK_FONT_PATH} (fonts-dejavu-core)",
)


def test_parses_one_header_row_two_modules_of_the_same_subject(tmp_path):
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(
        path,
        [
            [
                SatGroup("Reading and Writing Module 1", [Row(1, "A", "A"), Row(2, "B", "C")]),
                SatGroup("Reading and Writing Module 2 - Higher Difficulty", [Row(1, "D", "D")]),
            ],
        ],
    )
    assert parse_sat_score_report_pdf(path) == {
        ("reading and writing module 1", 1): "A",
        ("reading and writing module 1", 2): "C",
        ("reading and writing module 2", 1): "D",
    }


def test_a_title_wider_than_its_own_answer_columns_is_still_read_in_full(tmp_path):
    """Confirmed against a real generated report: a group's own title
    ("R & W Module 2 - Higher Difficulty") routinely overruns "Correct
    Answer"/"Your Answer"'s own combined column width -- must still parse
    as one whole title, not get cut off partway through "Module 2"."""
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(
        path,
        [
            [
                SatGroup("R & W Module 1", [Row(1, "A", "A")]),
                SatGroup("R & W Module 2 - Higher Difficulty", [Row(1, "B", "B")]),
            ],
        ],
    )
    assert parse_sat_score_report_pdf(path) == {
        ("reading and writing module 1", 1): "A",
        ("reading and writing module 2", 1): "B",
    }


def test_omitted_question_reads_as_blank_not_the_correct_answer(tmp_path):
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(
        path,
        [[SatGroup("Math Module 1", [Row(1, "A", None)])]],
    )
    assert parse_sat_score_report_pdf(path)[("math module 1", 1)] == ""


def test_two_header_rows_on_one_page_are_both_parsed(tmp_path):
    """The real page's own shape: Reading and Writing's pair of modules on
    one header row, Math's own pair further down the same page."""
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(
        path,
        [
            [
                SatGroup("Reading and Writing Module 1", [Row(1, "A", "A")]),
                SatGroup("Reading and Writing Module 2", [Row(1, "B", "B")]),
            ],
            [
                SatGroup("Math Module 1", [Row(1, "C", "C")]),
                SatGroup("Math Module 2", [Row(1, "D", "D")]),
            ],
        ],
    )
    result = parse_sat_score_report_pdf(path)
    assert result == {
        ("reading and writing module 1", 1): "A",
        ("reading and writing module 2", 1): "B",
        ("math module 1", 1): "C",
        ("math module 2", 1): "D",
    }


def test_groups_of_different_lengths_each_stop_at_their_own_last_row(tmp_path):
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(
        path,
        [
            [
                SatGroup("Math Module 1", [Row(n, "A", "A") for n in range(1, 8)]),  # 7 rows
                SatGroup("Math Module 2", [Row(1, "B", "B"), Row(2, "C", "C")]),  # 2 rows
            ],
        ],
    )
    result = parse_sat_score_report_pdf(path)
    assert set(q for section, q in result if section == "math module 1") == {1, 2, 3, 4, 5, 6, 7}
    assert set(q for section, q in result if section == "math module 2") == {1, 2}


def test_no_sat_table_on_the_page_raises(tmp_path):
    import fitz

    path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()

    with pytest.raises(ValueError, match="No SAT/DSAT-shaped table"):
        parse_sat_score_report_pdf(path)


def test_an_unparseable_group_title_raises_a_clear_error(tmp_path):
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(path, [[SatGroup("Not A Recognizable Title", [Row(1, "A", "A")])]])

    with pytest.raises(ValueError, match="Couldn't parse a subject/module title"):
        parse_sat_score_report_pdf(path)


def test_page_index_picks_a_specific_page_out_of_several(tmp_path):
    import fitz

    sat_path = tmp_path / "sat.pdf"
    write_sat_scoresheet_pdf(sat_path, [[SatGroup("Math Module 1", [Row(1, "A", "A")])]])

    blank_doc = fitz.open()
    blank_doc.new_page()
    blank_path = tmp_path / "blank.pdf"
    blank_doc.save(str(blank_path))
    blank_doc.close()

    combined = fitz.open()
    combined.insert_pdf(fitz.open(str(blank_path)))
    combined.insert_pdf(fitz.open(str(sat_path)))
    combined_path = tmp_path / "combined.pdf"
    combined.save(str(combined_path))
    combined.close()

    # No page_index -- exactly one page has a table, found automatically
    # (not hardcoded to any fixed page number -- see this reader's own
    # module docstring).
    assert parse_sat_score_report_pdf(combined_path) == {("math module 1", 1): "A"}
    assert parse_sat_score_report_pdf(combined_path, page_index=1) == {("math module 1", 1): "A"}


def test_multiple_pages_with_tables_and_no_page_index_is_ambiguous(tmp_path):
    import fitz

    page_path = tmp_path / "page.pdf"
    write_sat_scoresheet_pdf(page_path, [[SatGroup("Math Module 1", [Row(1, "A", "A")])]])

    combined = fitz.open()
    combined.insert_pdf(fitz.open(str(page_path)))
    combined.insert_pdf(fitz.open(str(page_path)))
    combined_path = tmp_path / "combined.pdf"
    combined.save(str(combined_path))
    combined.close()

    with pytest.raises(ValueError, match="multiple pages"):
        parse_sat_score_report_pdf(combined_path)


def test_conflicting_answers_for_the_same_question_raise(tmp_path):
    """Shouldn't happen with a real template's own non-overlapping
    (subject, module) section keys, but must be caught rather than
    silently picking one -- same posture score_report_pdf_reader.py's
    ACT counterpart takes. Forced here by giving both of one header row's
    own groups the same title (so they collapse to the same section)."""
    path = tmp_path / "report.pdf"
    write_sat_scoresheet_pdf(
        path,
        [
            [
                SatGroup("Math Module 1", [Row(1, "A", "A")]),
                SatGroup("Math Module 1", [Row(1, "B", "B")]),
            ],
        ],
    )
    with pytest.raises(ValueError, match="Conflicting entries"):
        parse_sat_score_report_pdf(path)
