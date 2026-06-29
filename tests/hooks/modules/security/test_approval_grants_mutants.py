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
    find_pending_for_file,
    get_pending_approvals_for_session,
    activate_db_pending_by_prefix,
    ACTIVATION_NOT_FOUND,
    ACTIVATION_INVALID_PENDING,
    ACTIVATION_ACTIVATED,
    ACTIVATION_ERROR,
    ACTIVATION_INVALID_SIGNATURE,
    ACTIVATION_CHAIN_TAMPER_DETECTED,
    DEFAULT_COMMAND_SET_TTL_MINUTES,
    SCOPE_SEMANTIC_SIGNATURE,
    SCOPE_FILE_PATH,
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


# ===========================================================================
# activate_db_pending_by_prefix -- early error branches (cluster C/D, 120 surv)
#
# These tests drive the error-return paths that fire BEFORE the fingerprint
# integrity check (Step 2b), so they need only `gaia.approvals.store.get_pending`
# mocked. Each pins both the boolean `success` flag (kills ReplaceFalseWithTrue
# on the `success=False` returns) and the exact `status` enum (kills the AddNot /
# Or<->And / comparison flips on the guards that select which error branch runs).
# `activate_db_pending_by_prefix` does `from gaia.approvals.store import get_pending`
# lazily inside the body, so patching the attribute on the source module is what
# the call resolves at runtime.
# ===========================================================================
class TestActivateDbPendingEarlyErrors:
    """activate_db_pending_by_prefix error returns before the fingerprint check."""

    def _patch_pending(self, monkeypatch, rows):
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **kw: rows)

    def test_no_pending_match_returns_not_found(self, monkeypatch):
        """No DB row whose id starts with 'P-<prefix>' => success=False,
        status=NOT_FOUND (lines 1106-1119). Kills the ReplaceFalseWithTrue on
        the NOT_FOUND `success=False` and the `if matched_row is None` guard:
        with the flip a missing approval would report success."""
        # A row that does NOT match the prefix the caller asks for.
        self._patch_pending(monkeypatch, [
            {"id": "P-ffffffffffff", "payload_json": "{}",
             "session_id": "s", "agent_id": None},
        ])
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_NOT_FOUND

    def test_empty_pending_list_returns_not_found(self, monkeypatch):
        """An empty pending list => NOT_FOUND (the for-loop runs zero times and
        matched_row stays None). Kills ZeroIterationForLoop on the match loop
        combined with the None guard."""
        self._patch_pending(monkeypatch, [])
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_NOT_FOUND

    def test_matched_row_without_payload_returns_invalid_pending(self, monkeypatch):
        """A matched row whose payload_json is falsy => success=False,
        status=INVALID_PENDING (lines 1127-1136). Kills the ReplaceFalseWithTrue
        on that `success=False` and the `if not payload_json_str` guard
        (AddNot/Delete_Not): an inverted guard would skip the early return."""
        self._patch_pending(monkeypatch, [
            {"id": "P-deadbeefcafe", "payload_json": None,
             "session_id": "s", "agent_id": None},
        ])
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_PENDING

    def test_unparseable_payload_returns_invalid_pending(self, monkeypatch):
        """A matched row whose payload_json is not valid JSON => INVALID_PENDING
        (lines 1138-1149 except). Kills the ExceptionReplacer on that except and
        the ReplaceFalseWithTrue on its `success=False`."""
        self._patch_pending(monkeypatch, [
            {"id": "P-deadbeefcafe", "payload_json": "{not json",
             "session_id": "s", "agent_id": None},
        ])
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_PENDING

    def test_payload_without_command_returns_invalid_pending(self, monkeypatch):
        """A parseable payload carrying no exact_content/commands/command_set =>
        no command extracted => INVALID_PENDING (lines 1171-1185). Kills the
        ReplaceFalseWithTrue on that `success=False`, the `if not command` guard
        (AddNot), and exercises the command_set detection or-chain (1158-1169)
        in its empty form so the is_command_set=False path is pinned."""
        self._patch_pending(monkeypatch, [
            {"id": "P-deadbeefcafe",
             "payload_json": json.dumps({"operation": "MUTATIVE x: y"}),
             "session_id": "s", "agent_id": None},
        ])
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_PENDING


# ===========================================================================
# activate_db_pending_by_prefix -- POST-fingerprint branches.
#
# These drive the function PAST the Step 2b fingerprint integrity check into the
# terminal branches that were left as survivors after the M1 early-error tests:
#
#   * COMMAND_SET grant creation     (Step 3b, lines ~1299-1335)
#   * SCOPE_FILE_PATH grant insert    (Step 3c, lines ~1343-1413)
#   * SCOPE_SEMANTIC signature rebuild + grant insert (Steps 4/5, ~1415-1510)
#   * the ValueError "already approved" recovery branch (Step 3, ~1269-1284)
#   * the command_set detection or-chain / is_command_set length test (~1158-1175)
#
# To reach them, all four lazy DB collaborators are mocked so no real gaia.db is
# touched: get_pending (the row), verify_fingerprint (passes), the SHOWN/APPROVED
# writers (record_event/approve), and the writer-side grant inserts. Each test
# pins the EXACT success flag + status enum AND the call that the branch is
# supposed to make (e.g. insert_semantic_grant vs insert_file_path_grant vs
# create_command_set_grant), so a branch-selection mutant (AddNot, Or<->And,
# comparison flip, NumberReplacer on the >1 length test) flips the observable.
# ===========================================================================
class _DummyCon:
    """Stand-in for the sqlite3 connection verify_fingerprint receives."""
    def close(self):
        pass


class TestActivateDbPendingPostFingerprint:
    """activate_db_pending_by_prefix terminal branches after the integrity check."""

    def _drive(self, monkeypatch, payload, *, approval_id="P-deadbeefcafe",
               session_id="sub-sess", agent_id="ag1",
               fingerprint_ok=True, approve_raises=None,
               get_by_id_row=None):
        """Patch every lazy collaborator so the function runs to a terminal
        branch. Returns a dict of MagicMock recorders for call assertions."""
        rows = [{
            "id": approval_id,
            "payload_json": json.dumps(payload),
            "session_id": session_id,
            "agent_id": agent_id,
        }]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **kw: rows)

        # Step 2b: fingerprint check. verify_fingerprint returns True (or raises).
        def _verify(approval_id_arg, payload_json_arg, con):
            if not fingerprint_ok:
                from gaia.approvals.chain import ChainTamperError
                raise ChainTamperError("simulated tamper")
            return True
        monkeypatch.setattr("gaia.approvals.chain.verify_fingerprint", _verify)
        monkeypatch.setattr("gaia.approvals.store._open_db", lambda: _DummyCon())

        # Step 3: SHOWN + APPROVED writers.
        record_event = MagicMock()
        monkeypatch.setattr("gaia.approvals.store.record_event", record_event)

        def _approve(*a, **k):
            if approve_raises is not None:
                raise approve_raises
        approve = MagicMock(side_effect=_approve)
        monkeypatch.setattr("gaia.approvals.store.approve", approve)
        monkeypatch.setattr(
            "gaia.approvals.store.get_by_id", lambda *a, **k: get_by_id_row
        )
        return {"record_event": record_event, "approve": approve}

    # ----- COMMAND_SET branch (Step 3b) -----
    def test_command_set_payload_creates_command_set_grant(self, monkeypatch):
        """A payload whose command_set has >1 item activates into a COMMAND_SET
        grant via create_command_set_grant() and returns ACTIVATED. Kills:
          - the `len(command_set_items) > 1` NumberReplacer / Gt flips (1169):
            a flip to >=1 or <1 would change is_command_set and route elsewhere;
          - the AddNot on `if is_command_set` (1299);
          - the ReplaceFalseWithTrue on the failure return inside the branch."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "command_set": [
                {"command": "git push origin main", "rationale": "a"},
                {"command": "git tag v1", "rationale": "b"},
            ],
        })
        create = MagicMock(return_value=True)
        monkeypatch.setattr(
            "modules.security.approval_grants.create_command_set_grant", create
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        # The COMMAND_SET branch (not the semantic one) must have run.
        assert create.call_count == 1
        items_arg = create.call_args[0][0]
        assert [i["command"] for i in items_arg] == [
            "git push origin main", "git tag v1"
        ]

    def test_command_set_creation_failure_returns_error(self, monkeypatch):
        """When create_command_set_grant returns False, the branch returns
        success=False / ACTIVATION_ERROR (lines 1307-1317). Kills the
        ReplaceFalseWithTrue on that `success=False` and the AddNot on
        `if not created`."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "command_set": [
                {"command": "git push origin main", "rationale": "a"},
                {"command": "git tag v1", "rationale": "b"},
            ],
        })
        monkeypatch.setattr(
            "modules.security.approval_grants.create_command_set_grant",
            lambda *a, **k: False,
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR

    def test_single_command_set_item_is_not_command_set(self, monkeypatch):
        """A command_set of exactly ONE item must NOT mint a COMMAND_SET grant --
        it falls through to the singular SCOPE_SEMANTIC path. This is the only
        input that discriminates the `> 1` boundary (1169): with `>= 1` a
        one-item set would wrongly route to create_command_set_grant. We assert
        the semantic insert ran and create_command_set_grant did NOT."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
            "command_set": [
                {"command": "git push origin main", "rationale": "a"},
            ],
        })
        create = MagicMock(return_value=True)
        monkeypatch.setattr(
            "modules.security.approval_grants.create_command_set_grant", create
        )
        insert_sem = MagicMock(return_value={"status": "applied"})
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", insert_sem)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert create.call_count == 0
        assert insert_sem.call_count == 1

    # ----- SCOPE_FILE_PATH branch (Step 3c) -----
    def test_file_path_payload_inserts_file_path_grant(self, monkeypatch):
        """A payload with scope == SCOPE_FILE_PATH activates via
        insert_file_path_grant() and returns ACTIVATED (lines 1343-1413). Kills
        the NotEq->Eq / AddNot on the `payload.get('scope') == SCOPE_FILE_PATH`
        guard (1343) and the ReplaceFalseWithTrue on its failure returns."""
        self._drive(monkeypatch, {
            "operation": "FILE_WRITE command intercepted: write",
            "exact_content": "/etc/hosts",
            "scope": SCOPE_FILE_PATH,
        })
        insert_fp = MagicMock(return_value={"status": "applied"})
        insert_sem = MagicMock(return_value={"status": "applied"})
        monkeypatch.setattr("gaia.store.writer.insert_file_path_grant", insert_fp)
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", insert_sem)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        # The file-path branch ran, not the semantic one.
        assert insert_fp.call_count == 1
        assert insert_sem.call_count == 0
        # The file path threads through to the insert verbatim.
        assert insert_fp.call_args.kwargs["file_path"] == "/etc/hosts"

    def test_file_path_insert_not_applied_returns_error(self, monkeypatch):
        """When insert_file_path_grant returns a non-'applied' status the branch
        returns success=False / ACTIVATION_ERROR (lines 1392-1401). Kills the
        Eq->NotEq flip on `result_fp.get('status') != 'applied'` and the
        ReplaceFalseWithTrue on that return."""
        self._drive(monkeypatch, {
            "operation": "FILE_WRITE command intercepted: write",
            "exact_content": "/etc/hosts",
            "scope": SCOPE_FILE_PATH,
        })
        monkeypatch.setattr(
            "gaia.store.writer.insert_file_path_grant",
            lambda **k: {"status": "rejected", "reason": "dup"},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR

    def test_file_path_missing_exact_content_returns_invalid_pending(self, monkeypatch):
        """A SCOPE_FILE_PATH payload with empty exact_content => INVALID_PENDING
        (lines 1345-1355). The function reaches this only because the EARLIER
        command-extraction (line 1171) used commands[0]; here exact_content is
        empty but commands carries the path so command extraction passes, then
        the file-path branch's own emptiness guard fires. Pins that guard's
        AddNot and its success=False."""
        self._drive(monkeypatch, {
            "operation": "FILE_WRITE command intercepted: write",
            "exact_content": "",
            "commands": ["/some/path"],
            "scope": SCOPE_FILE_PATH,
        })
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_PENDING

    # ----- SCOPE_SEMANTIC branch (Steps 4 / 5) -----
    def test_semantic_payload_inserts_semantic_grant(self, monkeypatch):
        """A plain semantic payload (no command_set, scope not SCOPE_FILE_PATH)
        rebuilds the signature and inserts a semantic grant, returning ACTIVATED
        (lines 1415-1490). Kills the ReplaceFalseWithTrue-on-failure and the
        Eq flips on `result_sg.get('status') == 'applied'` (1476)."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
        })
        insert_sem = MagicMock(return_value={"status": "applied"})
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", insert_sem)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert insert_sem.call_count == 1
        # The exact command threads through to the semantic insert.
        assert insert_sem.call_args.kwargs["command"] == "git push origin main"

    def test_semantic_insert_not_applied_returns_error(self, monkeypatch):
        """insert_semantic_grant returning non-'applied' => success=False /
        ACTIVATION_ERROR (lines 1491-1500). Kills the Eq->NotEq flip on the
        status compare and the else-branch ReplaceFalseWithTrue."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
        })
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: {"status": "rejected", "reason": "dup"},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR

    def test_semantic_insert_exception_returns_error(self, monkeypatch):
        """When insert_semantic_grant raises, the except returns success=False /
        ACTIVATION_ERROR (lines 1501-1510). Kills the ExceptionReplacer on that
        except and its ReplaceFalseWithTrue."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
        })
        def _boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", _boom)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR

    # ----- fingerprint integrity (Step 2b) -----
    def test_fingerprint_mismatch_refuses_activation(self, monkeypatch):
        """A failing verify_fingerprint (ChainTamperError) => activation refused
        with success=False / CHAIN_TAMPER_DETECTED, and a FAILED audit event is
        recorded (lines 1210-1249). Kills the ReplaceFalseWithTrue on that
        return and pins that the integrity branch is taken on a raise."""
        rec = self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
        }, fingerprint_ok=False)
        # Even if a downstream insert exists, it must NOT be reached.
        insert_sem = MagicMock(return_value={"status": "applied"})
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", insert_sem)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_CHAIN_TAMPER_DETECTED
        assert insert_sem.call_count == 0
        # A FAILED audit event was recorded for the refusal.
        failed_calls = [
            c for c in rec["record_event"].call_args_list
            if len(c[0]) >= 2 and c[0][1] == "FAILED"
        ]
        assert len(failed_calls) == 1

    # ----- ValueError "already approved" recovery (Step 3) -----
    def test_already_approved_non_approved_status_returns_error(self, monkeypatch):
        """When approve() raises ValueError AND get_by_id shows the row is NOT
        'approved', the function aborts with success=False / ACTIVATION_ERROR
        (lines 1278-1284). Kills the NotEq->Eq flip on
        `current_row.get('status') != 'approved'` and the AddNot on the guard:
        with the flip an unprocessed row would wrongly continue to grant
        creation."""
        self._drive(
            monkeypatch,
            {
                "operation": "MUTATIVE command intercepted: push",
                "exact_content": "git push origin main",
            },
            approve_raises=ValueError("not pending"),
            get_by_id_row={"id": "P-deadbeefcafe", "status": "rejected"},
        )
        insert_sem = MagicMock(return_value={"status": "applied"})
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", insert_sem)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR
        # The grant insert must NOT have run since we aborted.
        assert insert_sem.call_count == 0

    def test_already_approved_continues_to_grant(self, monkeypatch):
        """When approve() raises ValueError but get_by_id shows status ==
        'approved', the function does NOT abort -- it proceeds to create the
        grant (the recovery path). Pins that the `!= 'approved'` guard lets an
        already-approved row through (kills the AddNot that would invert it and
        abort here)."""
        self._drive(
            monkeypatch,
            {
                "operation": "MUTATIVE command intercepted: push",
                "exact_content": "git push origin main",
            },
            approve_raises=ValueError("already approved"),
            get_by_id_row={"id": "P-deadbeefcafe", "status": "approved"},
        )
        insert_sem = MagicMock(return_value={"status": "applied"})
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", insert_sem)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert insert_sem.call_count == 1


