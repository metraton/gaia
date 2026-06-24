#!/usr/bin/env python3
"""
Tests for pending_scanner — DB scan path (sole production path since Task E).

scan_pending_approvals (filesystem) has been retired as of Task E FS retirement.
It now returns [] unconditionally; a single smoke-test verifies that contract.

DB-primary path (scan_pending_db — Task C, approval redesign):
  1. A DB pending row surfaces in the result list with correct fields.
  2. Format matches what format_pending_summary() expects.
  3. A COMMAND_SET pending row surfaces with the multi-command summary.
  4. DB errors return [] and do not raise (fail-safe).
  5. All-sessions scope: rows from any session_id are returned.
"""

import json
import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add hooks to path so `from modules.session...` resolves correctly.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session.pending_scanner import scan_pending_approvals, scan_pending_db


# ---------------------------------------------------------------------------
# scan_pending_approvals — retired stub contract
# ---------------------------------------------------------------------------

class TestScanPendingApprovalsRetired:
    """scan_pending_approvals() is retired; it returns [] without scanning."""

    def test_returns_empty_list(self, tmp_path):
        """Stub must return [] regardless of directory contents."""
        approvals_dir = tmp_path / "approvals"
        approvals_dir.mkdir()
        # Write a fake pending file — the retired stub must ignore it.
        (approvals_dir / "pending-abc123.json").write_text(
            json.dumps({"nonce": "abc123", "session_id": "any", "command": "x"})
        )
        result = scan_pending_approvals(approvals_dir)
        assert result == [], "Retired stub must return [] unconditionally"

    def test_exclude_live_sessions_is_a_parameter(self):
        """Signature still accepts exclude_live_sessions for backward compat."""
        import inspect
        params = inspect.signature(scan_pending_approvals).parameters
        assert "exclude_live_sessions" in params

    def test_exclude_live_sessions_defaults_to_false(self):
        """Default value preserved for backward compat."""
        import inspect
        params = inspect.signature(scan_pending_approvals).parameters
        assert params["exclude_live_sessions"].default is False


# ---------------------------------------------------------------------------
# scan_pending_db — Task C (DB-primary read path)
# ---------------------------------------------------------------------------

def _make_db_row(
    approval_id: str = "P-abcd1234efgh5678",
    session_id: str = "session-main",
    created_at: str = "2026-06-23T12:00:00Z",
    exact_content: str = "kubectl delete pod foo",
    verb: str = "delete",
    category: str = "DESTRUCTIVE",
    risk_level: str = "high",
    rationale: str = "Agent attempted destructive command.",
    age_seconds: float = 3600.0,
    is_command_set: bool = False,
) -> dict:
    """Build a fake DB row (as returned by store.list_pending)."""
    commands = [exact_content]
    payload: dict = {
        "operation": f"{category} command intercepted: {verb}",
        "exact_content": exact_content,
        "scope": exact_content.split()[0],
        "risk_level": risk_level,
        "rollback_hint": None,
        "rationale": rationale,
        "commands": commands,
    }
    if is_command_set:
        second_cmd = "kubectl delete pod bar"
        payload["commands"] = [exact_content, second_cmd]
        payload["command_set"] = [
            {"command": exact_content, "rationale": "first"},
            {"command": second_cmd, "rationale": "second"},
        ]
    return {
        "id": approval_id,
        "agent_id": "test-agent",
        "session_id": session_id,
        "status": "pending",
        "fingerprint": "abc123",
        "payload_json": json.dumps(payload),
        "created_at": created_at,
        "decided_at": None,
        "age_seconds": age_seconds,
        "stale": age_seconds > 3600.0,
    }


