import datetime as dt

import pytest

from answer_extractor.scan_filename import parse_scan_filename


def test_parse_scan_filename_with_day():
    result = parse_scan_filename("Student, Jane 2027 ACT 25MC1 January 17 2026")

    assert result.last_name == "Student"
    assert result.first_name == "Jane"
    assert result.grad_year == 2027
    assert result.test_family == "ACT"
    assert result.test_code == "25MC1"
    assert result.test_date == dt.date(2026, 1, 17)
    assert result.day_known is True
    assert result.student_name == "Jane Student"
    assert result.formatted_test_date == "January 17, 2026"


def test_parse_scan_filename_without_day_falls_back_to_month_year():
    result = parse_scan_filename("Student, Jane 2027 ACT 25MC1 January 2026")

    assert result.day_known is False
    assert result.test_date == dt.date(2026, 1, 1)  # internal fallback, not shown to a person
    assert result.formatted_test_date == "January 2026"


def test_parse_scan_filename_handles_sat():
    result = parse_scan_filename("Smith, John 2026 SAT 1234 March 2026")

    assert result.test_family == "SAT"
    assert result.test_code == "1234"


def test_parse_scan_filename_handles_dsat():
    result = parse_scan_filename("Smith, John 2026 DSAT 8 March 8 2026")

    assert result.test_family == "DSAT"
    assert result.test_code == "8"


def test_parse_scan_filename_is_case_insensitive_on_family_and_month():
    result = parse_scan_filename("Student, Jane 2027 act 25MC1 january 17 2026")

    assert result.test_family == "ACT"
    assert result.test_date == dt.date(2026, 1, 17)


def test_parse_scan_filename_tolerates_no_space_after_comma():
    result = parse_scan_filename("Student,Jane 2027 ACT 25MC1 January 2026")

    assert result.first_name == "Jane"


def test_parse_scan_filename_raises_on_unrecognized_month():
    with pytest.raises(ValueError, match="Smarch"):
        parse_scan_filename("Student, Jane 2027 ACT 25MC1 Smarch 17 2026")


def test_parse_scan_filename_raises_on_malformed_input():
    with pytest.raises(ValueError, match="doesn't match"):
        parse_scan_filename("not_a_real_filename")


def test_parse_scan_filename_ignores_a_trailing_descriptive_suffix():
    """A real filename's own naming convention is often followed by
    descriptive text that isn't part of it at all (e.g. "Test Scan &
    Bubble", or a debug note) -- confirmed common in practice, not an
    edge case."""
    result = parse_scan_filename("Student, Jane 2027 ACT 25MC1 August 2026 Test Scan & Bubble")

    assert result.last_name == "Student"
    assert result.test_code == "25MC1"
    assert result.test_date == dt.date(2026, 8, 1)
    assert result.day_known is False


def test_parse_scan_filename_ignores_the_page_index_suffix_a_multipage_pdf_gets():
    """loading.py appends "_p{page_number}" to each page's own label when
    splitting a multi-page PDF apart (see its `label = ... f"{stem}_p..."`)
    -- that suffix must not break parsing the label back into a
    ScanFilename, since it's this pipeline's own addition, not something
    the original filename could have avoided."""
    result = parse_scan_filename("Student, Jane 2027 ACT 25MC1 August 2026_p3")

    assert result.test_code == "25MC1"
    assert result.test_date == dt.date(2026, 8, 1)


def test_parse_scan_filename_ignores_a_descriptive_suffix_plus_page_index():
    """Both of the above stacked together, exactly as seen live: a
    multi-page PDF whose own filename already had a descriptive suffix."""
    result = parse_scan_filename(
        "Kelson, Gabriella 2028 ACT 25MC1 August 2026 Test Scan & Bubble_p49"
    )

    assert result.last_name == "Kelson"
    assert result.first_name == "Gabriella"
    assert result.grad_year == 2028
    assert result.test_code == "25MC1"
    assert result.test_date == dt.date(2026, 8, 1)
    assert result.day_known is False


def test_parse_scan_filename_raises_on_invalid_calendar_date():
    with pytest.raises(ValueError, match="invalid date"):
        parse_scan_filename("Student, Jane 2027 ACT 25MC1 February 30 2026")
