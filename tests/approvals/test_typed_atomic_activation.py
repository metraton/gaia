"""Typed activation keeps approval state and executable grants indivisible."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
for import_path in (REPO_ROOT / "bin", HOOKS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from gaia.approvals import store  # noqa: E402
from gaia.approvals.command_set import command_fingerprint, request_fingerprint  # noqa: E402
from gaia.store import writer  # noqa: E402
from cli.approvals import cmd_approve  # noqa: E402
from modules.security.approval_scopes import build_file_path_signature  # noqa: E402
from modules.tools.bash_validator import _build_sealed_payload  # noqa: E402

SESSION_ID = "ses-typed-activation"
AGENT_ID = "agent-typed-activation"
COMMAND = "git push origin main"


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Bind every production store lookup to this test's private database."""
    db_path = tmp_path / "gaia.db"
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    with sqlite3.connect(db_path) as con:
        con.executescript(writer._SCHEMA_PATH.read_text())
    from gaia.paths import db_path as resolved_db_path

    assert resolved_db_path().resolve() == db_path.resolve()
    with writer._connect() as con:
        actual = Path(con.execute("PRAGMA database_list").fetchone()[2])
    assert actual.resolve() == db_path.resolve()
    return db_path


def _semantic_payload() -> dict:
    return _build_sealed_payload(
        COMMAND,
        verb="push",
        category="MUTATIVE",
        agent_type=AGENT_ID,
    )


def _seed(payload: dict) -> str:
    return store.insert_requested(payload, agent_id=AGENT_ID, session_id=SESSION_ID)


def _approve_args(approval_id: str) -> argparse.Namespace:
    return argparse.Namespace(approval_id=approval_id, yes=True, json=True)


def _command_set_payload() -> dict:
    commands = [COMMAND, "docker push registry/app:1"]
    return {
        "request_type": "COMMAND_SET",
        "command_set": [
            {
                "command": command,
                "fingerprint": command_fingerprint(command),
                "rationale": "publish",
            }
            for command in commands
        ],
        "request_fingerprint": request_fingerprint(commands),
        "scope": "COMMAND_SET",
        "operation": "publish",
        "exact_content": commands[0],
    }


def _file_path_payload(tmp_path: Path) -> dict:
    file_path = str((tmp_path / "protected.py").resolve())
    signature = build_file_path_signature(file_path)
    assert signature is not None
    return {
        "operation": "FILE_WRITE command intercepted: write",
        "exact_content": file_path,
        "scope": "file_path",
        "scope_signature": signature.to_dict(),
        "commands": [file_path],
    }


