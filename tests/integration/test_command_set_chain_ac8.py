#!/usr/bin/env python3
"""AC-8 (brief: endurecimiento-de-tests-del-security-core).

Chains of T3 sub-commands joined by ``&&`` / ``;`` must be covered by ONE
COMMAND_SET pending approval, not re-blocked sub-command by sub-command.

The gap this guards against
---------------------------
Before the fix, a subagent running ``cmd1 && cmd2 && cmd3`` where each
sub-command is T3 hit a double-approval: _validate_compound_command iterated
the components, the FIRST ungranted T3 minted a single-signature pending and
short-circuited, so one approval covered only ``cmd1``; ``cmd2`` re-blocked
with a fresh single pending and the user had to approve again.

The fix (bash_validator)
------------------------
_validate_compound_command now collects per-component results first. When >= 2
sub-commands are ungranted T3 (and none is hard-blocked), it mints ONE
COMMAND_SET pending over exactly those T3 sub-commands via
decide_t3_outcome(command_set=...). One approval covers the chain; each
sub-command is still consumed byte-for-byte by its own signature at retry --
no consent is widened, the commands are only grouped under one approval_id.

Controls
--------
  * a single T3 sub-command in a chain keeps the singular semantic-signature
    pending (no command_set key);
  * a chain with no T3 sub-command mints no pending and is allowed.

A subagent context (is_subagent=True) routes to deny + approval_id (Gaia flow).
"""

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from modules.tools.bash_validator import validate_bash_command  # noqa: E402

from tests.fixtures.db_helpers import apply_approvals_schema  # noqa: E402


SESSION = "ac8-chain-session"


# ---------------------------------------------------------------------------
# Isolated DB fixture: one temp SQLite file carrying BOTH the approvals /
# approval_events tables (the pending plane) AND approval_grants (the grant
# plane), wired into gaia.store.writer._connect so the full intake ->
# activation -> consume cycle runs against the test-local DB.
# ---------------------------------------------------------------------------

@pytest.fixture()
def chain_db(tmp_path, monkeypatch):
    db_file = tmp_path / "ac8_chain.db"

    def _make_con(db_path_arg=None):
        con = sqlite3.connect(str(db_file))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1,
            lambda v: hashlib.sha256((v or "").encode()).hexdigest(),
            deterministic=True,
        )
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_grants (
                approval_id           TEXT PRIMARY KEY,
                agent_id              TEXT,
                session_id            TEXT,
                command_set_json      TEXT NOT NULL,
                scope                 TEXT NOT NULL DEFAULT 'COMMAND_SET',
                created_at            TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                expires_at            TEXT,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                consumed_indexes_json TEXT,
                consumed_at           TEXT,
                revoked_at            TEXT,
                multi_use             INTEGER NOT NULL DEFAULT 0,
                confirmed             INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        apply_approvals_schema(con)
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", _make_con)
    # gaia.approvals.store._open_db delegates to writer._connect, but it also
    # has its own _open_db; patch get_pending to honour the isolated con too.
    import gaia.approvals.store as astore
    monkeypatch.setattr(astore, "_open_db", _make_con)
    _orig_get_pending = astore.get_pending

    def _patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = _make_con()
        return _orig_get_pending(
            session_id=session_id, all_sessions=all_sessions, con=con
        )

    monkeypatch.setattr(astore, "get_pending", _patched_get_pending)

    return _make_con


def _hook_output(result):
    return result.block_response["hookSpecificOutput"]


def _pending_rows(make_con):
    con = make_con()
    try:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id, payload_json FROM approvals WHERE status = 'pending'"
            ).fetchall()
        ]
    finally:
        con.close()


# ===========================================================================
# AC-8 core: a chain of 2 T3 sub-commands mints ONE COMMAND_SET pending.
# ===========================================================================

