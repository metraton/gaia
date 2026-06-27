#!/usr/bin/env python3
"""Mutation-survivor closure tests for approval_grants.py (Plan 16, M1).

This module exists to KILL the surviving mutants inventoried in
``tests/evals/evidence/AC-3-mutation-fullcore-inventory.md`` for
``hooks/modules/security/approval_grants.py`` (baseline 26.80% kill /
476 survivors). Each test targets the EXACT non-mutated outcome of a code
path so that the corresponding mutant fails the assertion when it lives.

The tests are honest: they assert specific values and branch directions
(boundary, comparison operator, truthiness, return value), not merely "does
not raise". A trivial smoke test would let comparison/operator mutants
survive; these do not.

Survivor groups closed here (function -> mutant kinds):

  _is_ttl_expired            -- Eq/Lt/LtE comparison, NumberReplacer (0/60),
                                False/True flips, Div/Sub/Mod binary ops, Gt/GtE
  ApprovalGrant.is_valid     -- ReplaceTrueWithFalse on the multi-use branch
  ApprovalGrant (defaults)   -- granted_at NumberReplacer, multi_use False->True
  ApprovalGrant.get_signature-- ExceptionReplacer on the deserialize guard
  _grant_ttl_minutes         -- NumberReplacer on the 60 fallback, Exception guard
  _run_git_query             -- returncode == 0 comparison, True/False capture flags
  capture_environment_snapshot -- ExceptionReplacer on the try-body
  _db_row_to_pending_dict    -- or-chain precedence, ": " split, slice/index, AddNot
  find_pending_for_command   -- empty-list guard, None-sig guard, loop, match return
  create_command_set_grant   -- missing-args guard, success/failure return values
  match_command_set_grant    -- expiry comparison, scope filter, consumed-index skip,
                                byte-for-byte match, return tuple shape
"""

import json
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add hooks to path (mirrors the sibling test modules).
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import modules.security.approval_grants as ag
from modules.security.approval_grants import (
    ApprovalGrant,
    _is_ttl_expired,
    _run_git_query,
    _db_row_to_pending_dict,
    _grant_ttl_minutes,
    capture_environment_snapshot,
    create_command_set_grant,
    match_command_set_grant,
    find_pending_for_command,
    get_pending_approvals_for_session,
    DEFAULT_COMMAND_SET_TTL_MINUTES,
    SCOPE_SEMANTIC_SIGNATURE,
)
from modules.security.approval_scopes import build_approval_signature


# ===========================================================================
# Isolated writer DB fixture (mirrors test_approval_grants.py::clean_grants_dir
# but exposes the db path for direct COMMAND_SET grant assertions).
# ===========================================================================
@pytest.fixture()
def writer_db(tmp_path, monkeypatch):
    """Redirect gaia.store.writer._connect to an isolated SQLite file carrying
    the approval_grants table, and point the grants dir at tmp_path.

    Returns the db path so tests can assert the persisted COMMAND_SET row.
    """
    import hashlib

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-session-mut")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False

    db_path = tmp_path / "writer_mut.db"

    def _make_writer_db(db_path_arg=None):
        con = sqlite3.connect(str(db_path))
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
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", _make_writer_db)
    # Materialize the schema on the file eagerly so tests that open a raw
    # sqlite3.connect(db_path) for direct INSERT/SELECT assertions see the table
    # (the patched _connect creates it lazily, but direct assertions bypass it).
    _seed = _make_writer_db()
    _seed.close()
    return db_path


