"""Exercise generated permissions under documented host precedence, composed with Gaia's real guard."""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "hooks"))

from cli import _install_helpers  # noqa: E402
from modules.security import gaia_cli_only_guard  # noqa: E402


def _permission_decision(tool: str, value: str, *layers: dict) -> str:
    """Model the documented last-match wildcard rules, global first and agent last; not a host integration."""
    decision = "ask"
    for layer in layers:
        for name, rules in layer.items():
            if name not in ("*", tool):
                continue
            for pattern, action in (rules if isinstance(rules, dict) else {"*": rules}).items():
                expression = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
                if re.fullmatch(expression, value, re.DOTALL):
                    decision = action
    return decision


def _permissions(package: Path = ROOT, existing: object = None) -> dict:
    """Generate the real orchestrator config rather than a hand-authored permission fixture."""
    policy = json.loads((ROOT / "opencode" / "agent-policy.json").read_text())
    return _install_helpers._opencode_agents(package, policy, existing)["gaia-orchestrator"]["permission"]


@pytest.mark.parametrize("arguments", [
    "", " --help", " context project gaia",
    " contract view --draft-id aedfca4e32e396d64.f72cde6e55cc --json",
    " evidence show 1",
])
def test_canonical_cli_read_passes_both_layers(arguments):
    command = f"{ROOT / 'bin' / 'gaia'}{arguments}"
    assert _permission_decision("bash", command, {"*": "deny"}, _permissions()) == "allow"
    allowed, reason = gaia_cli_only_guard.check(command, {})
    assert allowed, reason


@pytest.mark.parametrize("command", [
    "gaia --help", "./bin/gaia --help", "/tmp/gaia --help", "ls /tmp",
    f"{ROOT / 'bin' / 'gaia'}-other --help",
    f"env X=1 {ROOT / 'bin' / 'gaia'} --help",
])
def test_host_does_not_grant_other_shell_commands_even_over_global_allow(command):
    assert _permission_decision("bash", command, {"*": "allow"}, _permissions()) == "deny"


@pytest.mark.parametrize("arguments", [
    "install", "doctor --fix", "task gate set-status brief 1 1 pass",
    "--help; ls /tmp", "--help $(ls)",
])
def test_host_admission_still_requires_gaia_verb_and_composition_guard(arguments):
    command = f"{ROOT / 'bin' / 'gaia'} {arguments}"
    assert _permission_decision("bash", command, _permissions()) == "allow"
    allowed, reason = gaia_cli_only_guard.check(command, {})
    assert not allowed
    assert reason


@pytest.mark.parametrize("override", [False, True])
def test_only_canonical_artifact_reads_are_admitted(tmp_path, monkeypatch, override):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    if override:
        root = tmp_path / "relocated data"
        monkeypatch.setenv("GAIA_DATA_DIR", str(root))
    else:
        root = home / ".gaia"
        monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "separate-db" / "gaia.db"))
    permissions = _permissions()

    for path in [root / "scratch" / "turn.json", root / "evidence" / "ws" / "brief" / "AC-1" / "result.txt"]:
        assert _permission_decision("external_directory", str(path), {"*": "deny"}, permissions) == "allow"
        assert _permission_decision("read", str(path), {"*": "deny"}, permissions) == "allow"
        for tool in ("edit", "glob", "grep"):
            assert _permission_decision(tool, str(path), {"*": "allow"}, permissions) == "deny"

    for path in [
        root / "gaia.db", root / "logs" / "log", root / "scratch-other" / "file",
        root / "evidence-other" / "file", home / "private" / "file",
        tmp_path / "separate-db" / "gaia.db",
    ]:
        assert _permission_decision("external_directory", str(path), {"*": "allow"}, permissions) == "deny"
    if override:
        old_artifact = str(home / ".gaia" / "scratch" / "old")
        assert _permission_decision("external_directory", old_artifact, permissions) == "deny"


def test_upgrade_replaces_stale_permissions_without_touching_foreign_agents():
    foreign = {"mode": "subagent", "permission": {"bash": "ask"}}
    existing = {
        "gaia-orchestrator": {"permission": {"bash": {"gaia *": "allow"}, "edit": "allow"}},
        "foreign": foreign,
    }
    policy = json.loads((ROOT / "opencode" / "agent-policy.json").read_text())
    generated = _install_helpers._opencode_agents(ROOT, policy, existing)
    repeated = _install_helpers._opencode_agents(ROOT, policy, generated)
    assert repeated == generated
    assert generated["foreign"] == foreign
    assert existing["gaia-orchestrator"]["permission"]["edit"] == "allow"
    assert _permission_decision("bash", "gaia --help", generated["gaia-orchestrator"]["permission"]) == "deny"
    assert _permission_decision("edit", "/tmp/file", generated["gaia-orchestrator"]["permission"]) == "deny"


def test_stable_package_link_and_quoted_paths_survive_generation(tmp_path):
    stable = tmp_path / "workspace with spaces" / "node_modules" / "@jaguilar87" / "gaia"
    stable.parent.mkdir(parents=True)
    stable.symlink_to(ROOT, target_is_directory=True)
    selected = _install_helpers._opencode_package_root(stable.parents[2], ROOT)
    assert selected == stable
    permissions = _permissions(selected)
    command = f"{shlex.quote(str(stable / 'bin' / 'gaia'))} --help"
    assert _permission_decision("bash", command, permissions) == "allow"
    allowed, reason = gaia_cli_only_guard.check(command, {})
    assert allowed, reason


@pytest.mark.parametrize("value", ["data*", "data?"])
def test_wildcards_in_storage_paths_cannot_broaden_permissions(tmp_path, monkeypatch, value):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / value))
    with pytest.raises(ValueError, match="wildcard"):
        _permissions()