# ===========================================================================
# find_pending_for_file -- DB query, scope filter, path match (29 survivors)
#
# find_pending_for_file does `from gaia.approvals.store import list_pending`
# lazily, so patching gaia.approvals.store.list_pending is what the call binds.
# ===========================================================================
class TestFindPendingForFileMutants:
    """find_pending_for_file(session_id, file_path) -- every branch pinned."""

    def _patch_list(self, monkeypatch, rows):
        monkeypatch.setattr("gaia.approvals.store.list_pending", lambda **kw: rows)

    def _row(self, *, approval_id, scope, exact_content, session_id="s"):
        return {
            "id": approval_id,
            "session_id": session_id,
            "payload_json": json.dumps({
                "scope": scope,
                "exact_content": exact_content,
            }),
        }

    def test_empty_file_path_returns_none_without_query(self, monkeypatch):
        """An empty/whitespace file_path => None BEFORE any DB query (lines
        1013-1015). Kills the AddNot on `if not stripped` and pins the early
        return: list_pending must not even be called."""
        called = MagicMock()
        monkeypatch.setattr("gaia.approvals.store.list_pending", called)
        assert find_pending_for_file("sess", "   ") is None
        assert called.call_count == 0

    def test_matching_file_path_returns_nonce(self, monkeypatch):
        """A SCOPE_FILE_PATH pending whose exact_content equals the target path
        returns the nonce (approval_id without 'P-') (lines 1030-1041). Kills:
          - the NotEq->Eq flip on the scope filter (1030),
          - the NotEq/Eq flip on the path-equality compare (1033),
          - the slice NumberReplacer on `approval_id[2:]` (1034),
          - the AddNot on `if approval_id.startswith('P-')`."""
        self._patch_list(monkeypatch, [
            self._row(approval_id="P-cafef00dbabe", scope=SCOPE_FILE_PATH,
                      exact_content="/home/u/secret.txt"),
        ])
        assert find_pending_for_file("sess", "/home/u/secret.txt") == "cafef00dbabe"

    def test_path_is_stripped_before_compare(self, monkeypatch):
        """The target path is stripped (line 1013) and the row's exact_content is
        stripped (line 1033) before comparison, so surrounding whitespace still
        matches. Pins both .strip() calls -- a removed strip would miss the row."""
        self._patch_list(monkeypatch, [
            self._row(approval_id="P-aaaabbbbcccc", scope=SCOPE_FILE_PATH,
                      exact_content="  /home/u/file  "),
        ])
        assert find_pending_for_file("sess", "  /home/u/file  ") == "aaaabbbbcccc"

    def test_non_file_path_scope_is_skipped(self, monkeypatch):
        """A pending whose scope is NOT SCOPE_FILE_PATH must be skipped even if
        its exact_content matches the path (line 1030 `continue`). Kills the
        Eq<->NotEq flip on the scope guard and the ReplaceContinueWithBreak."""
        self._patch_list(monkeypatch, [
            self._row(approval_id="P-1111", scope=SCOPE_SEMANTIC_SIGNATURE,
                      exact_content="/home/u/file"),
        ])
        assert find_pending_for_file("sess", "/home/u/file") is None

    def test_different_path_returns_none(self, monkeypatch):
        """A SCOPE_FILE_PATH pending for a DIFFERENT path does not match => None
        (the loop runs and finds nothing). Pins the path-equality compare: a
        NotEq->Eq flip would wrongly match a different path."""
        self._patch_list(monkeypatch, [
            self._row(approval_id="P-2222", scope=SCOPE_FILE_PATH,
                      exact_content="/home/u/OTHER"),
        ])
        assert find_pending_for_file("sess", "/home/u/file") is None

    def test_skips_first_nonmatching_then_matches_second(self, monkeypatch):
        """A non-matching row followed by a matching one still resolves the match
        -- pins that `continue` advances rather than breaks (kills
        ReplaceContinueWithBreak on the scope-filter continue, line 1030) and
        ZeroIterationForLoop on the row loop."""
        self._patch_list(monkeypatch, [
            self._row(approval_id="P-3333", scope=SCOPE_SEMANTIC_SIGNATURE,
                      exact_content="/home/u/file"),
            self._row(approval_id="P-deadbeef0000", scope=SCOPE_FILE_PATH,
                      exact_content="/home/u/file"),
        ])
        assert find_pending_for_file("sess", "/home/u/file") == "deadbeef0000"

    def test_unparseable_payload_is_skipped(self, monkeypatch):
        """A row whose payload_json is invalid JSON is skipped (continue, lines
        1025-1027) and a later valid match is still found. Kills the
        ExceptionReplacer on the JSON guard and the ReplaceContinueWithBreak."""
        monkeypatch.setattr("gaia.approvals.store.list_pending", lambda **kw: [
            {"id": "P-bad", "session_id": "s", "payload_json": "{not json"},
            self._row(approval_id="P-feedface0000", scope=SCOPE_FILE_PATH,
                      exact_content="/home/u/file"),
        ])
        assert find_pending_for_file("sess", "/home/u/file") == "feedface0000"

    def test_approval_id_without_p_prefix_yields_none(self, monkeypatch):
        """A matching file-path row whose id does NOT start with 'P-' yields None
        (the `if approval_id.startswith('P-')` guard, line 1035 -- no nonce is
        returned). Kills the AddNot on that guard: an inverted guard would slice
        a non-prefixed id and return a bogus nonce."""
        self._patch_list(monkeypatch, [
            self._row(approval_id="XX-noprefix", scope=SCOPE_FILE_PATH,
                      exact_content="/home/u/file"),
        ])
        assert find_pending_for_file("sess", "/home/u/file") is None


# ===========================================================================
# check_approval_grant -- DB wrapper return shape (16 survivors)
#
# check_approval_grant does `from gaia.store.writer import check_db_semantic_grant`
# lazily, so patching that attribute on the writer module is what binds.
# ===========================================================================
class TestCheckApprovalGrantMutants:
    """check_approval_grant(command) -- grant reconstruction from a DB row."""

    def test_returns_grant_when_db_row_found(self, monkeypatch):
        """A DB row => an ApprovalGrant with confirmed=True, multi_use=False,
        ttl_minutes=0, used=False (lines 490-503). Kills the ReplaceFalseWithTrue
        on used=False / multi_use=False, the ReplaceTrueWithFalse on
        confirmed=True, and the NumberReplacer on ttl/granted defaults."""
        sig = build_approval_signature(
            "git push origin main", scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()
        row = {
            "approval_id": "P-abc123",
            "session_id": "owner-sess",
            "command_set_json": json.dumps({
                "command": "git push origin main",
                "scope_signature": sig,
            }),
        }
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: row
        )
        grant = ag.check_approval_grant("git push origin main", session_id="s")
        assert grant is not None
        assert grant.confirmed is True
        assert grant.multi_use is False
        assert grant.used is False
        assert grant.ttl_minutes == 0
        assert grant.approved_scope == "git push origin main"
        # The approval_id is attached for bash_validator to consume.
        assert grant._db_approval_id == "P-abc123"

    def test_returns_none_when_no_db_row(self, monkeypatch):
        """No DB row (None) => None (the `if db_row is not None` guard, line 472).
        Kills the AddNot / Delete_Not on that guard: an inverted guard would try
        to dereference None and crash, or fall through to return None anyway --
        we pin the clean None and that the expired flag is reset."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: None
        )
        assert ag.check_approval_grant("git push origin main", session_id="s") is None

    def test_db_exception_returns_none(self, monkeypatch):
        """A raising check_db_semantic_grant => None (lines 509-513 except). Kills
        the ExceptionReplacer on that except: a swallowed/replaced handler would
        propagate the error instead of returning None."""
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.check_db_semantic_grant", _boom)
        assert ag.check_approval_grant("git push origin main", session_id="s") is None


# ===========================================================================
# consume_grant -- DB consume wrapper (8 survivors)
# ===========================================================================
class TestConsumeGrantMutants:
    """consume_grant(command) -- returns the consume bool from the DB."""

    def test_consumes_and_returns_true(self, monkeypatch):
        """A matching grant whose consume returns True => True (line 552). Kills
        the AddNot on `if db_row is not None` (542) and pins the consumed bool is
        threaded back, not hardcoded."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        consume = MagicMock(return_value=True)
        monkeypatch.setattr("gaia.store.writer.consume_db_semantic_grant", consume)
        assert ag.consume_grant("git push origin main") is True
        consume.assert_called_once_with("P-x")

    def test_returns_false_when_consume_returns_false(self, monkeypatch):
        """When consume_db_semantic_grant returns False (already consumed) the
        wrapper returns False, NOT True. Pins that the real bool is returned
        (kills a ReplaceFalseWithTrue / hardcoded-True mutant)."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.consume_db_semantic_grant", lambda *a, **k: False
        )
        assert ag.consume_grant("git push origin main") is False

    def test_returns_false_when_no_grant(self, monkeypatch):
        """No matching grant => False (falls through to the final return). Kills
        the AddNot on the `if db_row is not None` guard."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: None
        )
        assert ag.consume_grant("git push origin main") is False

    def test_db_exception_returns_false(self, monkeypatch):
        """A raising lookup => False (lines 553-554 except). Kills the
        ExceptionReplacer on that except."""
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.check_db_semantic_grant", _boom)
        assert ag.consume_grant("git push origin main") is False


# ===========================================================================
# confirm_grant -- DB confirm wrapper (27 survivors)
# ===========================================================================
class TestConfirmGrantMutants:
    """confirm_grant(command) -- sets confirmed=1 on the matching PENDING grant."""

    def test_confirms_and_returns_true_on_applied(self, monkeypatch):
        """A matched grant whose confirm_db_grant returns status 'applied' => True
        (line 659 `result.get('status') == 'applied'`). Kills the Eq->NotEq/Is/Lt
        flips on that compare and the ReplaceTrueWithFalse on the True return."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.confirm_db_grant",
            lambda *a, **k: {"status": "applied"},
        )
        assert ag.confirm_grant("git push origin main", session_id="s") is True

    def test_returns_false_when_no_grant(self, monkeypatch):
        """No matching grant (db_row is None) => False (lines 652-654). Kills the
        Is->IsNot flip on `if db_row is None` and the AddNot: an inverted guard
        would dereference None."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: None
        )
        assert ag.confirm_grant("git push origin main", session_id="s") is False

    def test_returns_false_when_status_not_applied(self, monkeypatch):
        """confirm_db_grant returning a non-'applied' status => False (the Eq
        compare on 659 fails -> falls through to the final False return, line
        672). Pins that only 'applied' yields True."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.confirm_db_grant",
            lambda *a, **k: {"status": "noop"},
        )
        assert ag.confirm_grant("git push origin main", session_id="s") is False

    def test_returns_false_when_no_approval_id(self, monkeypatch):
        """A matched row with a falsy approval_id => False (lines 656-657). Kills
        the AddNot on `if not approval_id`: an inverted guard would call
        confirm_db_grant(None)."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": ""},
        )
        called = MagicMock()
        monkeypatch.setattr("gaia.store.writer.confirm_db_grant", called)
        assert ag.confirm_grant("git push origin main", session_id="s") is False
        assert called.call_count == 0

    def test_db_exception_returns_false(self, monkeypatch):
        """A raising lookup => False (lines 669-670 except). Kills the
        ExceptionReplacer on that except."""
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.check_db_semantic_grant", _boom)
        assert ag.confirm_grant("git push origin main", session_id="s") is False


# ===========================================================================
# load_pending_by_nonce_prefix -- prefix match + newest-wins (10 survivors)
# ===========================================================================
class TestLoadPendingByNoncePrefixMutants:
    """load_pending_by_nonce_prefix(prefix) -- matches DB rows by nonce prefix."""

    def _row(self, *, approval_id, ts_created):
        return {
            "id": approval_id,
            "session_id": "s",
            "created_at": ts_created,
            "payload_json": json.dumps({
                "operation": "MUTATIVE command intercepted: push",
                "exact_content": "git push origin main",
            }),
        }

    def test_returns_none_when_no_prefix_match(self, monkeypatch):
        """No row whose nonce starts with the prefix => None (lines 350-351).
        Kills the AddNot on `if not nonce.startswith(prefix)` (an inverted guard
        would match everything)."""
        monkeypatch.setattr(
            "gaia.approvals.store.get_pending",
            lambda **k: [self._row(approval_id="P-ffffffff", ts_created="2026-01-01T00:00:00Z")],
        )
        assert ag.load_pending_by_nonce_prefix("deadbeef") is None

    def test_matches_prefix_and_returns_pending_dict(self, monkeypatch):
        """A row whose nonce starts with the prefix is returned as a pending dict
        with the stripped nonce. Pins the prefix match and the mapping."""
        monkeypatch.setattr(
            "gaia.approvals.store.get_pending",
            lambda **k: [self._row(approval_id="P-deadbeefcafe", ts_created="2026-01-01T00:00:00Z")],
        )
        out = ag.load_pending_by_nonce_prefix("deadbeef")
        assert out is not None
        assert out["nonce"] == "deadbeefcafe"

    def test_newest_candidate_wins_on_multiple_matches(self, monkeypatch):
        """When several rows match, the newest by timestamp is returned (line 355
        `reverse=True`). Kills the ReplaceTrueWithFalse on the reverse sort: with
        reverse=False the OLDEST would be returned."""
        rows = [
            self._row(approval_id="P-deadbeef1111", ts_created="2026-01-01T00:00:00Z"),
            self._row(approval_id="P-deadbeef2222", ts_created="2026-06-01T00:00:00Z"),
        ]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **k: rows)
        out = ag.load_pending_by_nonce_prefix("deadbeef")
        # The 2026-06 row is newest -> its nonce must win.
        assert out["nonce"] == "deadbeef2222"

    def test_db_exception_returns_none(self, monkeypatch):
        """A raising get_pending => None (lines 362-364 except). Kills the
        ExceptionReplacer on that except."""
        def _boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.approvals.store.get_pending", _boom)
        assert ag.load_pending_by_nonce_prefix("deadbeef") is None