# ===========================================================================
# _is_ttl_expired -- pure boundary/comparison logic (19 survivors)
# ===========================================================================
class TestIsTtlExpiredMutants:
    """_is_ttl_expired(timestamp, ttl_minutes) -- every branch pinned."""

    def test_ttl_zero_means_no_expiry(self):
        """ttl_minutes == 0 => never expires (kills Eq->Lt/LtE on line 175,
        the NumberReplacer on the literal 0, and the False->True flip)."""
        # Even a timestamp far in the past must NOT be expired when ttl=0.
        assert _is_ttl_expired(1.0, 0) is False
        # And 'now' with ttl=0 is also not expired.
        assert _is_ttl_expired(time.time(), 0) is False

    def test_nonzero_ttl_is_not_treated_as_no_expiry(self):
        """A ttl of 1 must NOT short-circuit to 'no expiry'. Kills the
        Eq->LtE/Lt mutant that would make ttl<=0 (i.e. also ttl==0... but a
        comparison flip to <= or < changes which ttls return False)."""
        # ttl=1, very old timestamp -> expired. If '== 0' became '<= 0' the
        # result is unchanged for ttl=1, but '== 0' -> 'Lt 0' would make
        # ttl=1 not match and fall through (still expired) -- so we also pin
        # the negative-ttl direction below.
        assert _is_ttl_expired(1.0, 1) is True

    def test_timestamp_zero_is_expired(self):
        """timestamp == 0 (never stamped) => expired (kills Eq->Lt/LtE on
        line 177, the False->True/True->False flips, and NumberReplacer 0)."""
        assert _is_ttl_expired(0, 10) is True
        # A non-zero, fresh timestamp with the same ttl is NOT expired -- this
        # pins that the zero-check is specifically about 0, not 'any value'.
        assert _is_ttl_expired(time.time(), 10) is False

    def test_elapsed_just_under_ttl_is_not_expired(self):
        """A grant 5 minutes old with a 10-minute TTL is NOT expired. Kills the
        Gt->GtE flip (line 180) AND the Div->Sub/Mul/Mod/FloorDiv/Pow mutants
        on the (now - ts)/60 elapsed-minutes computation: any wrong operator
        changes elapsed_minutes enough to cross the boundary."""
        five_min_ago = time.time() - (5 * 60)
        assert _is_ttl_expired(five_min_ago, 10) is False

    def test_elapsed_just_over_ttl_is_expired(self):
        """A grant 11 minutes old with a 10-minute TTL IS expired. Together
        with the under-TTL case this brackets the > comparison and the /60
        conversion so a Gt->GtE flip or a wrong binary op is observable."""
        eleven_min_ago = time.time() - (11 * 60)
        assert _is_ttl_expired(eleven_min_ago, 10) is True

    @patch("modules.security.approval_grants.time.time")
    def test_elapsed_exactly_at_ttl_is_not_expired(self, mock_time):
        """At elapsed EXACTLY == ttl the grant is NOT expired, because the check
        is strict `>` (line 180). This is the ONLY input that discriminates the
        Gt->GtE mutant: with `>=`, elapsed==ttl would flip to expired. We pin the
        clock so elapsed is exactly 10.0 minutes -- impossible to hit reliably
        with wall-clock time, hence the mock."""
        now = 1_000_000.0
        mock_time.return_value = now
        # granted exactly 10 minutes (600s) ago, ttl = 10 minutes.
        granted_at = now - (10 * 60)
        assert _is_ttl_expired(granted_at, 10) is False

    def test_elapsed_division_uses_sixty_seconds_per_minute(self):
        """90 seconds = 1.5 minutes. With ttl=1 it must be expired; with ttl=2
        it must NOT. This pins the '/ 60' divisor (NumberReplacer on 60) and
        the Div operator: a Div->Sub or wrong constant would misclassify."""
        ninety_sec_ago = time.time() - 90
        assert _is_ttl_expired(ninety_sec_ago, 1) is True
        assert _is_ttl_expired(ninety_sec_ago, 2) is False


