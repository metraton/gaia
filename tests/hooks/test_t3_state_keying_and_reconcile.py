#!/usr/bin/env python3
"""Tests for the two T3 approval-audit fixes.

FIX 1 -- Key hook state by (session_id, tool_use_id):
    Two interleaved tool calls with different tool_use_ids must NOT clobber
    each other's consumed_approval_id. Each PostToolUse retrieves its own
    keyed state and records EXECUTED for the right approval. This is the
    concurrency race that lost EXECUTED terminal events under a single global
    state file.

FIX 2 -- Record FAILED via a Stop-hook reconciliation:
    The host does NOT fire PostToolUse for a non-zero Bash exit, so an approved
    T3 command that FAILED never records its terminal event -- its keyed
    pre-hook state is left dangling. At Stop, adapt_stop reconciles each
    dangling entry into a FAILED event, reading the failure detail from the
    session transcript's bare-string ``toolUseResult``. A no-double-record
    guard skips reconciliation when a terminal event already exists.
"""

from __future__ import annotations

import hashlib
import json
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
    record_event,
    transition,
    replay_for_approval,
)
from modules.core.paths import clear_path_cache  # noqa: E402
from modules.core.state import (  # noqa: E402
    create_pre_hook_state,
    save_hook_state,
    get_hook_state,
    clear_hook_state,
    iter_dangling_states,
)


# The generic error text the reconciliation stores when the transcript entry
# for a dangling tool_use_id cannot be found. Kept in sync with
# ClaudeCodeAdapter._reconcile_dangling_t3_on_stop.
_GENERIC_FALLBACK = "command failed; no PostToolUse fired (reconciled at Stop)"


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


def _adapter():
    from adapters.claude_code import ClaudeCodeAdapter
    return ClaudeCodeAdapter()


# ---------------------------------------------------------------------------
# FIX 1 -- keyed state defeats the concurrent-clobber race.
# ---------------------------------------------------------------------------

