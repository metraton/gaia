"""
verification-rubric / rubric_verdict -- pure aggregation half of the
semantic/self_review judging cycle (see SKILL.md, "Reference implementation").

This module is DB-free, LLM-free, and I/O-free -- it mirrors the style of
``gaia.state.gate_validation.validate_gate``: given data already in memory, it
returns a deterministic verdict. It does NOT judge a criterion against the
produced work; that step (deciding whether a criterion is "met" by reading the
evidence) is the LLM-as-judge call the loading agent makes. This module only
does the two mechanical halves of the cycle around that call:

  1. ``parse_rubric_criteria`` -- split a rubric's raw prose (the gate's
     ``evidence_shape``) into discrete, independently-gradable criteria lines.
  2. ``assemble_verdict`` -- once each criterion has been assessed (met or not,
     with reasoning), aggregate those assessments into one justified pass/fail
     verdict.

Structurally enforces the honesty rule from ``agent-protocol``: an assessment
with no stated reasoning, or an empty assessment list, is rejected outright --
a verdict may never be assembled from an unjustified or absent judgment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A criterion line is a bullet ("-", "*") or a numbered item ("1.", "2)") --
# the common rubric-authoring conventions. Anything else is treated as one
# undivided criterion (a rubric authored as a single paragraph).
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)\s*$")


def parse_rubric_criteria(evidence_shape: str) -> list[str]:
    """Split rubric prose into discrete, checkable criteria.

    Lines matching a bullet or numbered-list marker become one criterion each
    (marker stripped). If no line matches, the whole (stripped) text is
    returned as a single criterion. Blank lines are dropped. Raises
    ``ValueError`` on empty/whitespace-only input -- there is nothing to
    parse.
    """
    if not isinstance(evidence_shape, str) or not evidence_shape.strip():
        raise ValueError("evidence_shape must be a non-empty rubric string")

    criteria: list[str] = []
    for line in evidence_shape.splitlines():
        if not line.strip():
            continue
        m = _BULLET_RE.match(line)
        criteria.append(m.group(1) if m else line.strip())

    if not criteria:
        return [evidence_shape.strip()]
    return criteria


@dataclass(frozen=True)
class CriterionAssessment:
    """One judged criterion: the claim, whether it was met, and why.

    ``reasoning`` is mandatory content, not decoration -- an assessment
    without a stated observation is an assertion, not a judgment (see
    SKILL.md, "Hollow verdict").
    """

    criterion: str
    met: bool
    reasoning: str


@dataclass(frozen=True)
class RubricVerdict:
    """The justified, structured verdict ``assemble_verdict`` returns.

    ``verdict`` is ``"pass"`` only when every assessment's ``met`` is True;
    otherwise ``"fail"``. ``overall_reasoning`` names every unmet criterion so
    the verdict is defensible without re-reading each assessment.
    """

    verdict: str
    criteria: list[CriterionAssessment] = field(default_factory=list)
    overall_reasoning: str = ""


def assemble_verdict(assessments: list[CriterionAssessment]) -> RubricVerdict:
    """Aggregate judged criteria into one justified pass/fail verdict.

    Rejects (``ValueError``):
      * an empty ``assessments`` list -- a verdict needs at least one judged
        criterion;
      * any assessment whose ``reasoning`` is blank -- the honesty rule
        enforced structurally: no assessment may carry a verdict with nothing
        stated to justify it.
    """
    if not assessments:
        raise ValueError(
            "assemble_verdict requires at least one CriterionAssessment"
        )
    for a in assessments:
        if not a.reasoning or not a.reasoning.strip():
            raise ValueError(
                f"criterion {a.criterion!r} has no reasoning -- a hollow "
                "assessment cannot be assembled into a verdict"
            )

    unmet = [a for a in assessments if not a.met]
    if unmet:
        verdict = "fail"
        overall = "unmet: " + "; ".join(
            f"{a.criterion!r} ({a.reasoning})" for a in unmet
        )
    else:
        verdict = "pass"
        overall = "all criteria met: " + "; ".join(
            f"{a.criterion!r} ({a.reasoning})" for a in assessments
        )

    return RubricVerdict(
        verdict=verdict,
        criteria=list(assessments),
        overall_reasoning=overall,
    )
