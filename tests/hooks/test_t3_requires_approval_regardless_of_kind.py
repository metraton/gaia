#!/usr/bin/env python3
"""T3 still requires approval, REGARDLESS of a turn's binding / kind (plan 34 task 8).

Brief: contrato-binding-y-verificacion-por-task-id (plan_id=34, task order_num=8).

The task-8 anti-leak keys the COMPLETE-promotion decision on the turn's dispatch
binding (``plan_task_id``): a plan-task-bound producer turn may not self-COMPLETE,
an UNBOUND turn (no plan_task_id) may. That is a decision about the turn's
TERMINAL STATUS -- a completely different axis from EXECUTION CONSENT (the T3
security tier / approval gate). This suite locks that the two axes stay
ORTHOGONAL: making an unbound turn free to self-COMPLETE must NOT leak into
letting it run a state-mutating (T3) command without approval.

Concretely:
  1. Tier classification is a PURE FUNCTION OF THE COMMAND -- ``classify_command_tier``
     takes no plan_task_id / kind / binding parameter, so it cannot depend on the
     turn's binding. A mutative verb is T3 (requires approval); a read-only verb is
     not. Unchanged by task 8.
  2. End-to-end: a T3 subagent command -- issued in a turn carrying NO plan_task_id
     -- is STILL blocked (deny) pending approval, with a pending ``P-<hex>`` row and
     a REQUESTED audit event. ``validate_bash_command`` has no binding parameter, so
     the approval requirement cannot be weakened by an unbound turn.
  3. The approvals / tiers framework itself is untouched by task 8: the anti-leak
     lives entirely in the binding-keyed COMPLETE gate (dispatch_binding, the CLI
     finalize seam, the reaper) and never in the tier classifier or approval store.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.security.tiers import (  # noqa: E402
    SecurityTier,
    classify_command_tier,
)
from modules.tools.bash_validator import validate_bash_command  # noqa: E402


# ===========================================================================
# 1. Tier classification is a pure function of the command (no binding input)
# ===========================================================================

class TestTierClassificationIsBindingBlind:
    def test_classify_command_tier_has_no_binding_parameter(self):
        """The classifier's signature carries no plan_task_id / kind / binding
        argument -- the tier decision cannot depend on the turn's binding, so an
        unbound turn cannot classify a mutation any lower than a bound one."""
        params = set(inspect.signature(classify_command_tier).parameters)
        for forbidden in ("plan_task_id", "kind", "turn_role", "binding"):
            assert forbidden not in params, (
                f"tier classification must not depend on {forbidden!r}"
            )

    @pytest.mark.parametrize("command", [
        "kubectl apply -f deploy.yaml",
        "terraform apply",
        "git push origin main",
        "gcloud compute instances create x",
        "helm upgrade release chart",
    ])
    def test_mutative_command_is_t3_requires_approval(self, command):
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T3_BLOCKED, f"{command!r} must be T3"
        assert tier.requires_approval is True

    @pytest.mark.parametrize("command", [
        "kubectl get pods",
        "ls -la",
        "git status",
        "terraform plan",
    ])
    def test_read_or_dryrun_command_does_not_require_approval(self, command):
        tier = classify_command_tier(command)
        assert tier.requires_approval is False, (
            f"{command!r} is not a mutation and must not require approval"
        )


# ===========================================================================
# 2. End-to-end: a T3 command in an UNBOUND turn is still blocked pending approval
# ===========================================================================

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_file_db(db_path: Path) -> None:
    """Apply the v12 approval schema to a file-backed SQLite DB (mirror of the
    helper in tests/hooks/test_t3_cutover_integration.py)."""
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript("""
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
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END;
    """)
    con.commit()
    con.close()


@pytest.fixture()
def v12_file_db(tmp_path):
    db_path = tmp_path / "t3_kind_test.db"
    _make_v12_file_db(db_path)
    assert_con = sqlite3.connect(str(db_path))
    assert_con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    yield db_path, assert_con
    assert_con.close()


def test_validate_bash_command_has_no_binding_parameter():
    """The T3 approval entry point takes no plan_task_id / kind -- the approval
    requirement is structurally independent of the turn's binding (the axis the
    task-8 anti-leak keys on)."""
    params = set(inspect.signature(validate_bash_command).parameters)
    for forbidden in ("plan_task_id", "kind", "turn_role", "binding"):
        assert forbidden not in params


def test_t3_in_unbound_turn_still_blocked_pending_approval(v12_file_db, monkeypatch):
    """A T3 subagent command -- with NO plan_task_id context whatsoever -- is
    still DENIED with a pending approval, exactly as before task 8."""
    import gaia.approvals.store as astore

    db_path, assert_con = v12_file_db

    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )
    _orig_get_pending = astore.get_pending

    def _patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = sqlite3.connect(str(db_path))
        return _orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

    monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

    session_id = "unbound-turn-session"
    # A state-mutating command, issued in a turn with no plan_task_id binding.
    result = validate_bash_command(
        "git push origin main", is_subagent=True, session_id=session_id,
    )

    assert not result.allowed, "a T3 command must be blocked even in an unbound turn"
    assert result.block_response is not None
    hook_output = result.block_response.get("hookSpecificOutput", {})
    assert hook_output.get("permissionDecision") == "deny"

    reason = hook_output.get("permissionDecisionReason", "")
    p_match = re.search(r"approval_id:\s*(P-[0-9a-f]+)", reason)
    assert p_match, f"denial must carry a pending approval_id, got:\n{reason}"
    approval_id = p_match.group(1)

    # The approval store recorded a genuine pending request (framework intact).
    ap_row = assert_con.execute(
        "SELECT status, session_id FROM approvals WHERE id = ?", (approval_id,),
    ).fetchone()
    assert ap_row is not None
    assert ap_row[0] == "pending"
    assert ap_row[1] == session_id
    ev_row = assert_con.execute(
        "SELECT event_type FROM approval_events WHERE approval_id = ? "
        "ORDER BY id ASC LIMIT 1",
        (approval_id,),
    ).fetchone()
    assert ev_row is not None and ev_row[0] == "REQUESTED"


def test_t0_read_command_allowed_in_unbound_turn(v12_file_db, monkeypatch):
    """The mirror: a read-only command needs no approval -- the tier framework is
    unchanged, T3 is not over-applied to reads."""
    db_path, _assert_con = v12_file_db
    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )
    result = validate_bash_command(
        "git status", is_subagent=True, session_id="unbound-read-session",
    )
    assert result.allowed, "a read-only (T0) command must not be blocked"
