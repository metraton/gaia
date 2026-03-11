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

    def test_injected_context_is_stripped(self, tmp_path):
        """When pre_tool_use injects project context, the real prompt is after the separator."""
        injected_prefix = (
            "# Project Context (Auto-Injected)\n\n"
            '{"contract": {"section": "data"}}\n\n'
            "---\n\n"
            "# User Task\n\n"
        )
        real_prompt = "Investigate the broken inventory-service deployment."
        transcript = tmp_path / "transcript.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry(injected_prefix + real_prompt),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == real_prompt

    def test_injected_context_with_bare_separator(self, tmp_path):
        """Fallback: separator without '# User Task' header."""
        injected_prefix = (
            "# Project Context (Auto-Injected)\n\n"
            '{"contract": {}}\n\n'
            "---\n\n"
        )
        real_prompt = "Run terraform plan on the VPC module."
        transcript = tmp_path / "transcript.jsonl"
        _write_jsonl(transcript, [
            _make_user_entry(injected_prefix + real_prompt),
        ])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == real_prompt


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

    def test_injected_context_without_any_separator_returns_empty(self, tmp_path):
        """Injected context header present but no --- separator."""
        broken = "# Project Context (Auto-Injected)\n\nsome data without separator"
        transcript = tmp_path / "no_sep.jsonl"
        _write_jsonl(transcript, [_make_user_entry(broken)])

        result = extract_task_description_from_transcript(str(transcript))
        assert result == ""

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
