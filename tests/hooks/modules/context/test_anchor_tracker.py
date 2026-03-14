"""Tests for context anchor hit tracking."""

import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.context.anchor_tracker import (
    MAX_TOOL_CALLS_TO_CHECK,
    TRACKABLE_TOOLS,
    _extract_searchable_text,
    cleanup_anchors,
    compute_anchor_hits,
    extract_anchors,
    extract_tool_calls_from_transcript,
    load_anchors,
    save_anchors,
)


# ============================================================================
# extract_anchors
# ============================================================================


class TestExtractAnchors:
    """Tests for extract_anchors()."""

    def test_extracts_paths_from_contract(self):
        payload = {
            "project_knowledge": {
                "terraform_infrastructure": {
                    "layout": {"base_path": "./qxo-monorepo/terraform"},
                },
                "gitops_configuration": {
                    "repository": {"path": "./qxo-monorepo/gitops"},
                },
            },
            "metadata": {},
        }
        anchors = extract_anchors(payload)
        assert "qxo-monorepo/terraform" in anchors
        assert "qxo-monorepo/gitops" in anchors

    def test_extracts_names_and_ids(self):
        payload = {
            "project_knowledge": {
                "cluster_details": {
                    "cluster_name": "oci-pos-dev-cluster",
                    "project": "oci-pos-dev-471216",
                    "region": "us-east4",
                },
            },
            "metadata": {},
        }
        anchors = extract_anchors(payload)
        assert "oci-pos-dev-cluster" in anchors
        assert "oci-pos-dev-471216" in anchors
        assert "us-east4" in anchors

    def test_extracts_from_metadata(self):
        payload = {
            "project_knowledge": {},
            "metadata": {
                "project_id": "my-project-123",
                "cluster_name": "prod-cluster",
                "region": "us-central1",
            },
        }
        anchors = extract_anchors(payload)
        assert "my-project-123" in anchors
        assert "prod-cluster" in anchors
        assert "us-central1" in anchors

    def test_skips_short_values(self):
        payload = {
            "project_knowledge": {
                "section": {"name": "ab"},  # too short
            },
            "metadata": {"region": "us"},  # too short
        }
        anchors = extract_anchors(payload)
        assert len(anchors) == 0

    def test_strips_leading_dot_slash(self):
        payload = {
            "project_knowledge": {
                "section": {"base_path": "./apps/service"},
            },
            "metadata": {},
        }
        anchors = extract_anchors(payload)
        assert "apps/service" in anchors
        assert "./apps/service" not in anchors

    def test_empty_payload(self):
        assert extract_anchors({}) == set()
        assert extract_anchors({"project_knowledge": {}, "metadata": {}}) == set()

    def test_deeply_nested(self):
        payload = {
            "project_knowledge": {
                "level1": {
                    "level2": {
                        "level3": {
                            "service_name": "my-api-service",
                        }
                    }
                }
            },
            "metadata": {},
        }
        anchors = extract_anchors(payload)
        assert "my-api-service" in anchors

    def test_service_accounts(self):
        payload = {
            "project_knowledge": {
                "workload": {
                    "service_account": "cart-service-sa",
                },
            },
            "metadata": {},
        }
        anchors = extract_anchors(payload)
        assert "cart-service-sa" in anchors


# ============================================================================
# save_anchors / load_anchors / cleanup_anchors
# ============================================================================


class TestAnchorPersistence:
    """Tests for save/load/cleanup anchor files."""

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )
        anchors = {"qxo-monorepo/terraform", "oci-pos-dev-cluster", "us-east4"}
        save_anchors("session-123", "terraform-architect", anchors)
        loaded = load_anchors("session-123", "terraform-architect")
        assert loaded == anchors

    def test_load_nonexistent_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )
        assert load_anchors("no-session", "no-agent") == set()

    def test_save_empty_anchors_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )
        result = save_anchors("session-1", "agent-1", set())
        assert result is None

    def test_cleanup_removes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )
        anchors = {"some-anchor"}
        save_anchors("session-x", "agent-y", anchors)
        assert load_anchors("session-x", "agent-y") == anchors
        cleanup_anchors("session-x", "agent-y")
        assert load_anchors("session-x", "agent-y") == set()

    def test_special_chars_in_session_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )
        anchors = {"test-anchor"}
        save_anchors("session/with:special@chars", "agent.name", anchors)
        loaded = load_anchors("session/with:special@chars", "agent.name")
        assert loaded == anchors


# ============================================================================
# extract_tool_calls_from_transcript
# ============================================================================


