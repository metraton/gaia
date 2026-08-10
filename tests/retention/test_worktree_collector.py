"""
The worktree collector distinguishes a LIVE turn from an ABANDONED one and
recycles only the second (AC-8).

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

The property under test, stated once: over five worktrees, the collector
selects exactly three -- the terminal contract past grace, the unfinished
contract whose session is dead and past grace, and the one carrying an
explicit death-proving cut_reason -- while protecting the one whose session
is alive right now, and never touching a worktree it cannot prove is Gaia's
own. Run again with an unreadable database, the same five worktrees yield
NOTHING: fail-closed applies to every case, not only the ambiguous ones.

The pair that proves the liveness signal actually does something is (b) vs
(c): both carry the exact same birth ``cut_reason`` (``never_finalized``)
and neither has closed, so without ``session_dead_past_grace`` they are
indistinguishable. Only the session heartbeat tells them apart.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

HOUR = 3600.0


# ---------------------------------------------------------------------------
# Harness
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
    return repo


def _touch(path: Path, age_seconds: float) -> None:
    when = time.time() - age_seconds
    os.utime(path, (when, when))


def _seed_rows(rows) -> None:
    """``rows`` is an iterable of ``(contract_id, session_id, agent_state, cut_reason)``.

    The minimal ``agent_contract_handoffs`` shape every retention test in
    this package already uses (see test_session_liveness.py,
    test_fs_rules_paused_liveness.py) -- just the four columns this module's
    read path touches, not the full production schema.
    """
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


def _merge_registry(tmp_path: Path, entries) -> None:
    registry_file = tmp_path / "session_registry.json"
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
def collector(tmp_path, monkeypatch):
    """Redirect Gaia's data substrate and the session registry file to tmp."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    import gaia.retention.fs_rules as fs_rules_mod
    import gaia.retention.liveness as liveness_mod
    import gaia.retention.worktree_collector as mod

    importlib.reload(fs_rules_mod)
    importlib.reload(liveness_mod)
    importlib.reload(mod)

    from hooks.modules.session import session_registry

    registry_file = tmp_path / "session_registry.json"
    monkeypatch.setattr(session_registry, "_get_registry_path", lambda: registry_file)

    return mod


# ---------------------------------------------------------------------------
# The five worktrees, built once and reused across both assertions (healthy
# DB, then illegible DB) so both runs judge the EXACT same fixture.
# ---------------------------------------------------------------------------

def _build_five_worktrees(repo, tmp_path):
    from gaia.worktree import create_agentic_worktree
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    worktrees = {}

    # (a) TERMINAL (COMPLETE), cut_reason cleared by a real finalize, quiet
    # 48h -- well past a 24h grace window.
    wt_a = create_agentic_worktree(repo, "a-terminal-past-grace", "agent-a", branch="wt-a")
    _touch(wt_a, 48 * HOUR)
    worktrees["a"] = wt_a

    # (b) Still running RIGHT NOW: never closed (birth cut_reason), session
    # heartbeat fresh. Aged 48h on disk too, on purpose -- proves liveness,
    # not mere age, is what protects it.
    wt_b = create_agentic_worktree(repo, "b-live-running-now", "agent-b", branch="wt-b")
    _touch(wt_b, 48 * HOUR)
    worktrees["b"] = wt_b

    # (c) Same shape as (b) -- never closed, birth cut_reason -- but the
    # session's heartbeat is stale past the TTL AND the entry is quiet past
    # grace. This is the pair that proves the signal: (b) and (c) are
    # identical on every column except session liveness.
    wt_c = create_agentic_worktree(repo, "c-dead-unfinished-past-grace", "agent-c", branch="wt-c")
    _touch(wt_c, 48 * HOUR)
    worktrees["c"] = wt_c

    # (d) Explicit death-proving cut_reason (REAPED). Fresh on disk (0h) and
    # even given a LIVE-looking session on purpose -- an explicit reap proves
    # death regardless of grace or a stale/misleading liveness reading.
    wt_d = create_agentic_worktree(repo, "d-explicit-reaped", "agent-d", branch="wt-d")
    worktrees["d"] = wt_d

    # (e) A worktree Gaia never locked with its own identity -- no row can
    # even be looked up for it. Never enters the candidate set at all.
    wt_e = repo.parent / "foreign-worktree"
    _git(repo, "worktree", "add", "--quiet", str(wt_e), "-b", "wt-foreign")
    _git(repo, "worktree", "lock", str(wt_e), "--reason", "manual investigation, not Gaia's")
    worktrees["e"] = wt_e

    _seed_rows([
        ("a-terminal-past-grace", "sess-a", "COMPLETE", None),
        ("b-live-running-now", "sess-b", "DISPATCHED", "never_finalized"),
        ("c-dead-unfinished-past-grace", "sess-c", "DISPATCHED", "never_finalized"),
        ("d-explicit-reaped", "sess-d", "IN_PROGRESS", "reaped"),
    ])
    _merge_registry(tmp_path, {
        "sess-b": time.time() - 30,  # fresh heartbeat -- ALIVE
        "sess-c": time.time() - (HEARTBEAT_TTL_SECONDS + HOUR),  # stale -- DEAD
        "sess-d": time.time() - 30,  # fresh/ALIVE-looking, deliberately misleading
    })

    return worktrees


