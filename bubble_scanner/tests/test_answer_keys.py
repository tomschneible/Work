"""Tests for bubble_scanner.answer_keys: identifying which known SAT test
a score report is from, and whether each section's second module was the
easier or harder adaptive variant -- using synthetic reference keys and
synthetic score-report PDFs throughout (no real SAT content)."""
import pytest

from bubble_scanner.answer_keys import (
    AnswerKeyLibrary,
    annotate_rows,
    identify_test_and_modules,
    load_answer_keys,
)
from bubble_scanner.score_report import base_module_labels, parse_score_report
from tests.score_report_synth import write_score_report_pdf

SAMPLE_CSV = """Test,Section,Question,Module1,Module2Easy,Module2Hard
Practice A,Reading and Writing,1,D,D,B
Practice A,Reading and Writing,2,B,B,D
Practice A,Reading and Writing,3,C,A,C
Practice A,Math,1,A,B,A
Practice A,Math,2,D,D,C
Practice B,Reading and Writing,1,A,C,A
Practice B,Reading and Writing,2,B,D,B
Practice B,Reading and Writing,3,D,B,D
Practice B,Math,1,C,A,C
Practice B,Math,2,B,C,B
"""


def make_library() -> AnswerKeyLibrary:
    return AnswerKeyLibrary.from_csv_text(SAMPLE_CSV)


# -- AnswerKeyLibrary parsing -------------------------------------------------


def test_from_csv_text_parses_tests_and_lookups():
    library = make_library()
    assert set(library.tests()) == {"Practice A", "Practice B"}
    assert library.module1_answers("Practice A", "Reading and Writing") == {1: "D", 2: "B", 3: "C"}
    assert library.module2_variant_answers("Practice A", "Math", "easy") == {1: "B", 2: "D"}
    assert library.module2_variant_answers("Practice A", "Math", "hard") == {1: "A", 2: "C"}


def test_from_csv_text_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing column"):
        AnswerKeyLibrary.from_csv_text("Test,Section,Question,Module1\nA,Math,1,B\n")


def test_from_csv_text_ignores_blank_rows():
    text = SAMPLE_CSV + "\n,,,,,\n"
    library = AnswerKeyLibrary.from_csv_text(text)
    assert set(library.tests()) == {"Practice A", "Practice B"}


# -- identify_test_and_modules ------------------------------------------------


def _rows_for(test: str, module2_variant: str):
    """Build synthetic ScoreReportRow-shaped tuples (question, section,
    correct_answer, your_answer, correctness) for write_score_report_pdf,
    using `test`'s own reference key as ground truth so identification
    should succeed with high confidence."""
    library = make_library()
    rows = []
    for section in ("Reading and Writing", "Math"):
        m1 = library.module1_answers(test, section)
        for q in sorted(m1):
            rows.append((q, section, m1[q], m1[q], "Correct"))
        m2 = library.module2_variant_answers(test, section, module2_variant)
        for q in sorted(m2):
            rows.append((q, section, m2[q], m2[q], "Correct"))
    return rows


def test_identifies_test_and_easy_module(tmp_path):
    library = make_library()
    path = tmp_path / "report.pdf"
    write_score_report_pdf(path, _rows_for("Practice A", "easy"))
    rows = parse_score_report(path)

    result = identify_test_and_modules(rows, library)
    assert result.test == "Practice A"
    assert result.test_confidence == pytest.approx(1.0)
    # module 1 for each section is untouched; module 2 (the 2nd occurrence
    # of each section) should be labeled Easier.
    labels = set(result.module_labels.values())
    assert any("Easier" in label for label in labels)
    assert not any("Harder" in label for label in labels)


def test_identifies_test_and_hard_module(tmp_path):
    library = make_library()
    path = tmp_path / "report.pdf"
    write_score_report_pdf(path, _rows_for("Practice B", "hard"))
    rows = parse_score_report(path)

    result = identify_test_and_modules(rows, library)
    assert result.test == "Practice B"
    labels = set(result.module_labels.values())
    assert any("Harder" in label for label in labels)
    assert not any("Easier" in label for label in labels)


def test_no_confident_match_falls_back_to_plain_labels(tmp_path):
    library = make_library()
    path = tmp_path / "report.pdf"
    # Answers that don't correspond to either known test at all.
    write_score_report_pdf(
        path,
        [
            (1, "Reading and Writing", "A", "A", "Correct"),
            (2, "Reading and Writing", "A", "A", "Correct"),
            (3, "Reading and Writing", "A", "A", "Correct"),
            (1, "Reading and Writing", "A", "A", "Correct"),
            (2, "Reading and Writing", "A", "A", "Correct"),
            (3, "Reading and Writing", "A", "A", "Correct"),
        ],
    )
    rows = parse_score_report(path)
    result = identify_test_and_modules(rows, library)

    assert result.test == ""
    assert result.module_labels == base_module_labels(rows)


