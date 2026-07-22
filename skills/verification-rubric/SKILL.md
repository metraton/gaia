---
name: verification-rubric
description: Use when judging a task_gates entry whose verification_type is semantic or self_review -- reading the gate's evidence_shape as an explicit rubric, assessing the produced work against each stated criterion, and emitting a justified pass/fail verdict. Loaded by the verifier role for judgment-based gates.
---

# Verification Rubric

Verification-rubric is the judging discipline for a `task_gates` entry whose
`verification_type` is `semantic` or `self_review`: it reads the gate's
`evidence_shape` as an explicit rubric, assesses the produced work against
each stated criterion independently, and emits a structured, justified
pass/fail verdict -- never a hollow "looks fine." It is the judgment-based
counterpart to the deterministic-oracle mode, which re-runs a `command`/`code`
gate and diffs the result; there is no command to re-run here, only evidence
to weigh against criteria.

## Core principle

Two of the four `verification_type` values (`gaia.state.VALID_VERIFICATION_TYPES`,
`gaia/state/__init__.py`) route to this skill: **`semantic`** (`evidence_shape`
carries an externally authored rubric -- the contract-envelope's own
`requires_human` marker names the same discipline: "needs human/rubric
validation") and **`self_review`** (`evidence_shape` is the producer's own
statement of what it checked -- the envelope's `reviewed` field is the same
idea, and it is the honesty-rule floor from `agent-protocol`, not a shortcut
around it). Both are LLM-as-judge tasks, not code execution.

Three forces shape the judgment:

- **Explicit criteria beat holistic impression.** Read the rubric as a list of
  discrete, independently-checkable claims, not one paragraph to eyeball. A
  verdict built on an overall feeling collapses the moment someone asks "which
  part, exactly?"
- **Judge each criterion on its own -- resist position and verbosity bias.**
  Do not let a strong criterion compensate for a failing one (verdict
  inflation), do not let whichever criterion you read first anchor the whole
  read (position bias), and do not treat a longer or more polished artifact as
  automatically meeting more criteria (verbosity bias) -- effort and length
  are not what the rubric asks for.
- **Confirmed beats assumed.** A criterion is "met" only when the produced
  work was actually observed to satisfy it. An unobserved criterion is not
  met -- it is unknown, and unknown is not pass (mirrors `investigation`'s
  confirmed-vs-assumed line).

## The judging cycle

1. **Load the gate.** Read the `task_gates` row: `verification_type`
   (`semantic`|`self_review`), `evidence_shape` (the rubric prose or
   self-review statement), `artifact_path` (the produced work, if any). Run
   `gaia.state.gate_validation.validate_gate` first -- a structurally invalid
   gate (missing/empty `evidence_shape`) has nothing to judge; return that
   rejection, not a verdict.
2. **Parse the rubric into criteria.** Split `evidence_shape` into discrete,
   checkable statements -- one criterion, one claim. A rubric with a single
   paragraph is read as one criterion; a well-authored rubric names several,
   and each is graded independently.
3. **Gather the evidence.** Read the produced work at `artifact_path` (or
   wherever the task's outcome lives). Judge from what actually exists, never
   from the task's own description of itself.
4. **Assess each criterion independently.** For each: is it met, and why --
   the observation that grounds the call. An assessment with no stated reason
   is an assertion, not a judgment.
5. **Aggregate to the overall verdict.** Pass requires every criterion met,
   unless the rubric itself marks one optional/advisory (name that
   explicitly if so). One unmet, unwaived criterion is fail, regardless of how
   many others passed.
6. **Emit the structured, justified verdict** -- `{gate_id, verdict, criteria,
   overall_reasoning}` (see Reference implementation below). Justification is
   not optional: the rubric exists so the verdict can be defended
   criterion-by-criterion, not merely announced.

## `self_review` vs `semantic`

- **`self_review`** -- `evidence_shape` is the producer's own statement of
  what it checked, not a rubric someone else authored. Judge it by the same
  discipline: does the statement name concrete checks and observations, or is
  it a hollow "looks good"? A statement that names nothing checked fails the
  honesty-rule floor regardless of how confident it reads.
- **`semantic`** -- `evidence_shape` is an externally authored, typically
  multi-criterion rubric -- the case this skill's cycle is built around.

## Reference implementation

`scripts/rubric_verdict.py` provides the deterministic assembly half of the
cycle: `parse_rubric_criteria()` splits rubric prose into discrete criteria
(step 2), and `assemble_verdict()` aggregates a list of `CriterionAssessment`
(`criterion`, `met`, `reasoning`) into a `RubricVerdict` (`verdict`,
`criteria`, `overall_reasoning`), rejecting an empty assessment list and any
assessment with blank `reasoning` -- the honesty rule enforced structurally,
mirroring `gaia.state.gate_validation.validate_gate`'s pure/deterministic
style. The per-criterion `met` call -- reading the evidence against the
criterion's wording -- is the judge's job (step 4); this module only
assembles what has already been judged, it does not automate the judgment.

## Anti-patterns

- **Holistic pass** -- grading the whole artifact by one overall impression
  instead of per-criterion. A strong majority does not offset one unmet
  criterion.
- **Hollow verdict** -- `pass`/`fail` with no stated reasoning. The rubric
  exists so the verdict can be defended, not merely declared.
- **Verbosity bias** -- crediting a longer or more detailed artifact as
  automatically meeting more criteria. Judge against the criterion's wording,
  not against effort.
- **Position bias** -- anchoring the whole judgment on whichever criterion was
  read first instead of giving each an independent look.
- **Treating `self_review`'s floor as license to skip observation** --
  `self_review` still requires naming what was checked; "I reviewed it, it's
  fine" is a shrug, not a review.
