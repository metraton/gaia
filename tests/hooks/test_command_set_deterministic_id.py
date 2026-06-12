"""Tests for the deterministic, content-derived COMMAND_SET approval_id.

These tests prove the fix for the cross-session miss on plan-first COMMAND_SET
approvals: the SubagentStop intake mints a CONTENT-derived id (not uuid4) so the
orchestrator can reproduce the SAME id from the command_set it reads in the
contract, with NO DB search.

Covers:
  * derive_command_set_id() is deterministic and order-sensitive.
  * The intake (_intake_command_set_pending) writes the pending row under the
    id derive_command_set_id() produces over the same (post-filter) commands.
  * The orchestrator-side derivation (the gaia.approvals.store function the CLI
    `derive-id` calls) yields the SAME id as the DB-minted pending row -- no
    search needed.
  * Singular T3 approvals still use a uuid4 id (unaffected).
  * Fingerprint dedup / idempotency still holds, and the deterministic id flows
    through verify_fingerprint / chain integrity unchanged.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_REPO_ROOT), str(_HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia.approvals.store import (  # noqa: E402
    derive_command_set_id,
    insert_requested,
    get_by_id,
)
from gaia.approvals.chain import (  # noqa: E402
    canonical_payload,
    fingerprint_payload,
    validate_chain,
    verify_fingerprint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def gaia_db(tmp_path, monkeypatch):
    """Point gaia.paths at a fresh tmp data dir so _open_db() builds the real
    schema (approvals + approval_events + triggers) there.

    Both the store and the intake open ~/.gaia/gaia.db via writer._connect();
    redirecting GAIA_DATA_DIR makes them target the tmp DB instead.
    """
    data_dir = tmp_path / "gaia_home"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    return data_dir / "gaia.db"


def _command_set(commands):
    """Build a [{command, rationale}] list from command strings."""
    return [{"command": c, "rationale": f"step {i}"} for i, c in enumerate(commands)]


# ---------------------------------------------------------------------------
# derive_command_set_id: determinism + order sensitivity
# ---------------------------------------------------------------------------

def test_derive_is_deterministic():
    cmds = ["git add -A", "git commit -m 'x'", "git push origin main"]
    a = derive_command_set_id(cmds)
    b = derive_command_set_id(list(cmds))
    assert a == b
    assert a.startswith("P-")
    # 32 hex chars after the P- prefix (matches the uuid4.hex visual length).
    assert len(a) == len("P-") + 32
    assert all(c in "0123456789abcdef" for c in a[2:])


def test_derive_is_order_sensitive():
    a = derive_command_set_id(["a-cmd push", "b-cmd push"])
    b = derive_command_set_id(["b-cmd push", "a-cmd push"])
    assert a != b, "command order must change the derived id (consume is positional)"


def test_derive_ignores_rationale_and_session():
    # Only the command strings matter; both sides need only the commands to agree.
    cmds = ["terraform apply", "kubectl apply -f x.yaml"]
    assert derive_command_set_id(cmds) == derive_command_set_id(cmds)


# ---------------------------------------------------------------------------
# THE PROOF: intake mint-id == derive_command_set_id == orchestrator derivation
# ---------------------------------------------------------------------------

def test_intake_pending_row_id_equals_derived_id(gaia_db):
    """The id _intake_command_set_pending writes as the pending row id equals
    derive_command_set_id() over the same (post-filter mutative) commands.
    """
    from modules.agents.handoff_persister import _intake_command_set_pending

    # All mutative so the filter keeps them all.
    commands = ["git push origin main", "terraform apply -auto-approve"]
    approval_req = {
        "command_set": _command_set(commands),
        # plan-first: NO approval_id
    }

    minted_id = _intake_command_set_pending(
        approval_req, agent_id="agent-x", session_id="sess-1"
    )
    assert minted_id is not None
    assert minted_id.startswith("P-")

    # Orchestrator-side derivation: it has only the command strings (post-filter
    # they are all mutative here), no DB access.
    orchestrator_id = derive_command_set_id(commands)
    assert minted_id == orchestrator_id, (
        f"mint-id {minted_id!r} must equal orchestrator-derived id {orchestrator_id!r}"
    )

    # The pending row actually exists under that id (no search was needed to find it).
    row = get_by_id(minted_id)
    assert row is not None
    assert row["status"] == "pending"


def test_intake_filters_nonmutative_before_deriving(gaia_db):
    """When the raw command_set mixes non-mutative commands, the intake derives
    over the POST-FILTER mutative list -- and the orchestrator reproduces it by
    applying the same filter (here we feed the post-filter list directly).
    """
    from modules.agents.handoff_persister import (
        _intake_command_set_pending,
        _filter_mutative_command_set,
    )

    raw_commands = ["ls -la", "git push origin main", "cat file", "terraform apply"]
    approval_req = {"command_set": _command_set(raw_commands)}

    minted_id = _intake_command_set_pending(
        approval_req, agent_id="agent-y", session_id="sess-2"
    )
    assert minted_id is not None

    # Reproduce the orchestrator path: same filter, then derive.
    filtered = _filter_mutative_command_set(
        [{"command": c, "rationale": ""} for c in raw_commands]
    )
    filtered_cmds = [it["command"] for it in filtered]
    assert minted_id == derive_command_set_id(filtered_cmds)
    # Sanity: the non-mutative ls/cat were dropped before derivation.
    assert "ls -la" not in filtered_cmds
    assert "cat file" not in filtered_cmds


def test_derived_id_flows_through_fingerprint_and_chain(gaia_db):
    """The deterministic id still carries an intact hash chain and a verifiable
    fingerprint -- the security properties are unchanged.
    """
    from modules.agents.handoff_persister import _intake_command_set_pending

    commands = ["git push origin main", "gcloud run deploy svc"]
    approval_req = {"command_set": _command_set(commands)}
    minted_id = _intake_command_set_pending(
        approval_req, agent_id="agent-z", session_id="sess-3"
    )
    assert minted_id == derive_command_set_id(commands)

    row = get_by_id(minted_id)
    payload_json = row["payload_json"]

    from gaia.store.writer import _connect
    con = _connect(gaia_db)
    try:
        # Chain integrity intact for the deterministic id.
        assert validate_chain(minted_id, con) is True
        # Fingerprint of the stored payload matches the REQUESTED baseline.
        assert verify_fingerprint(minted_id, payload_json, con) is True
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Singular T3 approvals are unaffected: still uuid4
# ---------------------------------------------------------------------------

def test_singular_t3_still_uses_uuid4(gaia_db):
    """insert_requested without a supplied approval_id mints a uuid4 P- id."""
    sealed = {
        "operation": "Delete branch",
        "exact_content": "git branch -D feature/old",
        "scope": "feature/old",
        "risk_level": "medium",
        "rollback_hint": "git branch feature/old <sha>",
        "rationale": "stale",
        "commands": ["git branch -D feature/old"],
    }
    aid = insert_requested(sealed, agent_id="a", session_id="s")
    assert aid.startswith("P-")
    # uuid4.hex is 32 hex chars; it must NOT equal the content-derived id for
    # the same single command (different derivation entirely).
    assert aid != derive_command_set_id(["git branch -D feature/old"])
    # Two singular requests with DIFFERENT payloads get DIFFERENT uuid4 ids.
    sealed2 = dict(sealed, exact_content="git branch -D feature/other",
                   commands=["git branch -D feature/other"], scope="feature/other")
    aid2 = insert_requested(sealed2, agent_id="a", session_id="s")
    assert aid2 != aid


# ---------------------------------------------------------------------------
# Fingerprint dedup / idempotency still holds (and wins over a supplied id)
# ---------------------------------------------------------------------------

def test_fingerprint_dedup_reuses_existing_pending(gaia_db):
    """Two identical sealed_payloads map to ONE pending row (idempotency),
    regardless of a caller-supplied approval_id.
    """
    sealed = {
        "operation": "COMMAND_SET",
        "exact_content": "git push origin main",
        "scope": "git",
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": "release batch",
        "commands": ["git push origin main", "terraform apply"],
        "command_set": _command_set(["git push origin main", "terraform apply"]),
    }
    supplied = derive_command_set_id(["git push origin main", "terraform apply"])

    first = insert_requested(sealed, agent_id="a", session_id="s1", approval_id=supplied)
    assert first == supplied

    # Same payload, DIFFERENT supplied id and different session: dedup must
    # return the FIRST id, not mint a second row.
    second = insert_requested(
        dict(sealed), agent_id="a", session_id="s2", approval_id="P-deadbeef" * 1
    )
    assert second == first, "fingerprint dedup must reuse the existing pending id"

    # Exactly one pending row for that fingerprint.
    from gaia.store.writer import _connect
    con = _connect(gaia_db)
    try:
        fp = fingerprint_payload(sealed)
        rows = con.execute(
            "SELECT id FROM approvals WHERE fingerprint = ? AND status = 'pending'",
            (fp,),
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1


def test_intake_idempotent_same_command_set(gaia_db):
    """Two identical plan-first command_sets map to the same id (matches the
    existing fingerprint idempotency -- acceptable per design).
    """
    from modules.agents.handoff_persister import _intake_command_set_pending

    commands = ["git push origin main", "terraform apply"]
    req = {"command_set": _command_set(commands)}
    a = _intake_command_set_pending(dict(req), agent_id="a", session_id="s1")
    b = _intake_command_set_pending(dict(req), agent_id="a", session_id="s2")
    assert a == b == derive_command_set_id(commands)


# ---------------------------------------------------------------------------
# CLI derive-id mirrors the store function
# ---------------------------------------------------------------------------

def test_cli_derive_id_matches_store(monkeypatch):
    """`gaia approvals derive-id` (cmd_derive_id) yields the same id as the
    store function for the same post-filter command list.
    """
    import importlib
    sys.path.insert(0, str(_REPO_ROOT / "bin"))
    approvals_cli = importlib.import_module("cli.approvals")

    commands = ["git push origin main", "terraform apply -auto-approve"]

    class _Args:
        commands_json = '[{"command": "git push origin main"}, {"command": "terraform apply -auto-approve"}]'
        no_filter = False
        json = True

    captured = {}
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.setdefault("out", a[0] if a else ""))
    rc = approvals_cli.cmd_derive_id(_Args())
    assert rc == 0
    import json as _json
    out = _json.loads(captured["out"])
    assert out["approval_id"] == derive_command_set_id(commands)


def test_cli_derive_id_reports_non_batch(monkeypatch):
    """With fewer than 2 mutative commands after filter, derive-id reports no
    COMMAND_SET (singular path owns it) and exits 1.
    """
    import importlib
    sys.path.insert(0, str(_REPO_ROOT / "bin"))
    approvals_cli = importlib.import_module("cli.approvals")

    class _Args:
        commands_json = '["ls -la", "git push origin main"]'  # only 1 mutative
        no_filter = False
        json = True

    captured = {}
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.setdefault("out", a[0] if a else ""))
    rc = approvals_cli.cmd_derive_id(_Args())
    assert rc == 1
    import json as _json
    out = _json.loads(captured["out"])
    assert out["approval_id"] is None