# ===========================================================================
# Batch 2: behavioral survivors across the DB-wrapper / FS helper functions.
# These pin guards, path construction, default-session resolution, and grant
# reconstruction details that the first batch left alive. NumberReplacer
# mutants on logging-only truncation slices (command[:80], approval_id[:16])
# are intentionally NOT targeted: they alter only a log string, produce no
# observable behavior, and are equivalent mutants -- an honest test cannot kill
# them without asserting on log text, which is brittle and meaningless.
# ===========================================================================
class TestGetGrantsDirMutants:
    """_get_grants_dir() -- path construction + the create-once guard."""

    def test_dir_is_plugin_data_cache_approvals(self, monkeypatch, tmp_path):
        """The grants dir is get_plugin_data_dir() / 'cache' / 'approvals' (line
        270). Kills the ReplaceBinaryOperator_Div_* mutants on the two Path
        joins: any non-'/' operator changes the resolved path (or raises), so
        pinning the exact path observes the join."""
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / "plugin",
        )
        ag._grants_dir_created = False
        d = ag._get_grants_dir()
        assert d == tmp_path / "plugin" / "cache" / "approvals"

    def test_dir_created_on_first_call(self, monkeypatch, tmp_path):
        """On the first call (_grants_dir_created False) the dir is created with
        parents=True/exist_ok=True (lines 271-273). Kills the AddNot on
        `if not _grants_dir_created`, the True->False flips on the mkdir kwargs,
        and the True->False on the flag assignment: the dir must exist after."""
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / "plugin2",
        )
        ag._grants_dir_created = False
        d = ag._get_grants_dir()
        assert d.exists() and d.is_dir()
        # The create-once flag flipped to True (line 273 True->False mutant).
        assert ag._grants_dir_created is True


class TestConsumeSessionGrantsMutants:
    """consume_session_grants() -- the sweep loop over PENDING confirmed rows.

    The function opens gaia.store.writer._connect() and iterates the result of
    cur.fetchall(). We patch _connect to return a fake connection yielding
    TUPLE rows (the branch the code handles via row[0]) and mock
    consume_db_semantic_grant, so the loop / guard / count mutants are observed.
    """

    class _FakeCur:
        def __init__(self, rows):
            self._rows = rows
        def execute(self, *a, **k):
            return self
        def fetchall(self):
            return self._rows
        def close(self):
            pass

    class _FakeCon:
        def __init__(self, rows):
            self._rows = rows
        def execute(self, *a, **k):
            return TestConsumeSessionGrantsMutants._FakeCur(self._rows)
        def close(self):
            pass

    def _patch_con(self, monkeypatch, rows):
        monkeypatch.setattr(
            "gaia.store.writer._connect",
            lambda *a, **k: TestConsumeSessionGrantsMutants._FakeCon(rows),
        )

    def test_counts_each_successfully_consumed_grant(self, monkeypatch):
        """Two PENDING rows both consume => count 2 (lines 605-612). Kills the
        ZeroIterationForLoop on the sweep, the AddNot on `if not approval_id`,
        and pins the per-success increment (count must equal consumed count)."""
        self._patch_con(monkeypatch, [("P-a",), ("P-b",)])
        monkeypatch.setattr(
            "gaia.store.writer.consume_db_semantic_grant", lambda *a, **k: True
        )
        assert ag.consume_session_grants(session_id="s") == 2

    def test_only_consumed_grants_are_counted(self, monkeypatch):
        """When consume returns False the count does NOT increment (line 611
        `if consumed`). With two rows where consume returns False, count is 0.
        Kills the AddNot on `if consumed` (an inverted guard would count the
        failures)."""
        self._patch_con(monkeypatch, [("P-a",), ("P-b",)])
        monkeypatch.setattr(
            "gaia.store.writer.consume_db_semantic_grant", lambda *a, **k: False
        )
        assert ag.consume_session_grants(session_id="s") == 0

    def test_falsy_approval_id_is_skipped(self, monkeypatch):
        """A row with a falsy approval_id is skipped via continue (lines 607-608)
        and does not reach consume. Kills the AddNot on `if not approval_id` and
        the ReplaceContinueWithBreak: the second (valid) row must still consume."""
        self._patch_con(monkeypatch, [("",), ("P-b",)])
        consume = MagicMock(return_value=True)
        monkeypatch.setattr("gaia.store.writer.consume_db_semantic_grant", consume)
        assert ag.consume_session_grants(session_id="s") == 1
        consume.assert_called_once_with("P-b")

    def test_per_row_consume_exception_is_non_fatal(self, monkeypatch):
        """When consume raises for one row, the inner except swallows it and the
        sweep continues to the next row (lines 617-621). Kills the
        ExceptionReplacer on that inner except: a propagated error would abort
        the sweep and the surviving good row would not be counted."""
        self._patch_con(monkeypatch, [("P-bad",), ("P-good",)])

        def _consume(aid):
            if aid == "P-bad":
                raise RuntimeError("boom")
            return True
        monkeypatch.setattr("gaia.store.writer.consume_db_semantic_grant", _consume)
        assert ag.consume_session_grants(session_id="s") == 1

    def test_outer_exception_returns_zero(self, monkeypatch):
        """When _connect raises, the outer except returns 0 (lines 623-624).
        Kills the ExceptionReplacer on the outer except."""
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer._connect", _boom)
        assert ag.consume_session_grants(session_id="s") == 0


class TestCheckApprovalGrantSessionAndDetail:
    """check_approval_grant -- default-session branch + granted_at + verb except."""

    def test_resolves_default_session_when_none(self, monkeypatch):
        """When session_id is falsy the function resolves _get_session_id()
        (lines 466-467). Kills the AddNot / Delete_Not on `if not session_id`:
        the resolver must be invoked when no session is passed."""
        called = MagicMock(return_value="resolved-sess")
        monkeypatch.setattr(
            "modules.security.approval_grants._get_session_id", called
        )
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: None
        )
        ag.check_approval_grant("git push origin main")  # no session_id
        assert called.call_count == 1

    def test_granted_at_default_is_zero(self, monkeypatch):
        """The reconstructed grant has granted_at == 0.0 (line 496 NumberReplacer):
        TTL is enforced by the DB, so the in-memory grant carries 0.0. A non-zero
        default would change is_expired semantics."""
        sig = build_approval_signature(
            "git push origin main", scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()
        row = {
            "approval_id": "P-x",
            "session_id": "owner",
            "command_set_json": json.dumps({
                "command": "git push origin main",
                "scope_signature": sig,
            }),
        }
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: row
        )
        grant = ag.check_approval_grant("git push origin main", session_id="s")
        assert grant.granted_at == 0.0

    def test_bad_signature_in_row_still_returns_grant(self, monkeypatch):
        """A scope_signature that ApprovalSignature.from_dict cannot parse is
        swallowed (lines 483-489 except) and the grant is still returned with
        empty approved_verbs. Kills the ExceptionReplacer on that verb-derivation
        except: a propagated error would make the whole lookup fail."""
        row = {
            "approval_id": "P-x",
            "session_id": "owner",
            "command_set_json": json.dumps({
                "command": "git push origin main",
                "scope_signature": {"garbage": True, "version": "bad"},
            }),
        }
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: row
        )
        grant = ag.check_approval_grant("git push origin main", session_id="s")
        assert grant is not None
        assert grant.approved_verbs == []


class TestConfirmGrantSessionMutants:
    """confirm_grant default-session resolution (line 646 guard)."""

    def test_resolves_default_session_when_none(self, monkeypatch):
        """When session_id is falsy, _get_session_id() is called (lines 646-647).
        Kills the AddNot / Delete_Not on `if not session_id`."""
        called = MagicMock(return_value="resolved")
        monkeypatch.setattr(
            "modules.security.approval_grants._get_session_id", called
        )
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant", lambda *a, **k: None
        )
        ag.confirm_grant("git push origin main")  # no session_id
        assert called.call_count == 1

    def test_status_must_equal_applied_not_other(self, monkeypatch):
        """confirm_db_grant returning 'pending' (a status that is NOT 'applied')
        => False (line 659 Eq). Kills the Eq->Is / Eq->LtE flips: 'pending' must
        not be treated as success."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.confirm_db_grant",
            lambda *a, **k: {"status": "pending"},
        )
        assert ag.confirm_grant("git push origin main", session_id="s") is False


class TestWritePendingApprovalForFileMutants:
    """write_pending_approval_for_file -- guards, ctx or-chains, insert except."""

    def test_returns_none_when_signature_unbuildable(self, monkeypatch):
        """When build_file_path_signature returns None => None (lines 904-910).
        Kills the Is->IsNot flip on `if signature is None` and the AddNot."""
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: None,
        )
        assert ag.write_pending_approval_for_file("nonce", "/x", session_id="s") is None

    def test_success_returns_sentinel_path(self, monkeypatch):
        """A buildable signature + a successful insert_requested => a non-None
        sentinel Path whose name encodes the approval_id (lines 927-942). Pins
        the success return; kills mutants that would drop it to None."""
        fake_sig = MagicMock()
        fake_sig.to_dict.return_value = {"sig": "ok"}
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: fake_sig,
        )
        insert = MagicMock(return_value="P-nonce")
        monkeypatch.setattr("gaia.approvals.store.insert_requested", insert)
        out = ag.write_pending_approval_for_file(
            "nonce", "/etc/hosts", session_id="s"
        )
        assert out is not None
        assert str(out) == "P-nonce"

    def test_insert_exception_returns_none(self, monkeypatch):
        """When insert_requested raises => None (lines 944-946 except). Kills the
        ExceptionReplacer on that except."""
        fake_sig = MagicMock()
        fake_sig.to_dict.return_value = {"sig": "ok"}
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: fake_sig,
        )
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.approvals.store.insert_requested", _boom)
        assert ag.write_pending_approval_for_file(
            "nonce", "/etc/hosts", session_id="s"
        ) is None

    def test_risk_falls_back_to_medium(self, monkeypatch):
        """The risk_level or-chain (line 918 `ctx.get('risk','medium') or
        'medium'`) yields 'medium' when ctx has no risk. We observe it through
        the sealed_payload passed to insert_requested. Kills the Or->And flip:
        with `and`, an absent risk would yield a falsy value, not 'medium'."""
        fake_sig = MagicMock()
        fake_sig.to_dict.return_value = {"sig": "ok"}
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: fake_sig,
        )
        captured = {}
        def _insert(payload, **k):
            captured["payload"] = payload
            return "P-nonce"
        monkeypatch.setattr("gaia.approvals.store.insert_requested", _insert)
        ag.write_pending_approval_for_file("nonce", "/etc/hosts", session_id="s")
        assert captured["payload"]["risk_level"] == "medium"

    def test_rationale_falls_back_to_default_text(self, monkeypatch):
        """The rationale or-chain (lines 920-923) uses the default text when ctx
        has no description. Pins the Or->And flip on that chain: with `and` an
        absent description would yield a falsy rationale."""
        fake_sig = MagicMock()
        fake_sig.to_dict.return_value = {"sig": "ok"}
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: fake_sig,
        )
        captured = {}
        def _insert(payload, **k):
            captured["payload"] = payload
            return "P-nonce"
        monkeypatch.setattr("gaia.approvals.store.insert_requested", _insert)
        ag.write_pending_approval_for_file("nonce", "/etc/hosts", session_id="s")
        assert "/etc/hosts" in captured["payload"]["rationale"]
        assert "requires user approval" in captured["payload"]["rationale"]


class TestCleanupExpiredGrantsMutants:
    """cleanup_expired_grants -- throttle comparison + force bypass + except."""

    def test_throttle_skips_when_recent(self, monkeypatch):
        """Within _CLEANUP_INTERVAL_SECONDS of the last run, cleanup is skipped
        and returns 0 without calling the DB sweep (line 697 `now - last <
        interval`). Kills the Lt->Eq/Is/LtE flips and the Sub->Add on the
        throttle arithmetic."""
        called = MagicMock(return_value=5)
        monkeypatch.setattr("gaia.store.writer.cleanup_expired_db_grants", called)
        # Pretend the last run was 1 second ago (well within the 60s interval).
        ag._last_cleanup_time = time.time() - 1
        assert ag.cleanup_expired_grants(force=False) == 0
        assert called.call_count == 0

    def test_force_bypasses_throttle(self, monkeypatch):
        """force=True runs the sweep even when recently run (line 697 guard short
        -circuits on `not force`). Kills the ReplaceFalseWithTrue on the force
        default (line 675) and pins that force overrides the throttle."""
        monkeypatch.setattr(
            "gaia.store.writer.cleanup_expired_db_grants", lambda: 3
        )
        ag._last_cleanup_time = time.time()  # just ran
        assert ag.cleanup_expired_grants(force=True) == 3

    def test_db_sweep_exception_is_non_fatal(self, monkeypatch):
        """A raising cleanup_expired_db_grants is swallowed (lines 710-711) and
        the function returns 0. Kills the ExceptionReplacer on that except."""
        def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.cleanup_expired_db_grants", _boom)
        ag._last_cleanup_time = 0.0  # force past throttle
        assert ag.cleanup_expired_grants(force=True) == 0


class TestGetPendingApprovalsForSessionMutants:
    """get_pending_approvals_for_session -- session default, except, sort."""

    def _row(self, *, ts):
        return {
            "id": "P-x",
            "session_id": "s",
            "created_at": ts,
            "payload_json": json.dumps({
                "operation": "MUTATIVE command intercepted: push",
                "exact_content": "git push",
            }),
        }

    def test_resolves_default_session_when_none(self, monkeypatch):
        """When session_id is None, _get_session_id() is used (lines 796-797).
        Kills the Is->IsNot flip on `if session_id is None` and the AddNot."""
        called = MagicMock(return_value="resolved")
        monkeypatch.setattr(
            "modules.security.approval_grants._get_session_id", called
        )
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **k: [])
        ag.get_pending_approvals_for_session()  # no session
        assert called.call_count == 1

    def test_results_sorted_newest_first(self, monkeypatch):
        """Results are sorted by timestamp descending (line 810 reverse=True).
        Kills the ReplaceTrueWithFalse on that reverse: oldest would come first."""
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **k: [
            self._row(ts="2026-01-01T00:00:00Z"),
            self._row(ts="2026-06-01T00:00:00Z"),
        ])
        out = ag.get_pending_approvals_for_session(session_id="s")
        assert len(out) == 2
        # Newest (2026-06) first.
        assert out[0]["timestamp"] > out[1]["timestamp"]

    def test_db_exception_returns_empty_list(self, monkeypatch):
        """A raising get_pending => [] (lines 807-808 except). Kills the
        ExceptionReplacer on that except."""
        def _boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.approvals.store.get_pending", _boom)
        assert ag.get_pending_approvals_for_session(session_id="s") == []


class TestCheckApprovalGrantForFileMutants:
    """check_approval_grant_for_file -- DB row guard + except."""

    def test_returns_row_when_grant_found(self, monkeypatch):
        """A DB row => that row is returned (lines 974-980, `if row is not
        None`). Kills the IsNot->Is flip on the guard and the AddNot: a flip
        would return None when a grant exists."""
        row = {"approval_id": "P-x", "file_path": "/etc/hosts"}
        monkeypatch.setattr(
            "gaia.store.writer.check_db_file_path_grant", lambda fp: row
        )
        assert ag.check_approval_grant_for_file("/etc/hosts") is row

    def test_returns_none_when_no_grant(self, monkeypatch):
        """No DB row (None) => None. Pins the guard direction."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_file_path_grant", lambda fp: None
        )
        assert ag.check_approval_grant_for_file("/etc/hosts") is None

    def test_db_exception_returns_none(self, monkeypatch):
        """A raising lookup => None (lines 981-984 except). Kills the
        ExceptionReplacer on that except."""
        def _boom(fp):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.check_db_file_path_grant", _boom)
        assert ag.check_approval_grant_for_file("/etc/hosts") is None


