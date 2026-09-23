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
for review. Before flagging, each such row gets a second look against
every confirmed row in the section at once (see _group_by_references):
comparing against a single anchor can tie on a real sheet even when every
letter is clearly printed, since B against G scores just high enough
(0.85) to pass for the same letter.

`_is_likely_filled` (deciding which slots are safe to compare in the
first place) originally used a raw dark-pixel-fraction floor, the same
kind of signal score_bubbles' own fill_ratio uses. A real completed sheet
(the first one run through this template after it shipped -- see
_LIKELY_FILLED_SOLIDITY_MIN below) showed that signal misses most genuine
marks on this template: 98 of 133 real marked bubbles measured *below*
that floor, because a pencil fill's own dark-pixel coverage rarely
reaches all the way to this sheet's sampling radius the way a synthetic
full-circle fill does. Missing a mark here doesn't just under-count it
like a missed mark does for score_bubbles -- it leaves that mark's own
ink sitting in the comparison as if the slot were blank, corrupting the
glyph match at exactly that slot. Confirmed on that same real sheet:
Mathematics questions 14 and 15 are a genuine duplicate (both A/B/C/D),
but 14's own marked slot and 15's own marked slot each measured below the
old floor and so were compared as if unmarked -- two of the four
available slots voted "different" purely from the mark's ink distorting
the glyph shape, tying the vote 2-2 and guessing (flagged low_confidence,
but wrongly) a flip to F/G/H/J instead. Switched to the same
erosion-survival "solidity" signal detect.py's own _solidity/
_SOLID_FILL_MIN already use for the analogous problem there (a duplicated
implementation, not a shared import -- detect.py imports this module, so
the reverse would be circular): erosion collapses a printed ring or
letter stroke almost completely but leaves most of a genuine fill's area
intact regardless of how much of the sampling circle that fill actually
covers, which is exactly the distinction dark-pixel-fraction alone can't
make. Re-measured against that same real sheet: only 1 of 133 genuine
marks now falls below the new floor (a pattern-inferred answer with
essentially no ink of its own to measure -- expected, not a miss), and
the Mathematics 14/15 duplicate resolves correctly and confidently.
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

# See _group_by_references. On a real J-series sheet, every one of 412
# unmarked letters matched its own group's letters better than the other
# group's, by 0.073 at the least (G against B, the closest look-alikes).
_REFERENCE_VOTE_MARGIN = 0.05

# A bubble whose ink survives erosion by this many px, over at least this
# fraction of its own (already-binarized) dark area, is treated as marked
# -- its own glyph (if any) is obscured by ink, so it's skipped as a
# comparison source rather than trusted. See this module's own docstring
# for why this is an erosion-survival "solidity" check (mirroring
# detect.py's _solidity/_SOLID_FILL_MIN) rather than a plain dark-pixel
# fraction: a printed ring outline or letter stroke is only a few px
# thick and collapses under erosion almost entirely, while a genuine
# pencil fill stays mostly intact regardless of how much of the sampling
# circle it happens to cover. The floor itself sits well below
# detect.py's own _SOLID_FILL_MIN (0.32): re-measured against a real
# completed sheet, genuine marks' own solidity clustered tightly at
# exactly 0.315 (just under that stricter floor, which is tuned for a
# different, stricter question -- "confident enough to promote to a final
# answer" -- not this one), while unmarked slots clustered at 0.0 with a
# small tail up to the same 0.315. 0.15 sits in the wide, flat gap
# in between (0.10 through 0.20 all separated the same real sample
# identically), catching every genuine mark bar one (a pattern-inferred
# answer with no ink of its own to measure) at the cost of a modest
# number of genuinely-unmarked slots losing their turn as a comparison
# source -- deliberately the safer side to err on, since that only costs
# a comparison source, not correctness (see this module's own docstring).
_LIKELY_FILLED_SOLIDITY_MIN = 0.15
_SOLIDITY_ERODE_PX = 3


def _value_channel(gray: np.ndarray) -> np.ndarray:
    return gray if gray.ndim == 2 else np.max(gray, axis=2).astype(np.uint8)


