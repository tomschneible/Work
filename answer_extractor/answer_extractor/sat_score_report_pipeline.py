"""Bridge between score_report.py's ScoreReportRow objects (already run
through answer_keys.annotate_rows) and the Drive-backed SAT score-report
export (google_sat_score_report_export.py) -- the SAT counterpart to
score_report_pipeline.py, which does the same job for bubble-sheet
SheetResult objects.

Everything here operates on one student's rows at a time (all sharing one
`.source` -- see score_report.group_by_source for splitting a batch), the
same file that both the identifying filename convention
(scan_filename.parse_scan_filename) and answer_keys.annotate_rows already
key on.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from googleapiclient.discovery import Resource

from .google_sat_score_report_export import export_sat_score_report
from .gui_prompt import prompt_for_text
from .sat_score_report_writer import SatKey, normalize_subject
from .scan_filename import parse_scan_filename
from .score_report import ScoreReportRow

_MODULE_LABEL_PATTERN = re.compile(
    r"^Module\s+(?P<num>\d+)(?:\s*\((?P<qualifier>Easier|Harder)\))?\s*$", re.IGNORECASE
)
_SCORE_MIN, _SCORE_MAX = 200, 800


def _module_slot_for_label(label: str, section: str) -> str:
    """"module1" | "easier" | "harder", from a row's own module_label
    (answer_keys.annotate_rows's "Module 1"/"Module 2 (Easier)"/
    "Module 2 (Harder)" -- or the bare, unqualified "Module 2" it leaves
    a row at when identification wasn't confident enough). Raises
    ValueError for the bare, unqualified Module 2 case -- there's no slot
    to put those answers in without knowing which twin's flag to set
    (see sat_score_report_writer's module docstring), so this has to
    surface as a real error, not a guess."""
    match = _MODULE_LABEL_PATTERN.match(label.strip())
    if not match:
        raise ValueError(f"Unrecognized module label {label!r} for section {section!r}")
    if match.group("num") == "1":
        return "module1"
    qualifier = match.group("qualifier")
    if qualifier is None:
        raise ValueError(
            f"Could not determine {section!r} Module 2's difficulty from label {label!r} -- "
            "test/module identification wasn't confident enough (see answer_keys.annotate_rows)"
        )
    return qualifier.lower()


def answers_from_rows(rows: List[ScoreReportRow]) -> Dict[SatKey, str]:
    """{(subject, module_slot, question): answer} for
    google_sat_score_report_export.export_sat_score_report. Raises
    ValueError (via _module_slot_for_label) if any row's Module 2
    difficulty couldn't be confidently identified."""
    return {
        (normalize_subject(row.section), _module_slot_for_label(row.module_label, row.section), row.question): (
            row.your_answer
        )
        for row in rows
    }


def active_variants_from_rows(rows: List[ScoreReportRow]) -> Dict[str, str]:
    """{subject: "easier"/"harder"} -- which Module 2 variant each
    subject's rows say was administered. Raises ValueError if two rows
    for the same subject disagree (shouldn't happen -- would mean
    identify_test_and_modules labeled one section's own module
    inconsistently) or, via _module_slot_for_label, if a difficulty
    couldn't be confidently identified at all."""
    variants: Dict[str, str] = {}
    for row in rows:
        slot = _module_slot_for_label(row.module_label, row.section)
        if slot == "module1":
            continue
        subject = normalize_subject(row.section)
        existing = variants.get(subject)
        if existing is not None and existing != slot:
            raise ValueError(f"Conflicting Module 2 difficulty for {subject!r}: {existing!r} vs {slot!r}")
        variants[subject] = slot
    return variants


def _prompt_for_section_score(
    prompt_fn: Callable[[str, str], Optional[str]], student_name: str, subject: str
) -> Optional[int]:
    """One subject's score prompt, re-prompting (with the invalid entry
    kept as the new default, so fixing a typo doesn't mean retyping the
    whole thing) until a whole number 200-800 is entered or the dialog is
    cancelled -- unbounded only in the sense a person could keep entering
    garbage; nothing here loops on its own."""
    message = f"{student_name}'s {subject.title()} section score (200-800)?"
    default = ""
    while True:
        raw = prompt_fn(message, default)
        if raw is None:
            return None
        raw = raw.strip()
        if raw.isdigit() and _SCORE_MIN <= int(raw) <= _SCORE_MAX:
            return int(raw)
        default = raw
        message = (
            f"{raw!r} isn't a whole number from {_SCORE_MIN} to {_SCORE_MAX} -- "
            f"{student_name}'s {subject.title()} section score?"
        )


def export_sat_report(
    drive: Resource,
    sheets: Resource,
    templates_root_folder_id: str,
    rows: List[ScoreReportRow],
    output_dir: str | Path,
    prompt_fn: Callable[[str, str], Optional[str]] = prompt_for_text,
    temp_folder_id: Optional[str] = None,
) -> Path:
    """Produce one student's DSAT score-report PDF in `output_dir`, from
    `rows` -- every ScoreReportRow for one source file (see
    score_report.group_by_source), already run through
    answer_keys.annotate_rows. `temp_folder_id` is passed straight through
    to export_sat_score_report (see
    google_report_export_common.export_filled_report).

    Prompts once per subject present in `rows` for its scaled section
    score via `prompt_fn` (a native macOS dialog by default -- see
    gui_prompt.py), since nothing upstream can compute or extract that
    value yet (see sat_score_report_writer.fill_sat_score_report's
    docstring). Raises ValueError if `rows` is empty, its source
    filename isn't an ACT/DSAT/SAT-shaped name for the SAT family, a
    section's Module 2 difficulty couldn't be identified, or a score
    prompt was cancelled -- callers processing a batch should catch this
    per-student and fall back (e.g. into the combined .xlsx export) with
    a warning, rather than letting one incomplete or unidentified report
    fail the whole run, the same posture score_report_pipeline.py's
    export_sheet_report already takes toward a bubble sheet.
    """
    if not rows:
        raise ValueError("No rows given")
    output_dir = Path(output_dir)
    source = rows[0].source
    scan = parse_scan_filename(source)
    if scan.test_family not in ("SAT", "DSAT"):
        raise ValueError(f"{source!r} isn't a SAT/DSAT filename (test_family={scan.test_family!r})")

    answers = answers_from_rows(rows)
    active_variants = active_variants_from_rows(rows)
    test_date = scan.test_date if scan.day_known else scan.formatted_test_date
    base_name = scan.canonical_filename()

    subjects = sorted({normalize_subject(row.section) for row in rows})
    section_scores: Dict[str, int] = {}
    for subject in subjects:
        score = _prompt_for_section_score(prompt_fn, scan.student_name, subject)
        if score is None:
            raise ValueError(f"No {subject} score was entered for {scan.student_name} -- cancelled")
        section_scores[subject] = score

    pdf_bytes = export_sat_score_report(
        drive=drive,
        sheets=sheets,
        templates_root_folder_id=templates_root_folder_id,
        test_code=scan.test_code,
        answers=answers,
        active_variants=active_variants,
        student_name=scan.student_name,
        test_date=test_date,
        section_scores=section_scores,
        output_name=base_name,
        temp_folder_id=temp_folder_id,
    )
    pdf_path = output_dir / f"{base_name}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    return pdf_path