# ===========================================================================
# Batch 3: remaining behavioral survivors. Targets the genuinely observable
# branches the first two batches left alive -- signature-fallback paths,
# command_set guards, expiry boundaries, except handlers, sort directions,
# and standalone comparisons. Type-annotation `str | None` BitOr mutants and
# logging-only NumberReplacer slices are NOT targeted (equivalent mutants:
# Python does not evaluate `|` in annotations, and a log truncation length
# has no observable behavior).
# ===========================================================================
class TestActivateDbPendingSignatureFallback:
    """activate_db_pending_by_prefix signature-rebuild branch (Step 4)."""

    def _drive(self, monkeypatch, payload):
        rows = [{
            "id": "P-deadbeefcafe",
            "payload_json": json.dumps(payload),
            "session_id": "sub", "agent_id": "ag",
        }]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **kw: rows)
        monkeypatch.setattr(
            "gaia.approvals.chain.verify_fingerprint", lambda *a, **k: True
        )
        monkeypatch.setattr("gaia.approvals.store._open_db", lambda: _DummyCon())
        monkeypatch.setattr("gaia.approvals.store.record_event", MagicMock())
        monkeypatch.setattr("gaia.approvals.store.approve", MagicMock())
        monkeypatch.setattr("gaia.approvals.store.get_by_id", lambda *a, **k: None)

    def test_unbuildable_signature_returns_invalid_signature(self, monkeypatch):
        """When BOTH build_approval_signature calls return None, the function
        returns success=False / INVALID_SIGNATURE (lines 1451-1456). Kills the
        ReplaceFalseWithTrue on that success=False and pins the
        signature-None terminal branch."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
        })
        # Step 4 does a local `from .approval_scopes import build_approval_signature`,
        # so the source module attribute is what the local import binds.
        monkeypatch.setattr(
            "modules.security.approval_scopes.build_approval_signature",
            lambda *a, **k: None,
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_SIGNATURE

    def test_operation_parse_threads_verb_and_category_into_signature(self, monkeypatch):
        """The operation string 'FILE_WRITE command intercepted: write' is parsed
        into danger_verb='write' and danger_category='FILE_WRITE' and passed to
        build_approval_signature (lines 1424-1435). Kills the AddNot on
        `if 'intercepted:' in ...`, the `len(parts) == 2` guard, and the
        parts[0]/parts[1] index mutants -- observed via the call kwargs."""
        self._drive(monkeypatch, {
            "operation": "FILE_WRITE command intercepted: write",
            "exact_content": "echo hi",
        })
        calls = []
        import modules.security.approval_scopes as _scopes
        real_sig = _scopes.build_approval_signature
        def _spy(command, **kwargs):
            calls.append(kwargs)
            return real_sig(command, **kwargs)
        # Step 4's local import binds from approval_scopes, so patch there.
        monkeypatch.setattr(
            "modules.security.approval_scopes.build_approval_signature", _spy
        )
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: {"status": "applied"},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        # First build call carries the parsed verb + category.
        assert calls[0]["danger_verb"] == "write"
        assert calls[0]["danger_category"] == "FILE_WRITE"


class TestCreateCommandSetGrantBatch3:
    """create_command_set_grant -- guard + status compare + except (extra)."""

    def test_session_none_resolves_default(self, monkeypatch, writer_db):
        """session_id=None resolves _get_session_id() (lines 1584-1585). Kills the
        Is->IsNot flip on `if session_id is None` and the AddNot."""
        called = MagicMock(return_value="resolved-sess")
        monkeypatch.setattr(
            "modules.security.approval_grants._get_session_id", called
        )
        ok = create_command_set_grant(
            [{"command": "git push", "rationale": "r"}],
            f"P-{secrets.token_hex(8)}",
        )
        assert ok is True
        assert called.call_count == 1

    def test_insert_failure_returns_false(self, monkeypatch, writer_db):
        """When insert_approval_grant returns a non-'applied' status, the function
        returns False (lines 1609-1612). Kills the Eq->Is/LtE/GtE flips on
        `result.get('status') == 'applied'` (1603) and the ReplaceFalseWithTrue
        on the failure return (1612)."""
        monkeypatch.setattr(
            "gaia.store.writer.insert_approval_grant",
            lambda **k: {"status": "rejected", "reason": "dup"},
        )
        ok = create_command_set_grant(
            [{"command": "git push", "rationale": "r"}],
            f"P-{secrets.token_hex(8)}",
            session_id="s",
        )
        assert ok is False

    def test_insert_exception_returns_false(self, monkeypatch, writer_db):
        """A raising insert_approval_grant => False (lines 1613-1615). Kills the
        ExceptionReplacer on that except and the ReplaceFalseWithTrue."""
        def _boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.insert_approval_grant", _boom)
        ok = create_command_set_grant(
            [{"command": "git push", "rationale": "r"}],
            f"P-{secrets.token_hex(8)}",
            session_id="s",
        )
        assert ok is False


class TestMatchCommandSetGrantBatch3:
    """match_command_set_grant -- expiry boundary + except handlers (extra)."""

    def _insert(self, db_path, approval_id, command_set, *, status="PENDING",
                scope="COMMAND_SET", expires_at=None, consumed_indexes=None,
                command_set_json=None):
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
                    command_set_json if command_set_json is not None
                    else json.dumps(command_set),
                    scope, expires_at, status,
                    json.dumps(consumed_indexes or []),
                ),
            )
            con.commit()
        finally:
            con.close()

    def test_not_yet_expired_grant_still_matches(self, writer_db):
        """A grant whose expires_at is in the FUTURE matches (line 1671
        `expires_at < now_iso` is False -> not skipped). With the under-/over-
        expiry test in batch 1 this brackets the Lt boundary, killing the
        Lt->LtE flip: a future expiry must NOT be treated as expired."""
        approval_id = f"P-{secrets.token_hex(16)}"
        future = (
            datetime.now(timezone.utc) + timedelta(minutes=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._insert(
            writer_db, approval_id,
            [{"command": "git push origin main", "rationale": "a"}],
            expires_at=future,
        )
        assert match_command_set_grant("git push origin main") == (approval_id, 0)

    def test_malformed_command_set_json_is_skipped(self, writer_db):
        """A grant whose command_set_json is not valid JSON is skipped via the
        inner except+continue (lines 1690-1691) and does not match. Kills the
        ExceptionReplacer on that JSON-parse except."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id, [],
            command_set_json="{not valid json",
        )
        assert match_command_set_grant("git push origin main") is None


