"""Auto-detect which bubble-sheet template a scanned sheet actually is, so
a drag-and-drop droplet doesn't need to be told in advance which format was
dropped -- important once more than one sheet format exists (see
templates/act_answer_sheet.yaml vs. templates/legacy_act_answer_sheet.yaml)
and a batch might mix them.

Detection deliberately does NOT try to read any printed text on the sheet
(a test code, a form name, ...) to identify it -- that path was explored
for a related feature and found unreliable in practice across real sheets
(some printed/OCR-able, one handwritten, one entirely absent). Instead it
reuses grid_detect's own per-section structural check: the same
glyph-contour detection evaluate_sheet already relies on to know whether a
section's *corrected* bubble positions can be trusted for reading ink (see
grid_detect's module docstring) doubles as a fingerprint of which physical
sheet this is, since it only succeeds when the actual printed row/column
layout at the position a template predicts really is there. A wrong
template's sections essentially never all agree by coincidence, so "every
section matched" is a strong, ink-independent signal -- and conveniently,
it's exactly the check evaluate_sheet needs to run anyway once a template
is chosen.

Only returns a template when exactly one candidate gets a full match --
or when every other full match is a shorter sibling form of it (same
layout, fewer rows), whose grid is necessarily present on the longer
form's sheet too. Zero or more than one otherwise is reported as
ambiguous rather than guessed:
silently picking the wrong template would mean every answer on the sheet
gets read from the wrong bubble positions, and this project's standing
rule -- a wrong-but-confident answer is worse than one flagged for a human
to resolve -- applies just as much to picking the template as it does to
reading one bubble.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from . import grid_detect
from .align import align_to_template
from .template import Template

DEFAULT_TEMPLATES_DIR = "templates"

# Ships as a fill-in-the-blanks starting point for calibrating a brand-new
# sheet (see its own header comment for the intended workflow) -- never a
# real, already-measured format, so never a candidate for auto-detection.
_STARTER_TEMPLATE_FILENAME = "default_template.yaml"


def discover_template_paths(templates_dir: str | Path = DEFAULT_TEMPLATES_DIR) -> List[Path]:
    """Every calibrated template file auto-detection should try: every
    templates/*.yaml except the generic starter template. A new template
    (e.g. templates/legacy_act_answer_sheet.yaml) is picked up
    automatically the moment it's added to that directory -- nothing else
    to register."""
    templates_dir = Path(templates_dir)
    return sorted(p for p in templates_dir.glob("*.yaml") if p.name != _STARTER_TEMPLATE_FILENAME)


@dataclasses.dataclass(frozen=True)
class TemplateMatch:
    """One candidate template's structural fit against a sheet image."""

    path: Path
    template: Template
    matched_sections: List[str]
    unmatched_sections: List[str]
    # BGR, already warped to this template's page size -- callers that
    # accept this match can feed it straight to evaluate_sheet instead of
    # aligning the original image a second time.
    aligned_image: np.ndarray
    used_contour: bool

    @property
    def is_full_match(self) -> bool:
        return not self.unmatched_sections


def score_template(image: np.ndarray, path: Path, template: Template) -> TemplateMatch:
    """Check `template`'s printed bubble-grid geometry -- not any pencil
    marks -- against `image`. See module docstring for why this is a
    reliable way to identify the sheet."""
    alignment = align_to_template(image, template.page_width, template.page_height)
    gray = cv2.cvtColor(alignment.image, cv2.COLOR_BGR2GRAY)
    matched: List[str] = []
    unmatched: List[str] = []
    for section in template.sections:
        detected = grid_detect.locate_section_bubbles(gray, template, section)
        (matched if detected is not None else unmatched).append(section.name)
    return TemplateMatch(
        path=path,
        template=template,
        matched_sections=matched,
        unmatched_sections=unmatched,
        aligned_image=alignment.image,
        used_contour=alignment.used_contour,
    )


@dataclasses.dataclass(frozen=True)
class DetectionResult:
    match: Optional[TemplateMatch]
    attempts: List[TemplateMatch]

    def describe_failure(self) -> str:
        """Human-readable explanation for why no template was confidently
        picked -- for callers to show the user instead of a bare
        "couldn't detect the template"."""
        if self.match is not None:
            return ""
        full_matches = [a for a in self.attempts if a.is_full_match]
        if len(full_matches) > 1:
            names = ", ".join(a.path.stem for a in full_matches)
            return f"matched more than one template ({names}) -- ambiguous"
        if not self.attempts:
            return "no templates available to try"
        parts = [
            f"{a.path.stem} (no match on {', '.join(a.unmatched_sections)})"
            for a in self.attempts
        ]
        return "didn't match any known template -- " + "; ".join(parts)


def detect_template(
    image: np.ndarray, templates_dir: str | Path = DEFAULT_TEMPLATES_DIR
) -> DetectionResult:
    """Try every known template against `image` and return the one whose
    structure fully matched -- only when exactly one candidate does (see
    module docstring)."""
    attempts: List[TemplateMatch] = []
    for path in discover_template_paths(templates_dir):
        template = Template.from_yaml(path)
        template.validate()
        attempts.append(score_template(image, path, template))
    full_matches = [a for a in attempts if a.is_full_match]
    match = full_matches[0] if len(full_matches) == 1 else _longest_of_siblings(full_matches)
    return DetectionResult(match=match, attempts=attempts)


def _longest_of_siblings(full_matches: List[TemplateMatch]) -> Optional[TemplateMatch]:
    """The match every other one is a shorter sibling of (see
    _is_shorter_sibling) -- a shorter form's grid is fully present on a
    longer form's sheet too, but never the reverse, so that's the sheet."""
    for candidate in full_matches:
        others = [a for a in full_matches if a is not candidate]
        if others and all(_is_shorter_sibling(o.template, candidate.template) for o in others):
            return candidate
    return None


def _is_shorter_sibling(short: Template, long: Template) -> bool:
    """`short` is `long`'s layout with fewer rows: same sections, every
    column at one of `long`'s columns with the same row height, starting
    within a row of it and no longer."""
    long_sections = {s.name: s for s in long.sections}
    if {s.name for s in short.sections} != set(long_sections):
        return False
    for section in short.sections:
        for col in section.columns:
            twin = next((c for c in long_sections[section.name].columns if abs(c.x_start - col.x_start) <= 2), None)
            if (
                twin is None
                or twin.row_height != col.row_height
                or abs(twin.y_start - col.y_start) > col.row_height
                or col.last_question - col.first_question > twin.last_question - twin.first_question
            ):
                return False
    return sum(s.num_questions for s in short.sections) < sum(s.num_questions for s in long.sections)
