"""Resolve, per question, which of a `dynamic_choices` section's two
declared choice groups (the template's `choices` -- even/odd -- block) is
actually printed at that row, for a sheet family confirmed not to reliably
alternate between them by simple odd/even question parity.

Real evidence this exists for: two blank copies of the same ACT "Page 2"
answer-sheet form (identical geometry, identical booklet form code) were
provided for calibrating a new template. Both alternate A/B/C/D and
F/G/H/J normally almost everywhere -- but on one copy, Mathematics
questions 14 and 15 are BOTH printed A/B/C/D (no flip to F/G/H/J between
them); on the *other* copy, that same section's questions 16 and 17 are
the ones both printed A/B/C/D instead, with 14/15 alternating normally.
Since the row where alternation breaks moves between two nominally
identical printed forms, no static per-question choice list -- however
carefully hand-transcribed from one real example -- can be correct for
both; the actual choices have to be read off each scan itself. (Also
confirmed: Reading breaks the same way, at a different row again; English
and Science happened not to break on either sample, but nothing here
assumes they never could on some other sheet.)

The geometry itself (bubble pixel positions) is completely unaffected by
which group is printed at a row -- both groups are always the same
length, so a slot's (x, y) is identical either way (see
Template.bubbles/choices_for). That means grid_detect.locate_section_bubbles
already finds the right *positions* for a `dynamic_choices` section using
nothing more than the template's ordinary static choices (whichever
parity guess that happens to be) -- only the choice *label* attached to
each position can be wrong. Resolution therefore runs *after* that
detection, relabeling its result, rather than needing its own separate
position-finding pass.

How a row's real group is decided: never by recognizing an absolute
letter shape (no "this glyph is an A" classifier, and deliberately no
bundled reference glyph images extracted from either provided sheet --
these are a real, copyrighted ACT publication, and the geometry
measurements this project's templates already encode are one thing, but
shipping crops of the actual printed artwork is another). Instead, each
question's own glyph is compared directly against the nearest earlier
question in the same section whose group is already confidently known,
using plain image similarity (cv2.matchTemplate) on that live scan --
two instances of the same printed letter, from the same page, same scan,
same print run, correlate far higher than two different letters do (see
_SIMILARITY_SAME_MIN's own comment for the real numbers this was
calibrated against). A high score means "still the same group as the
anchor" (an alternation break); a low score means "flipped" (ordinary
alternation); starting from question 1, which this project's templates
already always declare as the "odd" list by convention. Comparing
against whichever bubble slot(s) are actually unmarked on *both* rows
(reusing the normal case that at most one of a question's choices is
ever filled in) means this only ever needs pixels already on the page,
never a mark-free reference sheet.

A row that can't be confidently compared at all (e.g. every slot marked
enough on one side to obscure its glyph) doesn't break the chain: it just
carries the last confidently-resolved anchor forward unchanged --
guessing a normal flip from that anchor, same as the ordinary case, but
flagged low_confidence for a human to double check, per this project's
standing rule that a wrong-but-confident answer is worse than one flagged
for review.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Set, Tuple

import cv2
import numpy as np

from .template import Section, Template

# Bubble crop half-size (px) glyph comparisons are cropped to, and the extra
# search slack around it -- see _glyph_similarity. Both scale with the
# template's own bubble_radius rather than a fixed pixel count, so this
# isn't tied to one specific template's own resolution.
_CROP_HALF_RATIO = 1.0
_SEARCH_SLACK_RATIO = 0.45

# Calibrated against real scans (two blank copies of the same ACT sheet
# used to build the first `dynamic_choices` template -- see this module's
# own docstring): comparing every matched pair of *different*-group glyphs
# (A/B/C/D against F/G/H/J, the only two groups that sheet has) at every
# available slot scored between 0.52 and 0.63; every matched pair of
# *same*-group glyphs (two rows both showing A/B/C/D) scored between 0.94
# and 0.95. The gap between those clusters is wide -- these two thresholds
# sit well inside it on both sides, rather than splitting the difference
# down the middle, so a score that lands between them (unexpected on every
# sample seen so far) is treated as inconclusive rather than forced to a
# side it didn't clearly earn.
_SIMILARITY_SAME_MIN = 0.85
_SIMILARITY_DIFF_MAX = 0.70

# A bubble whose sampled interior is at least this dark a fraction is
# treated as marked -- its own glyph (if any) is obscured by ink, so it's
# skipped as a comparison source rather than trusted. Deliberately looser
# than detect.py's own fill_ratio_min (this only needs to rule out "too
# inked to read a glyph off of", not decide whether a mark counts as a
# genuine answer -- getting this a little too eager just costs a
# comparison source, not correctness, as long as at least one of a
# question's slots is usually left unmarked, the normal case).
_LIKELY_FILLED_DARK_FRACTION = 0.45
_DARK_PIXEL_VALUE = 60  # out of 255, same "genuinely dark" floor detect.py's own _dark_fraction uses


def _value_channel(gray: np.ndarray) -> np.ndarray:
    return gray if gray.ndim == 2 else np.max(gray, axis=2).astype(np.uint8)


def _is_likely_filled(value: np.ndarray, x: int, y: int, radius: int) -> bool:
    """Coarse "is there real pencil/pen ink here, not just the printed ring
    and letter" check -- see _LIKELY_FILLED_DARK_FRACTION for why this can
    afford to be looser than detect.py's own fill-ratio floor."""
    r = max(1, round(radius * 0.85))
    y0, y1 = max(0, y - r), min(value.shape[0], y + r + 1)
    x0, x1 = max(0, x - r), min(value.shape[1], x + r + 1)
    patch = value[y0:y1, x0:x1]
    if patch.size == 0:
        return True  # off the edge of the image -- nothing readable here either way
    dark_fraction = float(np.mean(patch < _DARK_PIXEL_VALUE))
    return dark_fraction >= _LIKELY_FILLED_DARK_FRACTION


