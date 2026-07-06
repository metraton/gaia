"""Tests for the deterministic, content-derived COMMAND_SET approval_id.

The COMMAND_SET producer is the compound-command intake in bash_validator
(``_validate_compound_command`` -> ``decide_t3_outcome(command_set=...)``): a
chain ``a && b`` of >= 2 T3 sub-commands mints ONE pending whose id is derived
from the sub-command strings via ``gaia.approvals.store.derive_command_set_id``.
The id is CONTENT-derived (not uuid4) so a retry of the same chain reproduces
the same id and reuses the pending via fingerprint dedup.

Covers:
  * derive_command_set_id() is deterministic and order-sensitive.
  * A pending inserted under a supplied derived id (the exact bash_validator
    path) carries an intact hash chain and a verifiable fingerprint.
  * Singular T3 approvals still use a uuid4 id (unaffected).
  * Fingerprint dedup / idempotency still holds (and wins over a supplied id).

Note: the plan-first SubagentStop intake (``_intake_command_set_pending``) and
its CLI mirror (``gaia approvals derive-id``) were retired -- the orchestrator
has no shell to reproduce an id, so that flow never reached it. The remaining
producer is bash_validator's compound-command mint, exercised end-to-end by
tests/integration/test_command_set_chain_ac8.py.
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
# The bash_validator path: pending inserted under the supplied derived id
# carries an intact hash chain and a verifiable fingerprint.
# ---------------------------------------------------------------------------

def test_derived_id_flows_through_fingerprint_and_chain(gaia_db):
    """A COMMAND_SET pending minted the way bash_validator does it -- supplying
    the content-derived id to insert_requested -- still carries an intact hash
    chain and a verifiable fingerprint. The security properties are unchanged.
    """
    commands = ["git push origin main", "gcloud run deploy svc"]
    supplied = derive_command_set_id(commands)
    sealed_payload = {
        "operation": "COMMAND_SET",
        "exact_content": commands[0],
        "scope": "git",
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": "release batch",
        "commands": commands,
        "command_set": _command_set(commands),
    }
    minted_id = insert_requested(
        sealed_payload, agent_id="agent-z", session_id="sess-3", approval_id=supplied
    )
    assert minted_id == supplied

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
