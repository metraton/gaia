#!/usr/bin/env python3
"""Behavior tests for T1.4: transcript reading is decoupled from the host format.

Closure evidence for AC-4 (grep is the floor, behavior test is the closure):
the readers in ``modules.agents.transcript_reader`` must obtain transcript
entries VIA the adapter (``adapters.host_transcript.iter_transcript_entries``),
NOT by assuming the host's JSONL serialization. These tests substitute / drive
the adapter and verify the readers work without any ``.jsonl`` file on disk --
proving the host-format knowledge now lives behind the adapter layer.

Two halves:
  1. Reader-side: monkeypatch the adapter to yield normalized (role, content)
     entries from a NON-JSONL source. The readers must produce the correct
     result without ever touching a .jsonl file. If a reader still parsed JSONL
     itself, swapping the adapter would have no effect and the assertion would
     fail.
  2. Adapter-side: the adapter alone owns JSONL + the ``message``-nesting
     convention, and a partially-written transcript degrades gracefully.
"""

import json
import sys
from pathlib import Path

import pytest

# hooks/ is placed on sys.path by tests/conftest.py; make it explicit too.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters import host_transcript
from modules.agents import transcript_reader
from modules.agents.transcript_reader import (
    read_first_user_content_from_transcript,
    read_transcript,
)


# ============================================================================
# READER-SIDE: readers consume the adapter, not JSONL
# ============================================================================

class TestReadersGoViaAdapter:
    """The readers must source entries from iter_transcript_entries, so a
    substituted adapter -- backed by NO .jsonl file -- changes their output."""

    def test_read_transcript_uses_adapter_entries_without_jsonl(self, monkeypatch):
        """read_transcript joins assistant text from adapter-supplied entries.

        The fake adapter yields entries from an in-memory list (no file, no
        JSON, no JSONL). If read_transcript still parsed JSONL itself, the path
        token below would be opened and yield nothing -- the assertion proves
        it instead consumed the adapter.
        """
        normalized_entries = [
            ("user", "Diagnose the failing pods."),
            ("assistant", "First, I will check the pods."),
            ("assistant", [
                {"type": "text", "text": "Then "},
                {"type": "text", "text": "the rollout."},
            ]),
        ]

        captured = {}

        def fake_iter(path):
            captured["path"] = path
            return iter(normalized_entries)

        monkeypatch.setattr(transcript_reader, "iter_transcript_entries", fake_iter)

        # A path that is NOT a real .jsonl file anywhere on disk.
        result = read_transcript("not-a-real-jsonl://memory")

        assert captured["path"] == "not-a-real-jsonl://memory"
        assert result == "First, I will check the pods.\nThen \nthe rollout."

    def test_read_first_user_content_uses_adapter_entries_without_jsonl(self, monkeypatch):
        """read_first_user_content returns the first user entry from the adapter."""
        normalized_entries = [
            ("assistant", "ignored"),
            ("user", "The real task prompt."),
            ("user", "A later prompt that must be ignored."),
        ]
        monkeypatch.setattr(
            transcript_reader, "iter_transcript_entries",
            lambda path: iter(normalized_entries),
        )

        result = read_first_user_content_from_transcript("not-a-real-jsonl://memory")
        assert result == "The real task prompt."

    def test_read_first_user_content_list_blocks_via_adapter(self, monkeypatch):
        """List content from the adapter is normalized to joined text."""
        monkeypatch.setattr(
            transcript_reader, "iter_transcript_entries",
            lambda path: iter([
                ("user", [
                    {"type": "text", "text": "Check rollout "},
                    {"type": "text", "text": "for orders."},
                ]),
            ]),
        )
        result = read_first_user_content_from_transcript("memory://x")
        assert result == "Check rollout  for orders."

    def test_alternative_host_format_works_through_adapter(self, monkeypatch, tmp_path):
        """A hypothetical non-JSONL host: only the adapter changes. The readers
        stay correct because they see normalized (role, content) tuples, never
        the wire format. Here the on-disk transcript is a single JSON array (NOT
        JSONL); a JSONL-assuming reader would parse it as one bad line and yield
        nothing. The readers produce the right answer only because the swapped
        adapter abstracts the format away.
        """
        transcript = tmp_path / "transcript.json"  # a JSON array, not JSONL
        transcript.write_text(json.dumps({"messages": [
            {"author": "user", "body": "Array-format task."},
            {"author": "assistant", "body": "Array-format answer."},
        ]}))

        def array_format_iter(path):
            doc = json.loads(Path(path).read_text())
            for entry in doc["messages"]:
                yield entry["author"], entry["body"]

        monkeypatch.setattr(transcript_reader, "iter_transcript_entries", array_format_iter)

        assert read_transcript(str(transcript)) == "Array-format answer."
        assert read_first_user_content_from_transcript(str(transcript)) == "Array-format task."

    def test_no_user_message_via_adapter_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            transcript_reader, "iter_transcript_entries",
            lambda path: iter([("assistant", "only assistant")]),
        )
        assert read_first_user_content_from_transcript("memory://x") is None


