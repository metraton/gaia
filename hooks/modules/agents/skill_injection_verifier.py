"""
Skill injection verifier -- transcript fingerprint checking.

At SubagentStop, verifies that skills were actually injected into the
agent's context by searching for unique fingerprint strings from each
SKILL.md. The set of skills checked is the UNION of two independent
sources, so a gap is caught regardless of which one names it:

    - declared_skills: what the agent's own frontmatter lists.
    - written_paths: what artifact_skill_map.py says SHOULD have been
      loaded, given the files the agent actually wrote or edited --
      derived from the artifact, never from the frontmatter. This is
      what lets the check catch an agent that writes a class of file
      (e.g. a Python hook module) without its governing skill
      (coding-standards) ever appearing in the transcript, even when
      that agent's frontmatter never declared the skill in the first
      place -- a gap the frontmatter-only check cannot see by
      construction.

Returns an optional anomaly dict (advisory) when an expected skill is
missing from the transcript, indicating a potential injection gap.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from .artifact_skill_map import expected_skills_for_paths

logger = logging.getLogger(__name__)


# Fingerprint strings per skill: unique phrases from SKILL.md that confirm injection.
# Each skill maps to a list of candidate fingerprints -- at least one must appear
# in the transcript for the skill to be considered present.
SKILL_FINGERPRINTS: Dict[str, List[str]] = {
    "agent-protocol": [
        "agent_contract_handoff",
        "agent_state",
        "evidence_report",
    ],
    "security-tiers": [
        "T0_READ_ONLY",
        "T3_BLOCKED",
        "Classification heuristic",
        "Enforcement anchors",
    ],
    "investigation": [
        "Context is the map",
        "Scope decides what matters",
        "Confirmed beats assumed",
    ],
    "command-execution": [
        "ONE COMMAND. ONE RESULT. ONE EXIT CODE",
        "No indirect-execution wrappers",
        "cloud_pipe_validator",
    ],
    "fast-queries": [
        "fast-queries",
        "triage",
    ],
    "coding-standards": [
        "why-not-what test",
        "Redundancy is the threshold, not a line count",
        "No tooling, AI, or plan-system traces",
    ],
}


def verify_skill_injection(
    agent_type: str,
    transcript_text: str,
    declared_skills: Optional[List[str]],
    written_paths: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Verify that expected skills were injected into the agent transcript.

    Searches the transcript for fingerprint strings that confirm each
    expected skill was loaded, where "expected" is the union of
    ``declared_skills`` and whatever ``written_paths`` maps to via
    ``artifact_skill_map.expected_skills_for_paths`` -- so a skill an
    artifact required is checked even when the frontmatter never declared
    it. Returns an anomaly dict if any expected skill has no fingerprint
    match in the transcript.

    Args:
        agent_type: The agent type string (e.g. "cloud-troubleshooter").
        transcript_text: The transcript text to search -- callers should
            pass the FULL transcript (all roles), since a skill's
            fingerprint is typically injected earlier in the turn (a
            tool-result/user-role entry), not necessarily in the agent's
            own last message.
        declared_skills: List of skill names from agent frontmatter.
        written_paths: Paths the agent wrote or edited during the turn.
            Optional and independent of declared_skills -- a path here
            can add an expected skill even with no frontmatter match.

    Returns:
        An anomaly dict (type: skill_injection_gap, severity: advisory) if
        any expected skill is missing from the transcript. None if all
        expected skills are present or if the check does not apply.
    """
    declared_skills = declared_skills or []
    artifact_skills = expected_skills_for_paths(written_paths or [])

    if not transcript_text or not (declared_skills or artifact_skills):
        return None

    # expected_skills is the union, declared first so its order is stable
    # for callers/tests that only ever pass declared_skills; artifact-only
    # additions are appended in the order their path was seen.
    expected_skills: List[str] = list(declared_skills)
    artifact_triggers: Dict[str, List[str]] = {}
    for path, skill_name in artifact_skills.items():
        artifact_triggers.setdefault(skill_name, []).append(path)
        if skill_name not in expected_skills:
            expected_skills.append(skill_name)

    missing_skills: List[str] = []

    for skill_name in expected_skills:
        fingerprints = SKILL_FINGERPRINTS.get(skill_name)
        if fingerprints is None:
            # No fingerprints defined for this skill -- skip (cannot verify)
            logger.debug(
                "No fingerprints defined for skill '%s', skipping verification",
                skill_name,
            )
            continue

        # At least one fingerprint must appear in the transcript
        found = any(fp in transcript_text for fp in fingerprints)
        if not found:
            missing_skills.append(skill_name)

    if not missing_skills:
        return None

    triggering_artifacts = {
        skill_name: paths
        for skill_name, paths in artifact_triggers.items()
        if skill_name in missing_skills
    }

    return {
        "type": "skill_injection_gap",
        "severity": "advisory",
        "agent_type": agent_type,
        "missing_skills": missing_skills,
        "triggering_artifacts": triggering_artifacts,
        "message": (
            f"Agent '{agent_type}' was expected to have {len(expected_skills)} "
            f"skills injected but {len(missing_skills)} skill(s) have no "
            f"transcript fingerprint: {', '.join(missing_skills)}"
        ),
    }
