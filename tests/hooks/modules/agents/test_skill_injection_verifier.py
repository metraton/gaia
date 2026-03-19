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
        transcript = "The agent loaded json:contract and plan_status correctly."
        result = verify_skill_injection(
            agent_type="devops-developer",
            transcript_text=transcript,
            declared_skills=["agent-protocol"],
        )
        assert result is None

    def test_multiple_skills_all_present(self):
        """Multiple declared skills all with fingerprints in transcript -> None."""
        transcript = (
            "Using json:contract for protocol. "
            "T0_READ_ONLY classification applied. "
            "Start From Injected Context was followed. "
            "ONE COMMAND. ONE RESULT. ONE EXIT CODE enforced."
        )
        result = verify_skill_injection(
            agent_type="terraform-architect",
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
        transcript = "The Hook Enforcement module was checked."
        result = verify_skill_injection(
            agent_type="cloud-troubleshooter",
            transcript_text=transcript,
            declared_skills=["security-tiers"],
        )
        assert result is None

    def test_fingerprint_as_substring(self):
        """Fingerprints found as substrings of larger text still match."""
        transcript = "Before doing anything, the agent invoked CONTEXT_UPDATE to enrich data."
        result = verify_skill_injection(
            agent_type="devops-developer",
            transcript_text=transcript,
            declared_skills=["context-updater"],
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
            agent_type="devops-developer",
            transcript_text=transcript,
            declared_skills=["agent-protocol"],
        )
        assert result is not None
        assert result["type"] == "skill_injection_gap"
        assert result["severity"] == "advisory"
        assert "agent-protocol" in result["missing_skills"]
        assert result["agent_type"] == "devops-developer"

    def test_some_present_some_missing(self):
        """When some skills are present and others missing, only missing ones are reported."""
        transcript = "Agent used json:contract and plan_status. No other skills."
        result = verify_skill_injection(
            agent_type="terraform-architect",
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
            agent_type="devops-developer",
            transcript_text=transcript,
            declared_skills=["nonexistent-skill"],
        )
        assert result is None

    def test_mix_of_unknown_and_missing_skills(self):
        """Unknown skills are skipped, but known missing ones still produce anomaly."""
        transcript = "Nothing relevant here."
        result = verify_skill_injection(
            agent_type="devops-developer",
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
            agent_type="devops-developer",
            transcript_text="",
            declared_skills=["agent-protocol", "security-tiers"],
        )
        assert result is None

    def test_none_transcript_returns_none(self):
        """None transcript returns None (early return path)."""
        result = verify_skill_injection(
            agent_type="devops-developer",
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
            agent_type="devops-developer",
            transcript_text="Some transcript content with json:contract.",
            declared_skills=[],
        )
        assert result is None

    def test_none_declared_skills_returns_none(self):
        result = verify_skill_injection(
            agent_type="devops-developer",
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
            agent_type="devops-developer",
            transcript_text="   \n\t  ",
            declared_skills=["agent-protocol"],
        )
        assert result is not None
        assert "agent-protocol" in result["missing_skills"]

    def test_case_sensitive_fingerprint_matching(self):
        """Fingerprint matching is case-sensitive."""
        transcript = "JSON:CONTRACT and PLAN_STATUS"
        result = verify_skill_injection(
            agent_type="devops-developer",
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