def _make_transcript(entries, path):
    """Write transcript JSONL entries to a file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestExtractToolCalls:
    """Tests for extract_tool_calls_from_transcript()."""

    def test_extracts_glob_and_read(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Glob",
                            "input": {
                                "pattern": "**/*.tf",
                                "path": "/home/user/qxo-monorepo/terraform",
                            },
                        }
                    ],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {
                                "file_path": "/home/user/qxo-monorepo/terraform/main.tf",
                            },
                        }
                    ],
                }
            },
        ]
        _make_transcript(entries, transcript)
        calls = extract_tool_calls_from_transcript(str(transcript))
        assert len(calls) == 2
        assert calls[0]["tool_name"] == "Glob"
        assert calls[0]["call_index"] == 1
        assert calls[1]["tool_name"] == "Read"
        assert calls[1]["call_index"] == 2

    def test_skips_non_trackable_tools(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Agent", "input": {"prompt": "test"}},
                        {"type": "tool_use", "name": "Grep", "input": {"pattern": "foo", "path": "/bar"}},
                    ],
                }
            },
        ]
        _make_transcript(entries, transcript)
        calls = extract_tool_calls_from_transcript(str(transcript))
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "Grep"

    def test_respects_max_calls(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        entries = []
        for i in range(10):
            entries.append({
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": f"/file{i}.txt"}},
                    ],
                }
            })
        _make_transcript(entries, transcript)
        calls = extract_tool_calls_from_transcript(str(transcript), max_calls=3)
        assert len(calls) == 3

    def test_skips_user_messages(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"message": {"role": "user", "content": "Do something"}},
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    ],
                }
            },
        ]
        _make_transcript(entries, transcript)
        calls = extract_tool_calls_from_transcript(str(transcript))
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "Bash"

    def test_empty_transcript(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")
        assert extract_tool_calls_from_transcript(str(transcript)) == []

    def test_nonexistent_path(self):
        assert extract_tool_calls_from_transcript("/nonexistent/path.jsonl") == []

    def test_empty_path(self):
        assert extract_tool_calls_from_transcript("") == []


# ============================================================================
# _extract_searchable_text
# ============================================================================


class TestExtractSearchableText:
    """Tests for _extract_searchable_text()."""

    def test_glob(self):
        text = _extract_searchable_text("Glob", {"pattern": "**/*.tf", "path": "/home/terraform"})
        assert "**/*.tf" in text
        assert "/home/terraform" in text

    def test_grep(self):
        text = _extract_searchable_text("Grep", {"pattern": "foo", "path": "/bar", "glob": "*.py"})
        assert "foo" in text
        assert "/bar" in text
        assert "*.py" in text

    def test_read(self):
        text = _extract_searchable_text("Read", {"file_path": "/home/user/main.tf"})
        assert "/home/user/main.tf" in text

    def test_bash(self):
        text = _extract_searchable_text("Bash", {"command": "ls /home/terraform"})
        assert "ls /home/terraform" in text

    def test_unknown_tool(self):
        text = _extract_searchable_text("Unknown", {"something": "value"})
        assert text == ""


# ============================================================================
# compute_anchor_hits
# ============================================================================


class TestComputeAnchorHits:
    """Tests for compute_anchor_hits()."""

    def test_all_hits(self):
        tool_calls = [
            {"tool_name": "Glob", "arguments": {"pattern": "**/*.tf", "path": "/home/qxo-monorepo/terraform"}, "call_index": 1},
            {"tool_name": "Read", "arguments": {"file_path": "/home/qxo-monorepo/gitops/helmrelease.yaml"}, "call_index": 2},
        ]
        anchors = {"qxo-monorepo/terraform", "qxo-monorepo/gitops"}
        result = compute_anchor_hits(tool_calls, anchors)
        assert result["total_checked"] == 2
        assert result["hits"] == 2
        assert result["hit_rate"] == 1.0
        assert all(d["hit"] for d in result["details"])

    def test_no_hits(self):
        tool_calls = [
            {"tool_name": "Bash", "arguments": {"command": "ls /tmp"}, "call_index": 1},
            {"tool_name": "Read", "arguments": {"file_path": "/etc/hosts"}, "call_index": 2},
        ]
        anchors = {"qxo-monorepo/terraform", "oci-pos-dev-cluster"}
        result = compute_anchor_hits(tool_calls, anchors)
        assert result["total_checked"] == 2
        assert result["hits"] == 0
        assert result["hit_rate"] == 0.0
        assert not any(d["hit"] for d in result["details"])

    def test_partial_hits(self):
        tool_calls = [
            {"tool_name": "Glob", "arguments": {"pattern": "**/*.tf", "path": "/home/qxo-monorepo/terraform"}, "call_index": 1},
            {"tool_name": "Bash", "arguments": {"command": "ls /tmp"}, "call_index": 2},
            {"tool_name": "Read", "arguments": {"file_path": "/home/random/file.txt"}, "call_index": 3},
        ]
        anchors = {"qxo-monorepo/terraform"}
        result = compute_anchor_hits(tool_calls, anchors)
        assert result["total_checked"] == 3
        assert result["hits"] == 1
        assert result["hit_rate"] == 0.33
        assert result["details"][0]["hit"] is True
        assert result["details"][0]["anchor"] == "qxo-monorepo/terraform"
        assert result["details"][1]["hit"] is False
        assert result["details"][2]["hit"] is False

    def test_empty_tool_calls(self):
        result = compute_anchor_hits([], {"some-anchor"})
        assert result["total_checked"] == 0
        assert result["hits"] == 0
        assert result["hit_rate"] == 0.0

    def test_empty_anchors(self):
        tool_calls = [
            {"tool_name": "Read", "arguments": {"file_path": "/test"}, "call_index": 1},
        ]
        result = compute_anchor_hits(tool_calls, set())
        assert result["total_checked"] == 1
        assert result["hits"] == 0

    def test_both_empty(self):
        result = compute_anchor_hits([], set())
        assert result["total_checked"] == 0
        assert result["hits"] == 0
        assert result["hit_rate"] == 0.0

    def test_details_structure(self):
        tool_calls = [
            {"tool_name": "Grep", "arguments": {"pattern": "cluster", "path": "/home/oci-pos-dev-cluster/config"}, "call_index": 1},
        ]
        anchors = {"oci-pos-dev-cluster"}
        result = compute_anchor_hits(tool_calls, anchors)
        detail = result["details"][0]
        assert detail["call_index"] == 1
        assert detail["tool"] == "Grep"
        assert detail["anchor"] == "oci-pos-dev-cluster"
        assert detail["hit"] is True

    def test_bash_command_matching(self):
        tool_calls = [
            {"tool_name": "Bash", "arguments": {"command": "kubectl get pods -n dev"}, "call_index": 1},
        ]
        anchors = {"kubectl", "dev-namespace"}
        # "kubectl" is in the command but "dev-namespace" is not (just "dev" alone)
        result = compute_anchor_hits(tool_calls, anchors)
        assert result["hits"] == 1
        assert result["details"][0]["anchor"] == "kubectl"


# ============================================================================
# End-to-end: save -> load -> compare flow
# ============================================================================


class TestSaveLoadCompareFlow:
    """Verifies the full anchor tracking pipeline: extract -> save -> load -> compute_anchor_hits."""

    def test_full_pipeline(self, tmp_path, monkeypatch):
        """Extract anchors from context, save them, load with same session_id, and compute hits."""
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )

        # 1. Build a realistic context payload
        context_payload = {
            "project_knowledge": {
                "terraform_infrastructure": {
                    "layout": {"base_path": "./qxo-monorepo/terraform"},
                },
                "cluster_details": {
                    "cluster_name": "oci-pos-dev-cluster",
                    "namespace": "cart-service-ns",
                },
            },
            "metadata": {
                "project_id": "oci-pos-dev-471216",
                "region": "us-east4",
            },
        }

        # 2. Extract anchors (same as context_injector does)
        anchors = extract_anchors(context_payload)
        assert len(anchors) > 0
        assert "qxo-monorepo/terraform" in anchors
        assert "oci-pos-dev-cluster" in anchors

        # 3. Save with a specific session_id (simulating injection time)
        session_id = "session-143025-a1b2c3d4"
        agent_type = "terraform-architect"
        save_anchors(session_id, agent_type, anchors)

        # 4. Load with the SAME session_id (simulating subagent_stop time)
        loaded = load_anchors(session_id, agent_type)
        assert loaded == anchors, "Loaded anchors must match saved anchors"

        # 5. Build tool calls that reference some anchors
        tool_calls = [
            {"tool_name": "Read", "arguments": {"file_path": "/home/user/qxo-monorepo/terraform/main.tf"}, "call_index": 1},
            {"tool_name": "Bash", "arguments": {"command": "kubectl get pods -n cart-service-ns"}, "call_index": 2},
            {"tool_name": "Glob", "arguments": {"pattern": "*.txt", "path": "/tmp/random"}, "call_index": 3},
        ]

        # 6. Compute hits
        result = compute_anchor_hits(tool_calls, loaded)
        assert result["total_checked"] == 3
        assert result["hits"] == 2  # terraform path + namespace
        assert result["hit_rate"] > 0
        assert result["details"][0]["hit"] is True
        assert result["details"][1]["hit"] is True
        assert result["details"][2]["hit"] is False

    def test_mismatched_session_id_produces_no_data(self, tmp_path, monkeypatch):
        """When save and load use different session_ids, load returns empty (the original bug)."""
        monkeypatch.setattr(
            "modules.context.anchor_tracker._anchors_dir", lambda: tmp_path,
        )
        anchors = {"qxo-monorepo/terraform", "oci-pos-dev-cluster"}
        save_anchors("session-A", "terraform-architect", anchors)

        # Load with a different session_id -- simulates the original bug
        loaded = load_anchors("session-B", "terraform-architect")
        assert loaded == set(), "Mismatched session_id must return empty anchors"

        # Compute hits with empty anchors -> no data
        tool_calls = [
            {"tool_name": "Read", "arguments": {"file_path": "/home/user/qxo-monorepo/terraform/main.tf"}, "call_index": 1},
        ]
        result = compute_anchor_hits(tool_calls, loaded)
        assert result["hits"] == 0
        assert result["hit_rate"] == 0.0