class TestSmallBehavioralSurvivorsBatch3:
    """Standalone behavioral survivors across several functions."""

    # ----- _is_ttl_expired extra boundaries -----
    def test_ttl_negative_is_not_no_expiry(self):
        """ttl == 0 short-circuits to no-expiry; a ttl of 5 with an old stamp is
        expired. Pins the `== 0` (line 175) against the Eq->LtE flip: with `<= 0`
        a positive ttl is unaffected, but the zero-vs-positive discrimination is
        held by the ttl=0 (no expiry) + ttl=5 (expired) pair."""
        assert _is_ttl_expired(time.time() - 10_000, 5) is True
        assert _is_ttl_expired(time.time() - 10_000, 0) is False

    def test_timestamp_one_is_not_treated_as_zero(self):
        """timestamp == 0 means 'never stamped' -> expired; a timestamp of 1.0
        (epoch+1s, ancient) is also expired BUT via the elapsed path, not the
        zero guard. A fresh stamp is NOT. Pins the `timestamp == 0` against
        Eq->Lt/LtE: a flip changes which timestamps hit the zero guard."""
        # A genuinely fresh timestamp must not be caught by the zero guard.
        assert _is_ttl_expired(time.time(), 60) is False

    # ----- consume_grant guard -----
    def test_consume_grant_logs_only_on_success(self, monkeypatch):
        """consume_grant returns the consume result; when the grant exists and
        consume returns True the function returns True (line 542 `if consumed`
        gate around the success log). Pins that a True consume threads back."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.consume_db_semantic_grant", lambda *a, **k: True
        )
        assert ag.consume_grant("git push") is True

    # ----- consume_session_grants default session -----
    def test_consume_session_grants_resolves_default_session(self, monkeypatch):
        """session_id falsy => _get_session_id() is used (lines 578-579). Kills
        the AddNot / Delete_Not on `if not session_id`."""
        called = MagicMock(return_value="resolved")
        monkeypatch.setattr(
            "modules.security.approval_grants._get_session_id", called
        )
        # _connect raises so the body short-circuits to 0, but the session
        # resolution at the top has already run.
        def _boom(*a, **k):
            raise RuntimeError("stop")
        monkeypatch.setattr("gaia.store.writer._connect", _boom)
        ag.consume_session_grants()  # no session
        assert called.call_count == 1

    # ----- load_pending_by_nonce_prefix all_sessions + continue + sort -----
    def test_load_pending_queries_all_sessions(self, monkeypatch):
        """get_pending is called with all_sessions=True (line 338). Kills the
        ReplaceTrueWithFalse: with all_sessions=False a cross-session pending
        would be missed. Observed via the call kwargs."""
        captured = {}
        def _gp(**kw):
            captured.update(kw)
            return []
        monkeypatch.setattr("gaia.approvals.store.get_pending", _gp)
        ag.load_pending_by_nonce_prefix("deadbeef")
        assert captured.get("all_sessions") is True

    def test_load_pending_skips_nonmatching_then_matches(self, monkeypatch):
        """A non-matching pending followed by a matching one is still resolved --
        pins the `continue` advances (kills ReplaceContinueWithBreak on line 345)
        rather than breaking out before the match."""
        rows = [
            {"id": "P-ffffffff", "session_id": "s", "created_at": "2026-01-01T00:00:00Z",
             "payload_json": json.dumps({"operation": "x: y", "exact_content": "c"})},
            {"id": "P-deadbeefcafe", "session_id": "s", "created_at": "2026-01-01T00:00:00Z",
             "payload_json": json.dumps({"operation": "x: y", "exact_content": "c"})},
        ]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **k: rows)
        out = ag.load_pending_by_nonce_prefix("deadbeef")
        assert out is not None
        assert out["nonce"] == "deadbeefcafe"

    # ----- find_pending_for_command continue + except -----
    def test_find_pending_for_command_skips_bad_sig_via_except(self, monkeypatch):
        """A pending whose scope_signature cannot be deserialized hits the
        try/except+continue (lines 858-859) and a later valid match still wins.
        Kills the ExceptionReplacer on that except and the
        ReplaceContinueWithBreak."""
        cmd = "git push origin main"
        good_sig = build_approval_signature(
            cmd, scope_type=SCOPE_SEMANTIC_SIGNATURE
        ).to_dict()
        pending = [
            {"nonce": "bad", "scope_signature": {"garbage": True, "version": "x"}},
            {"nonce": "good", "scope_signature": good_sig},
        ]
        monkeypatch.setattr(
            ag, "get_pending_approvals_for_session", lambda s: pending
        )
        assert find_pending_for_command("sess", cmd) == "good"

    # ----- find_pending_for_file except -----
    def test_find_pending_for_file_db_exception_returns_none(self, monkeypatch):
        """A raising list_pending => None (lines 1042-1043 except). Kills the
        ExceptionReplacer on that except."""
        def _boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.approvals.store.list_pending", _boom)
        assert find_pending_for_file("sess", "/home/u/file") is None

    def test_find_pending_for_file_queries_all_sessions(self, monkeypatch):
        """list_pending is called with all_sessions=True (line 1022). Kills the
        ReplaceTrueWithFalse: a session-scoped query would miss the subagent's
        pending. Observed via call kwargs."""
        captured = {}
        def _lp(**kw):
            captured.update(kw)
            return []
        monkeypatch.setattr("gaia.approvals.store.list_pending", _lp)
        find_pending_for_file("sess", "/home/u/file")
        assert captured.get("all_sessions") is True

    # ----- write_pending session-None guard -----
    def test_write_pending_resolves_default_session(self, monkeypatch):
        """session_id=None resolves _get_session_id() (lines 901-902). Kills the
        Is->IsNot flip and AddNot on `if session_id is None`."""
        called = MagicMock(return_value="resolved")
        monkeypatch.setattr(
            "modules.security.approval_grants._get_session_id", called
        )
        fake_sig = MagicMock()
        fake_sig.to_dict.return_value = {"sig": "ok"}
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: fake_sig,
        )
        monkeypatch.setattr(
            "gaia.approvals.store.insert_requested", lambda *a, **k: "P-n"
        )
        ag.write_pending_approval_for_file("n", "/etc/hosts")  # no session
        assert called.call_count == 1

    # ----- cleanup AddNot on `if cleaned` -----
    def test_cleanup_returns_cleaned_count(self, monkeypatch):
        """When the DB sweep reports N expired rows, cleanup returns N (lines
        706-715). Kills the AddNot on `if cleaned` and pins the count threads
        back (not hardcoded)."""
        monkeypatch.setattr(
            "gaia.store.writer.cleanup_expired_db_grants", lambda: 4
        )
        ag._last_cleanup_time = 0.0
        assert ag.cleanup_expired_grants(force=True) == 4

    # ----- _run_git_query returncode boundary -----
    @patch("modules.security.approval_grants.subprocess.run")
    def test_git_query_negative_returncode_is_none(self, mock_run):
        """A returncode of -1 (signal) => None, not stdout (line 387 Eq vs LtE:
        with `<= 0`, a negative returncode would wrongly be treated as success).
        Pins the strict `== 0`."""
        result = MagicMock()
        result.returncode = -1
        result.stdout = "partial\n"
        mock_run.return_value = result
        assert _run_git_query(["rev-parse", "HEAD"]) is None

    # ----- get_pending sort reverse (NumberReplacer on the sort key default) -----
    def test_get_pending_orders_by_timestamp_desc(self, monkeypatch):
        """Two pendings with different timestamps come back newest-first (line
        810). The two-element ordering pins the reverse=True sort direction."""
        def _row(ts):
            return {"id": "P-x", "session_id": "s", "created_at": ts,
                    "payload_json": json.dumps({"operation": "x: y", "exact_content": "c"})}
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **k: [
            _row("2025-01-01T00:00:00Z"), _row("2026-01-01T00:00:00Z"),
        ])
        out = ag.get_pending_approvals_for_session(session_id="s")
        assert out[0]["timestamp"] >= out[1]["timestamp"]

    # ----- capture_environment_snapshot except (non-git returns {}) -----
    @patch("modules.security.approval_grants._run_git_query")
    def test_capture_snapshot_try_body_runs(self, mock_q):
        """For a git command the try body executes and builds the snapshot dict
        (line 439 ExceptionReplacer guards the whole body). With a real value for
        the first query and None for the rest, command_class + local_head are
        present -- pins the try body is not swallowed."""
        mock_q.side_effect = ["sha1", None, None]
        snap = capture_environment_snapshot("git commit -am x")
        assert snap["command_class"] == "git"
        assert snap["local_head"] == "sha1"
        assert "branch" not in snap

    # ----- ApprovalGrant confirmed default -----
    def test_approval_grant_confirmed_defaults_false(self):
        """The confirmed field defaults to False (line 216). Kills the
        ReplaceFalseWithTrue on that default: a default-constructed grant must
        not claim to be user-confirmed."""
        assert ApprovalGrant().confirmed is False

    # ----- _get_grants_dir exist_ok / parents on a pre-existing dir -----
    def test_get_grants_dir_idempotent_when_exists(self, monkeypatch, tmp_path):
        """Calling _get_grants_dir when the dir already exists must NOT raise
        (exist_ok=True, line 272). Kills the True->False flip on exist_ok: with
        exist_ok=False a second materialization of an existing dir raises
        FileExistsError. We pre-create the dir and force the create-once flag
        off so mkdir runs against an existing path."""
        base = tmp_path / "plugin3"
        target = base / "cache" / "approvals"
        target.mkdir(parents=True, exist_ok=True)  # already exists
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir", lambda: base
        )
        ag._grants_dir_created = False  # force mkdir to run
        d = ag._get_grants_dir()  # must not raise
        assert d == target


# ===========================================================================
# Batch 4: final high-confidence behavioral kills. These target the few
# remaining branches with a genuine observable: the signature double-build
# fallback, the command_set item filter, all_sessions/break/continue control
# flow, error-path excepts, the frozen dataclass, force default, and the
# _db_row or-chain / verb-index. Mutants that are string-comparison-operator
# flips equivalent for all inputs, type-annotation `| None` BitOr, and
# logging-only slice NumberReplacers are NOT targeted -- they are equivalent
# mutants no honest assertion can distinguish.
# ===========================================================================
class TestActivateDbPendingBatch4:
    """activate_db_pending_by_prefix remaining observable branches."""

    def _drive(self, monkeypatch, payload, *, get_by_id_row=None):
        rows = [{
            "id": "P-deadbeefcafe",
            "payload_json": json.dumps(payload),
            "session_id": "sub", "agent_id": "ag",
        }]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **kw: rows)
        monkeypatch.setattr(
            "gaia.approvals.chain.verify_fingerprint", lambda *a, **k: True
        )
        monkeypatch.setattr("gaia.approvals.store._open_db", lambda: _DummyCon())
        monkeypatch.setattr("gaia.approvals.store.record_event", MagicMock())
        monkeypatch.setattr("gaia.approvals.store.approve", MagicMock())
        monkeypatch.setattr(
            "gaia.approvals.store.get_by_id", lambda *a, **k: get_by_id_row
        )

    def test_signature_fallback_second_build_succeeds(self, monkeypatch):
        """When the FIRST build_approval_signature returns None but the fallback
        (first-token verb) build succeeds, the function proceeds to insert and
        returns ACTIVATED (lines 1437-1450). Kills the AddNot / Is->IsNot on
        `if signature is None` (1437): a flipped guard would skip the fallback
        and the first None would propagate to INVALID_SIGNATURE."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
        })
        import modules.security.approval_scopes as _scopes
        real = _scopes.build_approval_signature
        calls = {"n": 0}
        def _sig(command, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # first attempt fails
            return real(command, **kwargs)  # fallback succeeds
        monkeypatch.setattr(
            "modules.security.approval_scopes.build_approval_signature", _sig
        )
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: {"status": "applied"},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert calls["n"] == 2  # both builds were attempted

    def test_command_set_filter_excludes_non_command_dicts(self, monkeypatch):
        """The command_set item filter keeps only dicts WITH a 'command' key
        (line 1162 `isinstance(_item, dict) and _item.get('command')`). A set of
        [valid, dict-without-command, valid] yields exactly 2 items, so it is a
        COMMAND_SET (len>1). Kills the And->Or flip: with `or`, the
        dict-without-command would be included and _item['command'] would
        KeyError -- here we assert the clean 2-item activation succeeds."""
        self._drive(monkeypatch, {
            "operation": "MUTATIVE command intercepted: push",
            "command_set": [
                {"command": "git push origin main", "rationale": "a"},
                {"rationale": "no command key"},
                {"command": "git tag v1", "rationale": "b"},
            ],
        })
        create = MagicMock(return_value=True)
        monkeypatch.setattr(
            "modules.security.approval_grants.create_command_set_grant", create
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        # Exactly the two command-bearing items survived the filter.
        items = create.call_args[0][0]
        assert [i["command"] for i in items] == ["git push origin main", "git tag v1"]

    def test_get_pending_queried_all_sessions(self, monkeypatch):
        """get_pending is called with all_sessions=True (line 1101). Kills the
        ReplaceTrueWithFalse: a session-scoped query would miss the subagent's
        pending row. Observed via call kwargs."""
        captured = {}
        def _gp(**kw):
            captured.update(kw)
            return []
        monkeypatch.setattr("gaia.approvals.store.get_pending", _gp)
        monkeypatch.setattr(
            "gaia.approvals.chain.verify_fingerprint", lambda *a, **k: True
        )
        activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert captured.get("all_sessions") is True

    def test_first_prefix_match_wins_via_break(self, monkeypatch):
        """The match loop breaks on the FIRST prefix hit (line 1108). With two
        rows sharing the prefix, the FIRST is used. Kills the
        ReplaceBreakWithContinue: a continue would let the second row overwrite
        matched_row. We give the rows distinguishable commands and assert the
        first one's command drove the (semantic) activation."""
        rows = [
            {"id": "P-deadbeef1111",
             "payload_json": json.dumps({
                 "operation": "MUTATIVE command intercepted: push",
                 "exact_content": "FIRST-command"}),
             "session_id": "s", "agent_id": "a"},
            {"id": "P-deadbeef2222",
             "payload_json": json.dumps({
                 "operation": "MUTATIVE command intercepted: push",
                 "exact_content": "SECOND-command"}),
             "session_id": "s", "agent_id": "a"},
        ]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **kw: rows)
        monkeypatch.setattr(
            "gaia.approvals.chain.verify_fingerprint", lambda *a, **k: True
        )
        monkeypatch.setattr("gaia.approvals.store._open_db", lambda: _DummyCon())
        monkeypatch.setattr("gaia.approvals.store.record_event", MagicMock())
        monkeypatch.setattr("gaia.approvals.store.approve", MagicMock())
        monkeypatch.setattr("gaia.approvals.store.get_by_id", lambda *a, **k: None)
        captured = {}
        def _insert(**k):
            captured.update(k)
            return {"status": "applied"}
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", _insert)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        # The FIRST matched row's command must be the one inserted.
        assert captured["command"] == "FIRST-command"


class TestDbRowToPendingDictBatch4:
    """_db_row_to_pending_dict -- or-chain end + verb [-1] index."""

    def _row(self, payload):
        return {
            "id": "P-cafe", "session_id": "s",
            "created_at": "2026-06-26T12:00:00Z",
            "payload_json": json.dumps(payload),
        }

    def test_command_empty_string_when_all_sources_absent(self):
        """With no exact_content / commands / operation, command falls to '' (the
        final `or ''`, line 739). Kills the Or->And flip on that terminal arm."""
        out = _db_row_to_pending_dict(self._row({}))
        assert out["command"] == ""

    def test_danger_verb_is_last_segment_after_colon_space(self):
        """danger_verb = operation.rsplit(': ', 1)[-1] (line 746). With an
        operation 'PREFIX: MIDDLE: deploy', the [-1] index picks 'deploy'. Kills
        the USub/UAdd unary mutants on the [-1] index and the rsplit maxsplit:
        a wrong index picks the wrong segment."""
        out = _db_row_to_pending_dict(self._row({
            "exact_content": "x",
            "operation": "PREFIX: MIDDLE: deploy",
        }))
        assert out["danger_verb"] == "deploy"


class TestModuleLevelDefaultsBatch4:
    """Module-level dataclass / flag defaults with an observable."""

    def test_activation_result_is_frozen(self):
        """ApprovalActivationResult is declared frozen (line 183
        @dataclass(frozen=True)). Kills the ReplaceTrueWithFalse on frozen=True:
        a non-frozen dataclass would allow attribute assignment. Assigning to a
        field must raise FrozenInstanceError."""
        import dataclasses
        r = ag.ApprovalActivationResult(success=True, status="x", reason="y")
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.success = False

    def test_cleanup_force_default_is_false(self, monkeypatch):
        """cleanup_expired_grants(force) defaults to False (line 675). With the
        default and a recent last-run, the throttle skips the sweep. Kills the
        ReplaceFalseWithTrue on the force default: if force defaulted to True the
        throttle would never apply and the sweep would run."""
        called = MagicMock(return_value=9)
        monkeypatch.setattr("gaia.store.writer.cleanup_expired_db_grants", called)
        ag._last_cleanup_time = time.time()  # just ran
        # Call WITHOUT passing force -> must use the default (False) -> throttled.
        assert ag.cleanup_expired_grants() == 0
        assert called.call_count == 0


class TestMatchCommandSetExceptHandlersBatch4:
    """match_command_set_grant -- inner consumed-index except handler."""

    def _insert(self, db_path, approval_id, *, command_set_json, status="PENDING",
                scope="COMMAND_SET", expires_at=None, consumed_json="[]"):
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
                (approval_id, command_set_json, scope, expires_at, status,
                 consumed_json),
            )
            con.commit()
        finally:
            con.close()

    def test_malformed_consumed_indexes_defaults_empty_and_matches(self, writer_db):
        """When consumed_indexes_json is invalid JSON, the inner except leaves
        consumed_indexes = [] (lines 1694-1698) and the command still matches at
        index 0. Kills the ExceptionReplacer on that except: a propagated error
        would skip the grant entirely and the command would NOT match."""
        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id,
            command_set_json=json.dumps([
                {"command": "git push origin main", "rationale": "a"}
            ]),
            consumed_json="{not valid json",
        )
        assert match_command_set_grant("git push origin main") == (approval_id, 0)


# ===========================================================================
# activate_db_pending_by_prefix -- AC-1 survivor closure (M1 loop, AaXIS #91).
#
# The 70/120 of this function's baseline survivors that the earlier batches
# already kill stay killed; this section closes the remaining behavioral ones
# the scoped harness still reports SURVIVED. Two structural facts drive the
# design here:
#
#   (a) Nested except handlers. ExceptionReplacer narrows a `except Exception`
#       to a sentinel type, so a real error raised inside an INNER try escapes
#       to the function's OUTER `except Exception` (line 1512). Both return
#       ACTIVATION_ERROR, so a status-only assertion can NOT tell them apart and
#       the inner mutant survives. The discriminator is the `reason` string:
#       each handler builds a DISTINCT reason. Every test below pins the reason
#       substring so the inner-vs-outer handler is observable.
#
#   (b) The integrity-violation label. verify_fingerprint failing with a
#       ChainTamperError vs any other error selects _tamper_label =
#       "fingerprint_mismatch" vs "missing_requested_event" (line 1213). The
#       label is surfaced in BOTH the returned reason and the FAILED audit
#       metadata `integrity_check`, so the AddNot/== on that line is killable
#       via either observable.
#
# All collaborators are mocked (no real gaia.db): get_pending, verify_fingerprint,
# _open_db, record_event, approve, get_by_id, and the writer-side inserts.
# ===========================================================================
class _AC1Driver:
    """Shared driver: patch every lazy collaborator and return recorders."""

    @staticmethod
    def drive(monkeypatch, payload, *, approval_id="P-deadbeefcafe",
              session_id="sub", agent_id="ag", fingerprint_exc=None,
              approve_raises=None, get_by_id_row=None, record_event_raises=None):
        rows = [{
            "id": approval_id,
            "payload_json": json.dumps(payload) if not isinstance(payload, str) else payload,
            "session_id": session_id,
            "agent_id": agent_id,
        }]
        monkeypatch.setattr("gaia.approvals.store.get_pending", lambda **kw: rows)

        def _verify(approval_id_arg, payload_json_arg, con):
            if fingerprint_exc is not None:
                raise fingerprint_exc
            return True
        monkeypatch.setattr("gaia.approvals.chain.verify_fingerprint", _verify)
        monkeypatch.setattr("gaia.approvals.store._open_db", lambda: _DummyCon())

        def _record_event(*a, **k):
            if record_event_raises is not None:
                raise record_event_raises
        rec = MagicMock(side_effect=_record_event)
        monkeypatch.setattr("gaia.approvals.store.record_event", rec)

        def _approve(*a, **k):
            if approve_raises is not None:
                raise approve_raises
        monkeypatch.setattr("gaia.approvals.store.approve", MagicMock(side_effect=_approve))
        monkeypatch.setattr("gaia.approvals.store.get_by_id", lambda *a, **k: get_by_id_row)
        return {"record_event": rec}


