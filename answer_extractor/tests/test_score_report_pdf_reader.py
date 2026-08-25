"""Tests for score_report_pdf_reader.parse_scoresheet_pdf, using synthetic
PDFs built by scoresheet_pdf_synth.py rather than a real (and likely
privacy-sensitive) score report. See that module's docstring for why it
needs a specific system font, and MARK_FONT_PATH below for why these tests
skip rather than fail when it's missing.
"""
from __future__ import annotations

import os

import pytest

from answer_extractor.score_report_pdf_reader import parse_scoresheet_pdf
from tests.scoresheet_pdf_synth import MARK_FONT_PATH, Block, Row, write_scoresheet_pdf

pytestmark = pytest.mark.skipif(
    not os.path.exists(MARK_FONT_PATH),
    reason=f"synthetic PDF fixtures need a Unicode-capable font at {MARK_FONT_PATH} (fonts-dejavu-core)",
)


def test_parses_a_single_block_two_subjects_one_group_each(tmp_path):
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [
            Block("English", "Math", [
                [Row(1, "A", "A"), Row(2, "B", "C")],
                [Row(1, "D", "D"), Row(2, "F", "F")],
            ]),
        ],
    )
    assert parse_scoresheet_pdf(path) == {
        ("english", 1): "A",
        ("english", 2): "C",
        ("mathematics", 1): "D",
        ("mathematics", 2): "F",
    }


def test_omitted_question_reads_as_blank_not_the_correct_answer(tmp_path):
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [Block("English", "Math", [[Row(1, "A", None)], [Row(1, "B", "B")]])],
    )
    assert parse_scoresheet_pdf(path)[("english", 1)] == ""


def test_a_subject_split_across_two_column_groups_merges_into_one_section(tmp_path):
    """English 1-3 in the first group, English 36 in the third -- the real
    template's own layout for a section too long for one group -- both
    land under the same "english" key, distinguished only by question
    number, not by which group produced them."""
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [
            Block("English", "Math", [
                [Row(1, "A", "A"), Row(2, "B", "B"), Row(3, "C", "C")],
                [Row(36, "D", "D")],
                [Row(1, "A", "A")],
                [Row(31, "C", "C")],
            ]),
        ],
    )
    result = parse_scoresheet_pdf(path)
    assert set(q for section, q in result if section == "english") == {1, 2, 3, 36}
    assert set(q for section, q in result if section == "mathematics") == {1, 31}


def test_groups_of_different_lengths_each_stop_at_their_own_last_row(tmp_path):
    """A shorter group ending early must not truncate -- or overrun into --
    a longer group sharing the same block."""
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [
            Block("English", "Math", [
                [Row(n, "A", "A") for n in range(1, 8)],  # 7 rows
                [Row(36, "D", "D")],  # 1 row
                [Row(1, "A", "A"), Row(2, "B", "B")],  # 2 rows
                [Row(31, "C", "C")],  # 1 row
            ]),
        ],
    )
    result = parse_scoresheet_pdf(path)
    assert set(q for section, q in result if section == "english") == {1, 2, 3, 4, 5, 6, 7, 36}
    assert set(q for section, q in result if section == "mathematics") == {1, 2, 31}


def test_trailing_category_legend_is_not_read_as_more_rows(tmp_path):
    """A group's column position is reused further down the page for a
    multi-line category legend once its real rows run out -- must not be
    mistaken for additional (non-consecutive, non-numeric) data rows."""
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [Block("English", "Math", [[Row(1, "A", "A"), Row(2, "B", "B")], [Row(1, "C", "C")]])],
        trailing_legend=True,
    )
    result = parse_scoresheet_pdf(path)
    assert result == {("english", 1): "A", ("english", 2): "B", ("mathematics", 1): "C"}


def test_two_blocks_on_one_page_are_both_parsed(tmp_path):
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [
            Block("English", "Math", [[Row(1, "A", "A")], [Row(1, "B", "B")]]),
            Block("Reading", "Science", [[Row(1, "C", "C")], [Row(1, "D", "D")]]),
        ],
    )
    result = parse_scoresheet_pdf(path)
    assert result == {
        ("english", 1): "A",
        ("mathematics", 1): "B",
        ("reading", 1): "C",
        ("science", 1): "D",
    }


def test_no_scoresheet_table_on_the_page_raises(tmp_path):
    import fitz

    path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()

    with pytest.raises(ValueError, match="No ScoreSheet-shaped table"):
        parse_scoresheet_pdf(path)


def test_page_index_picks_a_specific_page_out_of_several(tmp_path):
    import fitz

    scoresheet_path = tmp_path / "scoresheet.pdf"
    write_scoresheet_pdf(scoresheet_path, [Block("English", "Math", [[Row(1, "A", "A")], [Row(1, "B", "B")]])])

    blank_doc = fitz.open()
    blank_doc.new_page()
    blank_path = tmp_path / "blank.pdf"
    blank_doc.save(str(blank_path))
    blank_doc.close()

    combined = fitz.open()
    combined.insert_pdf(fitz.open(str(blank_path)))
    combined.insert_pdf(fitz.open(str(scoresheet_path)))
    combined_path = tmp_path / "combined.pdf"
    combined.save(str(combined_path))
    combined.close()

    # No page_index -- exactly one page has a table, found automatically.
    assert parse_scoresheet_pdf(combined_path) == {("english", 1): "A", ("mathematics", 1): "B"}
    # Explicit page_index picks it directly too.
    assert parse_scoresheet_pdf(combined_path, page_index=1) == {("english", 1): "A", ("mathematics", 1): "B"}


def test_multiple_pages_with_tables_and_no_page_index_is_ambiguous(tmp_path):
    import fitz

    page_path = tmp_path / "page.pdf"
    write_scoresheet_pdf(page_path, [Block("English", "Math", [[Row(1, "A", "A")], [Row(1, "B", "B")]])])

    combined = fitz.open()
    combined.insert_pdf(fitz.open(str(page_path)))
    combined.insert_pdf(fitz.open(str(page_path)))
    combined_path = tmp_path / "combined.pdf"
    combined.save(str(combined_path))
    combined.close()

    with pytest.raises(ValueError, match="multiple pages"):
        parse_scoresheet_pdf(combined_path)


def test_conflicting_answers_for_the_same_question_raise(tmp_path):
    """Two column-groups both claiming to answer the same (section,
    question) with different answers -- shouldn't happen with a real
    template's own non-overlapping question ranges, but must be caught
    rather than silently picking one, the same posture
    parse_reference_scoresheet takes toward its .xlsx counterpart. Forced
    here by giving both of "English"'s own groups question 1."""
    path = tmp_path / "report.pdf"
    write_scoresheet_pdf(
        path,
        [Block("English", "English", [[Row(1, "A", "A")], [Row(1, "B", "B")]])],
    )
    with pytest.raises(ValueError, match="Conflicting entries"):
        parse_scoresheet_pdf(path)