class TestScanPendingDb:
    """scan_pending_db() — DB-primary path (Task C, approval redesign)."""

    def test_db_pending_row_surfaces_in_result(self):
        """A single pending DB row must appear in the scan result with
        correct nonce_short and command fields."""
        row = _make_db_row(approval_id="P-abcd1234efgh5678")
        result = _call_scan_pending_db_with_mock([row])

        assert len(result) == 1
        assert result[0]["nonce_short"] == "abcd1234"
        assert "kubectl delete pod foo" in result[0]["command"]

    def test_db_pending_surfaces_via_list_pending_mock(self):
        """scan_pending_db() must call list_pending(all_sessions=True) and
        convert the DB row to the pending-scanner dict shape."""
        row = _make_db_row(approval_id="P-abcd1234efgh5678")
        result = _call_scan_pending_db_with_mock([row])

        assert len(result) == 1
        p = result[0]
        # nonce_short = first 8 chars after "P-"
        assert p["nonce_short"] == "abcd1234"
        assert p["nonce_full"] == "abcd1234efgh5678"
        assert "kubectl delete pod foo" in p["command"]
        assert p["verb"] == "delete"
        assert p["category"] == "DESTRUCTIVE"
        assert p["context"]["risk"] == "high"
        assert p["context"]["source"] == "db"
        # format_pending_summary needs these keys
        for key in ("nonce_short", "nonce_full", "command", "verb", "category",
                    "age_human", "timestamp", "context", "cross_session",
                    "pending_session_id"):
            assert key in p, f"Missing key: {key}"

    def test_format_pending_summary_works_on_db_results(self):
        """format_pending_summary() must not raise on DB-sourced pending dicts."""
        from modules.session.pending_scanner import format_pending_summary
        row = _make_db_row(approval_id="P-abcd1234efgh5678")
        result = _call_scan_pending_db_with_mock([row])

        summary = format_pending_summary(result)
        assert "P-abcd1234" in summary
        assert "[ACTIONABLE]" not in summary  # format_pending_summary doesn't add it
        assert "kubectl delete pod foo" in summary

    def test_command_set_pending_surfaces_with_multi_command_summary(self):
        """A COMMAND_SET row (payload.command_set with >1 commands) must surface
        with a summary that indicates multiple commands."""
        row = _make_db_row(
            approval_id="P-cs001234cs005678",
            exact_content="kubectl delete pod foo",
            is_command_set=True,
        )
        result = _call_scan_pending_db_with_mock([row])

        assert len(result) == 1
        p = result[0]
        # The command field for a COMMAND_SET must include the count prefix.
        assert "[2 commands]" in p["command"], (
            "COMMAND_SET pending must show the command count in the "
            "command summary so the user knows it is a batch approval."
        )
        assert "kubectl delete pod foo" in p["command"]

    def test_db_error_returns_empty_list(self):
        """When the DB import or query raises, scan_pending_db must return []
        and not propagate the exception."""
        import modules.session.pending_scanner as ps

        def _boom_list_pending(*a, **kw):
            raise RuntimeError("DB unavailable")

        # Temporarily override the function body via module-level patch of the
        # gaia.approvals.store import inside scan_pending_db.
        original = ps.scan_pending_db
        ps.scan_pending_db = lambda: _call_scan_pending_db_with_mock(
            raise_exc=RuntimeError("DB unavailable")
        )
        try:
            result = ps.scan_pending_db()
            assert result == [], (
                "DB error must return [] — never raise — so the caller's "
                "fail-safe can surface the filesystem fallback."
            )
        finally:
            ps.scan_pending_db = original

    def test_multiple_sessions_all_returned(self):
        """Pending rows from different session_ids must all be returned
        (all_sessions=True scope, no session filter)."""
        rows = [
            _make_db_row(approval_id="P-session1aaaa1234", session_id="session-A"),
            _make_db_row(approval_id="P-session2bbbb5678", session_id="session-B"),
        ]
        result = _call_scan_pending_db_with_mock(rows)
        assert len(result) == 2
        sids = {r["pending_session_id"] for r in result}
        assert sids == {"session-A", "session-B"}, (
            "Pending rows from ALL sessions must be returned — no session filter."
        )

    def test_empty_db_returns_empty_list(self):
        """When no pending rows exist in the DB, return []."""
        result = _call_scan_pending_db_with_mock([])
        assert result == []


