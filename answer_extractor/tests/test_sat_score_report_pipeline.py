import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.sat_score_report_pipeline import (
    active_variants_from_rows,
    answers_from_rows,
    export_sat_report,
)
from answer_extractor.score_report import ScoreReportRow

_MODULE = "answer_extractor.sat_score_report_pipeline"


def _row(module, question, section, your_answer, module_label):
    return ScoreReportRow(
        source="Student, Jane 2027 DSAT 8 March 8 2026",
        module=module,
        question=question,
        section=section,
        your_answer=your_answer,
        module_label=module_label,
    )


def test_answers_from_rows_keys_by_subject_slot_and_question():
    rows = [
        _row(1, 1, "Reading and Writing", "A", "Module 1"),
        _row(2, 1, "Reading and Writing", "C", "Module 2 (Harder)"),
        _row(3, 1, "Math", "B", "Module 1"),
        _row(4, 1, "Math", "D", "Module 2 (Easier)"),
    ]

    answers = answers_from_rows(rows)

    assert answers[("reading and writing", "module1", 1)] == "A"
    assert answers[("reading and writing", "harder", 1)] == "C"
    assert answers[("math", "module1", 1)] == "B"
    assert answers[("math", "easier", 1)] == "D"


def test_answers_from_rows_raises_when_module_2_difficulty_is_unidentified():
    rows = [_row(2, 1, "Reading and Writing", "C", "Module 2")]  # no (Easier)/(Harder) qualifier

    with pytest.raises(ValueError, match="couldn't be confidently identified|Could not determine"):
        answers_from_rows(rows)


def test_active_variants_from_rows_ignores_module1_and_maps_by_subject():
    rows = [
        _row(1, 1, "Reading and Writing", "A", "Module 1"),
        _row(2, 1, "Reading and Writing", "C", "Module 2 (Harder)"),
        _row(3, 1, "Math", "B", "Module 1"),
        _row(4, 1, "Math", "D", "Module 2 (Easier)"),
    ]

    assert active_variants_from_rows(rows) == {"reading and writing": "harder", "math": "easier"}


def test_active_variants_from_rows_raises_on_disagreement():
    rows = [
        _row(2, 1, "Reading and Writing", "C", "Module 2 (Harder)"),
        _row(2, 2, "Reading and Writing", "D", "Module 2 (Easier)"),  # same module, disagrees
    ]

    with pytest.raises(ValueError, match="Conflicting"):
        active_variants_from_rows(rows)


def test_export_sat_report_prompts_once_per_subject_and_writes_the_pdf(tmp_path):
    rows = [
        _row(1, 1, "Reading and Writing", "A", "Module 1"),
        _row(2, 1, "Reading and Writing", "C", "Module 2 (Harder)"),
        _row(3, 1, "Math", "B", "Module 1"),
        _row(4, 1, "Math", "D", "Module 2 (Easier)"),
    ]
    # First prompt is the test date (see gui_prompt.py's own module
    # docstring for why this isn't scan.test_date any more), then scores
    # fire in _SUBJECT_PROMPT_ORDER -- Reading and Writing before Math
    # (the exam's own section order), not alphabetically ("math" <
    # "reading and writing" would otherwise put Math first).
    prompt_fn = MagicMock(side_effect=["3/8/2026", "590", "620"])

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake") as export_mock:
        pdf_path = export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=prompt_fn)

    assert pdf_path == tmp_path / "Student, Jane 2027 DSAT 8 March 8 2026.pdf"
    assert pdf_path.read_bytes() == b"%PDF-fake"
    assert prompt_fn.call_count == 3
    assert "test date" in prompt_fn.call_args_list[0][0][0]  # prompted first
    assert "Reading" in prompt_fn.call_args_list[1][0][0]  # prompted second
    assert "Math" in prompt_fn.call_args_list[2][0][0]  # prompted third

    kwargs = export_mock.call_args.kwargs
    assert kwargs["test_code"] == "8"
    assert kwargs["student_name"] == "Jane Student"
    assert kwargs["test_date"] == dt.date(2026, 3, 8)  # the prompted date, not the filename's own
    assert kwargs["active_variants"] == {"reading and writing": "harder", "math": "easier"}
    assert kwargs["section_scores"] == {"reading and writing": 590, "math": 620}


def test_export_sat_report_reprompts_on_invalid_input_before_succeeding(tmp_path):
    rows = [_row(1, 1, "Math", "B", "Module 1")]
    prompt_fn = MagicMock(side_effect=["3/8/2026", "not a number", "9999", "620"])

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake"):
        export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=prompt_fn)

    assert prompt_fn.call_count == 4


def test_export_sat_report_raises_when_a_prompt_is_cancelled(tmp_path):
    rows = [_row(1, 1, "Math", "B", "Module 1")]
    prompt_fn = MagicMock(return_value=None)

    with patch(f"{_MODULE}.export_simple_sat_score_report"):
        with pytest.raises(ValueError, match="cancelled"):
            export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=prompt_fn)


def test_export_sat_report_raises_for_a_non_sat_filename(tmp_path):
    rows = [
        ScoreReportRow(
            source="Student, Jane 2027 ACT 25MC1 March 8 2026",
            module=1,
            question=1,
            section="Math",
            your_answer="B",
            module_label="Module 1",
        )
    ]

    with pytest.raises(ValueError, match="SAT/DSAT"):
        export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=MagicMock())


def test_export_sat_report_raises_on_empty_rows(tmp_path):
    with pytest.raises(ValueError, match="No rows"):
        export_sat_report(MagicMock(), MagicMock(), "ROOT", [], tmp_path, prompt_fn=MagicMock())
