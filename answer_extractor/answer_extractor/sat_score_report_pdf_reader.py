"""Parse a SAT/DSAT "Your Question-Level Feedback" grid straight out of a
rendered PDF -- the SAT/DSAT counterpart to score_report_pdf_reader.py's
ACT-shaped ScoreSheet reader, for the same reason: a caller that only has
a finished score-report PDF to check against (this pipeline's own
simplified-template export, or any other report using the same layout),
not the .xlsx/live Sheet that produced it.

Same general approach as score_report_pdf_reader.py -- PyMuPDF hands back
a flat bag of positioned words, and this does its own row/column
bucketing off them rather than trying to lean on find_tables() (see that
module's own docstring for why that mis-clusters this style of layout) --
but the real page's own shape (confirmed against a real generated report,
sat_simplified_score_report_writer.fill_simple_sat_score_report's own
output) differs enough from ACT's that this is its own reader, not a
parameterized version of that one:

  - A header row pairs one subject's *two modules* side by side (e.g.
    "Reading & Writing Module 1" next to "R & W Module 2 - Higher
    Difficulty"), not two different subjects the way ACT's does -- Math
    gets its own separate header row further down the page. Nothing here
    assumes both subjects fit on one page (every real export seen so far
    has, but header rows are found wherever they are, same as
    score_report_pdf_reader.py never assumes a fixed block count either).
  - Two label columns after the mark -- Domain, Skill -- not ACT's single
    Category.
  - No single shared title governing a pair of groups: each column-group
    has its own multi-word title directly above just it (the block's
    regenerated "<subject text> Module <1|2>[ - <difficulty>]" text --
    see sat_simplified_score_report_writer.fill_simple_sat_score_report's
    own docstring on why it's always regenerated wholesale, never a
    trustworthy placeholder). Confirmed against a real report that a
    group's own title is routinely *wider* than the group's own answer
    columns beneath it ("R & W Module 2 - Higher Difficulty" overruns
    "Correct Answer"/"Your Answer"'s own combined width) -- so unlike
    ACT's reader, a group's own title text can't be bounded by that
    group's own column window; it's bounded by the *next* group's own
    left edge instead (see _group_titles).

Returns the same {(section, question): answer} shape
score_report_pdf_reader.parse_scoresheet_pdf does, so both can feed
compare()/write_comparison_report() unchanged (see scoresheet_check.py,
which tries this reader as a fallback for a .pdf the ACT-shaped reader
doesn't recognize) -- but `section` here carries both subject *and*
module ("reading and writing module 1", not just "reading and writing"):
unlike ACT's own split-question-range blocks (English 1-35 and 36-75,
still one "english" section since the ranges never overlap), a SAT
subject's two modules each start back at question 1 -- collapsing them to
one section key the way ACT's groups collapse would collide Module 1's
own questions with Module 2's.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import fitz

from .sat_score_report_writer import normalize_subject
from .scoresheet_grid import QuestionKey

_MARK_CHARS = {"✔", "✘", "ø"}
# Same shape sat_simplified_score_report_writer._TITLE_PATTERN matches --
# not imported from there (that pattern is private to the writer side,
# and matches an openpyxl cell's own text directly, not text reassembled
# from separately-positioned PDF words) -- kept as this reader's own
# copy, the same way score_report_pdf_reader.py's own title-matching
# never reuses anything from score_report_writer.py either.
_TITLE_PATTERN = re.compile(r"^(?P<subject>.+?)\s+Module\s+(?P<module_num>1|2)\b")

# A PyMuPDF get_text("words") tuple: (x0, y0, x1, y1, text, block_no,
# line_no, word_no). Only x0/y0/text are used here.
_Word = Tuple[float, float, float, float, str, int, int, int]


@dataclasses.dataclass(frozen=True)
class _ColumnGroup:
    correct_x: float
    your_x: float
    domain_x: float
    header_y: float


def _find_column_groups(words: Sequence[_Word]) -> List[_ColumnGroup]:
    """Every (Correct Answer / Your Answer / Domain / Skill) column-group
    on the page -- anchored the same way score_report_pdf_reader.py's own
    _find_header_blocks anchors an ACT group, adapted for this page's own
    two-line "Correct"/"Answer" header (ACT's "Correct" is single-line):
    a "Correct" word with a matching "Answer" directly below it, a "Your"/
    "Answer" pair to its right on the same row, and a "Domain" word
    further right still (score_report_pdf_reader.py's counterpart looks
    for "Category" in that role). Requiring all of these together, not
    "Correct" alone, matters here for the same reason it does there: this
    page's own left-margin score summary also uses the word "Correct" in
    running text, not just a real column-group's own header."""
    groups: List[_ColumnGroup] = []
    for w in words:
        if w[4] != "Correct":
            continue
        correct_x, header_y = w[0], w[1]
        answer_below = [
            ow for ow in words
            if ow[4] == "Answer" and abs(ow[0] - correct_x) <= 5 and 3 <= ow[1] - header_y <= 12
        ]
        if not answer_below:
            continue
        your_candidates = [
            ow for ow in words if ow[4] == "Your" and abs(ow[1] - header_y) <= 2 and ow[0] > correct_x
        ]
        if not your_candidates:
            continue
        your_x = min(your_candidates, key=lambda ow: ow[0])[0]
        domain_candidates = [
            ow for ow in words if ow[4] == "Domain" and abs(ow[1] - header_y) <= 6 and ow[0] > correct_x
        ]
        if not domain_candidates:
            continue
        domain_x = min(domain_candidates, key=lambda ow: ow[0])[0]
        groups.append(_ColumnGroup(correct_x=correct_x, your_x=your_x, domain_x=domain_x, header_y=header_y))
    return groups


def _header_rows(groups: Sequence[_ColumnGroup]) -> List[List[_ColumnGroup]]:
    """`groups`, clustered by shared header_y and sorted left to right
    within each cluster -- mirrors score_report_pdf_reader.py's own
    _HeaderBlock grouping, needed here to work out each group's own title
    boundary (see _group_titles) rather than to split one shared title in
    half the way that reader's own _titles_for_block does."""
    header_ys = sorted({round(g.header_y, 1) for g in groups})
    return [
        sorted((g for g in groups if abs(g.header_y - hy) < 1), key=lambda g: g.correct_x) for hy in header_ys
    ]


def _group_titles(words: Sequence[_Word], row: Sequence[_ColumnGroup]) -> Dict[_ColumnGroup, str]:
    """One title string per group in `row` (a left-to-right-sorted header
    row from _header_rows) -- reconstructed from every word 8-25pt above
    the header row, in an x-window bounded on the left by this group's
    own left edge and on the right by the *next* group's own left edge
    (or the page's right edge for the last group in the row) -- see this
    module's own docstring on why a group's own answer-column width isn't
    wide enough to bound its own title text."""
    hy = row[0].header_y
    titles: Dict[_ColumnGroup, str] = {}
    for i, group in enumerate(row):
        left = group.correct_x - 30.0
        right = row[i + 1].correct_x - 30.0 if i + 1 < len(row) else float("inf")
        title_words = sorted(
            (w for w in words if hy - 25 <= w[1] <= hy - 8 and left <= w[0] < right), key=lambda w: w[0]
        )
        titles[group] = " ".join(w[4] for w in title_words)
    return titles


def _group_window(group: _ColumnGroup) -> Tuple[float, float]:
    """[left, right) covering just this group's question/correct/your/
    mark columns -- see this module's own docstring on why the Domain/
    Skill columns themselves are deliberately excluded (same reasoning as
    score_report_pdf_reader.py's own Category exclusion)."""
    return max(0.0, group.correct_x - 30.0), group.domain_x - 3.0


def _parse_group_rows(words: Sequence[_Word], group: _ColumnGroup) -> Dict[int, str]:
    """{question: your_answer} for one column-group -- identical
    row-walking technique to score_report_pdf_reader.py's own
    _parse_group_rows: cluster this group's own words by y, walk top to
    bottom, stop at the first row whose leftmost token isn't the next
    consecutive question number."""
    left, right = _group_window(group)
    header_bottom = group.header_y + 10
    in_window = [w for w in words if left <= w[0] < right and w[1] > header_bottom]
    text_words = sorted((w for w in in_window if w[4] not in _MARK_CHARS), key=lambda w: (w[1], w[0]))
    mark_words = [w for w in in_window if w[4] in _MARK_CHARS]

    rows: List[Dict[str, object]] = []
    for w in text_words:
        if rows and abs(w[1] - rows[-1]["y"]) < 3:
            rows[-1]["words"].append(w)
        else:
            rows.append({"y": w[1], "words": [w]})

    result: Dict[int, str] = {}
    expected: Optional[int] = None
    for row in rows:
        row_words = sorted(row["words"], key=lambda w: w[0])
        first_text = row_words[0][4]
        if not first_text.replace(".", "", 1).isdigit():
            break
        question = int(float(first_text))
        if expected is not None and question != expected:
            break
        row_y = row["y"]
        mark_candidates = [m for m in mark_words if -4 <= (row_y - m[1]) <= 4]
        if not mark_candidates:
            break
        mark = min(mark_candidates, key=lambda m: abs(row_y - m[1]))
        pre_mark = [w for w in row_words[1:] if w[0] < mark[0]]
        answer = "" if mark[4] == "ø" else (pre_mark[-1][4] if pre_mark else "")
        result[question] = answer
        expected = question + 1
    return result


def parse_sat_score_report_pdf(path: str | Path, page_index: Optional[int] = None) -> Dict[QuestionKey, str]:
    """Parse a rendered SAT/DSAT "Your Question-Level Feedback" PDF into
    {(section, question): your_answer} -- `section` is
    "<normalized subject> module <1|2>" (e.g. "reading and writing
    module 1"), not just the subject -- see this module's own docstring
    for why. The same shape scoresheet_check's parse_reference_scoresheet/
    score_report_pdf_reader.parse_scoresheet_pdf produce, so all three can
    feed the same compare()/write_comparison_report() pipeline.

    Searches every page for one with at least one Correct/Your Answer/
    Domain column-group unless `page_index` is given -- not hardcoded to
    "page 3" or any other fixed position, since how many pages precede
    this one depends on content this reader has no reason to assume
    (a cover page that may or may not split across two PDF pages, ...).
    Raises ValueError if no such page is found, if more than one page has
    one and `page_index` wasn't given (ambiguous -- pass page_index to
    pick), or if a group's own title can't be confidently parsed as
    "<subject> Module <1|2>"."""
    doc = fitz.open(str(path))
    candidate_indices = range(len(doc)) if page_index is None else [page_index]
    pages_with_groups = []
    for pi in candidate_indices:
        words = doc[pi].get_text("words")
        groups = _find_column_groups(words)
        if groups:
            pages_with_groups.append((pi, words, groups))

    if not pages_with_groups:
        raise ValueError(
            f"No SAT/DSAT-shaped table (Correct/Your Answer/Domain column headers) found in {path}"
        )
    if page_index is None and len(pages_with_groups) > 1:
        found = [pi for pi, _, _ in pages_with_groups]
        raise ValueError(
            f"Found SAT/DSAT-shaped tables on multiple pages {found} in {path} -- pass page_index to pick one"
        )

    result: Dict[QuestionKey, str] = {}
    for _pi, words, groups in pages_with_groups:
        for row in _header_rows(groups):
            titles = _group_titles(words, row)
            for group in row:
                title = titles[group]
                match = _TITLE_PATTERN.match(title)
                if not match:
                    raise ValueError(
                        f"Couldn't parse a subject/module title above a column-group in {path}: {title!r}"
                    )
                subject = normalize_subject(match.group("subject"))
                section = f"{subject} module {match.group('module_num')}"
                for question, answer in _parse_group_rows(words, group).items():
                    key = (section, question)
                    if key in result and result[key] != answer:
                        raise ValueError(f"Conflicting entries for {key} in {path}: {result[key]!r} vs {answer!r}")
                    result[key] = answer
    return result