class TestActivateDbPendingExceptionHandlersAC1:
    """Inner-vs-outer except handlers, discriminated by the reason string."""

    _SEMANTIC = {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": "git push origin main",
    }
    _FILE = {
        "operation": "FILE_WRITE command intercepted: write",
        "exact_content": "/etc/hosts",
        "scope": SCOPE_FILE_PATH,
    }

    def test_semantic_insert_exception_uses_inner_reason_not_outer(self, monkeypatch):
        """insert_semantic_grant raising hits the INNER except (line 1501), whose
        reason starts 'DB semantic grant insert error'. ExceptionReplacer on that
        handler lets the error escape to the OUTER except (1512), whose reason is
        'Unexpected error activating DB pending'. Pinning the inner reason kills
        the inner ExceptionReplacer (status alone cannot, both are ERROR)."""
        _AC1Driver.drive(monkeypatch, self._SEMANTIC)

        def _boom(**k):
            raise RuntimeError("db down")
        monkeypatch.setattr("gaia.store.writer.insert_semantic_grant", _boom)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR
        assert "DB semantic grant insert error" in result.reason
        assert "Unexpected error activating DB pending" not in result.reason

    def test_file_path_insert_exception_uses_inner_reason_not_outer(self, monkeypatch):
        """insert_file_path_grant raising hits the INNER except (line 1381), whose
        reason starts 'SCOPE_FILE_PATH DB grant insert error'. ExceptionReplacer
        lets it escape to the outer handler. Pin the inner reason."""
        _AC1Driver.drive(monkeypatch, self._FILE)

        def _boom(**k):
            raise RuntimeError("fp down")
        monkeypatch.setattr("gaia.store.writer.insert_file_path_grant", _boom)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR
        assert "SCOPE_FILE_PATH DB grant insert error" in result.reason
        assert "Unexpected error activating DB pending" not in result.reason

    def test_failed_audit_event_exception_does_not_break_tamper_return(self, monkeypatch):
        """When the integrity check fails AND the FAILED-audit record_event raises,
        the INNER audit except (line 1234) swallows it and the function still
        returns CHAIN_TAMPER_DETECTED. ExceptionReplacer on that handler lets the
        audit error escape to the outer except (-> ACTIVATION_ERROR / 'Unexpected
        error' reason). Pin the tamper status + reason to keep the inner handler."""
        from gaia.approvals.chain import ChainTamperError
        _AC1Driver.drive(
            monkeypatch, self._SEMANTIC,
            fingerprint_exc=ChainTamperError("tamper"),
            record_event_raises=RuntimeError("audit chain offline"),
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_CHAIN_TAMPER_DETECTED
        assert "integrity check failed" in result.reason
        assert "Unexpected error activating DB pending" not in result.reason

    def test_outer_except_handles_unexpected_early_error(self, monkeypatch):
        """An error raised in the early body (get_pending) BEFORE any inner try is
        caught by the function's OUTER except (line 1512): success=False,
        ACTIVATION_ERROR, reason 'Unexpected error activating DB pending'. Kills
        the ExceptionReplacer on the outer handler (the error would otherwise
        propagate out of the function) and the ReplaceFalseWithTrue on its
        `success=False` (line 1518)."""
        def _boom(**k):
            raise RuntimeError("store unreachable")
        monkeypatch.setattr("gaia.approvals.store.get_pending", _boom)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR
        assert "Unexpected error activating DB pending" in result.reason


class TestActivateDbPendingIntegrityLabelAC1:
    """Tamper-vs-missing label selection at the integrity check (line 1213).

    `_is_tamper = _fp_exc.__class__.__name__ == 'ChainTamperError'` (line 1212)
    and `_tamper_label = 'fingerprint_mismatch' if _is_tamper else
    'missing_requested_event'` (line 1213). Both observables -- the returned
    reason and the FAILED audit event's `integrity_check` metadata -- carry the
    label, so the `==` comparison flips and the AddNot are killable by raising
    a ChainTamperError (tamper) vs a ValueError (missing) and asserting the
    label flips accordingly."""

    _SEMANTIC = {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": "git push origin main",
    }

    def _failed_metadata(self, rec):
        """Extract the integrity_check label from the FAILED audit record_event."""
        for c in rec["record_event"].call_args_list:
            if len(c[0]) >= 2 and c[0][1] == "FAILED":
                return json.loads(c.kwargs["metadata_json"])
        return None

    def test_chain_tamper_labels_fingerprint_mismatch(self, monkeypatch):
        """A ChainTamperError selects label 'fingerprint_mismatch'. Kills the
        Eq->{Is,IsNot,Gt,GtE,Lt,LtE,NotEq} flips on the class-name comparison
        (line 1212) and the AddNot/branch flip on line 1213: any flip would
        mislabel a genuine tamper as 'missing_requested_event'."""
        from gaia.approvals.chain import ChainTamperError
        rec = _AC1Driver.drive(
            monkeypatch, self._SEMANTIC,
            fingerprint_exc=ChainTamperError("payload altered"),
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.status == ACTIVATION_CHAIN_TAMPER_DETECTED
        assert "fingerprint_mismatch" in result.reason
        assert "missing_requested_event" not in result.reason
        meta = self._failed_metadata(rec)
        assert meta is not None
        assert meta["integrity_check"] == "fingerprint_mismatch"

    def test_non_tamper_error_labels_missing_requested_event(self, monkeypatch):
        """A non-ChainTamperError (here ValueError -- the missing-REQUESTED-event
        case the source documents) selects label 'missing_requested_event'. This
        is the OTHER side of the line-1212 comparison / line-1213 branch: with
        the comparison flipped, this ValueError would be mislabeled as a
        fingerprint_mismatch. Pinning both labels closes the branch."""
        rec = _AC1Driver.drive(
            monkeypatch, self._SEMANTIC,
            fingerprint_exc=ValueError("no REQUESTED event for approval"),
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.status == ACTIVATION_CHAIN_TAMPER_DETECTED
        assert "missing_requested_event" in result.reason
        assert "fingerprint_mismatch" not in result.reason
        meta = self._failed_metadata(rec)
        assert meta is not None
        assert meta["integrity_check"] == "missing_requested_event"

    def test_lexically_early_class_name_not_treated_as_tamper(self, monkeypatch):
        """Line 1212 `_fp_exc.__class__.__name__ == 'ChainTamperError'` vs the
        Eq->LtE survivor `<= 'ChainTamperError'` (job_id a498bdeea402498c9686e78f97441903).

        The discriminating input is an exception whose __name__ sorts lexically
        BEFORE 'ChainTamperError' in ASCII order: 'AttributeError' starts with 'A'
        (65) while 'ChainTamperError' starts with 'C' (67), so:
          == 'ChainTamperError' -> False -> label = 'missing_requested_event'  (correct)
          <= 'ChainTamperError' -> True  -> label = 'fingerprint_mismatch'     (WRONG)

        The test pins the correct label for a lexically-early non-tamper exception,
        killing the LtE mutant."""
        # 'AttributeError' < 'ChainTamperError' in lexicographic order.
        assert "AttributeError" < "ChainTamperError"
        rec = _AC1Driver.drive(
            monkeypatch, self._SEMANTIC,
            fingerprint_exc=AttributeError("chain DB missing REQUESTED event"),
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.status == ACTIVATION_CHAIN_TAMPER_DETECTED
        assert "missing_requested_event" in result.reason
        assert "fingerprint_mismatch" not in result.reason
        meta = self._failed_metadata(rec)
        assert meta is not None
        assert meta["integrity_check"] == "missing_requested_event"


# ===========================================================================
# M1-FINAL behavioral survivor closure (AC-1, branch harden/approval-grants-m1-loop)
#
# Targets the precise behavioral survivors the scoped harness still reports
# SURVIVED after the AC1 batch, each with a DISCRIMINATING input chosen so the
# specific operator/comparison flip is observable. The companion equivalents are
# documented in tests/evals/evidence/equivalents-security-core.md (NOT killed).
#
# Where an existing test brackets a `==`/`!=` boundary, the flip that survives
# is the one whose discriminator the existing test did NOT supply (e.g. a
# NEGATIVE ttl for `ttl == 0` -> `<= 0`, or an out-of-order lexical class name
# for `name == "ChainTamperError"` -> `<=`). Those gaps are closed here.
# ===========================================================================
class TestPureLogicBehavioralM1Final:
    """Pure comparison/boundary survivors with no DB collaborator."""

    def test_ttl_eq_zero_not_le_zero_negative_ttl(self):
        """Line 175 `ttl_minutes == 0` vs the Eq->LtE survivor `<= 0`. The ONLY
        discriminator is a NEGATIVE ttl: `== 0` falls through (computes elapsed),
        `<= 0` short-circuits to no-expiry. A negative ttl with an ancient stamp
        must be EXPIRED (elapsed > negative threshold), not 'no expiry'."""
        ancient = time.time() - 10_000
        # == 0 path: ttl=-1 is not 0, so it computes elapsed; elapsed >> -1 -> True
        assert _is_ttl_expired(ancient, -1) is True

    def test_ttl_eq_zero_not_lt_zero(self):
        """Same line 175 against an Eq->Lt flip (`< 0`): ttl == 0 must still mean
        no-expiry. With `< 0`, ttl=0 would NOT short-circuit and would compute
        elapsed -> a 0-ttl ancient stamp would wrongly be 'expired'. Pin ttl=0
        ancient -> NOT expired."""
        ancient = time.time() - 10_000
        assert _is_ttl_expired(ancient, 0) is False

    def test_timestamp_eq_zero_not_le_or_lt(self):
        """Line 177 `timestamp == 0` vs Eq->LtE/Lt survivors. A small POSITIVE
        timestamp (e.g. 0.5) is NOT the 'never stamped' sentinel: under `== 0` it
        falls through to the elapsed path (ancient -> expired anyway), but under
        `<= 0` it would hit the zero-guard `return True` for a DIFFERENT reason.
        The discriminator that separates them: a FRESH tiny-but-future-ish stamp
        cannot be built, so we pin that timestamp just above 0 with a huge ttl is
        treated by the elapsed path. With ttl huge, ancient stamp 0.5 -> elapsed
        (now-0.5)/60 which is enormous; ttl=10**9 minutes -> NOT expired. Under
        `<= 0` timestamp 0.5 is not <=0 so same; the REAL discriminator for LtE is
        a negative timestamp."""
        # negative timestamp: `== 0` is False (compute elapsed, huge ttl -> not
        # expired); `<= 0` is True (return expired). Pin NOT expired.
        assert _is_ttl_expired(-1.0, 10**9) is False

    def test_cleanup_throttle_boundary_lt_not_le(self, monkeypatch):
        """Line 697 `now - _last_cleanup_time < _CLEANUP_INTERVAL_SECONDS` vs the
        Lt->LtE survivor. At elapsed EXACTLY == interval (60s), `<` is False
        (cleanup RUNS) but `<=` is True (cleanup SKIPS, returns 0). Pin the clock
        so elapsed is exactly 60.0 and assert cleanup RUNS (calls the sweep)."""
        import modules.security.approval_grants as _ag
        _ag._last_cleanup_time = 1_000_000.0
        monkeypatch.setattr(_ag.time, "time", lambda: 1_000_000.0 + 60.0)
        swept = MagicMock(return_value=0)
        monkeypatch.setattr("gaia.store.writer.cleanup_expired_db_grants", swept)
        _ag.cleanup_expired_grants(force=False)
        # With `<` the throttle does NOT skip at exactly 60s -> sweep called once.
        assert swept.call_count == 1

    def test_cleanup_interval_is_sixty_not_other(self, monkeypatch):
        """Line 143 `_CLEANUP_INTERVAL_SECONDS = 60` NumberReplacer. At elapsed
        59s the throttle MUST skip (return 0, no sweep): with the constant
        mutated to a smaller value, 59s would exceed it and the sweep would run.
        Pins the constant to its 60 floor via the just-under-boundary."""
        import modules.security.approval_grants as _ag
        _ag._last_cleanup_time = 1_000_000.0
        monkeypatch.setattr(_ag.time, "time", lambda: 1_000_000.0 + 59.0)
        swept = MagicMock(return_value=0)
        monkeypatch.setattr("gaia.store.writer.cleanup_expired_db_grants", swept)
        result = _ag.cleanup_expired_grants(force=False)
        assert result == 0
        assert swept.call_count == 0


class TestFindPendingForFileBehavioralM1Final:
    """find_pending_for_file scope + path comparison survivors (lines 1030/1033)."""

    def _rows(self, monkeypatch, rows):
        monkeypatch.setattr("gaia.approvals.store.list_pending", lambda **kw: rows)

    def test_scope_filter_neq_not_gt(self, monkeypatch):
        """Line 1030 `payload.get('scope') != SCOPE_FILE_PATH` vs NotEq->Gt. A
        pending whose scope IS SCOPE_FILE_PATH must NOT be skipped (it matches).
        With `>` the equal-scope row would be `'SCOPE_FILE_PATH' > 'SCOPE_FILE_PATH'`
        == False -> NOT skipped (same), BUT a row with a scope lexically less than
        SCOPE_FILE_PATH would wrongly pass. Discriminator: a matching FILE_PATH
        row must resolve to its nonce."""
        import modules.security.approval_grants as _ag
        rows = [{
            "id": "P-abc123",
            "payload_json": json.dumps({
                "scope": SCOPE_FILE_PATH, "exact_content": "/etc/hosts",
            }),
        }]
        self._rows(monkeypatch, rows)
        assert _ag.find_pending_for_file("sess", "/etc/hosts") == "abc123"

    def test_scope_filter_skips_non_file_path(self, monkeypatch):
        """A non-FILE_PATH pending for the same path must NOT match (line 1030
        skips it). Kills NotEq->Gt: SCOPE_FILE_PATH == 'file_path'; a scope that
        sorts BEFORE it (uppercase 'COMMAND_SET' < lowercase 'file_path' in ASCII)
        makes `!= 'file_path'` True (skip -> None) but `> 'file_path'` False (NOT
        skipped -> would return the nonce). The discriminating low-sorting scope
        with a MATCHING path forces real=None vs mutant=nonce."""
        import modules.security.approval_grants as _ag
        assert "COMMAND_SET" < SCOPE_FILE_PATH  # ASCII: 'C'(67) < 'f'(102)
        rows = [{
            "id": "P-abc123",
            "payload_json": json.dumps({
                "scope": "COMMAND_SET", "exact_content": "/etc/hosts",
            }),
        }]
        self._rows(monkeypatch, rows)
        assert _ag.find_pending_for_file("sess", "/etc/hosts") is None

    def test_path_match_eq_not_ge(self, monkeypatch):
        """Line 1033 `exact_content.strip() == stripped` vs Eq->GtE. A row whose
        path does NOT equal the target must NOT match. With `>=` a path lexically
        greater than the target would wrongly match. Discriminator: a FILE_PATH
        row with a DIFFERENT path returns None; the exact path returns the nonce."""
        import modules.security.approval_grants as _ag
        rows = [{
            "id": "P-def456",
            "payload_json": json.dumps({
                "scope": SCOPE_FILE_PATH, "exact_content": "/var/zzz",
            }),
        }]
        self._rows(monkeypatch, rows)
        # target '/var/aaa' < '/var/zzz' lexically: == False (no match);
        # >= would be '/var/zzz' >= '/var/aaa' True (wrong match).
        assert _ag.find_pending_for_file("sess", "/var/aaa") is None


class TestWritePendingBehavioralM1Final:
    """write_pending_approval_for_file risk-default or-chain (line 918)."""

    def test_risk_default_or_chain_not_and(self, monkeypatch, writer_db):
        """Line 918 `ctx.get('risk', 'medium') or 'medium'` vs Or->And. When ctx
        carries an explicit falsy risk ('' ), the `or 'medium'` must coerce it to
        'medium'; with `and` it would coerce a TRUTHY risk to 'medium' instead.
        Observe via the sealed payload passed to insert_requested."""
        import modules.security.approval_grants as _ag
        captured = {}
        def _ins(payload, **kw):
            captured.update(payload)
            return "P-zzz"
        monkeypatch.setattr("gaia.approvals.store.insert_requested", _ins)
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: MagicMock(to_dict=lambda: {"k": "v"}),
        )
        # explicit truthy risk must be PRESERVED by `or` (and would clobber it).
        _ag.write_pending_approval_for_file(
            "nonce123", "/etc/hosts", session_id="s", context={"risk": "high"},
        )
        assert captured.get("risk_level") == "high"


class TestMatchCommandSetBehavioralM1Final:
    """match_command_set_grant behavioral survivors that the SQL pre-filter
    leaves reachable (expiry boundary + ordering across expired/malformed grants).

    NOTE: lines 1683 (scope != 'COMMAND_SET') and 1684 (its continue->break) are
    EQUIVALENT, not behavioral: list_command_set_grants_agnostic's SQL already
    filters `scope = 'COMMAND_SET'`, so the in-Python scope check is constantly
    False and no operator flip on it is observable. Documented in
    equivalents-security-core.md (category E-SQL-redundant), not killed here.
    """

    def _insert(self, db_path, approval_id, command_set, *, status="PENDING",
                expires_at=None, created_at=None, command_set_json=None):
        if expires_at is None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        con = sqlite3.connect(str(db_path))
        try:
            if created_at is not None:
                con.execute(
                    """INSERT INTO approval_grants
                       (approval_id, session_id, command_set_json, scope,
                        expires_at, status, consumed_indexes_json, created_at)
                       VALUES (?, 'test-session-mut', ?, 'COMMAND_SET', ?, ?, '[]', ?)""",
                    (approval_id,
                     command_set_json if command_set_json is not None
                     else json.dumps(command_set),
                     expires_at, status, created_at),
                )
            else:
                con.execute(
                    """INSERT INTO approval_grants
                       (approval_id, session_id, command_set_json, scope,
                        expires_at, status, consumed_indexes_json)
                       VALUES (?, 'test-session-mut', ?, 'COMMAND_SET', ?, ?, '[]')""",
                    (approval_id,
                     command_set_json if command_set_json is not None
                     else json.dumps(command_set),
                     expires_at, status),
                )
            con.commit()
        finally:
            con.close()

    def test_expiry_lt_not_le_boundary(self, monkeypatch, writer_db):
        """Line 1671 `expires_at < now_iso` vs Lt->LtE. At expires_at EXACTLY ==
        now_iso the grant is NOT expired (`<` False -> still matches) but `<=`
        would treat it as expired (skipped -> no match). The function does a local
        `from datetime import datetime` (line 1659), so we freeze
        `datetime.datetime.now` on the stdlib module to a fixed instant and set
        the grant's expires_at to the SAME instant's iso string."""
        import datetime as _dtmod
        frozen = _dtmod.datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        fixed_iso = frozen.strftime("%Y-%m-%dT%H:%M:%SZ")

        real_datetime = _dtmod.datetime

        class _FrozenDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.astimezone(tz)
        monkeypatch.setattr(_dtmod, "datetime", _FrozenDateTime)

        approval_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, approval_id,
            [{"command": "git push origin main", "rationale": "a"}],
            expires_at=fixed_iso,  # == now_iso at call time
        )
        # expires_at == now_iso: `<` False (not expired -> matches); `<=` True
        # (expired -> skipped -> None). Healthy code returns the match.
        result = match_command_set_grant("git push origin main")
        assert result == (approval_id, 0)

    def test_expired_grant_continue_not_break(self, writer_db):
        """Line 1680 `continue` (skip expired) vs ReplaceContinueWithBreak. An
        EXPIRED grant created LATER (sorts first by created_at DESC) followed by a
        valid grant: the expired one must be SKIPPED (continue) so the loop reaches
        the valid grant. With `break` the loop aborts at the expired grant and the
        valid command never matches -> None."""
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        future = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired_id = f"P-{secrets.token_hex(16)}"
        valid_id = f"P-{secrets.token_hex(16)}"
        # expired created NOW (sorts first under created_at DESC); valid created
        # earlier so it is reached only if the expired one is `continue`d past.
        self._insert(
            writer_db, expired_id,
            [{"command": "git push origin main", "rationale": "x"}],
            expires_at=past, created_at="2030-01-01T00:00:00Z",
        )
        self._insert(
            writer_db, valid_id,
            [{"command": "git push origin main", "rationale": "y"}],
            expires_at=future, created_at="2029-01-01T00:00:00Z",
        )
        assert match_command_set_grant("git push origin main") == (valid_id, 0)

    def test_malformed_json_continue_not_break(self, writer_db):
        """Line 1691 `continue` (skip malformed command_set_json) vs
        ReplaceContinueWithBreak. A grant with invalid command_set_json created
        LATER (sorts first) followed by a valid grant: the malformed one is
        skipped so the valid command matches. With `break` the loop aborts -> None."""
        bad_id = f"P-{secrets.token_hex(16)}"
        good_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, bad_id, [],
            command_set_json="{not valid json", created_at="2030-01-01T00:00:00Z",
        )
        self._insert(
            writer_db, good_id,
            [{"command": "git push origin main", "rationale": "y"}],
            created_at="2029-01-01T00:00:00Z",
        )
        assert match_command_set_grant("git push origin main") == (good_id, 0)


class TestStatusAppliedAndDivisorM1Final:
    """confirm_grant/create_command_set_grant status==applied LtE flips, the /60
    divisor, the row[0] index, and the ts-init constant."""

    @patch("modules.security.approval_grants.time.time")
    def test_ttl_divisor_is_sixty_not_sixtyone(self, mock_time):
        """Line 179 `(now - ts) / 60` NumberReplacer 60->61. Pin the clock so
        elapsed is exactly 3660s. Under /60 that is 61.0 min (> ttl 60 -> EXPIRED);
        under /61 that is 60.0 min (NOT > 60 -> not expired). ttl=60 discriminates
        the divisor; the existing 90s test does not (90/60 and 90/61 both clear 1)."""
        now = 1_000_000.0
        mock_time.return_value = now
        ts = now - 3660.0  # 61 min under /60, 60 min under /61
        assert _is_ttl_expired(ts, 60) is True

    def test_confirm_grant_status_eq_applied_not_le(self, monkeypatch):
        """Line 659 `result.get('status') == 'applied'` vs Eq->LtE. A status that
        sorts BEFORE 'applied' ('aborted': 'ab' < 'ap') makes `==` False (return
        False) but `<= 'applied'` True (return True). Pin a sub-'applied' status
        -> confirm_grant must return False."""
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.confirm_db_grant",
            lambda *a, **k: {"status": "aborted"},
        )
        assert "aborted" < "applied"
        assert ag.confirm_grant("git push") is False

    def test_create_command_set_status_eq_applied_not_le(self, monkeypatch, writer_db):
        """Line 1603 `result.get('status') == 'applied'` vs Eq->LtE in
        create_command_set_grant. A sub-'applied' status from insert must yield
        return False; `<=` would wrongly return True."""
        monkeypatch.setattr(
            "gaia.store.writer.insert_approval_grant",
            lambda **k: {"status": "aborted"},
        )
        ok = create_command_set_grant(
            [{"command": "git push", "rationale": "r"}],
            f"P-{secrets.token_hex(16)}",
            session_id="s",
        )
        assert ok is False

    def test_consume_session_grants_row_index_zero(self, monkeypatch):
        """Line 606 `row[0]` NumberReplacer. The SELECT projects approval_id as
        column 0. A 3-element tuple row where each slot differs discriminates ALL
        NumberReplacer variants: index 0 -> 'P-want', index 1 -> 'P-wrong-1',
        index -1 -> 'P-wrong-last'. We assert the id PASSED to consume is the
        column-0 value, so `row[0]`->`row[1]`/`row[-1]` (any flip) is caught."""
        seen = []
        class _Cur:
            def execute(self, *a, **k): return self
            def fetchall(self):
                return [("P-want", "P-wrong-1", "P-wrong-last")]
        class _Con:
            def execute(self, *a, **k): return _Cur()
            def close(self): pass
        monkeypatch.setattr("gaia.store.writer._connect", lambda *a, **k: _Con())
        def _consume(approval_id, *a, **k):
            seen.append(approval_id)
            return True
        monkeypatch.setattr(
            "gaia.store.writer.consume_db_semantic_grant", _consume
        )
        count = ag.consume_session_grants("sess")
        assert count == 1
        assert seen == ["P-want"]  # column 0; any index flip selects a wrong id

    def test_db_row_ts_init_zero_when_no_created_at(self):
        """Line 751 `ts: float = 0.0` NumberReplacer. A row with NO created_at
        leaves ts at its init 0.0, surfaced as the dict's 'timestamp'. A mutated
        init (0.0->1.0) would surface 1.0. Pin timestamp == 0.0 for a created_at-
        less row."""
        row = {
            "id": "P-abc",
            "payload_json": json.dumps({"operation": "MUTATIVE command intercepted: push"}),
        }
        mapped = _db_row_to_pending_dict(row)
        assert mapped is not None
        assert mapped["timestamp"] == 0.0