def test_empty_library_falls_back_to_plain_labels(tmp_path):
    library = AnswerKeyLibrary.from_csv_text("Test,Section,Question,Module1,Module2Easy,Module2Hard\n")
    path = tmp_path / "report.pdf"
    write_score_report_pdf(path, _rows_for("Practice A", "easy"))
    rows = parse_score_report(path)

    result = identify_test_and_modules(rows, library)
    assert result.test == ""
    assert result.module_labels == base_module_labels(rows)


def test_handles_grid_in_style_multi_value_correct_answers(tmp_path):
    csv_text = """Test,Section,Question,Module1,Module2Easy,Module2Hard
Practice C,Math,1,11/28,54,336
"""
    library = AnswerKeyLibrary.from_csv_text(csv_text)
    path = tmp_path / "report.pdf"
    write_score_report_pdf(
        path,
        [
            # College Board score reports can list several acceptable forms
            # for a grid-in question, comma-separated.
            (1, "Math", ".3928, .3929, 11/28", "11/28", "Correct"),
            (1, "Math", "54", "54", "Correct"),
        ],
    )
    rows = parse_score_report(path)
    result = identify_test_and_modules(rows, library)
    assert result.test == "Practice C"


# -- annotate_rows -------------------------------------------------------------


def test_annotate_rows_identifies_each_source_independently(tmp_path):
    library = make_library()
    path_a = tmp_path / "a.pdf"
    path_b = tmp_path / "b.pdf"
    write_score_report_pdf(path_a, _rows_for("Practice A", "easy"))
    write_score_report_pdf(path_b, _rows_for("Practice B", "hard"))

    rows = parse_score_report(path_a) + parse_score_report(path_b)
    annotated = annotate_rows(rows, library)

    by_source = {}
    for row in annotated:
        by_source.setdefault(row.source, []).append(row)

    assert all(r.test == "Practice A" for r in by_source["a"])
    assert all(r.test == "Practice B" for r in by_source["b"])
    assert any("Easier" in r.module_label for r in by_source["a"])
    assert any("Harder" in r.module_label for r in by_source["b"])


# -- load_answer_keys (fetch/cache/fallback) -----------------------------------


def test_load_answer_keys_uses_bundled_file_without_refresh(tmp_path):
    bundled = tmp_path / "bundled.csv"
    bundled.write_text(SAMPLE_CSV)
    cache = tmp_path / "cache" / "keys.csv"

    library = load_answer_keys(refresh=False, cache_path=cache, bundled_path=bundled)
    assert set(library.tests()) == {"Practice A", "Practice B"}
    assert not cache.exists()


def test_load_answer_keys_fetches_via_file_url_and_writes_cache(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(SAMPLE_CSV)
    cache = tmp_path / "cache" / "keys.csv"
    bundled = tmp_path / "bundled.csv"
    bundled.write_text("Test,Section,Question,Module1,Module2Easy,Module2Hard\n")

    library = load_answer_keys(
        refresh=True,
        cache_path=cache,
        bundled_path=bundled,
        url=source.as_uri(),
    )
    assert set(library.tests()) == {"Practice A", "Practice B"}
    assert cache.exists()
    assert cache.read_text() == SAMPLE_CSV


def test_load_answer_keys_falls_back_to_cache_when_fetch_fails(tmp_path):
    cache = tmp_path / "cache" / "keys.csv"
    cache.parent.mkdir(parents=True)
    cache.write_text(SAMPLE_CSV)
    bundled = tmp_path / "bundled.csv"
    bundled.write_text("Test,Section,Question,Module1,Module2Easy,Module2Hard\n")

    library = load_answer_keys(
        refresh=True,
        cache_path=cache,
        bundled_path=bundled,
        url="file:///does/not/exist.csv",
        timeout=1.0,
    )
    assert set(library.tests()) == {"Practice A", "Practice B"}


def test_load_answer_keys_falls_back_to_bundled_when_fetch_and_cache_fail(tmp_path):
    cache = tmp_path / "cache" / "keys.csv"  # doesn't exist
    bundled = tmp_path / "bundled.csv"
    bundled.write_text(SAMPLE_CSV)

    library = load_answer_keys(
        refresh=True,
        cache_path=cache,
        bundled_path=bundled,
        url="file:///does/not/exist.csv",
        timeout=1.0,
    )
    assert set(library.tests()) == {"Practice A", "Practice B"}
