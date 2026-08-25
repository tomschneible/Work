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


def test_parse_scan_filename_raises_on_invalid_calendar_date():
    with pytest.raises(ValueError, match="invalid date"):
        parse_scan_filename("Student, Jane 2027 ACT 25MC1 February 30 2026")
