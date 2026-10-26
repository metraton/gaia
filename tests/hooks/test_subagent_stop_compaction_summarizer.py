"""The compaction summarizer closes with no contract; every other contractless turn is still gated."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402
from modules.session import compaction_bracket  # noqa: E402

SESSION_ID = "sess-compaction-summarizer"
SUMMARY = "<analysis>a</analysis>\n<summary>1. Primary Request and Intent: probe</summary>"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "_get_gaia_agent_names",
        lambda self: {"gaia-orchestrator", "gaia-system"},
    )
    clear_path_cache()
    yield
    clear_path_cache()


def _run_compact_hook(name: str) -> None:
    spec = importlib.util.spec_from_file_location(f"_{name}_under_test", _HOOKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        getattr(module, f"_handle_{name}")(SimpleNamespace(session_id=SESSION_ID, payload={}))


def _stop(agent_type: str):
    adapter = ClaudeCodeAdapter()
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_id": "aaf7d33b349d75c6b",
        "agent_type": agent_type,
        "agent_transcript_path": "",
        "last_assistant_message": SUMMARY,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    return adapter.adapt_subagent_stop(adapter.parse_event(json.dumps(payload)))


def _backstop_rows(tmp_path) -> int:
    db = tmp_path / "gaia_data" / "gaia.db"
    if not db.exists():
        return 0
    try:
        with sqlite3.connect(db) as conn:
            return conn.execute(
                "SELECT count(*) FROM agent_contract_handoffs "
                "WHERE contract_id LIKE 'hook-backstop.%'"
            ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _rejected_turn_files() -> list:
    from gaia.paths import rejected_turns_dir

    directory = rejected_turns_dir()
    return list(directory.glob("*.txt")) if directory.exists() else []


def test_the_summarizer_inside_the_compaction_window_leaves_nothing_behind(tmp_path):
    _run_compact_hook("pre_compact")

    response = _stop("gaia:gaia-orchestrator")

    assert response.exit_code == 0
    assert response.output.get("compaction_summarizer") is True
    assert _backstop_rows(tmp_path) == 0
    assert _rejected_turn_files() == []


def test_a_gaia_subagent_closing_inside_the_window_is_still_rejected():
    _run_compact_hook("pre_compact")

    response = _stop("gaia-system")

    assert response.exit_code == 2
    assert not response.output.get("compaction_summarizer")


def test_the_orchestrator_type_outside_a_window_is_still_rejected():
    response = _stop("gaia-orchestrator")

    assert response.exit_code == 2
    assert not response.output.get("compaction_summarizer")


def test_post_compact_closes_the_window():
    _run_compact_hook("pre_compact")
    _run_compact_hook("post_compact")

    response = _stop("gaia-orchestrator")

    assert response.exit_code == 2


def test_a_window_past_its_ttl_exempts_nothing():
    compaction_bracket._marker(SESSION_ID).write_text(
        str(time.time() - compaction_bracket.TTL_SECONDS - 1), encoding="utf-8",
    )

    assert not compaction_bracket.in_bracket(SESSION_ID)
