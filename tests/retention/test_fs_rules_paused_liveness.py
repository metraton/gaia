"""Widening AC-4's paused-turn retention with AC-9's liveness signal.

``test_fs_rules_adversarial.py::test_paused_non_terminal_turn_is_never_
collected`` established the narrowing task 8 needed: a turn that only
PAUSED (``BLOCKED``, ``NEEDS_INPUT``, ``APPROVAL_REQUEST``,
``NEEDS_VERIFICATION``) keeps its scratch, because it can resume under the
same contract id. That correctly stopped a live case from being swept, but
it also stranded a producer turn's scratch forever whenever a verifier
promotes the TASK's row and never the producer's: the producer's contract
never reaches ``COMPLETE``, so under the narrowed rule alone it can never
become collectible again.

This file proves the widening -- ``gaia.retention.fs_rules.
collectable_turn_scoped`` consulting ``gaia.retention.liveness.
session_dead_past_grace`` for a paused row -- holds in BOTH directions on
the SAME entry shape, for all four paused states. A suite that only proved
the dead-session direction would silently reopen exactly the hole task 8
just closed, which is why every test below asserts the ALIVE/UNKNOWN side
in the same breath as the DEAD side.
"""

import hashlib
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

AGENT_A = valid_agent_id("plv-a1")
HOUR = 3600.0

PAUSED_STATES = ["BLOCKED", "NEEDS_INPUT", "APPROVAL_REQUEST", "NEEDS_VERIFICATION"]


def _hex_token(tag: str) -> str:
    """A deterministic hex token for *tag*, conforming to the contract id
    shape (``_CONTRACT_ID_RE`` requires the segment after the dot to be
    hex-only) -- an arbitrary label like the state name is not hex.
    """
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8]


def _contract_id(tag: str) -> str:
    return f"{AGENT_A}.{_hex_token(tag)}"