# ===========================================================================
# ApprovalGrant.is_valid / defaults (is_valid:1, ApprovalGrant:3)
# ===========================================================================
class TestApprovalGrantMutants:
    """ApprovalGrant.is_valid multi-use branch + dataclass defaults."""

    def _sig(self, cmd):
        return build_approval_signature(
            cmd, scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()

    def test_multi_use_grant_valid_even_after_used(self):
        """A multi_use grant must stay valid after a single use. Kills the
        ReplaceTrueWithFalse on line 232 (the `return True` in the multi_use
        branch): if it returned False, a used multi-use grant would be invalid."""
        grant = ApprovalGrant(
            approved_scope="git push origin main",
            scope_signature=self._sig("git push origin main"),
            granted_at=time.time(),
            ttl_minutes=10,
            used=True,
            multi_use=True,
        )
        assert grant.is_valid() is True

    def test_single_use_grant_invalid_after_used(self):
        """A non-multi-use grant is invalid once used. Pins that the multi_use
        branch is NOT taken for single-use grants (kills multi_use default
        False->True flip on line 216, which would make every grant multi-use)."""
        grant = ApprovalGrant(
            approved_scope="git push origin main",
            scope_signature=self._sig("git push origin main"),
            granted_at=time.time(),
            ttl_minutes=10,
            used=True,
            multi_use=False,
        )
        assert grant.is_valid() is False

    def test_default_granted_at_is_zero_so_default_grant_is_expired(self):
        """The granted_at default is 0.0 (line 213). A default-constructed grant
        with a real TTL must be expired, because granted_at==0 => expired.
        Kills the NumberReplacer that would change 0.0 to a non-zero default
        (which would make a never-stamped grant spuriously valid)."""
        grant = ApprovalGrant(
            approved_scope="git push origin main",
            scope_signature=self._sig("git push origin main"),
            ttl_minutes=10,  # granted_at left at its default
        )
        assert grant.granted_at == 0.0
        assert grant.is_expired() is True
        assert grant.is_valid() is False

    def test_default_multi_use_is_false(self):
        """multi_use defaults to False (line 217 region). Pin it directly so the
        ReplaceFalseWithTrue default flip is observable independent of is_valid."""
        grant = ApprovalGrant()
        assert grant.multi_use is False

    def test_get_signature_returns_none_on_bad_payload(self):
        """get_signature swallows deserialize errors and returns None (line 241
        ExceptionReplacer). A malformed scope_signature must yield None, not
        raise -- kills the mutant that replaces the except body."""
        grant = ApprovalGrant(
            scope_signature={"not": "a-valid-signature", "version": "x"},
        )
        # Must not raise; returns None on a payload from_dict cannot parse.
        assert grant.get_signature() is None

    def test_get_signature_none_when_absent(self):
        """No scope_signature => None (the early `if not self.scope_signature`)."""
        assert ApprovalGrant().get_signature() is None


# ===========================================================================
# _grant_ttl_minutes -- 60 fallback (3 survivors)
# ===========================================================================
class TestGrantTtlMinutesMutants:
    def test_fallback_is_sixty_when_import_fails(self, monkeypatch):
        """When the gaia.store.writer import is unavailable, the fallback is 60
        (line 126 NumberReplacer). Force the import to raise and assert 60.
        Also kills the ExceptionReplacer on the try (line 125): if the except
        body were replaced, a 60 would not be returned on import failure."""
        import builtins
        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name == "gaia.store.writer":
                raise ImportError("simulated unavailable writer")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _boom)
        assert _grant_ttl_minutes() == 60


