#!/usr/bin/env python3
"""Tests for transcript_reader.extract_task_description_from_transcript().

Validates:
- Normal extraction from a valid transcript
- Empty/missing transcript
- Edge cases (malformed data, missing fields)
"""

import json
import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.transcript_reader import (
    extract_injected_context_payload_from_transcript,
    extract_task_description_from_transcript,
    read_first_user_content_from_transcript,
)


# ============================================================================
# HELPERS
# ============================================================================

def _write_jsonl(path: Path, entries: list) -> None:
    """Write a list of dicts as a JSONL file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _make_user_entry(content) -> dict:
    """Build a transcript JSONL entry with role=user."""
    return {"message": {"role": "user", "content": content}}


def _make_assistant_entry(content) -> dict:
    """Build a transcript JSONL entry with role=assistant."""
    return {"message": {"role": "assistant", "content": content}}


# ============================================================================
# NORMAL EXTRACTION
# ============================================================================

class TestNormalExtraction:
    """Extract task description from a well-formed transcript."""

    def test_simple_user_prompt_returns_text(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry("Diagnose the failing pods in the staging namespace."),
            _make_assistant_entry("I will investigate the staging pods."),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == "Diagnose the failing pods in the staging namespace."

    def test_content_as_list_of_text_blocks(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry([
                {"type": "text", "text": "Check the rollout status "},
                {"type": "text", "text": "for orders-service."},
            ]),
            _make_assistant_entry("Checking rollout..."),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert "Check the rollout status" in result
        assert "orders-service" in result

    def test_plain_prompt_returned_as_is(self, tmp_path):
        """Since Phase 2, context goes via additionalContext, so the first user
        message IS the original prompt -- no stripping needed."""
        prompt = "Investigate the broken inventory-service deployment."
        transcript = tmp_path / "transcript.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry(prompt),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == prompt

    def test_prompt_with_markdown_headers_returned_verbatim(self, tmp_path):
        """Prompts that happen to contain markdown headers are not stripped."""
        prompt = "# Deploy Plan\n\nRun terraform plan on the VPC module."
        transcript = tmp_path / "transcript.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry(prompt),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == prompt


# ============================================================================
# EMPTY / MISSING TRANSCRIPT
# ============================================================================

class TestEmptyMissingTranscript:
    """Handle absent, empty, or unreadable transcript paths."""

    def test_nonexistent_file_returns_empty(self, tmp_path):
        result = extract_task_description_from_transcript(
            str(tmp_path / "does_not_exist.jsonl")
        )
        assert result == ""

    def test_empty_path_string_returns_empty(self):
        result = extract_task_description_from_transcript("")
        assert result == ""

    def test_empty_file_returns_empty(self, tmp_path):
        transcript = tmp_path / "empty.jsonl"
        transcript.write_text("")

        result = extract_task_description_from_transcript(str(transcript))
        assert result == ""

    def test_file_with_only_whitespace_returns_empty(self, tmp_path):
        transcript = tmp_path / "whitespace.jsonl"
        transcript.write_text("   \n\n   \n")

        result = extract_task_description_from_transcript(str(transcript))
        assert result == ""


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEdgeCases:
    """Malformed data, missing fields, and boundary conditions."""

    def test_no_user_messages_returns_empty(self, tmp_path):
        transcript = tmp_path / "no_user.jsonl"
        _write_jsonl(transcript, [
            _make_assistant_entry("I am the assistant."),
            _make_assistant_entry("Still the assistant."),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == ""

    def test_invalid_json_lines_are_skipped(self, tmp_path):
        transcript = tmp_path / "bad_json.jsonl"
        with open(transcript, "w") as f:
            f.write("NOT VALID JSON\n")
            f.write(json.dumps(_make_user_entry("Valid prompt after garbage.")) + "\n")

        result = extract_task_description_from_transcript(str(transcript))
        assert result == "Valid prompt after garbage."

    def test_missing_message_key_falls_back_to_entry(self, tmp_path):
        """Entry has no 'message' key -- falls back to entry itself."""
        transcript = tmp_path / "no_message_key.jsonl"
        _write_jsonl(transcript, [
            {"role": "user", "content": "Fallback prompt."},
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == "Fallback prompt."

    def test_missing_content_key_returns_empty(self, tmp_path):
        transcript = tmp_path / "no_content.jsonl"
        _write_jsonl(transcript, [
            {"message": {"role": "user"}},
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == ""

    def test_content_is_none_returns_empty(self, tmp_path):
        transcript = tmp_path / "none_content.jsonl"
        _write_jsonl(transcript, [
            {"message": {"role": "user", "content": None}},
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == ""

    def test_truncation_at_500_chars(self, tmp_path):
        long_prompt = "A" * 600
        transcript = tmp_path / "long.jsonl"
        _write_jsonl(transcript, [_make_user_entry(long_prompt)])

        result = extract_task_description_from_transcript(str(transcript))
        assert len(result) == 500
        assert result == "A" * 500

    def test_any_text_content_is_returned(self, tmp_path):
        """Any text content in the first user message is returned as-is."""
        text = "# Project Context -- READ THIS FIRST\n\nsome data without separator"
        transcript = tmp_path / "any_text.jsonl"
        _write_jsonl(transcript, [_make_user_entry(text)])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == text

    def test_content_list_with_non_text_blocks(self, tmp_path):
        """Content list includes image blocks that should be ignored."""
        transcript = tmp_path / "mixed_blocks.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry([
                {"type": "image", "data": "base64data"},
                {"type": "text", "text": "Describe this image."},
            ]),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == "Describe this image."

    def test_tilde_path_expansion(self, tmp_path, monkeypatch):
        """Transcript path with ~ is expanded correctly."""
        monkeypatch.setenv("HOME", str(tmp_path))
        sub = tmp_path / "transcripts"
        sub.mkdir()
        transcript = sub / "agent.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry("Prompt via tilde path."),
        ])

        result = extract_task_description_from_transcript("~/transcripts/agent.jsonl")
        assert result == "Prompt via tilde path."

    def test_first_user_message_is_used_not_later_ones(self, tmp_path):
        """Only the first user message is extracted, even if multiple exist."""
        transcript = tmp_path / "multi_user.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry("First task prompt."),
            _make_assistant_entry("Working on it."),
            _make_user_entry("Follow-up question."),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == "First task prompt."


# ============================================================================
# INJECTED CONTEXT PAYLOAD EXTRACTION (extract_injected_context_payload_from_transcript)
# ============================================================================

class TestInjectedContextPayload:
    """extract_injected_context_payload_from_transcript() reads the auto-injected
    context JSON that context_injector persisted to <TMPDIR>/gaia-context-payloads/,
    matching the payload file to the transcript by agent-ID substring.
    """

    def _seed_payload(self, monkeypatch, tmp_path, stem: str, data: dict):
        """Create <tmp_path>/gaia-context-payloads/<stem>.json and point TMPDIR at it."""
        payload_dir = tmp_path / "gaia-context-payloads"
        payload_dir.mkdir()
        (payload_dir / f"{stem}.json").write_text(json.dumps(data))
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        return payload_dir

    def test_matching_transcript_returns_payload(self, monkeypatch, tmp_path):
        """A transcript whose stem shares the agent-ID substring matches its payload."""
        self._seed_payload(
            monkeypatch, tmp_path, "agent-abc123",
            {"surface_routing": {"multi_surface": True}},
        )
        result = extract_injected_context_payload_from_transcript(
            "/some/dir/agent-abc123.jsonl"
        )
        assert result == {"surface_routing": {"multi_surface": True}}

    # -- The empty-string regression (the real bug this fix closes) --------

    def test_empty_path_does_not_match_any_payload(self, monkeypatch, tmp_path):
        """REGRESSION: an empty transcript_path must NOT grab an arbitrary payload.

        Before the fix, Path("").stem == "" and ``"" in candidate.stem`` is always
        True, so an empty path returned the FIRST payload in the directory --
        making downstream consolidation logic depend on /tmp directory contents.
        The guard must return {} regardless of what payloads exist on disk.
        """
        self._seed_payload(
            monkeypatch, tmp_path, "agent-whatever",
            {"surface_routing": {"multi_surface": True}},
        )
        assert extract_injected_context_payload_from_transcript("") == {}

    def test_none_path_does_not_match_any_payload(self, monkeypatch, tmp_path):
        """None path is treated the same as empty: no match, regardless of disk."""
        self._seed_payload(
            monkeypatch, tmp_path, "agent-whatever",
            {"surface_routing": {"multi_surface": True}},
        )
        assert extract_injected_context_payload_from_transcript(None) == {}

    def test_empty_path_is_deterministic_across_payload_sets(self, monkeypatch, tmp_path):
        """An empty path returns {} whether the payload dir is empty or full --
        the result no longer depends on what happens to be in the directory.
        """
        # Directory exists but is empty.
        payload_dir = tmp_path / "gaia-context-payloads"
        payload_dir.mkdir()
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        empty_dir_result = extract_injected_context_payload_from_transcript("")

        # Now populate it with several payloads.
        for stem in ("agent-one", "agent-two", "agent-three"):
            (payload_dir / f"{stem}.json").write_text(
                json.dumps({"surface_routing": {"multi_surface": True}})
            )
        full_dir_result = extract_injected_context_payload_from_transcript("")

        assert empty_dir_result == full_dir_result == {}

    def test_root_path_stem_empty_does_not_match(self, monkeypatch, tmp_path):
        """A path like '/' yields an empty stem; it must not match either."""
        self._seed_payload(
            monkeypatch, tmp_path, "agent-whatever",
            {"surface_routing": {"multi_surface": True}},
        )
        assert extract_injected_context_payload_from_transcript("/") == {}

    def test_no_match_returns_empty_dict(self, monkeypatch, tmp_path):
        """A real path with no shared substring matches nothing -> {}."""
        self._seed_payload(
            monkeypatch, tmp_path, "agent-abc123",
            {"surface_routing": {"multi_surface": True}},
        )
        assert extract_injected_context_payload_from_transcript(
            "/some/dir/totally-different-xyz.jsonl"
        ) == {}
