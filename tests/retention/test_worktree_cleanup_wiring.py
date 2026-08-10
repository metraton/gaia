"""
Wiring pin for task 17 (AC-12, handoff_id=12369): `gaia cleanup` is the real
caller of `gaia.retention.worktree_collector.collect_worktrees`, task 15's
decision logic, which until this amendment had no caller outside its own
test file (`tests/retention/test_worktree_collector.py`).

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

This test never imports or calls `collect_worktrees`, `list_managed_worktrees`,
or `worktree_collect_reason` -- doing so would only re-prove the decision
logic task 15 already verified in isolation. It drives `cli.cleanup.
cmd_cleanup`, the exact same real CLI entry point that already fires
`collectable_turn_scoped` for scratch/tmp/cache, end to end: parse-free
`argparse.Namespace`, `_find_project_root` pointed at the fixture, real
stdout captured and parsed as JSON.

The fixture places one collectible worktree under Gaia's central root
(`gaia.paths.worktrees_dir()`) and a second, independently collectible
worktree under `.claude/worktrees` -- the harness-native root -- INSIDE THE
SAME REPO, so a single `gaia cleanup` invocation must account for both. Both
carry an explicit death-proving `cut_reason` (`reaped`) so collection fires
immediately, with no grace-window or liveness timing to get right in the
harness. A third worktree, with a session alive right now, proves the same
criterion this real entry point applies never touches a live turn.
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


# ---------------------------------------------------------------------------
# Harness -- mirrors tests/retention/test_worktree_collector.py's helpers.
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    (repo / ".claude").mkdir()
    return repo


def _seed_rows(rows) -> None:
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, "
        " agent_state text, cut_reason text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs "
        "(contract_id, session_id, agent_state, cut_reason) values (?, ?, ?, ?)",
        list(rows),
    )
    con.commit()
    con.close()


def _merge_registry(registry_file: Path, entries) -> None:
    sessions = {}
    if registry_file.exists():
        try:
            sessions = json.loads(registry_file.read_text(encoding="utf-8")).get(
                "sessions", {}
            )
        except Exception:
            sessions = {}
    for session_id, last_heartbeat in entries.items():
        sessions[session_id] = {
            "started_at": "2026-01-01T00:00:00+00:00",
            "is_headless": False,
            "last_heartbeat": last_heartbeat,
        }
    registry_file.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")


@pytest.fixture()
def wired_cleanup(tmp_path, monkeypatch):
    """Redirect Gaia's data substrate and reload every module `cmd_cleanup`
    touches, so this test never reads or writes the real ~/.gaia."""
    data_dir = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

    import gaia.retention.fs_rules as fs_rules_mod
    import gaia.retention.liveness as liveness_mod
    import gaia.retention.worktree_collector as collector_mod
    import cli.cleanup as cleanup_mod

    importlib.reload(fs_rules_mod)
    importlib.reload(liveness_mod)
    importlib.reload(collector_mod)
    importlib.reload(cleanup_mod)

    from hooks.modules.session import session_registry

    registry_file = data_dir / "session_registry.json"
    monkeypatch.setattr(session_registry, "_get_registry_path", lambda: registry_file)

    return cleanup_mod, registry_file


def _run_cleanup(cleanup_mod, root: Path, monkeypatch, *, dry_run: bool = False) -> dict:
    """Invoke the real `cmd_cleanup` entry point in --prune mode."""
    ns = argparse.Namespace(prune=True, retain=False, dry_run=dry_run, json=True)
    monkeypatch.setattr(cleanup_mod, "_find_project_root", lambda: root)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cleanup_mod.cmd_cleanup(ns)
    assert rc == 0
    return json.loads(buf.getvalue())


def _worktree_actions(result: dict) -> list:
    return [
        a for a in result["retention_actions"]
        if a["label"].startswith("Abandoned agentic worktrees")
    ]


# ---------------------------------------------------------------------------
# The test.
# ---------------------------------------------------------------------------

def test_real_cleanup_command_collects_both_roots_and_protects_the_live_one(
    wired_cleanup, tmp_path, monkeypatch
):
    cleanup_mod, registry_file = wired_cleanup
    repo = _init_repo(tmp_path)

    from gaia.worktree import create_agentic_worktree, lock_reason

    # (1) Gaia's central worktrees root.
    wt_central = create_agentic_worktree(
        repo, "central-reaped", "agent-central", branch="wt-central"
    )

    # (2) the harness-native root -- .claude/worktrees INSIDE this same repo.
    harness_dir = repo / ".claude" / "worktrees"
    harness_dir.mkdir(parents=True)
    wt_harness_target = harness_dir / "job-1"
    _git(repo, "worktree", "add", "--quiet", str(wt_harness_target), "-b", "wt-harness")
    _git(
        repo, "worktree", "lock", str(wt_harness_target), "--reason",
        lock_reason("harness-reaped", "agent-harness"),
    )
    wt_harness = wt_harness_target.resolve()

    # (3) protected: a session that is alive right now must never be touched,
    # through this same real entry point, by the same criterion as (1)/(2).
    wt_alive = create_agentic_worktree(repo, "alive-now", "agent-alive", branch="wt-alive")

    _seed_rows([
        ("central-reaped", "sess-central", "IN_PROGRESS", "reaped"),
        ("harness-reaped", "sess-harness", "IN_PROGRESS", "reaped"),
        ("alive-now", "sess-alive", "DISPATCHED", "never_finalized"),
    ])
    _merge_registry(registry_file, {"sess-alive": time.time() - 30})

    result = _run_cleanup(cleanup_mod, repo, monkeypatch)

    collected_paths = {a["path"] for a in _worktree_actions(result)}

    assert str(wt_central) in collected_paths, (
        "the central-root worktree was not collected by the real gaia cleanup command"
    )
    assert str(wt_harness) in collected_paths, (
        "the harness-native-root worktree was not collected by the real "
        "gaia cleanup command"
    )
    assert not wt_central.exists()
    assert not wt_harness.exists()

    assert str(wt_alive) not in collected_paths, (
        "a worktree with a live session was collected -- the real entry "
        "point must protect it exactly like the collector's own tests do"
    )
    assert wt_alive.exists()


def test_dry_run_previews_without_removing_either_root(wired_cleanup, tmp_path, monkeypatch):
    """`--dry-run` through the real command must report both roots' entries
    without touching disk, and must never diverge from the real sweep's
    decision (both share `collect_worktrees(..., dry_run=...)`)."""
    cleanup_mod, registry_file = wired_cleanup
    repo = _init_repo(tmp_path)

    from gaia.worktree import create_agentic_worktree, lock_reason

    wt_central = create_agentic_worktree(
        repo, "central-reaped-2", "agent-central-2", branch="wt-central-2"
    )
    harness_dir = repo / ".claude" / "worktrees"
    harness_dir.mkdir(parents=True)
    wt_harness_target = harness_dir / "job-2"
    _git(repo, "worktree", "add", "--quiet", str(wt_harness_target), "-b", "wt-harness-2")
    _git(
        repo, "worktree", "lock", str(wt_harness_target), "--reason",
        lock_reason("harness-reaped-2", "agent-harness-2"),
    )
    wt_harness = wt_harness_target.resolve()

    _seed_rows([
        ("central-reaped-2", "sess-central-2", "IN_PROGRESS", "reaped"),
        ("harness-reaped-2", "sess-harness-2", "IN_PROGRESS", "reaped"),
    ])

    result = _run_cleanup(cleanup_mod, repo, monkeypatch, dry_run=True)
    collected_paths = {a["path"] for a in _worktree_actions(result)}

    assert str(wt_central) in collected_paths
    assert str(wt_harness) in collected_paths
    # Nothing removed -- this is a preview.
    assert wt_central.exists()
    assert wt_harness.exists()
