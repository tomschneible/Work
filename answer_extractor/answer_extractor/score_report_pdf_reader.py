"""Parse an ACT-style "ScoreSheet" grid straight out of a rendered PDF --
the PDF counterpart to scoresheet_grid.py's xlsx-cell version, for callers
that only have a finished score-report PDF to check against (this
pipeline's own PDF export, or a report someone already produced by hand)
rather than the .xlsx workbook that produced it.

The page renders each section's answers as several side-by-side
(Question, Correct Answer, Your Answer, mark, Category) column-groups --
identical in spirit to the xlsx layout scoresheet_grid.py reads, but with
none of a spreadsheet's cell/row bookkeeping to lean on: PyMuPDF hands back
a flat bag of positioned words, and even its own find_tables() table
detector turns out to mis-cluster this specific layout (a wide multi-line
Category column intermittently grabs neighboring rows' question-number
labels into the wrong table cell -- confirmed by checking those "missing"
labels' own coordinates, which sit exactly on their true row). So this
reads words directly and does its own row/column bucketing instead of
going through find_tables():

  - A column-group is anchored by its "Correct"/"Your" header word pair
    (found once per group) and its "Category" header word, which together
    define a tight x-window covering just that group's own
    question/correct/your/mark columns -- deliberately excluding the
    Category column itself, since that's the one place stray text can
    leak in: a neighboring group's own category codes sit just past a
    group's left edge, and once a group's real rows run out the same
    x-position is reused further down the page for a multi-line category
    legend ("English Categories\\nPOW Production of Writing\\n...").
    Scoping the window to end a few points before the Category header
    excludes both.
  - Within one group's window, rows are found by clustering the group's
    own words by y (mark glyphs render on a slightly different, ~1.7pt
    higher baseline than the rest of their row, so mark words are matched
    to the nearest text-row cluster by y-offset rather than clustered
    together with it) and walked top to bottom, stopping the moment a
    row's leftmost token isn't the next consecutive question number --
    which is what naturally excludes that legend text once a group's
    real rows are exhausted, with no need to know a group's row count
    ahead of time.
  - Two subject titles govern each pair of header rows (e.g. "English"
    and "Math" both appear above one header row, each governing half of
    that row's column-groups, sorted left to right) -- found by their
    consistent ~16-17pt offset above the header row they sit over.

Validated against a real generated report (this pipeline's own PDF
export) with zero differences against the xlsx used to produce it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import fitz

from .scoresheet_grid import QuestionKey, normalize_section

_MARK_CHARS = {"✔", "✘", "ø"}
_SUBJECT_TITLES = {"English", "Math", "Reading", "Science"}

# A PyMuPDF get_text("words") tuple: (x0, y0, x1, y1, text, block_no,
# line_no, word_no). Only x0/y0/text are used here.
_Word = Tuple[float, float, float, float, str, int, int, int]


@dataclasses.dataclass(frozen=True)
class _ColumnGroup:
    correct_x: float
    your_x: float
    category_x: float
    header_y: float


@dataclasses.dataclass(frozen=True)
class _HeaderBlock:
    header_y: float
    groups: List[_ColumnGroup]


def _find_header_blocks(words: Sequence[_Word]) -> List[_HeaderBlock]:
    """Every (Correct, Your, Category) column-group on the page, grouped
    into blocks that share a header row (e.g. English/Math's shared
    header vs. Reading/Science's)."""
    groups: List[_ColumnGroup] = []
    for w in words:
        if w[4] != "Correct":
            continue
        correct_x, header_y = w[0], w[1]
        your_candidates = [
            ow for ow in words if ow[4] == "Your" and abs(ow[1] - header_y) <= 2 and ow[0] > correct_x
        ]
        if not your_candidates:
            continue
        your_x = min(your_candidates, key=lambda ow: ow[0])[0]
        category_candidates = [
            ow for ow in words if ow[4] == "Category" and abs(ow[1] - header_y) <= 6 and ow[0] > correct_x
        ]
        if not category_candidates:
            continue
        category_x = min(category_candidates, key=lambda ow: ow[0])[0]
        groups.append(_ColumnGroup(correct_x=correct_x, your_x=your_x, category_x=category_x, header_y=header_y))

    header_ys = sorted({round(g.header_y, 1) for g in groups})
    blocks = []
    for hy in header_ys:
        block_groups = sorted((g for g in groups if abs(g.header_y - hy) < 1), key=lambda g: g.correct_x)
        blocks.append(_HeaderBlock(header_y=hy, groups=block_groups))
    return blocks


def _titles_for_block(words: Sequence[_Word], block: _HeaderBlock) -> List[str]:
    """One section name per column-group in `block`: exactly two subject
    titles sit 8-25pt above the header row, and the first (leftmost)
    governs the left half of the block's groups, the second the right
    half -- matching how the page visually centers each title over the
    group(s) it covers."""
    hy = block.header_y
    titles = sorted(
        {(w[0], w[4]) for w in words if w[4] in _SUBJECT_TITLES and hy - 25 <= w[1] <= hy - 8},
        key=lambda item: item[0],
    )
    if len(titles) != 2:
        raise ValueError(
            f"Expected exactly 2 subject titles above the header row at y={hy}, found {[t[1] for t in titles]}"
        )
    if len(block.groups) % 2 != 0:
        raise ValueError(f"Expected an even number of column-groups under header row at y={hy}, found {len(block.groups)}")
    half = len(block.groups) // 2
    first_title, second_title = titles[0][1], titles[1][1]
    return [first_title if gi < half else second_title for gi in range(len(block.groups))]


def _group_window(group: _ColumnGroup) -> Tuple[float, float]:
    """[left, right) covering just this group's question/correct/
    your/mark columns -- see module docstring on why the Category column
    itself is deliberately excluded."""
    return max(0.0, group.correct_x - 30.0), group.category_x - 3.0


def _parse_group_rows(words: Sequence[_Word], header_y: float, group: _ColumnGroup) -> Dict[int, str]:
    """{question: your_answer} for one column-group, walking its rows top
    to bottom and stopping at the first row whose leftmost token isn't
    the next consecutive question number (see module docstring)."""
    left, right = _group_window(group)
    header_bottom = header_y + 10  # past the header's own "Category"/"Answer" sub-labels
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


def parse_scoresheet_pdf(path: str | Path, page_index: Optional[int] = None) -> Dict[QuestionKey, str]:
    """Parse a rendered ACT-style ScoreSheet PDF into {(normalized_section,
    question): your_answer}, the same shape scoresheet_check's
    parse_reference_scoresheet returns for a .xlsx ScoreSheet tab -- so
    both can feed the same compare()/write_comparison_report() pipeline.

    Searches every page for a page with at least one Correct/Your Answer
    column-group unless `page_index` is given. Raises ValueError if no
    such page is found, if more than one page has one and `page_index`
    wasn't given (ambiguous -- pass page_index to pick), or if a block's
    section titles can't be confidently identified.
    """
    doc = fitz.open(str(path))
    candidate_indices = range(len(doc)) if page_index is None else [page_index]
    pages_with_blocks = []
    for pi in candidate_indices:
        words = doc[pi].get_text("words")
        blocks = _find_header_blocks(words)
        if blocks:
            pages_with_blocks.append((pi, words, blocks))

    if not pages_with_blocks:
        raise ValueError(f"No ScoreSheet-shaped table (Correct/Your Answer column headers) found in {path}")
    if page_index is None and len(pages_with_blocks) > 1:
        found = [pi for pi, _, _ in pages_with_blocks]
        raise ValueError(
            f"Found ScoreSheet-shaped tables on multiple pages {found} in {path} -- pass page_index to pick one"
        )

    _, words, blocks = pages_with_blocks[0]
    result: Dict[QuestionKey, str] = {}
    for block in blocks:
        sections = _titles_for_block(words, block)
        for group, section in zip(block.groups, sections):
            for question, answer in _parse_group_rows(words, block.header_y, group).items():
                key = (normalize_section(section), question)
                if key in result and result[key] != answer:
                    raise ValueError(f"Conflicting entries for {key} in {path}: {result[key]!r} vs {answer!r}")
                result[key] = answer
    return result
