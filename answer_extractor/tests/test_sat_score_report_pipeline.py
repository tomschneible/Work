import dataclasses
import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.gui_prompt import SKIP
from answer_extractor.sat_score_report_pipeline import (
    _prompt_for_section_score,
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


def _complete(rows):
    """`rows` plus an omitted-answer row for every question they leave out
    of each module they touch -- a real score report always has every
    question (see _check_every_question_present), which most tests here
    don't need to spell out."""
    question_counts = {"Reading and Writing": 27, "Math": 22}
    completed = list(rows)
    present = {(r.module, r.question) for r in rows}
    first_row_by_module = {}
    for r in rows:
        first_row_by_module.setdefault(r.module, r)
    for module, first in first_row_by_module.items():
        for q in range(1, question_counts[first.section] + 1):
            if (module, q) not in present:
                completed.append(dataclasses.replace(first, question=q, your_answer=""))
    return completed


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


def test_prompt_for_section_score_returns_skip_for_the_word_test_without_retrying():
    prompt_fn = MagicMock(return_value="Test")

    result = _prompt_for_section_score(prompt_fn, "Jane Student", "math")

    assert result is SKIP
    assert prompt_fn.call_count == 1


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
        pdf_path = export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=prompt_fn)

    assert pdf_path == tmp_path / "Student, Jane 2027 DSAT 8 March 8 2026.pdf"
    assert pdf_path.read_bytes() == b"%PDF-fake"
    assert prompt_fn.call_count == 3
    assert "test date" in prompt_fn.call_args_list[0][0][0]  # prompted first
    assert "Reading" in prompt_fn.call_args_list[1][0][0]  # prompted second
    assert "Math" in prompt_fn.call_args_list[2][0][0]  # prompted third

    kwargs = export_mock.call_args.kwargs
    assert kwargs["test_code"] == "8"
    assert kwargs["student_name"] == "Jane Student"
    assert kwargs["test_date"] == dt.date(2026, 3, 8)  # the prompted date
    assert kwargs["active_variants"] == {"reading and writing": "harder", "math": "easier"}
    assert kwargs["section_scores"] == {"reading and writing": 590, "math": 620}


def test_export_sat_report_test_mode_skips_date_and_scores(tmp_path):
    """Typing "test" at the date prompt or a score prompt (any casing)
    leaves that field out entirely -- gui_prompt.SKIP, translated to
    plain None for the date and to "no entry in section_scores" for a
    score -- rather than failing the way a cancelled prompt does."""
    rows = [
        _row(1, 1, "Reading and Writing", "A", "Module 1"),
        _row(3, 1, "Math", "B", "Module 1"),
    ]
    prompt_fn = MagicMock(side_effect=["Test", "test", "TEST"])  # date, R&W score, Math score

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake") as export_mock:
        export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=prompt_fn)

    assert prompt_fn.call_count == 3  # no re-prompting -- "test" is accepted immediately
    kwargs = export_mock.call_args.kwargs
    assert kwargs["test_date"] is None
    assert kwargs["section_scores"] == {}


@pytest.mark.parametrize("typed, folder_date", [("3/10/2026", dt.date(2026, 3, 10)), ("test", None)])
def test_export_sat_report_files_the_sheet_by_the_prompted_date(tmp_path, typed, folder_date):
    rows = [_row(1, 1, "Math", "B", "Module 1")]
    report_folders = MagicMock()
    report_folders.folder_for.return_value = "DAY_FOLDER_ID"

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake") as export_mock:
        export_sat_report(
            MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path,
            prompt_fn=MagicMock(side_effect=[typed, "620"]), report_folders=report_folders,
        )

    report_folders.folder_for.assert_called_once_with(folder_date, "Jane Student")
    assert export_mock.call_args.kwargs["copy_folder_id"] == "DAY_FOLDER_ID"
    assert report_folders.save_copies.called == (folder_date is not None)  # a test run uploads no copies


def test_export_sat_report_copies_the_dropped_score_report_and_the_new_one_to_drive(tmp_path):
    dropped = "/Users/staff/Downloads/Student, Jane 2027 DSAT 8 March 8 2026.pdf"
    rows = [dataclasses.replace(_row(1, 1, "Math", "B", "Module 1"), source_path=dropped)]
    report_folders = MagicMock()
    report_folders.folder_for.return_value = "DAY_FOLDER_ID"

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake"):
        export_sat_report(
            MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path,
            prompt_fn=MagicMock(side_effect=["3/10/2026", "620"]), report_folders=report_folders,
        )

    report_folders.save_copies.assert_called_once_with(
        "DAY_FOLDER_ID", Path(dropped), "Student, Jane 2027 DSAT 8 March 10 2026.pdf", b"%PDF-fake", "Jane Student"
    )


