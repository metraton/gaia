#!/usr/bin/env python3
"""
Tests for skill_injection_verifier module.

Validates:
1. verify() with transcript containing skill fingerprints -> no anomalies
2. verify() with transcript missing a declared skill -> skill_injection_gap anomaly
3. Empty transcript -> reports all declared skills as missing
4. Empty declared_skills -> no anomalies
5. SKILL_FINGERPRINTS dict is non-empty and well-formed
"""

import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.skill_injection_verifier import (
    SKILL_FINGERPRINTS,
    verify_skill_injection,
)


# ============================================================================
# SKILL_FINGERPRINTS STRUCTURE
# ============================================================================

class TestSkillFingerprintsStructure:
    """Verify the SKILL_FINGERPRINTS dict is well-formed."""

    def test_fingerprints_non_empty(self):
        assert len(SKILL_FINGERPRINTS) > 0

    def test_all_keys_are_strings(self):
        for key in SKILL_FINGERPRINTS:
            assert isinstance(key, str), f"Key {key!r} is not a string"

    def test_all_values_are_non_empty_lists_of_strings(self):
        for skill_name, fingerprints in SKILL_FINGERPRINTS.items():
            assert isinstance(fingerprints, list), (
                f"Fingerprints for '{skill_name}' is not a list"
            )
            assert len(fingerprints) > 0, (
                f"Fingerprints for '{skill_name}' is empty"
            )
            for fp in fingerprints:
                assert isinstance(fp, str), (
                    f"Fingerprint {fp!r} for '{skill_name}' is not a string"
                )
                assert len(fp) > 0, (
                    f"Empty fingerprint string found for '{skill_name}'"
                )

    def test_expected_skills_present(self):
        """Core skills must have fingerprint entries."""
        expected = {"agent-protocol", "security-tiers", "investigation", "command-execution"}
        actual = set(SKILL_FINGERPRINTS.keys())
        missing = expected - actual
        assert not missing, f"Expected skills missing from SKILL_FINGERPRINTS: {missing}"


# ============================================================================
# TRANSCRIPT CONTAINS FINGERPRINTS -> NO ANOMALIES
# ============================================================================

class TestAllFingerprintsPresent:
    """When transcript contains at least one fingerprint per declared skill, no anomaly."""

    def test_single_skill_present(self):
        """A declared skill whose fingerprint appears in transcript -> None."""
        transcript = "The agent loaded evidence_report and plan_status correctly."
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=transcript,
            declared_skills=["agent-protocol"],
        )
        assert result is None

    def test_multiple_skills_all_present(self):
        """Multiple declared skills all with fingerprints in transcript -> None."""
        transcript = (
            "Using evidence_report for protocol. "
            "T0_READ_ONLY classification applied. "
            "Evidence ladder was followed. "
            "One command, one result, one exit code. enforced."
        )
        result = verify_skill_injection(
            agent_type="platform-architect",
            transcript_text=transcript,
            declared_skills=[
                "agent-protocol",
                "security-tiers",
                "investigation",
                "command-execution",
            ],
        )
        assert result is None

    def test_only_one_fingerprint_needed_per_skill(self):
        """A skill with multiple fingerprints only needs one to match."""
        transcript = "The Enforcement anchors section was checked."
        result = verify_skill_injection(
            agent_type="cloud-troubleshooter",
            transcript_text=transcript,
            declared_skills=["security-tiers"],
        )
        assert result is None

    def test_fingerprint_as_substring(self):
        """Fingerprints found as substrings of larger text still match."""
        transcript = "Before doing anything, the agent filled an evidence_report field."
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=transcript,
            declared_skills=["agent-protocol"],
        )
        assert result is None


# ============================================================================
# TRANSCRIPT MISSING A DECLARED SKILL -> ANOMALY
# ============================================================================