class TestKeyedStateNoClobber:
    """Interleaved tool calls keyed by (session_id, tool_use_id) stay isolated."""

    def test_interleaved_calls_do_not_clobber_consumed_approval_id(
        self, approvals_db, keyed_state_dir
    ):
        """Two PreToolUse saves with distinct tool_use_ids each keep their own
        consumed_approval_id; each PostToolUse retrieves its own and records
        EXECUTED for the correct approval.
        """
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

        # PostToolUse retrieval is keyed -- each call finds ONLY its own entry.
        state_a = get_hook_state(session_id=session, tool_use_id="toolu_AAA")
        state_b = get_hook_state(session_id=session, tool_use_id="toolu_BBB")
        assert state_a is not None and state_b is not None
        assert state_a.metadata["consumed_approval_id"] == id_a, (
            "call A must still see its own approval id -- not clobbered by B"
        )
        assert state_b.metadata["consumed_approval_id"] == id_b

        # Each PostToolUse records EXECUTED for its own approval (success path
        # unchanged), mirroring adapt_post_tool_use's keyed read + record.
        adapter = _adapter()
        adapter._record_t3_outcome_event(
            state_a.metadata["consumed_approval_id"],
            command=state_a.command, success=True, exit_code=0, session_id=session,
        )
        adapter._record_t3_outcome_event(
            state_b.metadata["consumed_approval_id"],
            command=state_b.command, success=True, exit_code=0, session_id=session,
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


# ---------------------------------------------------------------------------
# FIX 2 -- Stop-hook reconciliation records FAILED for dangling T3 entries.
# ---------------------------------------------------------------------------

class TestStopReconciliationRecordsFailed:
    """A dangling consumed_approval_id (no PostToolUse) becomes FAILED at Stop."""

    @staticmethod
    def _write_transcript(path: Path, tool_use_id: str, tool_use_result) -> None:
        """Write a realistic Claude Code transcript JSONL with one tool_result.

        The failure detail lives at the ENTRY's top level under
        ``toolUseResult`` (a bare string on a failed Bash command); the
        ``tool_result`` block inside ``message.content`` carries the
        matching tool_use_id.
        """
        entry = {
            "parentUuid": "p",
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "see toolUseResult",
                        "is_error": True,
                    }
                ],
            },
            "toolUseResult": tool_use_result,
            "uuid": "u1",
            "timestamp": "2026-07-13T00:00:00Z",
        }
        path.write_text(json.dumps(entry) + "\n")

    def test_dangling_entry_reconciled_to_failed_with_transcript_detail(
        self, approvals_db, keyed_state_dir, tmp_path
    ):
        session = "sess-FAIL"
        approval_id = _seed_approved(approvals_db, "kubectl apply -f bad.yaml", session)

        # PreToolUse wrote keyed state; PostToolUse NEVER fired (failed command),
        # so the entry is still present -> dangling.
        save_hook_state(create_pre_hook_state(
            "Bash", command="kubectl apply -f bad.yaml", tier="T3",
            session_id=session, tool_use_id="toolu_FAILA",
            allowed=True, consumed_approval_id=approval_id,
        ))

        # Realistic failure: tool_response was a bare string; the transcript
        # records it verbatim under toolUseResult.
        transcript = tmp_path / "session.jsonl"
        self._write_transcript(
            transcript, "toolu_FAILA",
            "Error: Exit code 127\n/bin/bash: line 1: kubectlx: command not found",
        )

        adapter = _adapter()
        adapter.adapt_stop({
            "hook_event_name": "Stop",
            "session_id": session,
            "transcript_path": str(transcript),
            "stop_reason": "end_turn",
        })

        con = approvals_db()
        events = replay_for_approval(approval_id, con=con)
        con.close()
        assert events[-1]["event_type"] == "FAILED", (
            f"Stop must reconcile the dangling entry into FAILED, got "
            f"{[e['event_type'] for e in events]}"
        )
        stored = json.loads(events[-1]["payload_json"])
        assert stored["outcome"] == "failure"
        assert stored["exit_code"] == 127, "exit code parsed from the transcript detail"
        # The REAL transcript stderr must be stored verbatim -- not the generic
        # fallback. Assert both the exact string and the negative.
        assert stored.get("error") == (
            "Error: Exit code 127\n"
            "/bin/bash: line 1: kubectlx: command not found"
        ), "the exact bare-string toolUseResult from the transcript is stored"
        assert stored["error"] != _GENERIC_FALLBACK, (
            "when the transcript entry is present the generic fallback must NOT "
            "be used"
        )

        # The keyed entry is cleared so it is not reprocessed on a later Stop.
        assert get_hook_state(session_id=session, tool_use_id="toolu_FAILA") is None
        assert list(iter_dangling_states(session)) == []

    def test_falls_back_to_generic_error_when_transcript_entry_absent(
        self, approvals_db, keyed_state_dir, tmp_path
    ):
        """When the transcript has no matching tool_use_id (or no transcript at
        all), reconciliation still records FAILED but with the generic fallback
        error and the default exit_code 1 -- it must not skip the event.
        """
        session = "sess-NODETAIL"
        approval_id = _seed_approved(approvals_db, "kubectl apply -f x.yaml", session)

        save_hook_state(create_pre_hook_state(
            "Bash", command="kubectl apply -f x.yaml", tier="T3",
            session_id=session, tool_use_id="toolu_MISSING",
            allowed=True, consumed_approval_id=approval_id,
        ))

        # A transcript that exists but records a DIFFERENT tool_use_id, so the
        # failure detail for toolu_MISSING cannot be found.
        transcript = tmp_path / "session.jsonl"
        self._write_transcript(transcript, "toolu_OTHER", "Error: Exit code 5")

        adapter = _adapter()
        adapter.adapt_stop({
            "hook_event_name": "Stop",
            "session_id": session,
            "transcript_path": str(transcript),
        })

        con = approvals_db()
        events = replay_for_approval(approval_id, con=con)
        con.close()
        assert events[-1]["event_type"] == "FAILED", (
            "reconciliation must still record FAILED even with no transcript detail"
        )
        stored = json.loads(events[-1]["payload_json"])
        assert stored["outcome"] == "failure"
        assert stored["error"] == _GENERIC_FALLBACK, (
            "with no matching transcript entry the generic fallback is used"
        )
        assert stored["exit_code"] == 1, "default exit code when no detail is found"
        assert get_hook_state(session_id=session, tool_use_id="toolu_MISSING") is None

    def test_no_double_record_when_executed_already_present(
        self, approvals_db, keyed_state_dir, tmp_path
    ):
        """The double-record guard: if a terminal event exists, Stop does not
        also write FAILED. It only clears the stale keyed entry.
        """
        session = "sess-GUARD"
        approval_id = _seed_approved(approvals_db, "kubectl apply -f ok.yaml", session)

        # PostToolUse DID fire and recorded EXECUTED for this approval...
        con = approvals_db()
        record_event(
            approval_id, "EXECUTED", session_id=session,
            payload_json=json.dumps({"command": "kubectl apply -f ok.yaml",
                                     "exit_code": 0, "outcome": "success"}),
            con=con,
        )
        con.commit()
        con.close()

        # ...but a keyed entry is somehow still present (e.g. clear failed).
        save_hook_state(create_pre_hook_state(
            "Bash", command="kubectl apply -f ok.yaml", tier="T3",
            session_id=session, tool_use_id="toolu_GUARD",
            allowed=True, consumed_approval_id=approval_id,
        ))

        transcript = tmp_path / "session.jsonl"
        self._write_transcript(transcript, "toolu_GUARD", "Error: Exit code 1")

        adapter = _adapter()
        adapter.adapt_stop({
            "hook_event_name": "Stop",
            "session_id": session,
            "transcript_path": str(transcript),
        })

        con = approvals_db()
        events = replay_for_approval(approval_id, con=con)
        con.close()
        terminal = [e["event_type"] for e in events
                    if e["event_type"] in ("EXECUTED", "FAILED")]
        assert terminal == ["EXECUTED"], (
            f"guard must prevent a duplicate terminal event, got {terminal}"
        )
        # Stale entry cleared even though nothing new was recorded.
        assert get_hook_state(session_id=session, tool_use_id="toolu_GUARD") is None

    def test_multiple_dangling_entries_all_reconciled(
        self, approvals_db, keyed_state_dir, tmp_path
    ):
        """Several dangling entries (incl. a prior-turn leftover) all reconcile."""
        session = "sess-MULTI"
        id1 = _seed_approved(approvals_db, "kubectl delete pod x", session)
        id2 = _seed_approved(approvals_db, "kubectl delete pod y", session)

        save_hook_state(create_pre_hook_state(
            "Bash", command="kubectl delete pod x", tier="T3",
            session_id=session, tool_use_id="toolu_M1",
            allowed=True, consumed_approval_id=id1,
        ))
        save_hook_state(create_pre_hook_state(
            "Bash", command="kubectl delete pod y", tier="T3",
            session_id=session, tool_use_id="toolu_M2",
            allowed=True, consumed_approval_id=id2,
        ))

        transcript = tmp_path / "session.jsonl"
        # Two entries in one transcript.
        lines = []
        for tuid, detail in (("toolu_M1", "Error: Exit code 1"),
                             ("toolu_M2", "Error: Exit code 2")):
            lines.append(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tuid,
                     "content": "x", "is_error": True}]},
                "toolUseResult": detail,
            }))
        transcript.write_text("\n".join(lines) + "\n")

        adapter = _adapter()
        adapter.adapt_stop({
            "hook_event_name": "Stop",
            "session_id": session,
            "transcript_path": str(transcript),
        })

        con = approvals_db()
        t1 = [e["event_type"] for e in replay_for_approval(id1, con=con)]
        t2 = [e["event_type"] for e in replay_for_approval(id2, con=con)]
        con.close()
        assert t1[-1] == "FAILED"
        assert t2[-1] == "FAILED"
        assert list(iter_dangling_states(session)) == []