class TestChainTwoT3MintsOneCommandSet:
    """``a && b`` where both are T3 -> ONE pending carrying command_set (2).

    Note: cloud CLIs (terraform/kubectl/gcloud/aws/helm/flux) are rejected by
    cloud_pipe_validator BEFORE the compound path with a "one-command-per-step"
    corrective deny, so they never reach the COMMAND_SET intake. The chains that
    DO reach it are non-cloud T3 verbs -- git push, docker push, npm publish.
    """

    CHAIN = "git push origin main && docker push registry/app:1.0"

    def test_two_t3_chain_denies_with_single_approval_id(self, chain_db):
        result = validate_bash_command(
            self.CHAIN, is_subagent=True, session_id=SESSION,
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "deny", (
            f"chain of T3 sub-commands must DENY with a Gaia approval, got: {out}"
        )
        assert "approval_id:" in out["permissionDecisionReason"]

    def test_two_t3_chain_persists_exactly_one_command_set_pending(self, chain_db):
        result = validate_bash_command(
            self.CHAIN, is_subagent=True, session_id=SESSION,
        )
        assert not result.allowed

        rows = _pending_rows(chain_db)
        # The double-approval bug minted ONE single-signature pending here and
        # would mint a SECOND on the next sub-command's retry. The fix mints
        # exactly ONE pending that already carries BOTH commands.
        assert len(rows) == 1, (
            f"chain must produce exactly ONE pending, got {len(rows)}: "
            f"{[r['id'] for r in rows]}"
        )
        payload = json.loads(rows[0]["payload_json"])
        cmd_set = payload.get("command_set")
        assert isinstance(cmd_set, list) and len(cmd_set) == 2, (
            "the single pending must be a COMMAND_SET over BOTH T3 sub-commands, "
            f"got command_set={cmd_set}"
        )
        commands = [it["command"] for it in cmd_set]
        assert commands == [
            "git push origin main",
            "docker push registry/app:1.0",
        ], f"command_set must carry the chain's T3 sub-commands in order: {commands}"

    def test_semicolon_chain_also_groups_into_command_set(self, chain_db):
        result = validate_bash_command(
            "git push origin main ; docker push registry/app:2.0",
            is_subagent=True,
            session_id=SESSION,
        )
        assert not result.allowed
        rows = _pending_rows(chain_db)
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert len(payload.get("command_set") or []) == 2


# ===========================================================================
# AC-8 end-to-end: one approval of the COMMAND_SET pending covers BOTH
# sub-commands on retry (each consumed by its own byte-for-byte signature).
# ===========================================================================

class TestOneApprovalCoversWholeChain:
    """Intake -> approve (activate) -> retry: BOTH sub-commands now run under
    the ONE grant, with NO second approval required."""

    def test_one_approval_then_both_subcommands_allowed(self, chain_db):
        from modules.security.approval_grants import activate_db_pending_by_prefix

        chain = "git push origin main && docker push registry/app:1.0"
        # 1. INTAKE: chain blocks, mints one COMMAND_SET pending.
        result = validate_bash_command(chain, is_subagent=True, session_id=SESSION)
        assert not result.allowed
        approval_id = re.search(
            r"approval_id:\s*([\w-]+)", _hook_output(result)["permissionDecisionReason"]
        ).group(1)

        # 2. APPROVE: user approves -> activation creates ONE COMMAND_SET grant
        # (the activation branches on payload.command_set, independent of how the
        # pending was minted).
        nonce_prefix = approval_id[2:10]  # strip 'P-' then first 8 hex
        activation = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=SESSION,
        )
        assert activation.success, f"activation failed: {activation.reason}"

        # 3. RETRY: the WHOLE chain now validates -- each sub-command matches the
        # COMMAND_SET grant by its own signature; no re-block, no second approval.
        retry = validate_bash_command(chain, is_subagent=True, session_id=SESSION)
        assert retry.allowed, (
            "after ONE approval the whole chain must run; a re-block here is the "
            f"double-approval gap. reason={retry.reason}"
        )

        # Note: the retry above already consumed both indexes; a fresh isolated
        # run of one sub-command would be a SECOND consumption. We assert the
        # full-chain retry was allowed (the user-facing behaviour) rather than
        # double-consuming here.


# ===========================================================================
# Controls: single T3 in a chain, and no T3 at all -- behaviour unchanged.
# ===========================================================================

class TestControlsUnchanged:
    def test_single_t3_in_chain_keeps_singular_pending(self, chain_db):
        # Only the second component is T3 (echo is safe).
        result = validate_bash_command(
            "echo starting && git push origin main",
            is_subagent=True,
            session_id=SESSION,
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "deny"
        rows = _pending_rows(chain_db)
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        # Singular path: NO command_set key (single semantic-signature pending).
        assert payload.get("command_set") is None, (
            "a single T3 sub-command must NOT mint a COMMAND_SET; the singular "
            f"hook-block path owns it. payload={payload}"
        )

    def test_chain_with_no_t3_is_allowed_and_mints_no_pending(self, chain_db):
        result = validate_bash_command(
            "echo hello && ls -la",
            is_subagent=True,
            session_id=SESSION,
        )
        assert result.allowed, f"safe chain must be allowed: {result.reason}"
        assert _pending_rows(chain_db) == [], "no T3 -> no pending approval"

    def test_single_standalone_t3_unchanged(self, chain_db):
        # Not a chain: the plain single-command path is untouched.
        result = validate_bash_command(
            "git push origin main", is_subagent=True, session_id=SESSION,
        )
        assert not result.allowed
        rows = _pending_rows(chain_db)
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload.get("command_set") is None
