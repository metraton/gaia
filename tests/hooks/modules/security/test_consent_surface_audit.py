#!/usr/bin/env python3
"""Tests for the consent surface: render it whole, persist what the user saw.

Two defects in the consent surface are covered here, both observed live:

  1. A ``COMMAND_SET`` payload covers N commands but ``exact_content`` holds
     only command [0], so a presentation built from that singular field asks
     the user to consent to ONE command while the activated grant covers N.
     The regression guard is ``verify_consent_surface_completeness``: the old
     single-line rendering of a 3-command payload must now be reported
     incomplete, and no proper subset of the commands may pass.
  2. The ``SHOWN`` event was written with an empty payload, so afterwards there
     was no way to establish what text the user was shown. The guard is that
     activation now persists the full question text and that a SHOWN event
     WITHOUT that text is detectable (``audit_consent_surface``) -- which is
     also what makes the change additive: legacy rows report not-auditable
     rather than needing migration.

Nonce activation is asserted end-to-end against the NEW label form, since a
label change that broke ``extract_nonce_from_label`` would silently disable
every approval.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Sys-path setup
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = "test-consent-surface-session"

# The exact shape of the live incident: one remote rewrite the user saw, plus
# two pushes they did not.
BATCH_COMMANDS = [
    "git remote set-url origin git@github.com:metraton/gaia.git",
    "git push origin main",
    "git push origin --tags",
]


def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _singular_payload(command: str) -> dict:
    return {
        "operation": "MUTATIVE command intercepted: apply",
        "exact_content": command,
        "scope": command.split()[0],
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": "Single command",
        "commands": [command],
    }


def _command_set_payload(commands: list[str]) -> dict:
    """Sealed payload as bash_validator emits it for a chained T3 batch.

    ``exact_content`` is command [0] -- the singular stand-in that must not be
    mistaken for the whole consent surface.
    """
    return {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": commands[0],
        "scope": "COMMAND_SET",
        "risk_level": "high",
        "rollback_hint": None,
        "rationale": "Batch under one consent",
        "commands": list(commands),
        "command_set": [
            {"command": c, "rationale": f"step {i}"}
            for i, c in enumerate(commands, start=1)
        ],
    }


def _legacy_singular_surface(payload: dict) -> str:
    """The pre-fix rendering: template.md's singular COMANDO line, verbatim.

    This is the text that was actually shown for the live COMMAND_SET -- the
    case that used to be accepted and must now be reported incomplete.
    """
    return (
        "APPROVAL REQUIRED\n\n"
        f"OPERACION:  {payload['operation']}\n"
        f"COMANDO:    {payload['exact_content']}\n"
        f"SCOPE:      {payload['scope']}\n"
        f"RIESGO:     {payload['risk_level']} -- {payload['rationale']}\n"
        "ROLLBACK:   NOT REVERSIBLE\n"
    )


def _make_v12_schema(con: sqlite3.Connection) -> None:
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
            revoked_at            TEXT
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


@pytest.fixture()
def approvals_db(tmp_path, monkeypatch):
    """File-backed approvals DB shared by gaia.approvals.store and gaia.store.writer."""
    db_path = tmp_path / "consent_surface.db"
    seed = sqlite3.connect(str(db_path))
    _make_v12_schema(seed)
    seed.commit()

    def _open() -> sqlite3.Connection:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
        return con

    monkeypatch.setattr("gaia.approvals.store._open_db", _open)

    import gaia.store.writer as writer
    monkeypatch.setattr(writer, "_connect", lambda db_path_arg=None: _open())

    import gaia.approvals.store as store
    orig_get_pending = store.get_pending

    def patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = _open()
        return orig_get_pending(
            session_id=session_id, all_sessions=all_sessions, con=con
        )

    monkeypatch.setattr("gaia.approvals.store.get_pending", patched_get_pending)
    monkeypatch.setenv("CLAUDE_SESSION_ID", SESSION_ID)

    import modules.security.approval_grants as ag
    monkeypatch.setattr(ag, "get_plugin_data_dir", lambda: tmp_path / ".claude")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False

    yield db_path, seed, store
    seed.close()


def _insert_pending(store, payload: dict) -> str:
    return store.insert_requested(
        payload, agent_id="test-agent", session_id=SESSION_ID
    )


def _shown_event(store, approval_id: str, con) -> dict:
    events = store.replay_for_approval(approval_id, con=con)
    shown = [e for e in events if e["event_type"] == "SHOWN"]
    assert shown, f"No SHOWN event for {approval_id}"
    return shown[-1]


# ---------------------------------------------------------------------------
# Defect 1 -- a COMMAND_SET cannot be presented as fewer than N commands
# ---------------------------------------------------------------------------

class TestConsentSurfaceCompleteness:
    """The rendering guard: N covered commands means N shown commands."""

    def test_legacy_singular_rendering_of_a_batch_is_incomplete(self):
        """REGRESSION: the pre-fix surface for the live incident must now fail.

        The user was shown ONE command (`git remote set-url`) for a payload
        covering three. That text was accepted before this guard existed; it is
        now reported incomplete, naming the two commands it hid.
        """
        from modules.security.approval_grants import (
            verify_consent_surface_completeness,
        )

        payload = _command_set_payload(BATCH_COMMANDS)
        complete, missing = verify_consent_surface_completeness(
            _legacy_singular_surface(payload), payload
        )

        assert complete is False, (
            "A surface showing only exact_content for a 3-command COMMAND_SET "
            "must be reported incomplete -- this is the consent-surface defect"
        )
        assert missing == BATCH_COMMANDS[1:], (
            f"The two hidden pushes must be named as missing, got {missing!r}"
        )

    def test_no_proper_subset_of_commands_passes(self):
        """Every strict subset of the covered commands is rejected."""
        from modules.security.approval_grants import (
            render_consent_surface,
            verify_consent_surface_completeness,
        )

        payload = _command_set_payload(BATCH_COMMANDS)
        full = render_consent_surface(payload, "P-" + "a" * 32)

        for size in range(len(BATCH_COMMANDS)):
            for subset in itertools.combinations(BATCH_COMMANDS, size):
                surface = full
                for hidden in (c for c in BATCH_COMMANDS if c not in subset):
                    surface = surface.replace(hidden, "")
                complete, missing = verify_consent_surface_completeness(
                    surface, payload
                )
                assert complete is False, (
                    f"A surface showing {size} of {len(BATCH_COMMANDS)} commands "
                    "must never be complete"
                )
                assert len(missing) == len(BATCH_COMMANDS) - size

    def test_render_lists_every_command_indexed(self):
        """The canonical batch rendering shows COMANDOS (N) and [i] per command."""
        from modules.security.approval_grants import (
            render_consent_surface,
            verify_consent_surface_completeness,
        )

        payload = _command_set_payload(BATCH_COMMANDS)
        surface = render_consent_surface(payload, "P-" + "b" * 32)

        assert "COMANDOS (3):" in surface, surface
        for index, command in enumerate(BATCH_COMMANDS, start=1):
            assert f"  [{index}] {command}" in surface, (
                f"Command {index} missing from rendered surface:\n{surface}"
            )
        assert verify_consent_surface_completeness(surface, payload)[0] is True

        # The 4 non-command sealed fields survive the batch layout.
        for label in ("OPERACION:", "SCOPE:", "RIESGO:", "ROLLBACK:"):
            assert label in surface, f"{label} missing from batch surface"

    def test_render_singular_uses_comando_line(self):
        """A one-command payload keeps the singular COMANDO layout."""
        from modules.security.approval_grants import render_consent_surface

        payload = _singular_payload("terraform apply")
        surface = render_consent_surface(payload, "P-" + "c" * 32)

        assert "COMANDO:    terraform apply" in surface, surface
        assert "COMANDOS" not in surface, (
            "A single command must not be rendered as a batch"
        )

    def test_payload_commands_prefers_the_set_over_exact_content(self):
        """command_set is authoritative; exact_content is only the stand-in."""
        from modules.security.approval_grants import payload_commands

        assert payload_commands(_command_set_payload(BATCH_COMMANDS)) == BATCH_COMMANDS
        assert payload_commands(_singular_payload("kubectl apply -f x.yaml")) == [
            "kubectl apply -f x.yaml"
        ]
        # SCOPE_FILE_PATH shape: exact_content is the blocked path, no commands.
        assert payload_commands(
            {"exact_content": "/etc/hosts", "scope": "SCOPE_FILE_PATH"}
        ) == ["/etc/hosts"]


# ---------------------------------------------------------------------------
# Nonce activation must survive the new label form
# ---------------------------------------------------------------------------

class TestNonceSurvivesTheBatchLabel:
    """The `[P-<nonce8>]` suffix is what activates the grant -- it must hold."""

    @pytest.mark.parametrize("payload_factory", [
        lambda: _singular_payload("git push origin main"),
        lambda: _command_set_payload(BATCH_COMMANDS),
    ])
    def test_rendered_label_yields_the_nonce_prefix(self, payload_factory):
        from modules.security.approval_grants import (
            extract_nonce_from_label,
            render_approve_label,
        )

        approval_id = "P-" + "75a44b5cfb6ae198b0ad444ed442bc7a"
        label = render_approve_label(payload_factory(), approval_id)

        assert extract_nonce_from_label(label) == "75a44b5c", (
            f"Nonce must be extractable from the rendered label: {label!r}"
        )

    def test_batch_label_names_the_count(self):
        from modules.security.approval_grants import render_approve_label

        label = render_approve_label(
            _command_set_payload(BATCH_COMMANDS), "P-" + "d" * 32
        )
        assert "(3 commands)" in label, label
        assert label.startswith("Approve"), label

    def test_activation_succeeds_through_the_rendered_batch_label(self, approvals_db):
        """End-to-end: render label -> extract nonce -> activate the COMMAND_SET."""
        db_path, assert_con, store = approvals_db
        from modules.security.approval_grants import (
            ACTIVATION_ACTIVATED,
            activate_db_pending_by_prefix,
            extract_nonce_from_label,
            render_approve_label,
        )

        payload = _command_set_payload(BATCH_COMMANDS)
        approval_id = _insert_pending(store, payload)
        label = render_approve_label(payload, approval_id)

        nonce_prefix = extract_nonce_from_label(label)
        assert nonce_prefix == approval_id[len("P-"):len("P-") + 8]

        result = activate_db_pending_by_prefix(
            nonce_prefix,
            current_session_id=SESSION_ID,
            presented_label=label,
        )

        assert result.success, f"Activation must still succeed: {result.reason}"
        assert result.status == ACTIVATION_ACTIVATED


# ---------------------------------------------------------------------------
# Defect 2 -- the SHOWN event records the surface, and its absence is detectable
# ---------------------------------------------------------------------------

class TestShownEventPersistsTheSurface:
    """What the user saw must be recoverable from the append-only chain."""

    def test_shown_payload_carries_all_n_commands(self, approvals_db):
        """A COMMAND_SET activation records a surface listing every command."""
        db_path, assert_con, store = approvals_db
        from modules.security.approval_grants import (
            CONSENT_SURFACE_RECONSTRUCTED,
            activate_db_pending_by_prefix,
            audit_consent_surface,
        )

        payload = _command_set_payload(BATCH_COMMANDS)
        approval_id = _insert_pending(store, payload)
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        assert activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=SESSION_ID
        ).success

        event = _shown_event(store, approval_id, assert_con)
        assert event["payload_json"], "SHOWN event must not carry an empty payload"
        record = json.loads(event["payload_json"])

        assert record["consent_surface_source"] == CONSENT_SURFACE_RECONSTRUCTED
        assert record["command_count"] == 3
        assert record["commands_shown"] == BATCH_COMMANDS
        assert record["complete"] is True
        for command in BATCH_COMMANDS:
            assert command in record["consent_surface"], (
                f"{command!r} missing from the persisted consent surface"
            )

        audit = audit_consent_surface(approval_id)
        assert audit.auditable is True
        assert audit.command_count == 3
        assert audit.complete is True

    def test_captured_question_is_stored_verbatim(self, approvals_db):
        """A supplied question text is the probative record, stored unaltered."""
        db_path, assert_con, store = approvals_db
        from modules.security.approval_grants import (
            CONSENT_SURFACE_CAPTURED,
            activate_db_pending_by_prefix,
            render_consent_surface,
        )

        payload = _command_set_payload(BATCH_COMMANDS)
        approval_id = _insert_pending(store, payload)
        presented = render_consent_surface(payload, approval_id)

        assert activate_db_pending_by_prefix(
            approval_id[len("P-"):len("P-") + 8],
            current_session_id=SESSION_ID,
            presented_question=presented,
        ).success

        record = json.loads(_shown_event(store, approval_id, assert_con)["payload_json"])
        assert record["consent_surface"] == presented
        assert record["consent_surface_source"] == CONSENT_SURFACE_CAPTURED

    def test_incomplete_captured_surface_is_recorded_not_refused(self, approvals_db):
        """An under-showing presentation leaves evidence but still activates.

        The user has already consented by activation time; refusing there would
        leave them unable to approve anything. The record is what makes the
        omission provable afterwards.
        """
        db_path, assert_con, store = approvals_db
        from modules.security.approval_grants import activate_db_pending_by_prefix

        payload = _command_set_payload(BATCH_COMMANDS)
        approval_id = _insert_pending(store, payload)

        result = activate_db_pending_by_prefix(
            approval_id[len("P-"):len("P-") + 8],
            current_session_id=SESSION_ID,
            presented_question=_legacy_singular_surface(payload),
        )
        assert result.success, "Activation must not be broken by an audit finding"

        record = json.loads(_shown_event(store, approval_id, assert_con)["payload_json"])
        assert record["complete"] is False
        assert record["missing_commands"] == BATCH_COMMANDS[1:]

    def test_shown_event_without_text_is_detectable(self, approvals_db):
        """A legacy SHOWN event (no payload) reports as not auditable.

        This is the additive-migration proof: rows written before this layer
        are readable and simply report that the surface was never recorded --
        nothing needs rewriting for the audit to work.
        """
        db_path, assert_con, store = approvals_db
        from modules.security.approval_grants import audit_consent_surface

        approval_id = _insert_pending(store, _singular_payload("git push origin main"))
        store.record_event(
            approval_id, "SHOWN", agent_id="test-agent", session_id=SESSION_ID
        )

        audit = audit_consent_surface(approval_id)
        assert audit.auditable is False, (
            "A SHOWN event with no persisted text must be detectable"
        )
        assert "consent_surface" in audit.reason
        assert audit.consent_surface is None

    def test_missing_shown_event_is_detectable(self, approvals_db):
        db_path, assert_con, store = approvals_db
        from modules.security.approval_grants import audit_consent_surface

        approval_id = _insert_pending(store, _singular_payload("terraform apply"))

        audit = audit_consent_surface(approval_id)
        assert audit.auditable is False
        assert "no SHOWN event" in audit.reason

    def test_consent_surface_reader_ignores_non_shown_events(self):
        from modules.security.approval_grants import consent_surface_from_shown_event

        assert consent_surface_from_shown_event(
            {"event_type": "APPROVED", "payload_json": '{"consent_surface": "x"}'}
        ) is None
        assert consent_surface_from_shown_event(
            {"event_type": "SHOWN", "payload_json": "not json"}
        ) is None
        assert consent_surface_from_shown_event(
            {"event_type": "SHOWN", "payload_json": '{"consent_surface": "   "}'}
        ) is None


# ---------------------------------------------------------------------------
# The chain must be untouched by the added payload
# ---------------------------------------------------------------------------

class TestChainUnaffected:
    """Persisting the surface is additive: no hash, table, or row changes."""

    def test_chain_valid_and_shown_fingerprint_still_null(self, approvals_db):
        db_path, assert_con, store = approvals_db
        from gaia.approvals.chain import validate_chain
        from modules.security.approval_grants import activate_db_pending_by_prefix

        payload = _command_set_payload(BATCH_COMMANDS)
        approval_id = _insert_pending(store, payload)
        assert activate_db_pending_by_prefix(
            approval_id[len("P-"):len("P-") + 8], current_session_id=SESSION_ID
        ).success

        con = sqlite3.connect(str(db_path))
        con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
        try:
            assert validate_chain(approval_id, con) is True
            row = con.execute(
                "SELECT fingerprint FROM approval_events "
                "WHERE approval_id = ? AND event_type = 'SHOWN'",
                (approval_id,),
            ).fetchone()
        finally:
            con.close()

        assert row is not None
        assert row[0] is None, (
            "SHOWN keeps a NULL fingerprint so this_hash is byte-identical to "
            "what it was before the consent surface was persisted"
        )

    def test_shown_payload_is_canonical_json(self):
        """The persisted record is canonical JSON (sorted keys, no whitespace)."""
        from modules.security.approval_grants import build_shown_event_payload

        payload = _command_set_payload(BATCH_COMMANDS)
        raw = build_shown_event_payload(payload, "P-" + "e" * 32)

        parsed = json.loads(raw)
        assert raw == json.dumps(parsed, sort_keys=True, separators=(",", ":"))