# ============================================================================
# ADAPTER-SIDE: the adapter owns the host JSONL format
# ============================================================================

class TestAdapterOwnsHostFormat:
    """iter_transcript_entries is the single place that knows JSONL +
    message-nesting. These tests pin that ownership."""

    def _write_jsonl(self, path: Path, entries: list) -> None:
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_parses_jsonl_with_message_nesting(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        self._write_jsonl(transcript, [
            {"message": {"role": "user", "content": "hi"}},
            {"message": {"role": "assistant", "content": "hello"}},
        ])
        out = list(host_transcript.iter_transcript_entries(str(transcript)))
        assert out == [("user", "hi"), ("assistant", "hello")]

    def test_flat_shape_fallback(self, tmp_path):
        """Entry with no 'message' key falls back to the entry itself."""
        transcript = tmp_path / "t.jsonl"
        self._write_jsonl(transcript, [{"role": "user", "content": "flat"}])
        out = list(host_transcript.iter_transcript_entries(str(transcript)))
        assert out == [("user", "flat")]

    def test_blank_and_invalid_lines_skipped(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        with open(transcript, "w") as f:
            f.write("\n")
            f.write("NOT JSON\n")
            f.write(json.dumps({"message": {"role": "user", "content": "ok"}}) + "\n")
        out = list(host_transcript.iter_transcript_entries(str(transcript)))
        assert out == [("user", "ok")]

    def test_empty_path_yields_nothing(self):
        assert list(host_transcript.iter_transcript_entries("")) == []

    def test_missing_file_yields_nothing(self, tmp_path):
        assert list(
            host_transcript.iter_transcript_entries(str(tmp_path / "missing.jsonl"))
        ) == []

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sub = tmp_path / "transcripts"
        sub.mkdir()
        self._write_jsonl(sub / "agent.jsonl", [
            {"message": {"role": "user", "content": "via tilde"}},
        ])
        out = list(host_transcript.iter_transcript_entries("~/transcripts/agent.jsonl"))
        assert out == [("user", "via tilde")]


# ============================================================================
# INTEGRATION: real JSONL file flows through adapter into the readers
# ============================================================================

class TestEndToEndThroughAdapter:
    """With the real adapter in place, a real JSONL file produces the same
    result as before -- confirming the refactor preserved behavior end to end."""

    def test_real_jsonl_read_transcript(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        with open(transcript, "w") as f:
            f.write(json.dumps({"message": {"role": "user", "content": "task"}}) + "\n")
            f.write(json.dumps({"message": {"role": "assistant", "content": "answer"}}) + "\n")
        assert read_transcript(str(transcript)) == "answer"

    def test_real_jsonl_first_user(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        with open(transcript, "w") as f:
            f.write(json.dumps({"message": {"role": "user", "content": "the prompt"}}) + "\n")
        assert read_first_user_content_from_transcript(str(transcript)) == "the prompt"
