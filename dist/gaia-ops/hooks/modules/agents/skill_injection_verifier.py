"""
Skill injection verifier -- transcript fingerprint checking.

At SubagentStop, verifies that skills declared in the agent's frontmatter
were actually injected into the agent's context by searching for unique
fingerprint strings from each SKILL.md.

Returns an optional anomaly dict (advisory) when declared skills are missing
from the transcript, indicating a potential injection gap.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Fingerprint strings per skill: unique phrases from SKILL.md that confirm injection.
# Each skill maps to a list of candidate fingerprints -- at least one must appear
# in the transcript for the skill to be considered present.
SKILL_FINGERPRINTS: Dict[str, List[str]] = {
    "agent-protocol": [
        "json:contract",
        "plan_status",
        "evidence_report",
    ],
    "security-tiers": [
        "T0_READ_ONLY",
        "T3_BLOCKED",
        "Tier Definitions",
        "Hook Enforcement",
    ],
    "investigation": [
        "Start From Injected Context",
        "Pattern Hierarchy",
        "Codebase first",
    ],
    "command-execution": [
        "ONE COMMAND. ONE RESULT. ONE EXIT CODE",
        "NO PIPES. NO CHAINS. NO REDIRECTS",
        "cloud_pipe_validator",
    ],
    "context-updater": [
        "CONTEXT_UPDATE",
        "context-updater",
    ],
    "fast-queries": [
        "fast-queries",
        "triage",
    ],
    "terraform-patterns": [
        "terraform-patterns",
        "Terragrunt",
    ],
    "gitops-patterns": [
        "gitops-patterns",
        "Flux",
        "HelmRelease",
    ],
    "developer-patterns": [
        "developer-patterns",
    ],
}


def verify_skill_injection(
    agent_type: str,
    transcript_text: str,
    declared_skills: List[str],
) -> Optional[Dict[str, Any]]:
    """Verify that declared skills were injected into the agent transcript.

    Searches the transcript for fingerprint strings that confirm each skill
    was loaded. Returns an anomaly dict if any declared skill has no
    fingerprint match in the transcript.

    Args:
        agent_type: The agent type string (e.g. "cloud-troubleshooter").
        transcript_text: The full agent transcript text.
        declared_skills: List of skill names from agent frontmatter.

    Returns:
        An anomaly dict (type: skill_injection_gap, severity: advisory) if
        any declared skill is missing from the transcript. None if all
        declared skills are present or if the check does not apply.
    """
    if not transcript_text or not declared_skills:
        return None

    missing_skills: List[str] = []

    for skill_name in declared_skills:
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

    return {
        "type": "skill_injection_gap",
        "severity": "advisory",
        "agent_type": agent_type,
        "missing_skills": missing_skills,
        "message": (
            f"Agent '{agent_type}' declared {len(declared_skills)} skills but "
            f"{len(missing_skills)} skill(s) have no transcript fingerprint: "
            f"{', '.join(missing_skills)}"
        ),
    }
