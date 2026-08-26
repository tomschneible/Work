"""Parse a scanned sheet's own filename into the identifying fields it
carries: "LastName, FirstName GradYear TestFamily TestCode Month [Day]
Year" -- e.g. "Student, Jane 2027 ACT 25MC1 January 17 2026" (day optional;
"Student, Jane 2027 ACT 25MC1 January 2026" is also valid, for files from
before day-level naming existed). This is this pipeline's only source for
a scan's student name, test date, and which Drive template to use --
nothing else in the input carries it.

TestFamily is "ACT", "SAT", or "DSAT" -- confirmed against a real
Digital SAT filename using "DSAT", not "SAT" (sat_score_report_pipeline.py
treats both as the same "SAT" Drive category).

Anything after Year is ignored, not just trimmed -- e.g.
"Student, Jane 2027 ACT 25MC1 January 2026 Test Scan & Bubble" and
"..._p3" (the page-index suffix loading.py appends to each page's own
label when a multi-page PDF is split apart) both still parse. Confirmed
against real filenames: a trailing descriptive suffix like "Test Scan &
Bubble" -- or worse, a debug note like "didn't find lines" -- after the
naming convention's own fields is common in practice, not the exception.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from typing import Optional

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_PATTERN = re.compile(
    r"^(?P<last>[^,]+),\s*(?P<first>\S+)\s+(?P<grad_year>\d{4})\s+(?P<test_family>ACT|DSAT|SAT)\s+"
    r"(?P<test_code>\S+)\s+(?P<month>[A-Za-z]+)(?:\s+(?P<day>\d{1,2}))?\s+(?P<year>\d{4})(?:[\s_].*)?$",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class ScanFilename:
    last_name: str
    first_name: str
    grad_year: int
    test_family: str  # "ACT" or "SAT", as it appeared in the filename
    test_code: str
    test_date: dt.date
    # False when the filename only gave a month/year (no day) -- test_date
    # above still holds a real date (day defaults to 1) so ordinary date
    # arithmetic/formatting works, but callers that display the date back
    # to a person (e.g. filling in a report's Test Date field) should
    # check this and show just "Month Year" instead when it's False,
    # rather than implying a specific day nothing in the input confirmed.
    day_known: bool

    @property
    def student_name(self) -> str:
        """"FirstName LastName" -- the order score-report templates want
        (see score_report_writer), the reverse of how the filename itself
        orders them."""
        return f"{self.first_name} {self.last_name}"

    @property
    def formatted_test_date(self) -> str:
        """The Test Date field's actual display value: the real date when
        the filename gave a day, else just "Month Year" -- never a
        fabricated day."""
        # Not strftime("%-d"/"%#d") for the day -- that flag is
        # Linux/Mac-only (Windows needs "%#d" instead), and this needs to
        # work on whatever OS the lab laptops actually run.
        if self.day_known:
            return f"{self.test_date.strftime('%B')} {self.test_date.day}, {self.test_date.year}"
        return self.test_date.strftime("%B %Y")


def parse_scan_filename(label: str) -> ScanFilename:
    """Raises ValueError if `label` doesn't match the expected shape --
    almost always means either a scan was dropped in without being
    renamed to this convention first, or the convention has drifted from
    what this parser expects."""
    match = _PATTERN.match(label.strip())
    if not match:
        raise ValueError(
            f"{label!r} doesn't match the expected "
            "'LastName, FirstName GradYear ACT/SAT TestCode Month [Day] Year' filename shape"
        )
    month_name = match.group("month").lower()
    month = _MONTHS.get(month_name)
    if month is None:
        raise ValueError(f"{label!r}: {match.group('month')!r} isn't a recognized month name")

    day_str: Optional[str] = match.group("day")
    day = int(day_str) if day_str else 1
    year = int(match.group("year"))
    try:
        test_date = dt.date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"{label!r}: invalid date ({exc})") from exc

    return ScanFilename(
        last_name=match.group("last").strip(),
        first_name=match.group("first").strip(),
        grad_year=int(match.group("grad_year")),
        test_family=match.group("test_family").upper(),
        test_code=match.group("test_code"),
        test_date=test_date,
        day_known=day_str is not None,
    )