# ---------------------------------------------------------------------------
# Healthy DB: exactly (a), (c), (d) selected; (b) protected; (e) never
# considered at all.
# ---------------------------------------------------------------------------

def test_five_worktrees_selects_exactly_terminal_dead_and_reaped(collector, tmp_path):
    repo = _init_repo(tmp_path)
    worktrees = _build_five_worktrees(repo, tmp_path)

    results = collector.collect_worktrees(
        repo, workspace="me", brief_slug="wt-collector-test", ac_id="AC-8",
        grace_hours=24,
    )

    selected_ids = {r["contract_id"] for r in results}
    assert selected_ids == {
        "a-terminal-past-grace",
        "c-dead-unfinished-past-grace",
        "d-explicit-reaped",
    }

    # Every selected worktree was clean, so reclaim_worktree's existing,
    # unconditionally safe exemption actually removed it.
    for r in results:
        assert r["status"] == "recycled"
    assert not worktrees["a"].exists()
    assert not worktrees["c"].exists()
    assert not worktrees["d"].exists()

    # (b) protected: alive session, never touched regardless of its age.
    assert worktrees["b"].exists()

    # (e) never even entered the candidate list -- it carries no Gaia identity.
    assert worktrees["e"].exists()
    assert "foreign-worktree" not in {str(r["path"]) for r in results}


def test_worktree_collect_reason_names_the_pair_directly(collector, tmp_path):
    """The (b)/(c) pair in isolation, asserted on the decision function
    itself rather than through the full collect_worktrees plumbing --
    pins that the divergence is liveness, not anything else on the row."""
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    _seed_rows([
        ("pair-b-alive", "sess-pair-alive", "DISPATCHED", "never_finalized"),
        ("pair-c-dead", "sess-pair-dead", "DISPATCHED", "never_finalized"),
    ])
    _merge_registry(tmp_path, {
        "sess-pair-alive": time.time() - 30,
        "sess-pair-dead": time.time() - (HEARTBEAT_TTL_SECONDS + HOUR),
    })
    old_mtime = time.time() - 48 * HOUR

    assert collector.worktree_collect_reason(
        "pair-b-alive", old_mtime, grace_hours=24
    ) is None
    assert collector.worktree_collect_reason(
        "pair-c-dead", old_mtime, grace_hours=24
    ) is not None


# ---------------------------------------------------------------------------
# (e) as a global property: the SAME five worktrees, with the database made
# illegible, yield NOTHING -- not even (a), (c), (d), which would otherwise
# qualify. Fail-closed is a property of the batch, not a per-row guess.
# ---------------------------------------------------------------------------

def test_unreadable_database_collects_nothing_from_the_same_five(
    collector, tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    worktrees = _build_five_worktrees(repo, tmp_path)
    monkeypatch.setattr(collector, "_ro_db_connect", lambda: None)

    results = collector.collect_worktrees(
        repo, workspace="me", brief_slug="wt-collector-test", ac_id="AC-8",
        grace_hours=24,
    )

    assert results == []
    for label, wt in worktrees.items():
        assert wt.exists(), f"worktree ({label}) was touched despite an illegible database"


# ---------------------------------------------------------------------------
# Never trust cut_reason's birth value alone -- the trap this plan already
# named. A row that never closed and never got an explicit cut always
# carries CUT_REASON_NEVER_FINALIZED; that alone must never read as death.
# ---------------------------------------------------------------------------

def test_birth_cut_reason_alone_is_never_treated_as_death(collector, tmp_path):
    from gaia.state import CUT_REASON_NEVER_FINALIZED

    _seed_rows([
        ("never-finalized-alone", "sess-nf", "IN_PROGRESS", CUT_REASON_NEVER_FINALIZED),
    ])
    _merge_registry(tmp_path, {"sess-nf": time.time() - 30})  # ALIVE
    old_mtime = time.time() - (365 * 24 * HOUR)  # arbitrarily old on disk

    reason = collector.worktree_collect_reason(
        "never-finalized-alone", old_mtime, grace_hours=24
    )

    assert reason is None, "the birth cut_reason value alone must never prove death"


def test_explicit_death_cut_reasons_named_set_excludes_birth_and_salvage(collector):
    from gaia.state import (
        CUT_REASON_BACKSTOP_CAPTURE,
        CUT_REASON_NEVER_FINALIZED,
        CUT_REASON_REAPED,
        CUT_REASON_SALVAGED_TRUNCATION,
    )

    assert collector.EXPLICIT_DEATH_CUT_REASONS == {
        CUT_REASON_REAPED,
        CUT_REASON_BACKSTOP_CAPTURE,
    }
    assert CUT_REASON_NEVER_FINALIZED not in collector.EXPLICIT_DEATH_CUT_REASONS
    assert CUT_REASON_SALVAGED_TRUNCATION not in collector.EXPLICIT_DEATH_CUT_REASONS