def test_export_sat_report_numbers_the_pdf_instead_of_overwriting_the_score_report_it_came_from(tmp_path):
    # The dropped score report sitting in the output folder under exactly the new report's name.
    dropped = tmp_path / "Student, Jane 2027 DSAT 8 March 8 2026.pdf"
    dropped.write_bytes(b"dropped report")
    rows = [dataclasses.replace(_row(1, 1, "Math", "B", "Module 1"), source_path=str(dropped))]

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake"):
        pdf_path = export_sat_report(
            MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path,
            prompt_fn=MagicMock(side_effect=["3/8/2026", "620"]),
        )

    assert dropped.read_bytes() == b"dropped report"
    assert pdf_path == tmp_path / "Student, Jane 2027 DSAT 8 March 8 2026 (2).pdf"
    assert pdf_path.read_bytes() == b"%PDF-fake"


def test_export_sat_report_makes_no_folder_for_a_report_cancelled_at_a_score_prompt(tmp_path):
    rows = [_row(1, 1, "Math", "B", "Module 1")]
    report_folders = MagicMock()

    with patch(f"{_MODULE}.export_simple_sat_score_report"):
        with pytest.raises(ValueError, match="cancelled"):
            export_sat_report(
                MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path,
                prompt_fn=MagicMock(side_effect=["3/10/2026", None]), report_folders=report_folders,
            )
    report_folders.folder_for.assert_not_called()


def test_export_sat_report_reprompts_on_invalid_input_before_succeeding(tmp_path):
    rows = [_row(1, 1, "Math", "B", "Module 1")]
    prompt_fn = MagicMock(side_effect=["3/8/2026", "not a number", "9999", "620"])

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake"):
        export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=prompt_fn)

    assert prompt_fn.call_count == 4


def test_export_sat_report_raises_when_a_prompt_is_cancelled(tmp_path):
    rows = [_row(1, 1, "Math", "B", "Module 1")]
    prompt_fn = MagicMock(return_value=None)

    with patch(f"{_MODULE}.export_simple_sat_score_report"):
        with pytest.raises(ValueError, match="cancelled"):
            export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=prompt_fn)


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
        export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=MagicMock())


@pytest.mark.parametrize("identified, ok", [("SAT Practice 8", True), ("SAT Practice 4", False), ("", True)])
def test_export_sat_report_checks_the_filenames_test_number_against_the_identified_test(tmp_path, identified, ok):
    rows = [dataclasses.replace(_row(1, 1, "Math", "B", "Module 1"), test=identified)]  # filename says DSAT 8
    prompt_fn = MagicMock(side_effect=["3/8/2026", "620"])

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake") as export_mock:
        if ok:
            export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=prompt_fn)
        else:
            expected = "^this appears to be DSAT 4, not DSAT 8 -- are you sure the file is named correctly[?]$"
            with pytest.raises(ValueError, match=expected):
                export_sat_report(MagicMock(), MagicMock(), "ROOT", _complete(rows), tmp_path, prompt_fn=prompt_fn)
    assert export_mock.called == ok
    assert prompt_fn.called == ok  # a mismatch is caught before anyone is asked anything


def test_export_sat_report_refuses_a_module_with_a_question_it_couldnt_read(tmp_path):
    rows = _complete([_row(1, 1, "Math", "B", "Module 1"), _row(2, 1, "Math", "C", "Module 2 (Harder)")])
    rows = [r for r in rows if not (r.module == 2 and r.question in (14, 15))]
    prompt_fn = MagicMock()

    with patch(f"{_MODULE}.export_simple_sat_score_report") as export_mock:
        with pytest.raises(
            ValueError,
            match=r"^couldn't find Math Module 2 \(Harder\) questions 14, 15 in the score report -- no report was made",
        ):
            export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=prompt_fn)
    prompt_fn.assert_not_called()  # caught before anyone is asked anything
    export_mock.assert_not_called()


def test_export_sat_report_accepts_complete_modules_with_omitted_answers(tmp_path):
    rows = _complete([_row(1, 1, "Math", "", "Module 1")])  # every question present, all omitted

    with patch(f"{_MODULE}.export_simple_sat_score_report", return_value=b"%PDF-fake") as export_mock:
        prompt_fn = MagicMock(side_effect=["3/8/2026", "620"])
        export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=prompt_fn)

    assert set(export_mock.call_args.kwargs["answers"].values()) == {""}


def test_export_sat_report_rejects_a_date_in_another_month_than_the_file_is_named_for(tmp_path):
    rows = _complete([_row(1, 1, "Math", "B", "Module 1")])  # named March 8 2026
    prompt_fn = MagicMock(side_effect=["4/8/2026", "620"])

    with patch(f"{_MODULE}.export_simple_sat_score_report") as export_mock:
        expected = r"^the test date typed \(4/8/2026\) is in April 2026, but the file is named March"
        with pytest.raises(ValueError, match=expected):
            export_sat_report(MagicMock(), MagicMock(), "ROOT", rows, tmp_path, prompt_fn=prompt_fn)
    assert prompt_fn.call_count == 1  # rejected before the score prompts
    export_mock.assert_not_called()


def test_export_sat_report_raises_on_empty_rows(tmp_path):
    with pytest.raises(ValueError, match="No rows"):
        export_sat_report(MagicMock(), MagicMock(), "ROOT", [], tmp_path, prompt_fn=MagicMock())