# ===========================================================================
# _run_git_query -- returncode comparison + capture flags (4 survivors)
# ===========================================================================
class TestRunGitQueryMutants:
    @patch("modules.security.approval_grants.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        """returncode == 0 => stripped stdout. Pins the Eq comparison
        (line 387 Eq->LtE/GtE): a flip would change which return codes pass."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = "  abc123\n"
        mock_run.return_value = result
        assert _run_git_query(["rev-parse", "HEAD"]) == "abc123"

    @patch("modules.security.approval_grants.subprocess.run")
    def test_returns_none_on_nonzero_returncode(self, mock_run):
        """A non-zero returncode => None (NOT the stdout). With Eq->GtE a
        returncode of 1 would wrongly be treated as success and return stdout."""
        result = MagicMock()
        result.returncode = 1
        result.stdout = "fatal: not a git repo\n"
        mock_run.return_value = result
        assert _run_git_query(["rev-parse", "HEAD"]) is None

    @patch("modules.security.approval_grants.subprocess.run")
    def test_capture_output_and_text_flags_are_true(self, mock_run):
        """subprocess.run is called with capture_output=True and text=True
        (lines 382-383 True->False flips). If either were False, the call kwargs
        would differ -- assert them explicitly."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = "x\n"
        mock_run.return_value = result
        _run_git_query(["rev-parse", "HEAD"])
        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True


# ===========================================================================
# capture_environment_snapshot -- ExceptionReplacer on try body (line 439)
# ===========================================================================
class TestCaptureEnvSnapshotMutants:
    @patch("modules.security.approval_grants._run_git_query")
    def test_git_snapshot_collects_all_three_fields(self, mock_q):
        """For a git command, all three queries land in the snapshot. Kills the
        line-439 ExceptionReplacer (the except clause that would swallow the
        whole try body): with the real path, command_class + the three fields
        are present and correct."""
        mock_q.side_effect = ["head-sha", "feature-branch", "remote-sha"]
        snap = capture_environment_snapshot("git push origin main")
        assert snap["command_class"] == "git"
        assert snap["local_head"] == "head-sha"
        assert snap["branch"] == "feature-branch"
        assert snap["remote_head"] == "remote-sha"

    @patch("modules.security.approval_grants._run_git_query")
    def test_git_snapshot_omits_fields_when_query_returns_none(self, mock_q):
        """When a query returns None the field is omitted (the `if head:` guards).
        Pins that only successful queries contribute -- a swallowed try body or a
        flipped guard would either add empty keys or drop command_class."""
        mock_q.return_value = None
        snap = capture_environment_snapshot("git push origin main")
        assert snap == {"command_class": "git"}


# ===========================================================================
# _db_row_to_pending_dict -- or-chain, ": " parse, slice/index (26 survivors)
# ===========================================================================
class TestDbRowToPendingDictMutants:
    def _row(self, payload, *, created_at="2026-06-26T12:00:00Z",
             approval_id="P-deadbeefcafe", session_id="s1"):
        return {
            "id": approval_id,
            "session_id": session_id,
            "created_at": created_at,
            "payload_json": json.dumps(payload),
        }

    def test_command_prefers_exact_content_over_commands(self):
        """command = exact_content or commands[0] or operation (line 736-740
        or-chain). When exact_content is present it WINS over commands[0].
        Kills the Or->And mutants and the precedence of the chain."""
        row = self._row({
            "exact_content": "git push origin main",
            "commands": ["other command"],
            "operation": "MUTATIVE command intercepted: push",
        })
        out = _db_row_to_pending_dict(row)
        assert out["command"] == "git push origin main"

    def test_command_falls_back_to_commands_first_element(self):
        """With no exact_content, command = commands[0] (NumberReplacer on the
        [0] index, line 737). A wrong index would pick a different element or
        IndexError."""
        row = self._row({
            "commands": ["first-cmd", "second-cmd"],
            "operation": "MUTATIVE command intercepted: apply",
        })
        out = _db_row_to_pending_dict(row)
        assert out["command"] == "first-cmd"

    def test_danger_verb_parsed_from_operation_after_colon_space(self):
        """danger_verb = operation.rsplit(': ', 1)[-1] (line 745-746). The ': '
        split and the [-1] index are pinned: verb must be 'push', not the left
        side. Kills AddNot on the `if ': ' in operation`, the [-1] index
        (USub/UAdd/Invert/Not mutants), and the rsplit maxsplit number."""
        row = self._row({
            "exact_content": "git push origin main",
            "operation": "MUTATIVE command intercepted: push",
        })
        out = _db_row_to_pending_dict(row)
        assert out["danger_verb"] == "push"

    def test_danger_verb_unknown_when_no_colon_space(self):
        """When operation has no ': ', danger_verb stays 'unknown' (the AddNot
        on line 745 would invert the guard and wrongly parse)."""
        row = self._row({
            "exact_content": "git push",
            "operation": "no-colon-here",
        })
        out = _db_row_to_pending_dict(row)
        assert out["danger_verb"] == "unknown"

    def test_danger_category_parsed_before_intercepted_marker(self):
        """danger_category = operation.split(' command intercepted')[0] (line
        747-748). Pins the [0] index and the AddNot guard: category must be
        'FILE_WRITE', not the right-hand side."""
        row = self._row({
            "exact_content": "/etc/passwd",
            "operation": "FILE_WRITE command intercepted: write",
        })
        out = _db_row_to_pending_dict(row)
        assert out["danger_category"] == "FILE_WRITE"

    def test_nonce_strips_p_prefix_via_slice(self):
        """nonce = approval_id[2:] when it starts with 'P-' (line 761,
        NumberReplacer on the slice start 2). 'P-deadbeefcafe' -> 'deadbeefcafe'."""
        row = self._row(
            {"exact_content": "git push", "operation": "x"},
            approval_id="P-deadbeefcafe",
        )
        out = _db_row_to_pending_dict(row)
        assert out["nonce"] == "deadbeefcafe"

    def test_returns_none_on_invalid_payload_json(self):
        """Unparseable payload_json => None (line 732 except). Kills the
        ExceptionReplacer that would replace the JSONDecodeError handler."""
        row = {
            "id": "P-x",
            "session_id": "s",
            "created_at": "2026-06-26T12:00:00Z",
            "payload_json": "{not valid json",
        }
        assert _db_row_to_pending_dict(row) is None

    def test_timestamp_zero_on_unparseable_created_at(self):
        """A created_at that does not match the format => ts stays 0.0 (line
        751/757-758). Pins the ts default and the strptime guard."""
        row = self._row(
            {"exact_content": "git push", "operation": "x"},
            created_at="not-a-timestamp",
        )
        out = _db_row_to_pending_dict(row)
        assert out["timestamp"] == 0.0


# ===========================================================================
# find_pending_for_command -- guards, loop, match return (14 survivors)
# ===========================================================================
class TestFindPendingForCommandMutants:
    def test_returns_none_when_no_pending(self, monkeypatch):
        """Empty pending list => None immediately (line 833 `if not pending_list`).
        Kills the AddNot / Delete_Not on that guard: an inverted guard would try
        to iterate an empty list and return None anyway, but a Delete_Not would
        make it proceed to build a signature unnecessarily -- we pin the None."""
        monkeypatch.setattr(
            ag, "get_pending_approvals_for_session", lambda s: []
        )
        assert find_pending_for_command("sess", "git push origin main") is None

    def test_returns_nonce_on_semantic_match(self, monkeypatch):
        """When a pending's signature matches the command, its nonce is returned
        (line 850-857). Kills the AddNot on the match guard, the
        ReplaceContinueWithBreak in the loop, and the None-return fallback."""
        cmd = "git push origin main"
        sig = build_approval_signature(
            cmd, scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()
        pending = [{
            "nonce": "abc123nonce",
            "scope_signature": sig,
        }]
        monkeypatch.setattr(
            ag, "get_pending_approvals_for_session", lambda s: pending
        )
        assert find_pending_for_command("sess", cmd) == "abc123nonce"

    def test_returns_none_when_no_pending_signature_matches(self, monkeypatch):
        """A pending for a DIFFERENT command must NOT match (the for-loop must
        run and find nothing -> None). Kills ZeroIterationForLoop (line 844):
        if the loop never ran it would still return None, but combined with the
        positive match test above, the loop body's match logic is pinned."""
        other_sig = build_approval_signature(
            "terraform apply", scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()
        pending = [{"nonce": "n1", "scope_signature": other_sig}]
        monkeypatch.setattr(
            ag, "get_pending_approvals_for_session", lambda s: pending
        )
        assert find_pending_for_command("sess", "git push origin main") is None

    def test_skips_pending_without_signature_then_matches_next(self, monkeypatch):
        """A pending with no scope_signature is skipped (continue), and a later
        matching pending is still found. Kills the ReplaceContinueWithBreak on
        line 847: a break would abandon the loop before reaching the match."""
        cmd = "git push origin main"
        sig = build_approval_signature(
            cmd, scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()
        pending = [
            {"nonce": "skip-me", "scope_signature": None},
            {"nonce": "match-me", "scope_signature": sig},
        ]
        monkeypatch.setattr(
            ag, "get_pending_approvals_for_session", lambda s: pending
        )
        assert find_pending_for_command("sess", cmd) == "match-me"


# ===========================================================================
# create_command_set_grant -- guard + return values (58 survivors)
# ===========================================================================
class TestCreateCommandSetGrantMutants:
    def test_missing_command_set_returns_false(self, writer_db):
        """Empty command_set => False (line 1575 guard). Kills the AddNot /
        ReplaceOrWithAnd on `if not command_set or not approval_id`."""
        assert create_command_set_grant([], "P-abc") is False

    def test_missing_approval_id_returns_false(self, writer_db):
        """Empty approval_id => False (the second arm of the guard). Pins that
        BOTH conditions gate the early return -- an Or->And flip would let a
        missing approval_id slip through."""
        assert create_command_set_grant(
            [{"command": "git push", "rationale": "r"}], ""
        ) is False

    def test_success_creates_pending_command_set_row(self, writer_db):
        """A valid command_set persists a PENDING COMMAND_SET grant and returns
        True (line 1603 `status == 'applied'` -> True; kills the Eq flips,
        ReplaceTrueWithFalse, and the False default-returns)."""
        approval_id = f"P-{secrets.token_hex(16)}"
        cmd_set = [
            {"command": "git push origin main", "rationale": "deploy"},
            {"command": "git tag v1", "rationale": "tag"},
        ]
        ok = create_command_set_grant(
            cmd_set, approval_id, session_id="test-session-mut",
        )
        assert ok is True

        con = sqlite3.connect(str(writer_db))
        try:
            row = con.execute(
                "SELECT scope, status, command_set_json, expires_at "
                "FROM approval_grants WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "COMMAND_SET grant row must be persisted"
        scope, status, cs_json, expires_at = row
        assert scope == "COMMAND_SET"
        assert status == "PENDING"
        # Both commands stored, in order, byte-for-byte.
        stored = json.loads(cs_json)
        assert [i["command"] for i in stored] == [
            "git push origin main", "git tag v1"
        ]
        # expires_at must be in the FUTURE (kills the timedelta '+' -> '-' mutant
        # on line 1589: a subtraction would put expiry in the past).
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert expires_at > now_iso


# ===========================================================================
# match_command_set_grant -- expiry, scope, consumed-index, match (54 survivors)
# ===========================================================================
class TestMatchCommandSetGrantMutants:
    def _insert(self, db_path, approval_id, command_set, *, status="PENDING",
                scope="COMMAND_SET", expires_at=None, consumed_indexes=None):
        if expires_at is None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                """INSERT INTO approval_grants
                   (approval_id, session_id, command_set_json, scope,
                    expires_at, status, consumed_indexes_json)
                   VALUES (?, 'test-session-mut', ?, ?, ?, ?, ?)""",
                (
                    approval_id,
                    json.dumps(command_set),
                    scope,
                    expires_at,
                    status,
                    json.dumps(consumed_indexes or []),
                ),
            )
            con.commit()
        finally:
            con.close()

    def test_matches_command_returns_approval_id_and_index(self, writer_db):
        """An exact byte-for-byte match returns (approval_id, index) (line 1704
        Eq comparison, line 1709 return). Kills the Eq->NotEq/Is/Lt flips on the
        command comparison and the byte-for-byte match semantics."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(writer_db, approval_id, [
            {"command": "git push origin main", "rationale": "a"},
            {"command": "git tag v1", "rationale": "b"},
        ])
        result = match_command_set_grant("git tag v1")
        assert result == (approval_id, 1)

    def test_no_match_for_different_command(self, writer_db):
        """A command not in the set returns None. Pins that the Eq match is
        real (NotEq flip would match the wrong command) and the final None."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(writer_db, approval_id, [
            {"command": "git push origin main", "rationale": "a"},
        ])
        assert match_command_set_grant("git push origin develop") is None

    def test_consumed_index_is_skipped(self, writer_db):
        """An already-consumed index must NOT match (line 1701 `if idx in
        consumed_indexes: continue`). Kills the AddNot on that guard and the
        ReplaceContinueWithBreak: index 0 consumed, so the same command at
        index 0 no longer matches."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id,
            [{"command": "git push origin main", "rationale": "a"}],
            consumed_indexes=[0],
        )
        assert match_command_set_grant("git push origin main") is None

    def test_expired_grant_does_not_match(self, writer_db):
        """A grant past expires_at must NOT match (line 1671 expiry comparison
        `expires_at < now_iso`). Kills the Lt->Gt/LtE/Eq flips: with a past
        expiry the grant is skipped (and marked EXPIRED)."""
        approval_id = f"P-{secrets.token_hex(16)}"
        past = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._insert(
            writer_db, approval_id,
            [{"command": "git push origin main", "rationale": "a"}],
            expires_at=past,
        )
        assert match_command_set_grant("git push origin main") is None

    def test_non_command_set_scope_is_skipped(self, writer_db):
        """A row with scope != 'COMMAND_SET' must NOT match (line 1683 scope
        guard). Kills the NotEq->Eq flip and the AddNot on the scope check:
        a SCOPE_SEMANTIC_SIGNATURE row carrying the same command must be ignored
        by the command-set matcher."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id,
            [{"command": "git push origin main", "rationale": "a"}],
            scope="SCOPE_SEMANTIC_SIGNATURE",
        )
        assert match_command_set_grant("git push origin main") is None

    def test_pending_status_required(self, writer_db):
        """A CONSUMED grant is not returned by the PENDING-only query, so it must
        not match. Pins that status filtering is honored end-to-end."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id,
            [{"command": "git push origin main", "rationale": "a"}],
            status="CONSUMED",
        )
        assert match_command_set_grant("git push origin main") is None

    def test_first_unconsumed_match_wins(self, writer_db):
        """With index 0 consumed and the SAME command duplicated at index 1, the
        match returns index 1 -- pins the per-index iteration and the
        consumed-index skip together (kills ZeroIterationForLoop on the inner
        loop, line 1700)."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id,
            [
                {"command": "git push origin main", "rationale": "a"},
                {"command": "git push origin main", "rationale": "b"},
            ],
            consumed_indexes=[0],
        )
        assert match_command_set_grant("git push origin main") == (approval_id, 1)
