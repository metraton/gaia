"""
Declarative artifact -> governing-skill map.

An agent can write a file whose class of artifact is governed by a skill it
never declared in frontmatter and never loaded. This module says WHICH skill
governs a given file, so skill_injection_verifier can check for it whether or
not the frontmatter names it.

It is separate from skill_injection_verifier.py because classifying an
artifact by its path does not depend on transcript content.

The map is a plain list of ``ArtifactSkillRule`` entries: a new artifact class
is one more entry, with no change to ``expected_skill_for_path`` or any
caller. The one rule today maps source-code extensions to ``code-standards``.
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


ARTIFACT_SKILL_RULES: List[ArtifactSkillRule] = [
    ArtifactSkillRule(
        skill="code-standards",
        extensions=frozenset({
            ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        }),
        reason=(
            "These extensions are source code meant to be read long after "
            "the session that produced it; code-standards is the skill "
            "that governs how it is named, structured, and commented for "
            "exactly that class of file."
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
