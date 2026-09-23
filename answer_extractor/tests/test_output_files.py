from answer_extractor.output_files import unused_base_name


def test_a_free_name_is_used_as_is(tmp_path):
    assert unused_base_name(tmp_path, "Student, Jane 2027 ACT 25MC1 January 17 2026", [".pdf"]) == (
        "Student, Jane 2027 ACT 25MC1 January 17 2026"
    )


def test_a_taken_name_gets_the_next_free_number(tmp_path):
    (tmp_path / "Report.pdf").write_bytes(b"")
    assert unused_base_name(tmp_path, "Report", [".pdf"]) == "Report (2)"
    (tmp_path / "Report (2).pdf").write_bytes(b"")
    assert unused_base_name(tmp_path, "Report", [".pdf"]) == "Report (3)"


def test_a_name_taken_under_any_of_the_extensions_counts_as_taken(tmp_path):
    (tmp_path / "Report FLAG (2).xlsx").write_bytes(b"")
    (tmp_path / "Report FLAG.pdf").write_bytes(b"")
    assert unused_base_name(tmp_path, "Report FLAG", [".pdf", ".xlsx"]) == "Report FLAG (3)"


def test_only_the_given_extensions_are_checked(tmp_path):
    (tmp_path / "Report.png").write_bytes(b"")  # a scan with the report's name, but not a PDF
    assert unused_base_name(tmp_path, "Report", [".pdf"]) == "Report"