def _crop(value: np.ndarray, x: int, y: int, half: int) -> np.ndarray:
    y0, y1 = max(0, y - half), min(value.shape[0], y + half)
    x0, x1 = max(0, x - half), min(value.shape[1], x + half)
    return value[y0:y1, x0:x1]


def _glyph_similarity(value: np.ndarray, ax: int, ay: int, bx: int, by: int, radius: int) -> float:
    """Best-alignment normalized cross-correlation between the glyph at
    (ax, ay) and the glyph at (bx, by) -- a small search slack around the
    second crop (rather than comparing at one fixed offset) absorbs a
    couple of px of row/column measurement noise that would otherwise
    understate how similar two genuinely-identical glyphs are; confirmed
    against real scans this is what separates an apparent ~0.79 same-glyph
    score (indistinguishable from a different-glyph one without it) from
    its real ~0.95."""
    crop_half = max(4, round(radius * _CROP_HALF_RATIO))
    search = max(2, round(radius * _SEARCH_SLACK_RATIO))
    template_patch = _crop(value, ax, ay, crop_half).astype(np.float32)
    search_patch = _crop(value, bx, by, crop_half + search).astype(np.float32)
    if template_patch.size == 0 or search_patch.size == 0:
        return 0.0
    if search_patch.shape[0] < template_patch.shape[0] or search_patch.shape[1] < template_patch.shape[1]:
        return 0.0
    result = cv2.matchTemplate(search_patch, template_patch, cv2.TM_CCOEFF_NORMED)
    return float(result.max())


def _other_group(section: Section, template: Template, group: Sequence[str]) -> List[str]:
    even = section.even_choices if section.even_choices is not None else template.even_choices
    odd = section.odd_choices if section.odd_choices is not None else template.odd_choices
    return list(odd) if group is even or list(group) == list(even) else list(even)


def resolve_section_choices(
    gray: np.ndarray,
    template: Template,
    section: Section,
    detected: Dict[int, List[tuple]],
) -> Tuple[Dict[int, List[tuple]], Set[int]]:
    """Relabel `detected` (grid_detect.locate_section_bubbles's own result
    for this section -- real positions, but choice labels only ever a
    parity guess) with each question's actual choice group, read off
    `gray` per this module's own docstring. Returns (relabeled, questions)
    -- `questions` is every question number whose own group couldn't be
    confidently confirmed and was instead carried forward from its last
    confidently-known anchor; callers should flag these low_confidence.

    Only meaningful for a `section.dynamic_choices` section -- callers
    that already check that flag before calling this don't need to check
    it again here, but nothing below assumes it either."""
    value = _value_channel(gray)
    radius = template.bubble_radius
    even = section.even_choices if section.even_choices is not None else template.even_choices
    odd = section.odd_choices if section.odd_choices is not None else template.odd_choices

    def relabel(bubbles: List[tuple], group: Sequence[str]) -> List[tuple]:
        return [(choice, x, y) for choice, (_, x, y) in zip(group, bubbles)]

    relabeled: Dict[int, List[tuple]] = {1: relabel(detected[1], odd)}
    low_confidence: Set[int] = set()
    anchor_question = 1
    anchor_group: List[str] = list(odd)

    for question in range(2, section.num_questions + 1):
        bubbles = detected[question]
        anchor_bubbles = detected[anchor_question]
        votes_same = votes_diff = 0
        for (_, ax, ay), (_, bx, by) in zip(anchor_bubbles, bubbles):
            if _is_likely_filled(value, ax, ay, radius) or _is_likely_filled(value, bx, by, radius):
                continue
            similarity = _glyph_similarity(value, ax, ay, bx, by, radius)
            if similarity >= _SIMILARITY_SAME_MIN:
                votes_same += 1
            elif similarity <= _SIMILARITY_DIFF_MAX:
                votes_diff += 1
            # else: neither threshold cleared -- not cast as a vote either way.

        confident = votes_same != votes_diff and (votes_same > 0 or votes_diff > 0)
        if confident and votes_same > votes_diff:
            group = anchor_group
        else:
            # Either a confident flip, or nothing readable to compare at
            # all -- both take the same "assume ordinary alternation from
            # the last known anchor" action; only the flag differs.
            group = _other_group(section, template, anchor_group)
            if not confident:
                low_confidence.add(question)

        relabeled[question] = relabel(bubbles, group)
        if confident:
            anchor_question, anchor_group = question, group
        # else: keep the previous anchor -- this question's own reading
        # was never confirmed, so the *next* question should still compare
        # against the last row that really was.

    return relabeled, low_confidence
