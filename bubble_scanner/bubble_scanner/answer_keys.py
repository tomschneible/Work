"""Reference answer keys for identifying which SAT test a score report is
from, and -- the harder part -- which variant (easier/harder) of each
section's adaptive second module was administered.

Digital SAT sections (Reading and Writing; Math) are two-stage adaptive:
everyone gets the same Module 1, then Module 2 is one of two different
question sets depending on Module 1 performance. A score report never
states in plain text which Module 2 variant a student got -- that's
external knowledge someone has to maintain per test. This module holds
that reference data (bubble_scanner/answer_keys/sat_answer_keys.csv) and
matches a parsed report against it.

The matching signal is the report's own "Correct Answer" column (Bluebook's
ground truth for whatever was actually administered), not the student's
picks -- comparing correct-answers to a reference key for the *same*
test/module should match ~100%, while a *different* test/variant's key
only agrees by chance (~25% for 4-option questions), so identification is
a clean high-confidence-vs-chance signal rather than a fuzzy one.

To let the reference file be updated centrally (e.g. edited on GitHub)
without every machine needing to `git pull` the code, load_answer_keys()
fetches the latest copy over HTTPS on each call, caches it locally, and
falls back to the cache (then the bundled copy shipped in the repo) if
offline -- so grading still works without a network connection, just
potentially with stale test coverage.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .score_report import ScoreReportRow, group_by_module, section_module_index

_BUNDLED_CSV_PATH = Path(__file__).parent / "answer_keys" / "sat_answer_keys.csv"
_CACHE_PATH = Path.home() / ".cache" / "bubble_scanner" / "sat_answer_keys.csv"
_DEFAULT_RAW_URL = (
    "https://raw.githubusercontent.com/tomschneible/Work/"
    "claude/bubble-sheet-scanner-7q6uf7/bubble_scanner/answer_keys/sat_answer_keys.csv"
)
# Override for testing, a different branch, or a fork -- e.g.
# BUBBLE_SCANNER_ANSWER_KEYS_URL=file:///path/to/keys.csv
_URL_ENV_VAR = "BUBBLE_SCANNER_ANSWER_KEYS_URL"

_REQUIRED_COLUMNS = ["Test", "Section", "Question", "Module1", "Module2Easy", "Module2Hard"]


@dataclasses.dataclass(frozen=True)
class AnswerKeyEntry:
    module1: str
    module2_easy: str
    module2_hard: str


class AnswerKeyLibrary:
    """Reference correct-answers for one or more known tests, keyed by
    (test, section, question)."""

    def __init__(self, entries: Dict[Tuple[str, str, int], AnswerKeyEntry]):
        self._entries = entries
        tests: List[str] = []
        for test, _section, _question in entries:
            if test not in tests:
                tests.append(test)
        self._tests = tests

    def tests(self) -> List[str]:
        return list(self._tests)

    def module1_answers(self, test: str, section: str) -> Dict[int, str]:
        return {
            question: entry.module1
            for (t, s, question), entry in self._entries.items()
            if t == test and s == section
        }

    def module2_variant_answers(self, test: str, section: str, variant: str) -> Dict[int, str]:
        attr = "module2_easy" if variant == "easy" else "module2_hard"
        return {
            question: getattr(entry, attr)
            for (t, s, question), entry in self._entries.items()
            if t == test and s == section
        }

    @classmethod
    def from_csv_text(cls, text: str) -> "AnswerKeyLibrary":
        reader = csv.DictReader(io.StringIO(text))
        missing = [c for c in _REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Answer key CSV is missing column(s): {missing}")

        entries: Dict[Tuple[str, str, int], AnswerKeyEntry] = {}
        for row in reader:
            test = row["Test"].strip()
            section = row["Section"].strip()
            question_str = row["Question"].strip()
            if not test or not question_str:
                continue
            question = int(question_str)
            entries[(test, section, question)] = AnswerKeyEntry(
                module1=row["Module1"].strip(),
                module2_easy=row["Module2Easy"].strip(),
                module2_hard=row["Module2Hard"].strip(),
            )
        return cls(entries)

    @classmethod
    def from_csv_file(cls, path: str | Path) -> "AnswerKeyLibrary":
        return cls.from_csv_text(Path(path).read_text())


def load_answer_keys(
    refresh: bool = True,
    timeout: float = 5.0,
    cache_path: Optional[Path] = None,
    bundled_path: Optional[Path] = None,
    url: Optional[str] = None,
) -> AnswerKeyLibrary:
    """Load the reference answer key library: try fetching the latest copy
    over HTTPS first (so a central edit reaches every machine without a
    code update), falling back to the last successfully fetched copy, and
    finally to the copy bundled in this checkout. `cache_path`,
    `bundled_path`, and `url` override the defaults -- mainly for tests."""
    cache_path = cache_path or _CACHE_PATH
    bundled_path = bundled_path or _BUNDLED_CSV_PATH
    text: Optional[str] = None

    if refresh:
        fetch_url = url or os.environ.get(_URL_ENV_VAR, _DEFAULT_RAW_URL)
        try:
            with urllib.request.urlopen(fetch_url, timeout=timeout) as response:
                text = response.read().decode("utf-8")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            text = None

    if text is None and cache_path.exists():
        text = cache_path.read_text()

    if text is None:
        text = bundled_path.read_text()

    return AnswerKeyLibrary.from_csv_text(text)


def _acceptable_values(report_value: str) -> List[str]:
    """Grid-in "Correct Answer" values may list several acceptable forms,
    e.g. ".3928, .3929, 11/28" -- split them so a key storing any one
    canonical form still matches."""
    return [v.strip() for v in report_value.split(",")]


def _match_fraction(key_answers: Dict[int, str], actual_answers: Dict[int, str]) -> float:
    common = set(key_answers) & set(actual_answers)
    if not common:
        return 0.0
    matches = sum(1 for q in common if key_answers[q] in _acceptable_values(actual_answers[q]))
    return matches / len(common)


@dataclasses.dataclass(frozen=True)
class IdentificationResult:
    test: str  # "" if no confident match
    test_confidence: float
    module_labels: Dict[int, str]  # module counter -> label, for every module present


# Below this, a test/variant match is treated as "no confident match" rather
# than trusted -- real matches are expected near 1.0, chance-level agreement
# on 4-option questions is ~0.25, so this sits well above noise.
_CONFIDENCE_THRESHOLD = 0.8


def identify_test_and_modules(
    rows: List[ScoreReportRow], library: AnswerKeyLibrary
) -> IdentificationResult:
    """Identify which known test a report is from (by matching Module 1's
    correct answers, which don't vary by adaptive branch) and, for each
    section's second module, whether it was the easier or harder variant
    (by matching that module's correct answers against both variants'
    reference keys). Falls back to plain "Module N" labels wherever a
    confident match can't be established."""
    base_labels = {
        module_num: f"Module {idx}" for module_num, idx in section_module_index(rows).items()
    }
    blocks = group_by_module(rows)
    section_index = section_module_index(rows)

    module1_blocks = {m: b for m, b in blocks.items() if section_index[m] == 1}

    best_test = ""
    best_score = 0.0
    for test in library.tests():
        scores = []
        for module_num, block in module1_blocks.items():
            section = block[0].section
            key_answers = library.module1_answers(test, section)
            actual = {r.question: r.correct_answer for r in block}
            if key_answers and actual:
                scores.append(_match_fraction(key_answers, actual))
        if not scores:
            continue
        score = sum(scores) / len(scores)
        if score > best_score:
            best_score = score
            best_test = test

    if best_score < _CONFIDENCE_THRESHOLD:
        return IdentificationResult(test="", test_confidence=best_score, module_labels=base_labels)

    labels = dict(base_labels)
    for module_num, block in blocks.items():
        if section_index[module_num] == 1:
            continue  # Module 1 doesn't vary by branch; base label is fine.
        section = block[0].section
        actual = {r.question: r.correct_answer for r in block}
        easy_key = library.module2_variant_answers(best_test, section, "easy")
        hard_key = library.module2_variant_answers(best_test, section, "hard")
        easy_score = _match_fraction(easy_key, actual) if easy_key else 0.0
        hard_score = _match_fraction(hard_key, actual) if hard_key else 0.0

        if easy_score >= _CONFIDENCE_THRESHOLD and easy_score > hard_score:
            labels[module_num] = f"{base_labels[module_num]} (Easier)"
        elif hard_score >= _CONFIDENCE_THRESHOLD and hard_score > easy_score:
            labels[module_num] = f"{base_labels[module_num]} (Harder)"
        # else: leave the base label -- ambiguous or no reference data.

    return IdentificationResult(test=best_test, test_confidence=best_score, module_labels=labels)


def annotate_rows(rows: List[ScoreReportRow], library: AnswerKeyLibrary) -> List[ScoreReportRow]:
    """Return new rows with `test` and `module_label` filled in. Rows from
    different source files are identified independently, since a batch can
    mix reports from different tests."""
    by_source: Dict[str, List[ScoreReportRow]] = {}
    for row in rows:
        by_source.setdefault(row.source, []).append(row)

    annotated: List[ScoreReportRow] = []
    for source_rows in by_source.values():
        result = identify_test_and_modules(source_rows, library)
        for row in source_rows:
            annotated.append(
                dataclasses.replace(
                    row,
                    test=result.test,
                    module_label=result.module_labels.get(row.module, f"Module {row.module}"),
                )
            )
    return annotated
