"""
Declarative artifact -> governing-skill map.

An agent can write a file whose class of artifact is governed by a skill it
never declared in frontmatter and never loaded -- the failure
skill_injection_verifier exists to catch, but only if something first says
WHICH skill a given file *should* have triggered. That "which skill governs
this artifact" knowledge did not exist anywhere in the codebase before this
module: skill_injection_verifier only ever checked declared skills against
what the transcript shows, so an undeclared gap was invisible by
construction.

This lives as its OWN module, separate from skill_injection_verifier.py,
because the two are different concerns that happen to feed the same check:
skill_injection_verifier answers "was this skill's fingerprint present in the
transcript?", a question about transcript content. This module answers "which
skill SHOULD have been present, given what the agent wrote?", a question
about artifact classification that has nothing to do with transcripts and
may be reused by other consumers (compliance scoring, an audit report) that
never touch transcript text at all.

The map is a plain list of rules so that adding a future artifact class --
Terraform files, Kubernetes manifests, shell scripts -- is adding one
``ArtifactSkillRule`` entry, never touching ``expected_skill_for_path`` or
any caller. Today's rule set covers exactly one class per the initial scope:
source-code files, governed by ``coding-standards``.
"""

from pathlib import PurePosixPath
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Sequence


class ArtifactSkillRule(NamedTuple):
    """One artifact class: the skill that governs it, and how to recognize it.

    ``extensions`` are lowercase, dot-prefixed suffixes (``.py``, not
    ``py``), matched against a path's own suffix -- case-insensitively, since
    a file system may hand back either case.
    """

    skill: str
    extensions: FrozenSet[str]
    reason: str


# Add a future artifact class by appending one rule here -- no other code
# in this module (or its callers) needs to change.
ARTIFACT_SKILL_RULES: List[ArtifactSkillRule] = [
    ArtifactSkillRule(
        skill="coding-standards",
        extensions=frozenset({
            ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        }),
        reason=(
            "These extensions are source code meant to be read long after "
            "the session that produced it; coding-standards is the skill "
            "that governs the house doc-header, inline-comment, and "
            "no-tooling-trace conventions for exactly that class of file."
        ),
    ),
]


def expected_skill_for_path(file_path: str) -> Optional[str]:
    """Return the skill that governs ``file_path``, or None if unrecognized.

    Looks up the path's suffix against every registered rule's extension
    set and returns the first match. A path whose extension matches no rule
    returns None -- this is a recognition list, not a default-deny; an
    unrecognized extension means "no expectation yet," not "violation."
    """
    if not file_path:
        return None
    suffix = PurePosixPath(file_path).suffix.lower()
    if not suffix:
        return None
    for rule in ARTIFACT_SKILL_RULES:
        if suffix in rule.extensions:
            return rule.skill
    return None


def expected_skills_for_paths(file_paths: Sequence[str]) -> Dict[str, str]:
    """Map each recognized path in ``file_paths`` to its governing skill.

    Paths whose extension matches no rule are omitted entirely rather than
    mapped to None, so callers can iterate the result without filtering.
    """
    result: Dict[str, str] = {}
    for path in file_paths:
        skill = expected_skill_for_path(path)
        if skill:
            result[path] = skill
    return result
