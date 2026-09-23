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


def test_parse_scan_filename_strips_a_leading_hash_from_test_code():
    """"DSAT #6" and "DSAT 6" are both real naming habits (confirmed live)
    for the exact same test -- stripped here so every downstream user of
    test_code (Drive template lookup, the "Digital SAT #N" label this
    pipeline writes into a filled report) sees one consistent value
    regardless of which the filename happened to use."""
    result = parse_scan_filename("Smith, John 2026 DSAT #6 March 6 2026")

    assert result.test_code == "6"


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
        "Learner, Priya 2028 ACT 25MC1 August 2026 Test Scan & Bubble_p49"
    )

    assert result.last_name == "Learner"
    assert result.first_name == "Priya"
    assert result.grad_year == 2028
    assert result.test_code == "25MC1"
    assert result.test_date == dt.date(2026, 8, 1)
    assert result.day_known is False


def test_parse_scan_filename_raises_on_invalid_calendar_date():
    with pytest.raises(ValueError, match="invalid date"):
        parse_scan_filename("Student, Jane 2027 ACT 25MC1 February 30 2026")


def test_canonical_filename_reconstructs_the_input_shape_with_a_day():
    scan = parse_scan_filename("Student, Jane 2027 ACT 25MC1 January 17 2026")

    assert scan.canonical_filename() == "Student, Jane 2027 ACT 25MC1 January 17 2026"


def test_canonical_filename_reconstructs_the_input_shape_without_a_day():
    scan = parse_scan_filename("Student, Jane 2027 ACT 25MC1 January 2026")

    assert scan.canonical_filename() == "Student, Jane 2027 ACT 25MC1 January 2026"


def test_canonical_filename_appends_flag_only_when_flagged():
    scan = parse_scan_filename("Student, Jane 2027 ACT 25MC1 January 17 2026")

    assert scan.canonical_filename(flagged=True) == "Student, Jane 2027 ACT 25MC1 January 17 2026 FLAG"
    assert scan.canonical_filename(flagged=False) == "Student, Jane 2027 ACT 25MC1 January 17 2026"


def test_canonical_filename_uses_the_family_exactly_as_parsed_never_a_separate_label():
    """Confirms there's no redundant "SAT DSAT ..." double-labeling: the
    family token is always exactly scan.test_family, whatever the input
    filename actually carried (ACT, SAT, or DSAT)."""
    dsat_scan = parse_scan_filename("Smith, John 2026 DSAT 8 March 8 2026")
    sat_scan = parse_scan_filename("Smith, John 2026 SAT 1234 March 2026")

    assert dsat_scan.canonical_filename() == "Smith, John 2026 DSAT 8 March 8 2026"
    assert sat_scan.canonical_filename() == "Smith, John 2026 SAT 1234 March 2026"


def test_canonical_filename_drops_a_leading_hash_the_input_filename_had():
    scan = parse_scan_filename("Smith, John 2026 DSAT #6 March 6 2026")

    assert scan.canonical_filename() == "Smith, John 2026 DSAT 6 March 6 2026"


@pytest.mark.parametrize("initial", ["M", "M."])
def test_parse_scan_filename_accepts_one_initial_after_the_first_name(initial):
    label = f"Student, Jane {initial} 2027 ACT 25MC1 January 17 2026"
    result = parse_scan_filename(label)

    assert result.first_name == "Jane"
    assert result.middle_initial == initial
    assert result.grad_year == 2027
    assert result.student_name == f"Jane {initial} Student"
    assert result.canonical_filename() == label


@pytest.mark.parametrize("year_part, suffix", [("2027C", "C"), ("2027 C", " C")])
def test_parse_scan_filename_accepts_a_c_after_the_graduation_year(year_part, suffix):
    label = f"Student, Jane {year_part} ACT 25MC1 January 17 2026"
    result = parse_scan_filename(label)

    assert result.grad_year == 2027
    assert result.grad_year_suffix == suffix
    assert result.test_family == "ACT"
    assert result.student_name == "Jane Student"  # the C isn't part of the name
    assert result.canonical_filename() == label


def test_parse_scan_filename_capitalizes_a_lowercase_c_like_the_rest_of_the_name():
    result = parse_scan_filename("Student, Jane 2027c act 25MC1 january 17 2026")

    assert result.canonical_filename() == "Student, Jane 2027C ACT 25MC1 January 17 2026"


def test_parse_scan_filename_accepts_an_initial_and_a_c_together():
    label = "Student, Jane M. 2027C DSAT 8 March 8 2026"
    result = parse_scan_filename(label)

    assert (result.middle_initial, result.grad_year_suffix, result.test_code) == ("M.", "C", "8")
    assert result.canonical_filename(flagged=True) == f"{label} FLAG"


@pytest.mark.parametrize(
    "label",
    [
        "Student, Mary Kate 2027 ACT 25MC1 January 17 2026",  # a second first name
        "Student, Jane MK 2027 ACT 25MC1 January 17 2026",  # a two-letter "initial"
        "Student, Jane M K 2027 ACT 25MC1 January 17 2026",  # two initials
        "Student, Jane M.K. 2027 ACT 25MC1 January 17 2026",
        "Student, Jane 2027B ACT 25MC1 January 17 2026",  # a letter other than C
        "Student, Jane 2027 B ACT 25MC1 January 17 2026",
        "Student, Jane 2027CC ACT 25MC1 January 17 2026",
        "Student, Jane 2027 C C ACT 25MC1 January 17 2026",
    ],
)
def test_parse_scan_filename_still_rejects_anything_beyond_those_two_additions(label):
    with pytest.raises(ValueError, match="doesn't match"):
        parse_scan_filename(label)


@pytest.mark.parametrize(
    "a, b, same",
    [
        ("Student, Jane M 2027 ACT 25MC1 January 17 2026", "Student, Jane 2027 ACT 25MC1 January 17 2026", True),
        ("Student, Jane M 2027 ACT 25MC1 January 17 2026", "Student, Jane M. 2027C ACT 25MC1 January 17 2026", True),
        ("Student, Jane M 2027 ACT 25MC1 January 17 2026", "Student, Jane K 2027 ACT 25MC1 January 17 2026", False),
        ("Student, Jane 2027 ACT 25MC1 January 17 2026", "Student, John 2027 ACT 25MC1 January 17 2026", False),
    ],
)
def test_could_be_same_student_only_lets_a_missing_initial_slide(a, b, same):
    assert parse_scan_filename(a).could_be_same_student(parse_scan_filename(b)) is same
    assert parse_scan_filename(b).could_be_same_student(parse_scan_filename(a)) is same