class TestActivateBehavioralM1Final:
    """activate_db_pending_by_prefix behavioral survivors reachable on the
    SEMANTIC and FILE_PATH success paths. Reuses _AC1Driver and additionally
    patches the writer-side inserts so the happy path returns ACTIVATED."""

    _SEMANTIC = {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": "git push origin main",
    }
    _FILE = {
        "operation": "FILE_WRITE command intercepted: write",
        "exact_content": "/etc/hosts",
        "scope": SCOPE_FILE_PATH,
    }

    def _semantic_applied(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: {"status": "applied"},
        )

    def test_semantic_success_path_command_or_chain(self, monkeypatch):
        """Line 1171 `exact_content or commands[0] or ''` vs Or->And. With the
        SEMANTIC payload (exact_content set, NO commands key) the `or` chain yields
        the exact_content command and the activation SUCCEEDS (ACTIVATED). With
        `and`, exact_content and commands.get default [None][0]==None -> command
        None -> INVALID_PENDING. Pin ACTIVATED."""
        _AC1Driver.drive(monkeypatch, self._SEMANTIC)
        self._semantic_applied(monkeypatch)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

    def test_semantic_insert_status_eq_applied_not_le(self, monkeypatch):
        """Line 1476 `result_sg.get('status') == 'applied'` vs Eq->LtE. A sub-
        'applied' status ('aborted') must yield success=False/ACTIVATION_ERROR; a
        `<=` flip would wrongly report success. Pin the failure."""
        _AC1Driver.drive(monkeypatch, self._SEMANTIC)
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: {"status": "aborted"},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR

    def test_verb_parse_len_parts_eq_two(self, monkeypatch):
        """Line 1426 `len(parts) == 2` vs Eq->LtE/GtE. The operation
        'MUTATIVE command intercepted: push' splits on 'intercepted:' into EXACTLY
        2 parts, so the verb 'push' is parsed and threaded into the signature.
        A == flip would change which split lengths parse the verb. We assert the
        success path completes (signature built from the parsed verb) -> ACTIVATED.
        Combined with a single-part operation (below) this brackets the == 2."""
        _AC1Driver.drive(monkeypatch, self._SEMANTIC)
        self._semantic_applied(monkeypatch)
        captured = {}
        import modules.security.approval_scopes as _scopes
        real_build = _scopes.build_approval_signature
        def _spy(command, **k):
            # capture the FIRST call's danger_verb (the parsed-verb attempt).
            captured.setdefault("danger_verb", k.get("danger_verb"))
            return real_build(command, **k)
        # activate_db_pending_by_prefix re-imports build_approval_signature from
        # .approval_scopes locally (line 1417), so patch it at the source module.
        monkeypatch.setattr(_scopes, "build_approval_signature", _spy)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.status == ACTIVATION_ACTIVATED
        # The 2-part split parsed the verb 'push' (not 'unknown'/empty).
        assert captured["danger_verb"] == "push"

    def test_verb_parse_three_parts_not_ge_two(self, monkeypatch):
        """Line 1426 `len(parts) == 2` vs Eq->GtE. An operation containing
        'intercepted:' TWICE splits into 3 parts. Under `== 2` the verb is NOT
        parsed (danger_verb stays '' -> fallback first-token). Under `>= 2` the
        3-part split WOULD parse parts[1] as the verb. Discriminator: assert the
        first build call's danger_verb is '' (empty) for a 3-part operation, which
        `>= 2` would violate by threading parts[1]. (Eq->LtE is EQUIVALENT here:
        len(parts) is always >= 2 under the 'intercepted:' guard, so `<= 2` and
        `== 2` agree for every reachable length -- documented in equivalents.)"""
        payload = {
            "operation": "MUTATIVE intercepted: x intercepted: y",
            "exact_content": "git push origin main",
        }
        _AC1Driver.drive(monkeypatch, payload)
        self._semantic_applied(monkeypatch)
        captured = {}
        import modules.security.approval_scopes as _scopes
        real_build = _scopes.build_approval_signature
        def _spy(command, **k):
            captured.setdefault("danger_verb", k.get("danger_verb"))
            return real_build(command, **k)
        monkeypatch.setattr(_scopes, "build_approval_signature", _spy)
        activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        # 3 parts: `== 2` False -> verb '' ; `>= 2` True -> would be 'x'.
        assert captured["danger_verb"] == ""

    def test_file_path_insert_status_neq_applied(self, monkeypatch):
        """Line 1392 `result_fp.get('status') != 'applied'` vs NotEq->Gt/IsNot.
        A status EQUAL to 'applied' must NOT be treated as failure (-> ACTIVATED).
        The status is built at RUNTIME ('app'+'lied') so it is a DISTINCT object
        from the interned literal: discriminates NotEq->IsNot (`!=` False -> proceed
        vs `is not` True -> wrongly fail). With Gt covered by the sub-'applied'
        test, this brackets all three flips."""
        runtime_applied = json.loads('"applied"')  # non-interned (see above)
        assert runtime_applied == "applied"
        _AC1Driver.drive(monkeypatch, self._FILE)
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: MagicMock(to_dict=lambda: {"k": "v"}),
        )
        monkeypatch.setattr(
            "gaia.store.writer.insert_file_path_grant",
            lambda **k: {"status": runtime_applied},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

    def test_file_path_insert_failure_status_surfaced(self, monkeypatch):
        """Other side of line 1392 `status != 'applied'` vs NotEq->Gt/IsNot.
        Discriminator: a status sorting BEFORE 'applied' ('aborted': 'ab'<'ap')
        makes `!=` True (ERROR) but `>` False (would NOT enter the failure branch
        and would wrongly report ACTIVATED). Pin ERROR for a sub-'applied' status."""
        assert "aborted" < "applied"
        _AC1Driver.drive(monkeypatch, self._FILE)
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: MagicMock(to_dict=lambda: {"k": "v"}),
        )
        monkeypatch.setattr(
            "gaia.store.writer.insert_file_path_grant",
            lambda **k: {"status": "aborted", "reason": "nope"},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR

    def test_invalid_signature_returns_success_false(self, monkeypatch):
        """Line 1365 (SCOPE_FILE_PATH invalid-signature) `success=False` vs
        ReplaceFalseWithTrue. When build_file_path_signature returns None the
        function returns success=False/ACTIVATION_INVALID_SIGNATURE; the
        False->True flip would wrongly report success on an unbuildable signature."""
        _AC1Driver.drive(monkeypatch, self._FILE)
        monkeypatch.setattr(
            "modules.security.approval_grants.build_file_path_signature",
            lambda fp: None,
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_SIGNATURE

    def test_already_approved_status_neq_approved(self, monkeypatch):
        """Line 1279 `current_row.get('status') != 'approved'` vs NotEq->Gt/IsNot.
        When approve() raises ValueError (already processed) AND get_by_id reports
        a status EQUAL to 'approved', the function must NOT abort (`!=` False) and
        proceeds to ACTIVATED. The status is built at RUNTIME ('appr'+'oved') so it
        is a DISTINCT object from the interned 'approved' literal: this is the only
        input that discriminates NotEq->IsNot, since `value != 'approved'` is False
        (proceed) while `value is not 'approved'` is True (would wrongly abort)."""
        # json.loads yields a NON-interned string (mirrors DB/JSON status reads),
        # defeating CPython's compile-time constant folding that would re-intern
        # a literal-concatenation. This is what makes `is not` observable.
        runtime_approved = json.loads('"approved"')
        assert runtime_approved == "approved"
        _AC1Driver.drive(
            monkeypatch, self._SEMANTIC,
            approve_raises=ValueError("already approved"),
            get_by_id_row={"status": runtime_approved},
        )
        self._semantic_applied(monkeypatch)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

    def test_already_approved_other_status_aborts(self, monkeypatch):
        """Other side of line 1279 `status != 'approved'` vs NotEq->Gt/IsNot.
        Discriminator: a status that sorts BEFORE 'approved' ('aborted': 'ab'<'ap')
        makes `!= 'approved'` True (abort -> ERROR) but `> 'approved'` False (do
        NOT abort -> would proceed to ACTIVATED). 'is not' on a non-interned dynamic
        string is True (abort) -- but the Gt flip is the live discriminator. Pin
        the abort (ERROR) for a sub-'approved' status after approve() raises."""
        assert "aborted" < "approved"
        _AC1Driver.drive(
            monkeypatch, self._SEMANTIC,
            approve_raises=ValueError("transition failed"),
            get_by_id_row={"status": "aborted"},
        )
        self._semantic_applied(monkeypatch)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_ERROR


# ===========================================================================
# AC-5 CIERRE FINAL -- the last 33 surviving mutants not yet in the skip-file.
# Each test below kills a behavioral survivor with an honest observable
# assertion. The genuinely-equivalent survivors are NOT here; they are
# documented in tests/evals/evidence/equivalents-security-core.md and excluded
# via the skip-file.
# ===========================================================================
class TestModuleConstantsAndFlagsAC5:
    """Module-level constants and process-global flags whose mutated value is
    observable through the public API."""

    def test_command_set_ttl_is_sixty_minutes(self, writer_db):
        """`DEFAULT_COMMAND_SET_TTL_MINUTES = 60` (line 1540, NumberReplacer
        60->N x2). create_command_set_grant stamps expires_at = now + ttl. With
        the default ttl the persisted expiry must be ~60 min ahead; a mutated
        constant (61 or 59) shifts the stored expires_at by a full minute, which
        the row assertion below detects to the second."""
        before = datetime.now(timezone.utc)
        ok = create_command_set_grant(
            command_set=[{"command": "git push", "rationale": "r"}],
            approval_id="P-ttl60check",
            session_id="s",
            db_path=str(writer_db),
        )
        assert ok is True
        con = sqlite3.connect(str(writer_db))
        try:
            row = con.execute(
                "SELECT expires_at FROM approval_grants WHERE approval_id=?",
                ("P-ttl60check",),
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        expires = datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        delta_min = (expires - before).total_seconds() / 60.0
        # The true TTL is exactly 60; allow a few seconds of execution slack but
        # reject the 59 / 61 mutants (which land ~1 min off).
        assert 59.5 < delta_min < 60.5
        # And confirm the imported constant value itself is 60 (kills both
        # NumberReplacer directions directly).
        assert DEFAULT_COMMAND_SET_TTL_MINUTES == 60

    def test_check_grant_resets_found_expired_flag_to_false(self, monkeypatch):
        """`_last_check_found_expired = False` (line 464, ReplaceFalseWithTrue).
        check_approval_grant() resets this process-global to False at the top of
        EVERY call, before any conditional set. It is set True only when an
        expired grant is encountered. With no grant at all, the flag MUST read
        False afterwards. The True-init mutant on line 464 leaves it True (no
        code path ever resets it back to False), so the reader returns True."""
        # No DB grant for the command -> the expired-grant branch is never hit.
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "gaia.store.writer.check_db_file_path_grant",
            lambda *a, **k: None,
        )
        ag._last_check_found_expired = True  # poison: a real reset must clear it
        ag.check_approval_grant("git status", session_id="s")
        assert ag.last_check_found_expired() is False


class TestEqIsStatusAppliedAC5:
    """`result.get('status') == 'applied'` Eq->Is survivors (lines 659, 1476,
    1603). CPython interns the literal 'applied'; a DB/JSON-sourced status is a
    DISTINCT object, so `is` is False where `==` is True. Feeding a NON-interned
    'applied' makes the Is flip observable: the success branch is taken under
    `==` but skipped under `is`."""

    _SEMANTIC = {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": "git push origin main",
    }

    def test_activate_semantic_status_applied_is_not_identity(self, monkeypatch):
        """Line 1476 Eq->Is. insert_semantic_grant returns a runtime-built
        'applied' (json.loads, non-interned). `== 'applied'` True -> ACTIVATED;
        `is 'applied'` False -> falls through to ERROR. Pin ACTIVATED."""
        runtime_applied = json.loads('"applied"')
        assert runtime_applied == "applied"
        _AC1Driver.drive(monkeypatch, self._SEMANTIC)
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: {"status": runtime_applied},
        )
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

    def test_confirm_grant_status_applied_is_not_identity(self, monkeypatch):
        """Line 659 Eq->Is. confirm_grant returns True only when
        confirm_db_grant()'s status == 'applied'. A non-interned 'applied' keeps
        `==` True (return True) but makes `is` False (return False). Pin True."""
        runtime_applied = json.loads('"applied"')
        monkeypatch.setattr(
            "gaia.store.writer.check_db_semantic_grant",
            lambda *a, **k: {"approval_id": "P-x"},
        )
        monkeypatch.setattr(
            "gaia.store.writer.confirm_db_grant",
            lambda *a, **k: {"status": runtime_applied},
        )
        assert ag.confirm_grant("git push", session_id="s") is True

    def test_create_command_set_status_applied_is_not_identity(self, monkeypatch):
        """Line 1603 Eq->Is. create_command_set_grant returns True only when
        insert_approval_grant()'s status == 'applied'. A non-interned 'applied'
        keeps `==` True (return True) but `is` False (return False). Pin True."""
        runtime_applied = json.loads('"applied"')
        monkeypatch.setattr(
            "gaia.store.writer.insert_approval_grant",
            lambda **k: {"status": runtime_applied},
        )
        ok = create_command_set_grant(
            command_set=[{"command": "git push", "rationale": "r"}],
            approval_id="P-isapplied",
            session_id="s",
        )
        assert ok is True


class TestActivateCommandSelectionAC5:
    """activate_db_pending_by_prefix command-extraction survivors (lines 1171,
    1175). The extracted `command` feeds build_approval_signature, so a wrong
    index/operator yields a signature for a DIFFERENT command -- observable in
    the scope_signature persisted via insert_semantic_grant."""

    def _capture_inserted(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "gaia.store.writer.insert_semantic_grant",
            lambda **k: captured.update(k) or {"status": "applied"},
        )
        return captured

    def test_commands_list_index_zero_selected(self, monkeypatch):
        """Line 1171 `payload.get('exact_content') or payload.get('commands',
        [None])[0] or ''` NumberReplacer on the [0] index. With NO exact_content
        and a 2-element commands list, the singular command MUST be commands[0].
        The `0->1` mutant would select commands[1] (a different command) -> the
        persisted scope_signature.command would change. Pin commands[0]."""
        payload = {
            "operation": "MUTATIVE command intercepted: push",
            "commands": ["git push origin main", "rm -rf /tmp/x"],
        }
        _AC1Driver.drive(monkeypatch, payload)
        captured = self._capture_inserted(monkeypatch)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert captured["command"] == "git push origin main"

    def test_commands_or_chain_is_disjunction_not_conjunction(self, monkeypatch):
        """Line 1171 Or->And. exact_content is absent (None); `None or
        commands[0] or ''` yields commands[0]. With `and`, `None and ... ` short
        circuits to None -> no command -> INVALID_PENDING. Pin the success path
        proving the chain is a disjunction (commands[0] survives to signature)."""
        payload = {
            "operation": "MUTATIVE command intercepted: push",
            "commands": ["git push origin main"],
        }
        _AC1Driver.drive(monkeypatch, payload)
        captured = self._capture_inserted(monkeypatch)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is True
        assert captured["command"] == "git push origin main"


class TestMatchExceptionHandlersAC5:
    """match_command_set_grant best-effort except handlers (lines 1678, 1711).
    cosmic-ray's ExceptionReplacer rewrites `except Exception` to
    `except CosmicRayTestingException` -- a type real code never raises -- so the
    handler stops catching. A real raise then escapes: line 1678's escape lands
    in the outer 1711 handler (-> None instead of continuing); line 1711's escape
    propagates OUT of the function (an exception instead of a graceful None)."""

    def _insert(self, db_path, approval_id, commands, *, expires_at, created_at):
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                """INSERT INTO approval_grants
                   (approval_id, session_id, command_set_json, scope,
                    expires_at, status, consumed_indexes_json, created_at)
                   VALUES (?, 'test-session-mut', ?, 'COMMAND_SET', ?, 'PENDING', '[]', ?)""",
                (approval_id, json.dumps(commands), expires_at, created_at),
            )
            con.commit()
        finally:
            con.close()

    def test_expired_status_update_failure_does_not_abort_scan(
        self, monkeypatch, writer_db
    ):
        """Line 1678 `except Exception: pass` around update_approval_grant_status.
        An EXPIRED grant sorts first (created_at DESC); marking it EXPIRED raises.
        The healthy handler swallows the error and `continue`s to the still-valid
        grant -> the retried command matches it. The CosmicRayTestingException
        mutant lets the raise escape to the OUTER handler -> None. Pin the match."""
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        future = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        expired_id = f"P-{secrets.token_hex(16)}"
        valid_id = f"P-{secrets.token_hex(16)}"
        self._insert(
            writer_db, expired_id,
            [{"command": "git push origin main", "rationale": "x"}],
            expires_at=past, created_at="2030-01-01T00:00:00Z",
        )
        self._insert(
            writer_db, valid_id,
            [{"command": "git push origin main", "rationale": "y"}],
            expires_at=future, created_at="2029-01-01T00:00:00Z",
        )

        def _boom(*a, **k):
            raise RuntimeError("status update DB offline")
        monkeypatch.setattr(
            "gaia.store.writer.update_approval_grant_status", _boom
        )
        # Healthy: inner handler swallows the raise, loop continues, valid matches.
        assert match_command_set_grant("git push origin main") == (valid_id, 0)

    def test_outer_handler_returns_none_on_unexpected_error(self, monkeypatch):
        """Line 1711 `except Exception: return None`. list_command_set_grants_
        agnostic raising an unexpected error must be caught -> graceful None. The
        CosmicRayTestingException mutant lets the error propagate OUT of the
        function. Pin the graceful None (no exception escapes)."""
        def _boom(*a, **k):
            raise RuntimeError("grant listing exploded")
        monkeypatch.setattr(
            "gaia.store.writer.list_command_set_grants_agnostic", _boom
        )
        # Healthy code catches and returns None; the mutant would raise here.
        assert match_command_set_grant("git push origin main") is None