class TestMissingSkillAnomaly:
    """When a declared skill has no fingerprint in the transcript, an anomaly is returned."""

    def test_one_skill_missing(self):
        """Declared skill with no fingerprint match -> anomaly."""
        transcript = "The agent did some work but never referenced any skill content."
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=transcript,
            declared_skills=["agent-protocol"],
        )
        assert result is not None
        assert result["type"] == "skill_injection_gap"
        assert result["severity"] == "advisory"
        assert "agent-protocol" in result["missing_skills"]
        assert result["agent_type"] == "developer"

    def test_some_present_some_missing(self):
        """When some skills are present and others missing, only missing ones are reported."""
        transcript = "Agent used evidence_report and plan_status. No other skills."
        result = verify_skill_injection(
            agent_type="platform-architect",
            transcript_text=transcript,
            declared_skills=["agent-protocol", "investigation"],
        )
        assert result is not None
        assert "investigation" in result["missing_skills"]
        assert "agent-protocol" not in result["missing_skills"]

    def test_anomaly_message_contains_counts(self):
        """The anomaly message should include declared count and missing count."""
        transcript = "Nothing relevant here."
        result = verify_skill_injection(
            agent_type="gitops-operator",
            transcript_text=transcript,
            declared_skills=["agent-protocol", "security-tiers", "investigation"],
        )
        assert result is not None
        assert "3 skills" in result["message"]
        assert "3 skill(s)" in result["message"]

    def test_unknown_skill_name_is_skipped(self):
        """A declared skill with no entry in SKILL_FINGERPRINTS is silently skipped."""
        transcript = "Nothing relevant here."
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=transcript,
            declared_skills=["nonexistent-skill"],
        )
        assert result is None

    def test_mix_of_unknown_and_missing_skills(self):
        """Unknown skills are skipped, but known missing ones still produce anomaly."""
        transcript = "Nothing relevant here."
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=transcript,
            declared_skills=["nonexistent-skill", "agent-protocol"],
        )
        assert result is not None
        assert "agent-protocol" in result["missing_skills"]
        assert "nonexistent-skill" not in result["missing_skills"]


# ============================================================================
# EMPTY TRANSCRIPT -> EARLY RETURN
# ============================================================================

class TestEmptyTranscript:
    """An empty transcript triggers the early return path (returns None)."""

    def test_empty_string_transcript_returns_none(self):
        """Empty transcript with declared skills returns None (early return path)."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="",
            declared_skills=["agent-protocol", "security-tiers"],
        )
        assert result is None

    def test_none_transcript_returns_none(self):
        """None transcript returns None (early return path)."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=None,
            declared_skills=["agent-protocol"],
        )
        assert result is None


# ============================================================================
# EMPTY DECLARED_SKILLS -> NO ANOMALIES
# ============================================================================