def _is_likely_filled(binary: np.ndarray, x: int, y: int, radius: int) -> bool:
    """Is there real pencil/pen ink here, not just the printed ring and
    letter -- see _LIKELY_FILLED_SOLIDITY_MIN for why this is an
    erosion-survival check on the already-binarized image, not a plain
    dark-pixel fraction on the grayscale one."""
    r = max(1, round(radius * 0.85))
    y0, y1 = max(0, y - r), min(binary.shape[0], y + r + 1)
    x0, x1 = max(0, x - r), min(binary.shape[1], x + r + 1)
    patch = binary[y0:y1, x0:x1]
    if patch.size == 0:
        return True  # off the edge of the image -- nothing readable here either way
    mask = np.zeros_like(patch, dtype=np.uint8)
    cv2.circle(mask, (x - x0, y - y0), r, 255, -1)
    masked = cv2.bitwise_and(patch, mask)
    total = cv2.countNonZero(masked)
    if total == 0:
        return False
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * _SOLIDITY_ERODE_PX + 1, 2 * _SOLIDITY_ERODE_PX + 1))
    eroded = cv2.erode(masked, kernel)
    solidity = float(cv2.countNonZero(eroded) / total)
    return solidity >= _LIKELY_FILLED_SOLIDITY_MIN


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
    binary: np.ndarray,
    template: Template,
    section: Section,
    detected: Dict[int, List[tuple]],
) -> Tuple[Dict[int, List[tuple]], Set[int]]:
    """Relabel `detected` (grid_detect.locate_section_bubbles's own result
    for this section -- real positions, but choice labels only ever a
    parity guess) with each question's actual choice group, read off
    `gray` (glyph shape comparisons) and `binary` (is-this-slot-marked
    checks -- see _is_likely_filled) per this module's own docstring.
    Returns (relabeled, questions) -- `questions` is every question number
    whose own group couldn't be confidently confirmed and was instead
    carried forward from its last confidently-known anchor; callers
    should flag these low_confidence.

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
            if _is_likely_filled(binary, ax, ay, radius) or _is_likely_filled(binary, bx, by, radius):
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

    confirmed = [(detected[q], tuple(c for c, _, _ in relabeled[q])) for q in relabeled if q not in low_confidence]
    for question in sorted(low_confidence):
        group = _group_by_references(value, binary, radius, detected[question], confirmed, (tuple(odd), tuple(even)))
        if group is not None:
            relabeled[question] = relabel(detected[question], group)
            low_confidence.discard(question)

    return relabeled, low_confidence


def _group_by_references(
    value: np.ndarray,
    binary: np.ndarray,
    radius: int,
    bubbles: List[tuple],
    confirmed: List[Tuple[List[tuple], Tuple[str, ...]]],
    groups: Tuple[Tuple[str, ...], Tuple[str, ...]],
) -> "Tuple[str, ...] | None":
    """Settle a row the anchor chain couldn't: each unmarked letter votes
    for whichever group's letters in the same slot, across every confirmed
    row, it matches better (median similarity, by _REFERENCE_VOTE_MARGIN at
    least). Only a unanimous vote counts."""
    votes = set()
    for slot, (_, tx, ty) in enumerate(bubbles):
        if _is_likely_filled(binary, tx, ty, radius):
            continue
        score = {}
        for group in groups:
            refs = [
                row[slot] for row, row_group in confirmed
                if row_group == group and not _is_likely_filled(binary, row[slot][1], row[slot][2], radius)
            ]
            if refs:
                score[group] = float(np.median([_glyph_similarity(value, rx, ry, tx, ty, radius) for _, rx, ry in refs]))
        if len(score) < 2:
            continue
        first, second = groups
        if score[first] - score[second] >= _REFERENCE_VOTE_MARGIN:
            votes.add(first)
        elif score[second] - score[first] >= _REFERENCE_VOTE_MARGIN:
            votes.add(second)
    return next(iter(votes)) if len(votes) == 1 else None