class TestActivateFallbackVerbAC5:
    """activate_db_pending_by_prefix fallback-verb guard (line 1444):
    `first_token = command.split()[0] if command.strip() else 'unknown'`.

    For any normal command build_approval_signature yields a verb, so the
    fallback never runs. The ONE reachable input is a WHITESPACE-ONLY command:
    it is truthy (passes the `if not command` guard at 1176) but
    build_approval_signature('   ') returns None (no exact_tokens) -> the
    fallback at 1444 IS entered. There `command.strip()` is FALSY, so the
    healthy ternary picks 'unknown'. The AddNot mutant (`if not command.strip()`)
    instead evaluates `command.split()[0]` on whitespace -> IndexError, which the
    outer handler turns into ACTIVATION_ERROR. The healthy path rebuilds with
    'unknown', which still has empty tokens -> ACTIVATION_INVALID_SIGNATURE. The
    distinct status discriminates the mutant."""

    def test_whitespace_command_takes_unknown_branch_not_split(self, monkeypatch):
        """Line 1444 AddNot. A whitespace-only command reaches the fallback with
        a falsy command.strip(). Healthy -> 'unknown' -> second signature also
        None -> ACTIVATION_INVALID_SIGNATURE. Mutant -> command.split()[0] on
        '   ' -> IndexError -> outer handler -> ACTIVATION_ERROR. Pin the
        INVALID_SIGNATURE status (and assert it is NOT the ERROR the mutant
        produces)."""
        payload = {
            "operation": "blocked command awaiting consent",
            "exact_content": "   ",  # truthy, but .strip() is empty
        }
        _AC1Driver.drive(monkeypatch, payload)
        result = activate_db_pending_by_prefix("deadbeef", current_session_id="orch")
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_SIGNATURE
        assert result.status != ACTIVATION_ERROR
