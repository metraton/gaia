"""Known false positives stay closed while their mutating and secret-reading twins keep their verdicts.

Judged through the Claude Code PreToolUse adapter: Grep filtering by a file
type is not a secret read, ``node --test`` over a directory or glob is a test
run rather than an unreadable script, and the Windows disk wipers the D140
floor missed are blocked in every shell context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.test.js").write_text("require('node:test')('a', () => {});\n")
    (tmp_path / "deploy.test.js").write_text("require('fs').rmSync('/srv/data', {recursive: true});\n")
    return tmp_path


def verdict(tool_name: str, tool_input: dict, cwd: str = "/") -> str:
    """Return ``free``, ``approval``, ``blocked`` (the permanent floor) or ``denied`` (any other refusal)."""
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "ses-known-false-positives",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": f"toolu-{tool_name}",
        "cwd": cwd,
        "agent_id": "a" + "8" * 16,
        "agent_type": "developer",
    }
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if response.approval_id:
        return "approval"
    if isinstance(response.output, str):
        reason = response.output
    else:
        specific = response.output.get("hookSpecificOutput", {})
        decision = specific.get("permissionDecision")
        if decision == "allow" or (decision is None and response.exit_code == 0):
            return "free"
        reason = specific.get("permissionDecisionReason", "")
    return "blocked" if "blocked by security policy" in reason else "denied"


@pytest.mark.parametrize("include", ["*.json", "**/*.json"])
def test_grep_filtering_by_json_type_is_free(project, include):
    assert verdict("Grep", {"pattern": "version", "glob": include, "path": str(project)}) == "free"


@pytest.mark.parametrize("include", ["credentials.json", "**/credentials.json", ".env", ".env*", "*.pem"])
def test_grep_glob_naming_a_secret_is_still_refused(project, include):
    assert verdict("Grep", {"pattern": "x", "glob": include, "path": str(project)}) == "denied"


@pytest.mark.parametrize("command", [
    "node --test tests/",
    "node --test 'tests/**/*.test.js'",
    "node --test --test-reporter=spec tests/",
    "node --test --test-reporter dot --test-concurrency 2 'tests/**/*.test.js'",
    "node --test --test-name-pattern=a --test-reporter-destination=stdout tests/",
    "NODE_ENV=test node --test tests/",
])
def test_node_test_over_a_directory_or_glob_is_free(project, command):
    assert verdict("Bash", {"command": command}, cwd=str(project)) == "free"


@pytest.mark.parametrize("command", [
    "node --test deploy.test.js",
    "node deploy.test.js",
    "node --test tests/ ./deploy.js",
    "node --test 'tests/*.test.js' ./deploy.js",
    "node --test --import=./deploy.js tests/",
    "node --test --require ./deploy.js tests/",
    "node --test -r ./deploy.js 'tests/*.test.js'",
    "node --test --experimental-loader=./deploy.mjs tests/",
    "node --test --loader=./deploy.mjs tests/",
    "node --test --loader ./deploy.mjs tests/",
    "node --test --test-global-setup=./deploy.mjs tests/",
    "node --test --test-foo=./x.js tests/",
    "node --test --test-reporter=./reporter.mjs tests/",
    "node --test --test-reporter-destination=./out.txt tests/",
    "node --import=./deploy.js --test tests/",
    "NODE_OPTIONS=--import=./deploy.js node --test tests/",
    "NODE_OPTIONS='--require ./deploy.js' node --test tests/",
    "env NODE_OPTIONS=--import=./deploy.js node --test tests/",
])
def test_node_on_a_named_or_preloaded_script_still_asks(project, command):
    assert verdict("Bash", {"command": command}, cwd=str(project)) == "approval"


@pytest.mark.parametrize("export,run", [
    ("export NODE_OPTIONS=--import=./deploy.js", "; node --test tests/"),
    ("export NODE_OPTIONS='--require ./deploy.js'", " && node --test 'tests/*.test.js'"),
])
def test_node_test_after_exporting_node_options_is_refused_like_an_unreadable_script(project, export, run):
    chain = verdict("Bash", {"command": export + run}, cwd=str(project))
    assert chain == verdict("Bash", {"command": export + " && node ./deploy.js"}, cwd=str(project)) == "denied"
    assert verdict("Bash", {"command": "export NODE_ENV=test" + run}, cwd=str(project)) == "free"


WIPERS = [
    "format C:/q",
    "format /fs:ntfs D:/q",
    "vssadmin resize shadowstorage /for=C: /on=C: /maxsize=1MB",
    "C:/Windows/System32/format.com C:/q",
    "C:\\Windows\\System32\\vssadmin.exe delete shadows /all",
]


def _in_every_context(command: str) -> list:
    return [
        ("Bash", command),
        ("Bash", f"cmd /c {command}"),
        ("Bash", f"powershell -Command '{command}'"),
        ("Bash", f"pwsh -NoProfile -c '{command}'"),
        ("PowerShell", command),
    ]


@pytest.mark.parametrize("tool_name,command", [row for c in WIPERS for row in _in_every_context(c)])
def test_windows_disk_wiper_is_blocked_like_mkfs(tool_name, command):
    assert verdict(tool_name, {"command": command}) == verdict("Bash", {"command": "mkfs.ext4 /dev/sdb"}) == "blocked"


@pytest.mark.parametrize("tool_name,command", [
    ("Bash", "git log --format=%H"),
    ("Bash", "vssadmin list shadowstorage"),
    ("PowerShell", "Get-Date -Format C:/q"),
])
def test_plain_word_uses_are_not_blocked(tool_name, command):
    assert verdict(tool_name, {"command": command}) != "blocked"
