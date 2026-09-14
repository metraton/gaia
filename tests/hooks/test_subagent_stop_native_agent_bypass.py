"""The native-agent bypass is granted only against a resolved roster.

An agent Gaia does not own emits no contract, so SubagentStop lets it past the
gate. The bypass therefore decides whether a turn is governed at all, and it
used to be handed out on two conditions that prove nothing: an agent roster
that failed to resolve made EVERY agent look native, and a payload carrying no
``agent_type`` arrived as the literal ``"unknown"``, which is never the stem of
an ``agents/*.md``.

Failing closed here gates the legitimate native agents too when a deployment
cannot see its own roster. That cost is bounded by the rejection circuit, and
it is the deliberate trade: the alternative leaves every turn ungated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402

WORKSPACE = "me"
SESSION_ID = "sess-native-bypass"
HARNESS_AGENT_ID = "a0000000000000001"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    yield


def _event(adapter: ClaudeCodeAdapter, agent_type: str | None):
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": "Done.",
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return adapter.parse_event(json.dumps(payload))


def _bypassed(response) -> bool:
    return response.output.get("native_agent") is True


def test_a_real_native_agent_is_bypassed(monkeypatch):
    """The behaviour the bypass exists for, kept intact by the fix."""
    monkeypatch.setattr(
        ClaudeCodeAdapter, "_get_gaia_agent_names",
        lambda self: {"gaia-system", "developer"},
    )

    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_subagent_stop(_event(adapter, "Explore"))

    assert _bypassed(response)
    assert response.exit_code == 0


def test_an_unresolved_roster_grants_no_bypass(monkeypatch):
    """The layout-dependent switch: no roster must not mean "all native"."""
    monkeypatch.setattr(
        ClaudeCodeAdapter, "_get_gaia_agent_names", lambda self: set(),
    )

    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_subagent_stop(_event(adapter, "Explore"))

    assert not _bypassed(response)


def test_a_payload_without_agent_type_grants_no_bypass(monkeypatch):
    """``unknown`` is the absence of an identity, not evidence of a native one."""
    monkeypatch.setattr(
        ClaudeCodeAdapter, "_get_gaia_agent_names",
        lambda self: {"gaia-system", "developer"},
    )

    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_subagent_stop(_event(adapter, None))

    assert not _bypassed(response)


def test_a_gaia_agent_is_never_bypassed(monkeypatch):
    monkeypatch.setattr(
        ClaudeCodeAdapter, "_get_gaia_agent_names",
        lambda self: {"gaia-system", "developer"},
    )

    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_subagent_stop(_event(adapter, "gaia-system"))

    assert not _bypassed(response)


def test_the_real_resolver_finds_this_checkouts_agents():
    names = ClaudeCodeAdapter()._get_gaia_agent_names()

    assert "gaia-system" in names, (
        "the roster resolved empty in a real Gaia checkout, which would hand "
        "every agent a native-agent bypass"
    )


def test_the_registry_lane_contributes_what_the_load_path_cannot_see(
    tmp_path, monkeypatch,
):
    """A checkout the load-relative lane can never reach still lands names.

    This is the lane that survives materialising the hooks away from their
    checkout, so it is asserted on a root chosen precisely for being nowhere
    near the running module.
    """
    import modules.security.protected_paths as protected_paths

    elsewhere = tmp_path / "another-checkout"
    (elsewhere / "agents").mkdir(parents=True)
    (elsewhere / "agents" / "agent-from-the-registry.md").write_text("x")
    monkeypatch.setattr(
        protected_paths, "declared_hook_tree_roots",
        lambda: (str(elsewhere / "hooks"),),
    )

    names = ClaudeCodeAdapter()._get_gaia_agent_names()

    assert "agent-from-the-registry" in names
    assert "gaia-system" in names, "a second lane must not displace the first"
