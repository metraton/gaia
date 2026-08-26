"""The control-plane dispatch gate, driven through the real bridge entry point.

Every verdict here is produced by ``bridge.handle`` -- the boundary the plugin
actually spawns -- rather than by the adapter directly: a hand-built
``RoleCapabilityContext`` handed to the adapter would skip the parse boundary
these cases exist to exercise, and three earlier rounds of this defect passed
against a shape no plugin ever emits.

The ledger and the database are both scratch. Issuance and resolution derive the
same namespace because ``host_run_id`` reads this process's own parent, so the
token minted here is the one the adapter resolves -- a test cannot align those
by naming a run.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.security.host_attestation import host_run_id, issue  # noqa: E402


ROLE_ISSUER = "opencode-runtime"
SESSION = "ses-control-plane"
CALL = "call-1"


@pytest.fixture
def scratch(tmp_path, monkeypatch, bootstrapped_db_template):
    """Ledger and database both under this test's own scratch directory."""
    db_path = tmp_path / "gaia.db"
    shutil.copy(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(tmp_path / "ledger"))
    return tmp_path


def _dispatch(**overrides):
    """One tool.execute.before for the task tool, as the plugin composes it."""
    event = {
        "event": "tool.execute.before",
        "sessionID": SESSION,
        "callID": CALL,
        "tool": "task",
        "args": {"subagent_type": "developer", "prompt": "do the thing"},
        "cwd": str(_ROOT),
    }
    event.update(overrides)
    return event


def _role_context(role, token):
    return {
        "role": role,
        "capabilities": [],
        "issuer": ROLE_ISSUER,
        "attestation": token,
        "verified": True,
    }


def _handle(event):
    import bridge

    return bridge.handle(event)


def test_v1_dispatch_with_no_agent_key_is_refused(scratch):
    """The reported failure: the plugin sent no agent, so nothing identified it.

    ``JSON.stringify`` drops an undefined value, so an absent name reaches the
    bridge as an absent key -- indistinguishable from a caller that never held
    an identity, and refused as one.
    """
    response = _handle(_dispatch())

    assert response["action"] == "deny"
    assert response["reason"] == (
        "ordinary OpenCode agents cannot issue control-plane dispatches"
    )


def test_v2_declared_control_plane_name_without_a_context_is_refused(scratch):
    """A name is not an identity: the second window, between naming and issuance."""
    response = _handle(_dispatch(agent="gaia-orchestrator"))

    assert response["action"] == "deny"
    assert response["reason"] == (
        "OpenCode control-plane role was declared without an attested runtime context"
    )


def test_v3_attested_depth_zero_control_plane_dispatch_is_admitted(scratch):
    """The whole chain, end to end, on a token this run's ledger actually holds."""
    issued = issue(
        host_run=host_run_id(),
        session_id=SESSION,
        role="gaia-orchestrator",
        issuer=ROLE_ISSUER,
    )

    response = _handle(
        _dispatch(
            agent="gaia-orchestrator",
            roleContext=_role_context("gaia-orchestrator", issued.token),
        )
    )

    assert response["action"] == "allow", response


def test_v4_unaligned_control_plane_spelling_is_refused_even_when_attested(scratch):
    """``orchestrator`` is a control-plane name to one predicate and not the other.

    The adapter's ``_CONTROL_PLANE_ROLES`` admits the bare spelling while
    ``host_attestation.CONTROL_PLANE_ROLE`` does not, so a real token issued for
    it is depth 0 without being control-plane bound, and the claim is refused at
    the attestation check rather than at the dispatch check. Asserting the
    verdict pins that divergence in place until all three predicates move
    together.
    """
    issued = issue(
        host_run=host_run_id(),
        session_id=SESSION,
        role="orchestrator",
        issuer=ROLE_ISSUER,
    )

    response = _handle(
        _dispatch(
            agent="orchestrator",
            roleContext=_role_context("orchestrator", issued.token),
        )
    )

    assert response["action"] == "deny"
    assert response["reason"] == (
        "OpenCode control-plane role is not attested by the runtime"
    )