@pytest.fixture()
def fs_rules(tmp_path, monkeypatch):
    """Redirect Gaia's data substrate and the session registry file to tmp."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    import gaia.retention.fs_rules as fs_mod
    import gaia.retention.liveness as liveness_mod

    importlib.reload(fs_mod)
    importlib.reload(liveness_mod)

    from hooks.modules.session import session_registry

    registry_file = tmp_path / "session_registry.json"
    monkeypatch.setattr(session_registry, "_get_registry_path", lambda: registry_file)

    return fs_mod


def _seed_rows(tmp_path, rows):
    """``rows`` is an iterable of ``(contract_id, session_id, agent_state)``."""
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, agent_state text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs (contract_id, session_id, agent_state) "
        "values (?, ?, ?)",
        list(rows),
    )
    con.commit()
    con.close()


def _merge_registry(tmp_path, entries):
    """Merge ``{session_id: last_heartbeat}`` into the registry file.

    Merges rather than overwrites so several sessions can be seeded across
    separate calls without clobbering the ones already written.
    """
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


def _touch_dir(path: Path, age_seconds: float) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    when = time.time() - age_seconds
    os.utime(path, (when, when))
    return path


# ---------------------------------------------------------------------------
# Direction (i) -- a live session is NEVER collected, for every paused state,
# however far past the grace window the entry's mtime already sits.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", PAUSED_STATES)
def test_paused_turn_with_alive_session_is_never_collected(fs_rules, tmp_path, state):
    from gaia.paths import scratch_dir

    contract_id = _contract_id(f"{state}-alive-entry")
    session_id = f"sess-alive-{state}"
    entry = _touch_dir(scratch_dir() / contract_id, age_seconds=240 * HOUR)
    _seed_rows(tmp_path, [(contract_id, session_id, state)])
    _merge_registry(tmp_path, {session_id: time.time() - 30})

    selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24)

    assert selected == []
    assert entry.exists()


# ---------------------------------------------------------------------------
# Direction (i), UNKNOWN side -- an illegible/absent session record must be
# treated exactly like ALIVE, never like DEAD. This is the case the gate
# calls out by name: DESCONOCIDA is never a route to collection.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", PAUSED_STATES)
def test_paused_turn_with_unknown_session_is_never_collected(fs_rules, tmp_path, state):
    from gaia.paths import scratch_dir

    contract_id = _contract_id(f"{state}-unknown-entry")
    session_id = f"sess-unknown-{state}"
    entry = _touch_dir(scratch_dir() / contract_id, age_seconds=240 * HOUR)
    _seed_rows(tmp_path, [(contract_id, session_id, state)])
    # No registry entry at all for this session -- illegible/absent liveness.

    selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24)

    assert selected == []
    assert entry.exists()


# ---------------------------------------------------------------------------
# Direction (ii) -- a dead session past grace IS collected, for every paused
# state. This is the utility task 10 exists to recover.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", PAUSED_STATES)
def test_paused_turn_with_dead_session_past_grace_is_collected(fs_rules, tmp_path, state):
    from gaia.paths import scratch_dir
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    contract_id = _contract_id(f"{state}-dead-entry")
    session_id = f"sess-dead-{state}"
    entry = _touch_dir(scratch_dir() / contract_id, age_seconds=240 * HOUR)
    _seed_rows(tmp_path, [(contract_id, session_id, state)])
    _merge_registry(tmp_path, {session_id: time.time() - (HEARTBEAT_TTL_SECONDS + HOUR)})

    selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24)

    assert [r["path"] for r in selected] == [str(entry)]
    assert state in selected[0]["reason"]


def test_paused_turn_with_dead_session_inside_grace_is_kept(fs_rules, tmp_path):
    """Dead is not the only condition -- the entry must also be quiet long enough."""
    from gaia.paths import scratch_dir
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    contract_id = _contract_id("blocked-dead-fresh-entry")
    session_id = "sess-dead-fresh-entry"
    entry = _touch_dir(scratch_dir() / contract_id, age_seconds=1 * HOUR)
    _seed_rows(tmp_path, [(contract_id, session_id, "BLOCKED")])
    _merge_registry(tmp_path, {session_id: time.time() - (HEARTBEAT_TTL_SECONDS + HOUR)})

    selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24)

    assert selected == []
    assert entry.exists()


# ---------------------------------------------------------------------------
# The full cross, in one assertion: BOTH directions hold simultaneously, for
# EVERY paused state, on entries that all share the same root and grace
# window -- pinning that the rule discriminates by liveness per-entry rather
# than happening to pass each state in isolation.
# ---------------------------------------------------------------------------

def test_all_four_paused_states_cross_liveness_correctly_in_one_sweep(fs_rules, tmp_path):
    from gaia.paths import scratch_dir
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    root = scratch_dir()
    alive_entries = {}
    dead_entries = {}
    unknown_entries = {}
    heartbeats = {}

    for state in PAUSED_STATES:
        alive_id = _contract_id(f"{state}-cross-alive")
        dead_id = _contract_id(f"{state}-cross-dead")
        unknown_id = _contract_id(f"{state}-cross-unknown")
        alive_session = f"sess-{state}-cross-alive"
        dead_session = f"sess-{state}-cross-dead"
        unknown_session = f"sess-{state}-cross-unknown"

        alive_entries[state] = _touch_dir(root / alive_id, age_seconds=240 * HOUR)
        dead_entries[state] = _touch_dir(root / dead_id, age_seconds=240 * HOUR)
        unknown_entries[state] = _touch_dir(root / unknown_id, age_seconds=240 * HOUR)

        _seed_rows(
            tmp_path,
            [
                (alive_id, alive_session, state),
                (dead_id, dead_session, state),
                (unknown_id, unknown_session, state),
            ],
        )
        heartbeats[alive_session] = time.time() - 30
        heartbeats[dead_session] = time.time() - (HEARTBEAT_TTL_SECONDS + HOUR)
        # unknown_session deliberately gets no registry entry at all.

    _merge_registry(tmp_path, heartbeats)

    selected_paths = {
        r["path"] for r in fs_rules.collectable_turn_scoped(root, grace_hours=24)
    }

    for state in PAUSED_STATES:
        assert str(alive_entries[state]) not in selected_paths, (
            f"{state}: ALIVE session was collected"
        )
        assert str(unknown_entries[state]) not in selected_paths, (
            f"{state}: UNKNOWN session was collected"
        )
        assert str(dead_entries[state]) in selected_paths, (
            f"{state}: DEAD session past grace was NOT collected"
        )
        for entry in (alive_entries[state], unknown_entries[state], dead_entries[state]):
            assert entry.exists(), "collectable_turn_scoped only selects, never deletes"


# ---------------------------------------------------------------------------
# What must not regress -- the already-closed case task 8 established.
# ---------------------------------------------------------------------------

def test_terminal_case_is_unaffected_by_the_liveness_widening(fs_rules, tmp_path):
    from gaia.paths import scratch_dir

    contract_id = _contract_id("terminal-no-regression")
    entry = _touch_dir(scratch_dir() / contract_id, age_seconds=48 * HOUR)
    _seed_rows(tmp_path, [(contract_id, "sess-terminal", "COMPLETE")])
    # No registry entry -- if TERMINAL rows were ever routed through the
    # liveness check, an UNKNOWN reading here would wrongly keep the entry.

    selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24)

    assert [r["path"] for r in selected] == [str(entry)]
    assert "terminal verdict" in selected[0]["reason"]


def test_unreadable_db_selects_nothing_even_for_paused_states(fs_rules, monkeypatch, tmp_path):
    """The fail-closed posture extends to the new liveness-gated path too."""
    from gaia.paths import scratch_dir
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    contract_id = _contract_id("blocked-unreadable-db")
    session_id = "sess-blocked-baddb"
    entry = _touch_dir(scratch_dir() / contract_id, age_seconds=365 * 24 * HOUR)
    _seed_rows(tmp_path, [(contract_id, session_id, "BLOCKED")])
    _merge_registry(tmp_path, {session_id: time.time() - (HEARTBEAT_TTL_SECONDS + HOUR)})
    monkeypatch.setattr(fs_rules, "_ro_db_connect", lambda: None)

    selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24)

    assert selected == []
    assert entry.exists()
