"""doctor's Identity check reports what the host actually ran in this workspace."""

import json
import sys
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

import cli.doctor as doctor_mod  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    root = tmp_path / "ws"
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "gaia-orchestrator.md").write_text("---\nname: gaia-orchestrator\n---\n")
    (root / ".claude" / "settings.local.json").write_text(json.dumps({"agent": "gaia-orchestrator"}))
    return root


def _registry(tmp_path, monkeypatch, *identities):
    sessions = {
        f"s{index}": {"started_at": identity["attested_at"], "is_headless": False,
                      "last_heartbeat": 1.0, "identity": identity}
        for index, identity in enumerate(identities)
    }
    path = tmp_path / "session_registry.json"
    path.write_text(json.dumps({"sessions": sessions}))
    monkeypatch.setattr(doctor_mod, "_SESSION_REGISTRY_PATH", path)


def _identity(workspace, agent_type, attested_at):
    return {"agent_type": agent_type, "build": "fb27693c", "channel": "plugin",
            "workspace": str(workspace), "attested_at": attested_at}


def test_reports_the_latest_attestation_for_the_workspace(workspace, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch,
              _identity(workspace, "", "2026-09-30T10:00:00+00:00"),
              _identity(workspace, "gaia-orchestrator", "2026-09-30T12:00:00+00:00"),
              _identity(tmp_path / "other", "general-purpose", "2026-09-30T13:00:00+00:00"))

    result = doctor_mod.check_identity(workspace)

    assert result["severity"] == "pass"
    assert "ran as gaia-orchestrator" in result["detail"]
    assert "2026-09-30T12:00:00+00:00" in result["detail"]


def test_a_host_that_ran_another_agent_is_an_error_even_with_config_right(workspace, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, _identity(workspace, "", "2026-09-30T12:00:00+00:00"))

    result = doctor_mod.check_identity(workspace)

    assert result["severity"] == "error"
    assert "ran as no agent" in result["detail"]


def test_no_attestation_keeps_the_config_verdict(workspace, tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)

    result = doctor_mod.check_identity(workspace)

    assert result["severity"] == "pass"
    assert "no session attested" in result["detail"]