def _status(db_path: Path, approval_id: str) -> str:
    with sqlite3.connect(db_path) as con:
        return con.execute(
            "SELECT status FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()[0]


def _grant_count(db_path: Path, approval_id: str) -> int:
    with sqlite3.connect(db_path) as con:
        return con.execute(
            "SELECT COUNT(*) FROM approval_grants WHERE approval_id=?",
            (approval_id,),
        ).fetchone()[0]


def test_malformed_payload_leaves_pending_without_capability(isolated_db):
    approval_id = _seed(_semantic_payload())
    with sqlite3.connect(isolated_db) as con:
        con.execute(
            "UPDATE approvals SET payload_json='{' WHERE id=?", (approval_id,)
        )

    result = store.activate_approval_atomically(
        approval_id, approver_session=SESSION_ID
    )

    assert result.success is False
    assert result.status is store.ActivationStatus.INVALID_PENDING
    assert _status(isolated_db, approval_id) == "pending"
    assert _grant_count(isolated_db, approval_id) == 0


def test_tampered_payload_is_audited_but_not_approved(isolated_db):
    payload = _semantic_payload()
    approval_id = _seed(payload)
    payload["exact_content"] = "git push origin production"
    with sqlite3.connect(isolated_db) as con:
        con.execute(
            "UPDATE approvals SET payload_json=? WHERE id=?",
            (json.dumps(payload, sort_keys=True), approval_id),
        )

    result = store.activate_approval_atomically(
        approval_id, approver_session=SESSION_ID
    )

    assert result.success is False
    assert result.status is store.ActivationStatus.CHAIN_TAMPER_DETECTED
    assert _status(isolated_db, approval_id) == "pending"
    assert _grant_count(isolated_db, approval_id) == 0
    assert store.get_history(approval_id)[-1]["event_type"] == "FAILED"


def test_duplicate_once_decision_reuses_exactly_one_grant(isolated_db):
    payload = _semantic_payload()
    approval_id = _seed(payload)

    first = store.activate_approval_atomically(
        approval_id, approver_session=SESSION_ID, shown_payload=payload
    )
    second = store.activate_approval_atomically(
        approval_id, approver_session=SESSION_ID, shown_payload=payload
    )

    assert first.success is True and first.idempotent is False
    assert second.success is True and second.idempotent is True
    assert _status(isolated_db, approval_id) == "approved"
    assert _grant_count(isolated_db, approval_id) == 1
    assert [
        event["event_type"] for event in store.get_history(approval_id)
    ].count("APPROVED") == 1


@pytest.mark.parametrize(
    "payload_factory,expected",
    [
        (
            lambda _path: _semantic_payload(),
            {
                "version": 1,
                "kind": "SCOPE_SEMANTIC_SIGNATURE",
                "command": COMMAND,
                "command_fingerprint": command_fingerprint(COMMAND),
            },
        ),
        (
            _file_path_payload,
            {
                "version": 1,
                "kind": "SCOPE_FILE_PATH",
                "tool_family": ["Write", "Edit"],
            },
        ),
        (
            lambda _path: _command_set_payload(),
            {
                "version": 1,
                "kind": "COMMAND_SET",
                "commands": [COMMAND, "docker push registry/app:1"],
                "fingerprints": [
                    command_fingerprint(COMMAND),
                    command_fingerprint("docker push registry/app:1"),
                ],
                "expected_index": 0,
                "request_fingerprint": request_fingerprint(
                    [COMMAND, "docker push registry/app:1"]
                ),
            },
        ),
    ],
)
def test_activation_returns_a_typed_retry_descriptor_for_singular_and_file_grants(
    isolated_db, tmp_path, payload_factory, expected
):
    payload = payload_factory(tmp_path)
    approval_id = _seed(payload)

    result = store.activate_approval_atomically(
        approval_id,
        approver_session=SESSION_ID,
        agent_id=AGENT_ID,
        binding={
            "agent_id": AGENT_ID,
            "session_id": SESSION_ID,
            "call_id": "call-original",
        },
    )

    assert result.success is True
    assert result.retry_descriptor is not None
    assert result.retry_descriptor | expected == result.retry_descriptor
    assert result.retry_descriptor["approval_id"] == approval_id
    assert result.retry_descriptor["agent_id"] == AGENT_ID
    assert result.retry_descriptor["session_id"] == SESSION_ID
    assert result.retry_descriptor["original_call_id"] == "call-original"
    if expected["kind"] == "SCOPE_FILE_PATH":
        assert result.retry_descriptor["canonical_path"] == payload["exact_content"]


def test_claude_question_lane_delegates_to_shared_typed_service():
    from modules.security import approval_grants

    expected = store.ApprovalActivationResult(
        True,
        store.ActivationStatus.ACTIVATED,
        "applied",
        grant_scope="SCOPE_SEMANTIC_SIGNATURE",
    )
    with (
        patch.object(store, "get_by_id", return_value=None),
        patch.object(store, "activate_approval_atomically", return_value=expected) as activate,
    ):
        result = approval_grants.activate_db_pending_by_id(
            "P-" + "a" * 32,
            current_session_id=SESSION_ID,
        )

    assert result is expected
    activate.assert_called_once_with(
        "P-" + "a" * 32,
        approver_session=SESSION_ID,
        shown_payload=None,
        ttl_minutes=approval_grants.DEFAULT_GRANT_TTL_MINUTES,
    )


@pytest.mark.parametrize(
    "payload_factory,expected_scope",
    [
        (lambda _path: _semantic_payload(), "SCOPE_SEMANTIC_SIGNATURE"),
        (_file_path_payload, "SCOPE_FILE_PATH"),
        (lambda _path: _command_set_payload(), "COMMAND_SET"),
    ],
)
def test_cmd_approve_activates_each_supported_grant_type(
    isolated_db, tmp_path, monkeypatch, capsys, payload_factory, expected_scope
):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    approval_id = _seed(payload_factory(tmp_path))

    assert cmd_approve(_approve_args(approval_id)) == 0

    with sqlite3.connect(isolated_db) as con:
        row = con.execute(
            "SELECT scope FROM approval_grants WHERE approval_id=?", (approval_id,)
        ).fetchone()
    assert row == (expected_scope,)
    assert _status(isolated_db, approval_id) == "approved"
    assert json.loads(capsys.readouterr().out)["status"] == "approved"


def test_cmd_approve_rejects_malformed_and_tampered_payloads(
    isolated_db, monkeypatch
):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    malformed_id = _seed(_semantic_payload())
    tampered_payload = _semantic_payload()
    tampered_id = _seed({**tampered_payload, "exact_content": "git push origin other"})
    with sqlite3.connect(isolated_db) as con:
        con.execute(
            "UPDATE approvals SET payload_json='{' WHERE id=?", (malformed_id,)
        )
        con.execute(
            "UPDATE approvals SET payload_json=? WHERE id=?",
            (json.dumps({**tampered_payload, "exact_content": "git push origin changed"}), tampered_id),
        )

    assert cmd_approve(_approve_args(malformed_id)) == 1
    assert cmd_approve(_approve_args(tampered_id)) == 1
    for approval_id in (malformed_id, tampered_id):
        assert _status(isolated_db, approval_id) == "pending"
        assert _grant_count(isolated_db, approval_id) == 0


def test_cmd_approve_duplicate_is_idempotent(isolated_db, monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    approval_id = _seed(_semantic_payload())

    assert cmd_approve(_approve_args(approval_id)) == 0
    assert cmd_approve(_approve_args(approval_id)) == 0

    assert _status(isolated_db, approval_id) == "approved"
    assert _grant_count(isolated_db, approval_id) == 1


def test_cmd_approve_grant_failure_rolls_back_approval(
    isolated_db, monkeypatch
):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    approval_id = _seed(_semantic_payload())
    monkeypatch.setattr(
        writer,
        "insert_semantic_grant",
        lambda *args, **kwargs: {"status": "error", "reason": "injected failure"},
    )

    assert cmd_approve(_approve_args(approval_id)) == 1
    assert _status(isolated_db, approval_id) == "pending"
    assert _grant_count(isolated_db, approval_id) == 0
