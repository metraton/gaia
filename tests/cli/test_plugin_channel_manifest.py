"""
Plugin channel: what the session hooks write into a workspace is recorded in
the install manifest, and `gaia uninstall` takes it back.

Every test runs the real hooks and the real CLI as subprocesses against a
temporary, registered workspace. HOME, the Gaia data directory, the database
and the plugin data directory all live under tmp_path, so the first scan and
the database migration a SessionStart triggers never reach the user's state.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SESSION_START = REPO / "hooks" / "session_start.py"
USER_PROMPT_SUBMIT = REPO / "hooks" / "user_prompt_submit.py"
GAIA = REPO / "bin" / "gaia"

# Written compactly on purpose: a revert that re-serialises the file instead of
# restoring its bytes changes the layout and fails the byte comparison.
USER_LOCAL = b'{"permissions":{"allow":["Bash(npm test:*)","mcp__user__tool"]},"myKey":{"keep":1}}'


@pytest.fixture
def env(tmp_path):
    """Isolated environment for the hook and CLI subprocesses, with a registered workspace."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "gaia-data"
    data.mkdir()
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    variables = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DATA_DIR": str(data),
        "GAIA_DB": str(data / "gaia.db"),
        "CLAUDE_PLUGIN_DATA": str(plugin_data),
        "CLAUDE_PLUGIN_ROOT": str(REPO),
        "PYTHONPATH": str(REPO),
    }
    for name in ("INIT_CWD", "CLAUDE_PROJECT_DIR"):
        variables.pop(name, None)
    registered = subprocess.run(
        [sys.executable, "-m", "gaia.install_root", str(workspace)],
        env=variables, cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert registered.returncode == 0, registered.stderr
    return {"vars": variables, "workspace": workspace}


def _hook(env, script: Path, event: dict) -> None:
    payload = {"session_id": "plugin-manifest-test", "cwd": str(env["workspace"]), **event}
    proc = subprocess.run(
        [sys.executable, str(script)], input=json.dumps(payload), env=env["vars"],
        cwd=env["workspace"], capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def _session(env) -> None:
    _hook(env, SESSION_START, {"hook_event_name": "SessionStart", "matcher": "startup"})


def _uninstall(env) -> dict:
    proc = subprocess.run(
        [sys.executable, str(GAIA), "uninstall", "--workspace", str(env["workspace"]), "--json", "--no-backup"],
        env=env["vars"], cwd=env["workspace"], capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["manifest"]


def _local(env) -> Path:
    return env["workspace"] / ".claude" / "settings.local.json"


def _manifest(env) -> dict | None:
    path = env["workspace"] / ".claude" / "gaia-manifest.json"
    return json.loads(path.read_text()) if path.exists() else None


def _gaia_free(doc: dict) -> None:
    assert "attribution" not in doc
    allow = doc.get("permissions", {}).get("allow", [])
    assert "Bash(*)" not in allow and "Read" not in allow
    assert not doc.get("permissions", {}).get("deny")


def test_a_session_edit_is_recorded_and_reverted_byte_for_byte(env):
    _local(env).write_bytes(USER_LOCAL)

    _session(env)

    assert _local(env).read_bytes() != USER_LOCAL
    manifest = _manifest(env)
    assert manifest is not None and manifest["channel"] == "plugin"
    assert ".claude/settings.local.json" in {e["path"] for e in manifest["entries"]}

    result = _uninstall(env)
    assert result["source"] == "manifest"
    assert _local(env).read_bytes() == USER_LOCAL
    assert _manifest(env) is None


def test_b_settings_the_session_created_are_removed(env):
    _session(env)
    assert _local(env).exists()

    _uninstall(env)

    assert not _local(env).exists()


def test_c_user_edits_after_the_session_survive(env):
    _local(env).write_bytes(USER_LOCAL)
    _session(env)
    doc = json.loads(_local(env).read_text())
    doc["later"] = True
    doc["permissions"]["allow"].append("mcp__user__later")
    _local(env).write_text(json.dumps(doc, indent=2) + "\n")

    _uninstall(env)

    reverted = json.loads(_local(env).read_text())
    _gaia_free(reverted)
    assert reverted["later"] is True and reverted["myKey"] == {"keep": 1}
    assert sorted(reverted["permissions"]["allow"]) == [
        "Bash(npm test:*)", "mcp__user__later", "mcp__user__tool",
    ]


def test_d_plugin_only_workspace_without_manifest_is_adopted(env):
    _local(env).write_bytes(b'{"permissions":{"allow":["mcp__user__tool"]},"myKey":{"keep":1}}')
    _session(env)
    (env["workspace"] / ".claude" / "gaia-manifest.json").unlink()

    result = _uninstall(env)

    assert result["source"] == "adopted"
    reverted = json.loads(_local(env).read_text())
    _gaia_free(reverted)
    assert reverted["myKey"] == {"keep": 1}
    assert reverted["permissions"]["allow"] == ["mcp__user__tool"]


def test_e_repeated_sessions_still_revert_exactly(env):
    _local(env).write_bytes(USER_LOCAL)

    _session(env)
    first = _manifest(env)
    _session(env)
    assert _manifest(env) == first

    _uninstall(env)

    assert _local(env).read_bytes() == USER_LOCAL


def test_f_registry_in_the_workspace_is_reverted_and_the_marker_stays_home(env):
    env["vars"].pop("CLAUDE_PLUGIN_DATA")
    _local(env).write_bytes(USER_LOCAL)
    before = sorted(p.name for p in (env["workspace"] / ".claude").iterdir())

    _session(env)
    _hook(env, USER_PROMPT_SUBMIT, {"hook_event_name": "UserPromptSubmit", "prompt": "hello"})
    written = {p.name for p in (env["workspace"] / ".claude").iterdir()}
    assert "plugin-registry.json" in written
    assert ".plugin-initialized" not in written
    assert (Path(env["vars"]["GAIA_DATA_DIR"]) / ".plugin-initialized").is_file()

    _uninstall(env)

    assert sorted(p.name for p in (env["workspace"] / ".claude").iterdir()) == before
    assert _local(env).read_bytes() == USER_LOCAL
