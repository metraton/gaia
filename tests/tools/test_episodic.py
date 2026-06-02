#!/usr/bin/env python3
"""
Tests for Episodic Memory System.

PRIORITY: MEDIUM - Important for context persistence.

After T4 of brief ``episodic-workflow-to-db`` the writer side targets the
``episodes`` and ``episode_anomalies`` tables in gaia.db. The tests use an
isolated tmp DB so the shared substrate at ``~/.gaia/gaia.db`` is never
touched. Reader-side methods (search_episodes, get_episode, list_episodes,
add_relationship, delete_episode, update_outcome, get_statistics,
cleanup_old_episodes) still read from the legacy filesystem layout until
T6 migrates them -- the tests that exercise those readers are out of scope
for T4 because ``store_episode`` no longer produces the legacy files.

Validates:
1. ``store_episode`` writes the ``episodes`` row in gaia.db
2. ``store_episode`` does NOT touch the legacy episodes.jsonl / per-episode
   JSON / index.json files
3. Anomalies in ``context["anomalies"]`` produce child rows in
   ``episode_anomalies``
4. Outcome validation (P0)
5. Episode dataclass / constants
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Add tools to path
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from memory.episodic import (  # noqa: E402
    EpisodicMemory,
    Episode,
    search_episodic_memory,
    RELATIONSHIP_TYPES,
    OUTCOME_VALUES,
)


@pytest.fixture
def isolated_workspace(monkeypatch):
    """Force store_episode to resolve the workspace to a known string so the
    DB never picks up the real environment workspace."""
    monkeypatch.setenv("GAIA_DISPATCH_WORKSPACE", "test_ws")
    yield "test_ws"
    monkeypatch.delenv("GAIA_DISPATCH_WORKSPACE", raising=False)


@pytest.fixture
def memory(tmp_path, isolated_workspace):
    """EpisodicMemory pinned to a tmp_path base dir and a tmp DB."""
    db_file = tmp_path / "gaia.db"
    return EpisodicMemory(
        base_path=tmp_path / "episodic-memory",
        db_path=db_file,
    )


def _db_row(memory: EpisodicMemory, episode_id: str) -> dict:
    con = sqlite3.connect(str(memory.db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _db_anomalies(memory: EpisodicMemory, episode_id: str) -> list:
    con = sqlite3.connect(str(memory.db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM episode_anomalies WHERE episode_id = ? "
            "ORDER BY id",
            (episode_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


class TestDirectoryCreation:
    """Post-T6: __init__ must NOT create filesystem artifacts as a side-effect."""

    def test_does_not_create_base_directory(self, tmp_path):
        base = tmp_path / "new-memory"
        EpisodicMemory(base_path=base, db_path=tmp_path / "gaia.db")
        assert not base.exists()

    def test_does_not_create_episodes_directory(self, tmp_path):
        base = tmp_path / "new-memory"
        EpisodicMemory(base_path=base, db_path=tmp_path / "gaia.db")
        assert not (base / "episodes").exists()

    def test_does_not_create_initial_index(self, tmp_path):
        base = tmp_path / "new-memory"
        EpisodicMemory(base_path=base, db_path=tmp_path / "gaia.db")
        assert not (base / "index.json").exists()


class TestStoreEpisode:
    """store_episode writes the DB row and skips the filesystem."""

    def test_inserts_episodes_row(self, memory):
        ep_id = memory.store_episode(prompt="Test prompt", episode_id="ep_store_test")
        row = _db_row(memory, ep_id)
        assert row
        assert row["episode_id"] == "ep_store_test"
        assert row["workspace"] == "test_ws"
        assert row["prompt"] == "Test prompt"

    def test_does_not_create_episode_jsonl(self, memory):
        memory.store_episode(prompt="No JSONL", episode_id="ep_no_jsonl")
        assert not memory.episodes_jsonl.exists()

    def test_does_not_create_per_episode_json_file(self, memory):
        memory.store_episode(prompt="No file", episode_id="ep_no_file")
        episode_file = memory.episodes_dir / "episode-ep_no_file.json"
        assert not episode_file.exists()

    def test_does_not_append_to_index(self, memory):
        memory.store_episode(prompt="No index", episode_id="ep_no_index")
        assert not memory.index_file.exists()

    def test_auto_generated_id(self, memory):
        ep_id = memory.store_episode(prompt="Auto ID test")
        assert ep_id.startswith("ep_")
        assert len(ep_id) > 10
        row = _db_row(memory, ep_id)
        assert row["episode_id"] == ep_id

    def test_stores_tags_as_json(self, memory):
        memory.store_episode(
            prompt="Tagged",
            tags=["terraform", "deploy"],
            episode_id="ep_tags",
        )
        row = _db_row(memory, "ep_tags")
        assert "terraform" in json.loads(row["tags"])

    def test_determines_episode_type(self, memory):
        memory.store_episode(
            prompt="Deploy application to production",
            episode_id="ep_type_test",
        )
        row = _db_row(memory, "ep_type_test")
        assert row["type"] == "deployment"

    def test_invalid_outcome_stripped(self, memory):
        memory.store_episode(
            prompt="Bad outcome",
            outcome="invalid_outcome",
            episode_id="ep_bad_outcome",
        )
        row = _db_row(memory, "ep_bad_outcome")
        assert row["outcome"] is None

    def test_outcome_persisted(self, memory):
        memory.store_episode(
            prompt="Good outcome",
            outcome="success",
            episode_id="ep_good_outcome",
        )
        row = _db_row(memory, "ep_good_outcome")
        assert row["outcome"] == "success"

    def test_workflow_metrics_mapped_to_columns(self, memory):
        memory.store_episode(
            prompt="With metrics",
            episode_id="ep_wf",
            workflow_metrics={
                "agent": "developer",
                "session_id": "sess-xyz",
                "task_id": "T4",
                "tier": "T0",
                "exit_code": 0,
                "plan_status": "COMPLETE",
                "output_length": 1024,
                "output_tokens_approx": 256,
                "prompt": "wf-prompt-text",
            },
        )
        row = _db_row(memory, "ep_wf")
        assert row["agent"] == "developer"
        assert row["session_id"] == "sess-xyz"
        assert row["task_id"] == "T4"
        assert row["tier"] == "T0"
        assert row["exit_code"] == 0
        assert row["plan_status"] == "COMPLETE"
        assert row["output_length"] == 1024
        assert row["output_tokens_approx"] == 256
        assert row["wf_prompt"] == "wf-prompt-text"

    def test_context_metrics_blob_excludes_anomalies(self, memory):
        memory.store_episode(
            prompt="With context",
            episode_id="ep_ctx",
            context={
                "metrics": {"k": "v"},
                "session_events": {"git_commits": []},
                "anomalies": [{"type": "x", "severity": "warning"}],
            },
        )
        row = _db_row(memory, "ep_ctx")
        blob = json.loads(row["context_metrics"])
        assert blob["metrics"] == {"k": "v"}
        assert "session_events" in blob
        assert "anomalies" not in blob

    def test_anomalies_inserted_as_child_rows(self, memory):
        memory.store_episode(
            prompt="With anomalies",
            episode_id="ep_anom",
            context={
                "anomalies": [
                    {
                        "type": "no_tool_use",
                        "severity": "warning",
                        "message": "agent emitted no tool call",
                    },
                    {
                        "type": "investigation_skip",
                        "severity": "info",
                        "message": "skipped investigation phase",
                    },
                ],
            },
        )
        rows = _db_anomalies(memory, "ep_anom")
        assert len(rows) == 2
        types = sorted(r["type"] for r in rows)
        assert types == ["investigation_skip", "no_tool_use"]
        assert all(r["workspace"] == "test_ws" for r in rows)
        first_payload = json.loads(rows[0]["payload"])
        assert "type" in first_payload


class TestStoreEpisodeFailureSurfacing:
    """Regression: store_episode used to swallow persistence failures by
    printing to stderr and returning the episode_id as if the row had been
    inserted. The hook would then log ``Captured episode: <id>`` while the
    DB had zero rows -- the bug went undetected for 26 days. The fix is
    that both the ImportError (gaia.store.writer missing) and the
    rejection (writer returned status != 'applied') now raise
    ``RuntimeError`` so the caller's logger can record the failure at
    ERROR level."""

    def test_insert_rejection_raises_runtime_error(self, memory, monkeypatch):
        # Patch insert_episode to simulate a rejection. The function is
        # imported inside store_episode, so we have to patch the module
        # attribute on gaia.store.writer.
        import gaia.store.writer as writer_mod

        def _fake_insert_episode(*args, **kwargs):
            return {"status": "error", "reason": "simulated_rejection"}

        monkeypatch.setattr(writer_mod, "insert_episode", _fake_insert_episode)

        with pytest.raises(RuntimeError, match="simulated_rejection"):
            memory.store_episode(
                prompt="should raise", episode_id="ep_should_raise"
            )

    def test_import_error_raises_runtime_error(self, memory, monkeypatch):
        # Force the ImportError branch by removing gaia.store from
        # sys.modules and shadowing the import. The cleanest way is to
        # monkeypatch builtins.__import__ for the specific module path.
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "gaia.store.writer":
                raise ImportError("simulated import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        with pytest.raises(RuntimeError, match="not importable"):
            memory.store_episode(
                prompt="should raise on import",
                episode_id="ep_should_raise_import",
            )


class TestWorkspaceResolution:
    """_resolve_workspace honours the env-var fallback order."""

    def test_dispatch_workspace_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DISPATCH_WORKSPACE", "dispatch_ws")
        monkeypatch.setenv("GAIA_WORKSPACE", "other_ws")
        mem = EpisodicMemory(
            base_path=tmp_path / "em", db_path=tmp_path / "gaia.db"
        )
        assert mem._resolve_workspace() == "dispatch_ws"

    def test_gaia_workspace_env_used_when_dispatch_missing(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("GAIA_DISPATCH_WORKSPACE", raising=False)
        monkeypatch.setenv("GAIA_WORKSPACE", "from_workspace_env")
        mem = EpisodicMemory(
            base_path=tmp_path / "em", db_path=tmp_path / "gaia.db"
        )
        assert mem._resolve_workspace() == "from_workspace_env"


class TestEpisodeDataclass:
    """Episode dataclass surface."""

    def test_episode_to_dict(self):
        episode = Episode(
            episode_id="ep_test",
            timestamp="2026-01-01T00:00:00+00:00",
            keywords=["test"],
            prompt="Test",
            clarifications={},
            enriched_prompt="Test",
            context={},
            outcome=None,
        )
        d = episode.to_dict()
        assert "outcome" not in d
        assert "episode_id" in d


class TestConstants:
    """Module constants."""

    def test_relationship_types_defined(self):
        assert "SOLVES" in RELATIONSHIP_TYPES
        assert "CAUSES" in RELATIONSHIP_TYPES
        assert "DEPENDS_ON" in RELATIONSHIP_TYPES

    def test_outcome_values_defined(self):
        assert "success" in OUTCOME_VALUES
        assert "partial" in OUTCOME_VALUES
        assert "failed" in OUTCOME_VALUES
        assert "abandoned" in OUTCOME_VALUES


class TestEdgeCases:
    """Edge-case helpers."""

    def test_extract_keywords(self, memory):
        keywords = memory._extract_keywords("the quick brown fox is a test")
        assert "the" not in keywords
        assert "is" not in keywords
        assert "quick" in keywords

    def test_keyword_limit(self, memory):
        long_text = " ".join([f"word{i}" for i in range(50)])
        keywords = memory._extract_keywords(long_text)
        assert len(keywords) <= 20

    def test_generate_title_truncation(self, memory):
        long_prompt = "A" * 100
        title = memory._generate_title(long_prompt)
        assert len(title) <= 63

    def test_determine_type_deployment(self, memory):
        assert memory._determine_type("deploy the app", {}) == "deployment"

    def test_determine_type_troubleshooting(self, memory):
        assert memory._determine_type("fix the error in pods", {}) == "troubleshooting"

    def test_determine_type_general(self, memory):
        assert memory._determine_type("random unrelated text", {}) == "general"

    def test_search_episodic_memory_convenience_returns_list(self):
        # The convenience helper should not crash even when the legacy index
        # is empty.
        results = search_episodic_memory("test query")
        assert isinstance(results, list)
