#!/usr/bin/env python3
"""
Integration tests for the transcript_analysis wiring in subagent_stop_hook().

Covers the reconnection of the transcript-analysis argument between
subagent_stop_hook() and workflow_auditor.audit(): the four
transcript-based checks (investigation_skip, context_ignored,
duration_outlier, pipe_retroactive) must run when a usable transcript is
available, and the hook must degrade cleanly -- with a recorded reason,
never a silent pass -- when it is not.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from subagent_stop import subagent_stop_hook
from modules.agents.response_contract import clear_contract_dir_cache
from modules.core.paths import clear_path_cache


@pytest.fixture(autouse=True)
def isolate_env(tmp_path, monkeypatch):
    """Isolate all file I/O to tmp_path."""
    clear_path_cache()
    clear_contract_dir_cache()
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    yield
    clear_path_cache()
    clear_contract_dir_cache()


@pytest.fixture
def base_task_info():
    return {
        "task_id": "T900",
        "agent_id": "a900001",
        "description": "Check compute instances",
        "agent": "cloud-troubleshooter",
        "tier": "T0",
        "tags": ["#gcp"],
    }


PLAIN_OUTPUT = "## Report\n\nChecked instances, all nominal.\n"


def _write_triggering_transcript(tmp_path: Path) -> Path:
    """A transcript designed to trip all four transcript-based checks:

    - first tool call is Bash -> investigation_skip
    - that Bash call's args carry no project-context path -> context_ignored
    - the Bash command pipes a cloud CLI's output -> pipe_retroactive
    - first/last timestamps span 11 minutes -> duration_outlier (> 10 min)
    """
    transcript_path = tmp_path / "agent_transcript.jsonl"
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2024-01-01T00:00:00.000Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "gcloud compute instances list | head -5"},
                }],
            },
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2024-01-01T00:11:00.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
            },
        }),
    ]
    transcript_path.write_text("\n".join(lines) + "\n")
    return transcript_path


class TestTranscriptChecksRunWhenAvailable:
    """With a usable transcript, the four checks fire through subagent_stop_hook()."""

    @patch("subagent_stop.write_episode", return_value="ep-test-901")
    def test_all_four_checks_fire(self, mock_write_episode, base_task_info, tmp_path):
        transcript_path = _write_triggering_transcript(tmp_path)
        task_info = dict(base_task_info)
        task_info["agent_transcript_path"] = str(transcript_path)

        result = subagent_stop_hook(task_info, PLAIN_OUTPUT)

        assert result["success"] is True
        assert mock_write_episode.called
        _, kwargs = mock_write_episode.call_args
        anomalies = kwargs.get("anomalies") or []
        types = {a["type"] for a in anomalies}

        expected = {
            "investigation_skip",
            "context_ignored",
            "duration_outlier",
            "pipe_retroactive",
        }
        assert expected.issubset(types), (
            f"Expected all four transcript-based anomalies, got: {types}"
        )
        assert "transcript_checks_skipped" not in types
        assert result["anomalies_detected"] >= len(expected)


class TestTranscriptChecksDegradeWhenMissing:
    """Without a usable transcript, the hook must not crash and must not
    silently report a clean pass -- a record of the skip is expected."""

    @patch("subagent_stop.write_episode", return_value="ep-test-902")
    def test_no_transcript_path_records_skip_reason(
        self, mock_write_episode, base_task_info
    ):
        task_info = dict(base_task_info)  # no agent_transcript_path key at all

        result = subagent_stop_hook(task_info, PLAIN_OUTPUT)

        assert result["success"] is True
        _, kwargs = mock_write_episode.call_args
        anomalies = kwargs.get("anomalies") or []
        types = {a["type"] for a in anomalies}

        assert "transcript_checks_skipped" in types
        skip_anomaly = next(a for a in anomalies if a["type"] == "transcript_checks_skipped")
        assert skip_anomaly["severity"] == "info"
        assert "no agent_transcript_path" in skip_anomaly["message"]

        # No false pass: the transcript-based checks must not silently appear
        # as if they ran and found nothing.
        transcript_check_types = {
            "investigation_skip",
            "context_ignored",
            "duration_outlier",
            "pipe_retroactive",
        }
        assert transcript_check_types.isdisjoint(types)

    @patch("subagent_stop.write_episode", return_value="ep-test-903")
    def test_missing_transcript_file_records_skip_reason(
        self, mock_write_episode, base_task_info, tmp_path
    ):
        task_info = dict(base_task_info)
        task_info["agent_transcript_path"] = str(tmp_path / "does-not-exist.jsonl")

        result = subagent_stop_hook(task_info, PLAIN_OUTPUT)

        assert result["success"] is True
        _, kwargs = mock_write_episode.call_args
        anomalies = kwargs.get("anomalies") or []
        skip_anomaly = next(
            (a for a in anomalies if a["type"] == "transcript_checks_skipped"), None
        )
        assert skip_anomaly is not None
        assert "transcript file not found" in skip_anomaly["message"]

    @patch("subagent_stop.write_episode", return_value="ep-test-904")
    def test_empty_transcript_path_string_records_skip_reason(
        self, mock_write_episode, base_task_info
    ):
        task_info = dict(base_task_info)
        task_info["agent_transcript_path"] = ""

        result = subagent_stop_hook(task_info, PLAIN_OUTPUT)

        assert result["success"] is True
        _, kwargs = mock_write_episode.call_args
        anomalies = kwargs.get("anomalies") or []
        types = {a["type"] for a in anomalies}
        assert "transcript_checks_skipped" in types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
