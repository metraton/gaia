#!/usr/bin/env python3
"""Hook state is keyed by (session_id, tool_use_id).

Two interleaved tool calls with different tool_use_ids must NOT clobber each
other's consumed_approval_id: each terminal event retrieves its own keyed state
and closes the right approval. This is the concurrency race that lost EXECUTED
terminal events under a single global state file. How a call closes from its
terminal event is pinned in test_terminal_event_close.py.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from gaia.approvals.store import (  # noqa: E402
    insert_requested,
    transition,
    replay_for_approval,
)
from modules.core.paths import clear_path_cache  # noqa: E402
from modules.core.state import (  # noqa: E402
    create_pre_hook_state,
    save_hook_state,
    get_hook_state,
    clear_hook_state,
)


# ---------------------------------------------------------------------------
# Shared helpers -- mirror tests/hooks/test_approval_events.py file-DB setup.
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _apply_v12_schema_to_file(db_path) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id           TEXT PRIMARY KEY,
            agent_id     TEXT,
            session_id   TEXT,
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','approved','rejected','revoked','expired')),
            fingerprint  TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            decided_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS approval_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id   TEXT NOT NULL,
            event_type    TEXT NOT NULL CHECK (event_type IN (
                              'REQUESTED','SHOWN','APPROVED','REJECTED',
                              'EXECUTED','FAILED','NOOP','REVOKED','REVERTED'
                          )),
            agent_id      TEXT,
            session_id    TEXT,
            payload_json  TEXT,
            fingerprint   TEXT,
            prev_hash     TEXT,
            this_hash     TEXT,
            metadata_json TEXT,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (approval_id) REFERENCES approvals(id)
        );
        CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
        BEFORE UPDATE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
        """
    )
    con.commit()
    con.close()


@pytest.fixture()
def approvals_db(tmp_path, monkeypatch):
    """File-backed v12 approvals DB wired into gaia.approvals.store._open_db.

    Yields an ``_open()`` factory the test can use to seed approved approvals
    and to open an assertion connection.
    """
    db_path = tmp_path / "t3_keying_test.db"
    _apply_v12_schema_to_file(db_path)

    def _open():
        con = sqlite3.connect(str(db_path))
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
        return con

    monkeypatch.setattr("gaia.approvals.store._open_db", _open)
    return _open


def _seed_approved(open_fn, command: str, session_id: str) -> str:
    """Seed an approved approval so record_event's FK is satisfied."""
    con = open_fn()
    payload = {"operation": "deploy", "commands": [command]}
    approval_id = insert_requested(payload, agent_id="ag", session_id=session_id, con=con)
    con.commit()
    transition(approval_id, "pending", "approved", agent_id="user", session_id=session_id, con=con)
    con.commit()
    con.close()
    return approval_id


@pytest.fixture()
def keyed_state_dir(tmp_path, monkeypatch):
    """Point hook-state storage at a temp .claude dir."""
    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    return claude_dir


class TestKeyedStateNoClobber:
    """Interleaved tool calls keyed by (session_id, tool_use_id) stay isolated."""

    def test_interleaved_calls_do_not_clobber_consumed_approval_id(
        self, approvals_db, keyed_state_dir
    ):
        """Two PreToolUse saves with distinct tool_use_ids each keep their own
        consumed_approval_id; each terminal event retrieves its own and records
        EXECUTED for the correct approval.
        """
        from gaia.approvals.core import close_call

        session = "sess-CONCURRENT"
        id_a = _seed_approved(approvals_db, "kubectl apply -f a.yaml", session)
        id_b = _seed_approved(approvals_db, "kubectl apply -f b.yaml", session)
        assert id_a != id_b

        # PreToolUse for call A, then interleaved PreToolUse for call B. Under
        # the old single global file, B's save would clobber A's approval id.
        save_hook_state(
            create_pre_hook_state(
                "Bash", command="kubectl apply -f a.yaml", tier="T3",
                session_id=session, tool_use_id="toolu_AAA",
                allowed=True, consumed_approval_id=id_a,
            )
        )
        save_hook_state(
            create_pre_hook_state(
                "Bash", command="kubectl apply -f b.yaml", tier="T3",
                session_id=session, tool_use_id="toolu_BBB",
                allowed=True, consumed_approval_id=id_b,
            )
        )

        # Terminal-event retrieval is keyed -- each call finds ONLY its own entry.
        state_a = get_hook_state(session_id=session, tool_use_id="toolu_AAA")
        state_b = get_hook_state(session_id=session, tool_use_id="toolu_BBB")
        assert state_a is not None and state_b is not None
        assert state_a.metadata["consumed_approval_id"] == id_a, (
            "call A must still see its own approval id -- not clobbered by B"
        )
        assert state_b.metadata["consumed_approval_id"] == id_b

        for state, tool_use_id in ((state_a, "toolu_AAA"), (state_b, "toolu_BBB")):
            close_call(
                state.metadata["consumed_approval_id"], command=state.command,
                session_id=session, tool_use_id=tool_use_id, exit_code=0,
                reserved=False, terminal_event="PostToolUse",
            )

        con = approvals_db()
        types_a = [e["event_type"] for e in replay_for_approval(id_a, con=con)]
        types_b = [e["event_type"] for e in replay_for_approval(id_b, con=con)]
        con.close()
        assert types_a[-1] == "EXECUTED"
        assert types_b[-1] == "EXECUTED"

    def test_keyed_clear_leaves_other_entry_intact(self, keyed_state_dir):
        """Clearing call A's keyed entry does not remove call B's."""
        session = "sess-CLEAR"
        save_hook_state(create_pre_hook_state(
            "Bash", tier="T3", session_id=session, tool_use_id="t1"))
        save_hook_state(create_pre_hook_state(
            "Bash", tier="T3", session_id=session, tool_use_id="t2"))

        clear_hook_state(session_id=session, tool_use_id="t1")
        assert get_hook_state(session_id=session, tool_use_id="t1") is None
        assert get_hook_state(session_id=session, tool_use_id="t2") is not None

    def test_missing_tool_use_id_degrades_to_global_file(self, keyed_state_dir):
        """No tool_use_id -> legacy single global file (back-compat fallback)."""
        save_hook_state(create_pre_hook_state("Bash", tier="T0", session_id="s"))
        # Retrieval with no key reads the same global file.
        assert get_hook_state() is not None
        assert (keyed_state_dir / ".hooks_state.json").exists()