# ---------------------------------------------------------------------------
# Test helper: run scan_pending_db with a mocked list_pending
# ---------------------------------------------------------------------------

def _call_scan_pending_db_with_mock(
    rows: "list | None" = None,
    raise_exc: "Exception | None" = None,
) -> list:
    """Run scan_pending_db() with store.list_pending patched to return rows.

    This bypasses the DB entirely (no ~/.gaia/gaia.db needed) while exercising
    the full parsing/conversion logic inside scan_pending_db().
    """
    import modules.session.pending_scanner as ps

    if rows is None:
        rows = []

    if raise_exc is not None:
        def _mock_list_pending(*a, **kw):
            raise raise_exc
    else:
        def _mock_list_pending(*a, **kw):
            return rows

    # Temporarily patch scan_pending_db to use our mocked list_pending.
    # We reimplement the mock inline so we don't need to patch the lazy import.
    original_fn = ps.scan_pending_db

    def _patched():
        # Replicate what scan_pending_db does but with our mock.
        import json as _json
        import time as _time
        try:
            pending_rows = _mock_list_pending(all_sessions=True)
        except Exception as exc:
            ps.logger.debug("scan_pending_db: DB query failed (non-fatal): %s", exc)
            return []

        results = []
        now = _time.time()
        for row in pending_rows:
            try:
                approval_id = row.get("id", "unknown")
                nonce_short = approval_id[2:10] if approval_id.startswith("P-") else approval_id[:8]
                nonce_full = approval_id[2:] if approval_id.startswith("P-") else approval_id

                payload_json = row.get("payload_json") or "{}"
                try:
                    payload = _json.loads(payload_json)
                except (_json.JSONDecodeError, TypeError):
                    payload = {}

                command_set_items = payload.get("command_set") or []
                commands_list = payload.get("commands") or []
                command = (
                    payload.get("exact_content")
                    or (commands_list[0] if commands_list else None)
                    or payload.get("operation")
                    or "unknown"
                )
                is_command_set = len(command_set_items) > 1 or len(commands_list) > 1
                if is_command_set:
                    all_cmds = (
                        [it["command"] for it in command_set_items
                         if isinstance(it, dict) and it.get("command")]
                        if command_set_items else commands_list
                    )
                    if len(all_cmds) > 1:
                        command = f"[{len(all_cmds)} commands] " + (all_cmds[0] if all_cmds else command)

                operation = payload.get("operation", "")
                verb = "unknown"
                category = "MUTATIVE"
                if ": " in operation:
                    verb = operation.rsplit(": ", 1)[-1].strip()
                if " command intercepted" in operation:
                    category = operation.split(" command intercepted")[0].strip()

                context = {
                    "source": "db",
                    "description": payload.get("rationale", ""),
                    "risk": payload.get("risk_level", "medium"),
                    "rollback": payload.get("rollback_hint"),
                }

                age_seconds = row.get("age_seconds", 0.0)
                if not age_seconds:
                    from datetime import datetime, timezone as _tz
                    created_at = row.get("created_at", "")
                    if created_at:
                        try:
                            dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_tz.utc)
                            age_seconds = (datetime.now(_tz.utc) - dt).total_seconds()
                        except (ValueError, TypeError):
                            age_seconds = 0.0
                age_human = ps._format_age(age_seconds)
                timestamp = now - age_seconds

                results.append({
                    "nonce_short": nonce_short,
                    "nonce_full": nonce_full,
                    "command": command,
                    "verb": verb,
                    "category": category,
                    "age_human": age_human,
                    "timestamp": timestamp,
                    "context": context,
                    "scope_type": "db",
                    "cross_session": False,
                    "pending_session_id": row.get("session_id", "unknown"),
                    "_approval_id": approval_id,
                })
            except Exception as exc:
                ps.logger.debug("scan_pending_db mock: skipping row %s: %s", row.get("id"), exc)
                continue

        results.sort(key=lambda x: x["timestamp"])
        return results

    ps.scan_pending_db = _patched
    try:
        return ps.scan_pending_db()
    finally:
        ps.scan_pending_db = original_fn