class TestEmptyDeclaredSkills:
    """When no skills are declared, there is nothing to verify."""

    def test_empty_list_returns_none(self):
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="Some transcript content with agent_contract_handoff.",
            declared_skills=[],
        )
        assert result is None

    def test_none_declared_skills_returns_none(self):
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="Some transcript content.",
            declared_skills=None,
        )
        assert result is None


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEdgeCases:
    """Additional edge cases for robustness."""

    def test_whitespace_only_transcript(self):
        """Whitespace-only transcript is truthy but has no fingerprints."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="   \n\t  ",
            declared_skills=["agent-protocol"],
        )
        assert result is not None
        assert "agent-protocol" in result["missing_skills"]

    def test_case_sensitive_fingerprint_matching(self):
        """Fingerprint matching is case-sensitive."""
        transcript = "JSON:CONTRACT and PLAN_STATUS"
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text=transcript,
            declared_skills=["agent-protocol"],
        )
        assert result is not None
        assert "agent-protocol" in result["missing_skills"]

    def test_all_map_skills_verifiable(self):
        """Every skill in SKILL_FINGERPRINTS can be verified with its own fingerprints."""
        for skill_name, fingerprints in SKILL_FINGERPRINTS.items():
            transcript = f"The agent used {fingerprints[0]} in its work."
            result = verify_skill_injection(
                agent_type="test-agent",
                transcript_text=transcript,
                declared_skills=[skill_name],
            )
            assert result is None, (
                f"Skill '{skill_name}' should be verified by its own fingerprint "
                f"'{fingerprints[0]}' but got anomaly: {result}"
            )


# ============================================================================
# FINGERPRINT SYNC -- EACH FINGERPRINT MUST EXIST IN ITS OWN SKILL.md
# ============================================================================

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILLS_DIR = REPO_ROOT / "skills"


class TestFingerprintsMatchSkillFiles:
    """Guards SKILL_FINGERPRINTS against drifting out of sync with the
    SKILL.md prose each entry is meant to identify.

    A fingerprint that no longer appears verbatim in its skill's SKILL.md
    (the source file edited, the fingerprint left behind) can never match a
    real transcript again -- the check then reports a false
    skill_injection_gap for every agent that declares that skill, forever,
    with nothing else in the system catching it. This test is the backstop:
    it reads every skill's actual SKILL.md and asserts each declared
    fingerprint is a literal substring of it.
    """

    def test_every_fingerprint_exists_in_its_skill_md(self):
        broken = []
        for skill_name, fingerprints in SKILL_FINGERPRINTS.items():
            skill_md = SKILLS_DIR / skill_name / "SKILL.md"
            if not skill_md.is_file():
                broken.append(f"{skill_name}: SKILL.md not found at {skill_md}")
                continue
            text = skill_md.read_text(encoding="utf-8")
            for fp in fingerprints:
                if fp not in text:
                    broken.append(
                        f"{skill_name}: fingerprint {fp!r} not found in {skill_md}"
                    )
        assert not broken, (
            "Stale fingerprint(s) in SKILL_FINGERPRINTS -- the fingerprint no "
            "longer appears in its skill's SKILL.md, so injection checks for "
            "that skill will always report a false gap:\n" + "\n".join(broken)
        )


# ============================================================================
# ARTIFACT-DERIVED EXPECTATION -- INDEPENDENT OF FRONTMATTER
# ============================================================================

class TestArtifactDerivedExpectation:
    """written_paths routes an expectation through artifact_skill_map,
    regardless of what declared_skills (the frontmatter) contains."""

    def test_written_python_file_expects_code_standards_undeclared(self):
        """A .py file written with no code-standards fingerprint in the
        transcript, and no frontmatter declaration at all, still flags."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="The agent wrote a module and moved on.",
            declared_skills=[],
            written_paths=["gaia/hooks/modules/agents/some_module.py"],
        )
        assert result is not None
        assert result["type"] == "skill_injection_gap"
        assert result["severity"] == "advisory"
        assert "code-standards" in result["missing_skills"]

    def test_written_python_file_with_fingerprint_present_no_anomaly(self):
        """Same written file, but the transcript shows the skill's own
        fingerprint -- no gap."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="A comment is the exception, not the default, so I cut the narration.",
            declared_skills=[],
            written_paths=["gaia/hooks/modules/agents/some_module.py"],
        )
        assert result is None

    def test_unrecognized_extension_adds_no_expectation(self):
        """A written path with no registered artifact rule adds nothing --
        empty declared_skills and an unrecognized path stays a no-op."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="Nothing relevant here.",
            declared_skills=[],
            written_paths=["README.md"],
        )
        assert result is None

    def test_declared_and_artifact_expectations_both_checked(self):
        """A declared skill and a file-derived skill are both verified;
        only the one truly missing from the transcript is reported."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="Using evidence_report for protocol.",
            declared_skills=["agent-protocol"],
            written_paths=["hooks/modules/agents/thing.py"],
        )
        assert result is not None
        assert "code-standards" in result["missing_skills"]
        assert "agent-protocol" not in result["missing_skills"]

    def test_no_declared_and_no_written_paths_returns_none(self):
        """written_paths defaults to None -- behaves exactly as before."""
        result = verify_skill_injection(
            agent_type="developer",
            transcript_text="Some transcript content.",
            declared_skills=[],
        )
        assert result is None
