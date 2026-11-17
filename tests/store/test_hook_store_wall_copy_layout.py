"""Hooks started through launch.sh write the real store from a copied .claude/hooks."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="hooks run through sh")

SESSION = "ses-copy-layout"
PROMPT = "Probe the copied hooks.\n\nproject=gaia task_id=1"


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GAIA_", "PYTEST_", "CLAUDE_"))}
    env["HOME"] = str(home)
    return env


@pytest.fixture
def layout(tmp_path):
    claude = tmp_path / "ws" / ".claude"
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(ROOT / "hooks", claude / "hooks", ignore=ignore)
    shutil.copytree(ROOT / "tools", claude / "tools", ignore=ignore)
    for linked in ("gaia", "opencode", "config"):
        (claude / linked).symlink_to(ROOT / linked, target_is_directory=True)
    return claude


def _hook(layout, name, event, *, launcher=True):
    entry = str(layout / "hooks" / name)
    argv = [SH, str(layout / "hooks" / "launch.sh"), entry] if launcher else [sys.executable, entry]
    return subprocess.run(
        argv, env=_env(layout.parents[1]), cwd=layout.parent, input=json.dumps(event),
        capture_output=True, text=True, timeout=120,
    )


def _gaia(home, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "bin" / "gaia"), *args],
        env=_env(home), cwd=home, capture_output=True, text=True, timeout=120,
    )


def _count(home: Path, sql: str, *args) -> int:
    with sqlite3.connect(home / ".gaia" / "gaia.db") as con:
        return con.execute(sql, args).fetchone()[0]


def _session_start(layout, **kwargs):
    return _hook(
        layout, "session_start.py",
        {"hook_event_name": "SessionStart", "session_id": SESSION, "source": "startup", "cwd": str(layout.parent)},
        **kwargs,
    )


def test_launcher_makes_a_copied_session_start_create_the_store(layout):
    home = layout.parents[1]

    started = _session_start(layout)

    assert started.returncode == 0, started.stderr
    assert _count(home, "SELECT count(*) FROM sqlite_master WHERE type='table'") > 0


def test_a_copied_entry_script_meets_the_wall_without_the_launcher(layout):
    home = layout.parents[1]
    assert _session_start(layout).returncode == 0
    (layout / "hooks" / "stray_probe.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(layout)!r})\n"
        "from gaia.store.writer import _connect\n"
        "_connect().execute('CREATE TABLE stray_probe(x)')\n"
    )

    bare = _hook(layout, "stray_probe.py", {}, launcher=False)

    assert bare.returncode != 0
    assert "accidental-write wall" in bare.stderr
    assert _count(home, "SELECT count(*) FROM sqlite_master WHERE name = 'stray_probe'") == 0


def test_dispatch_birth_through_the_launcher_lands_a_row(layout):
    home = layout.parents[1]
    assert _session_start(layout).returncode == 0

    dispatched = _hook(layout, "pre_tool_use.py", {
        "hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": SESSION,
        "tool_use_id": "toolu_birth", "cwd": str(layout.parent),
        "tool_input": {"subagent_type": "gaia-system", "description": "probe", "prompt": PROMPT},
    })

    assert dispatched.returncode == 0, dispatched.stderr
    assert _count(home, "SELECT count(*) FROM agent_contract_handoffs WHERE session_id = ?", SESSION) == 1


def test_approval_presentation_through_the_launcher_lands_shown(layout):
    home = layout.parents[1]
    assert _session_start(layout).returncode == 0
    requested = _gaia(
        home, "approvals", "request-set", "--json", "--command", "git push origin feat/probe",
        "--cwd", str(home), "--what", "Publish the probe branch.", "--question", "Publish the probe?",
        "--does", "Pushes the probe branch.", "--impact", "The branch becomes visible.",
        "--verification", "git ls-remote", "--shared-state", "Yes: the remote.",
        "--rollback", "Delete the remote branch.", "--agent-id", "gaia-system", "--session-id", "ses-requester",
    )
    assert requested.returncode == 0, requested.stderr
    approval_id = json.loads(requested.stdout)["approval_id"]
    asked = _gaia(home, "approvals", "question", approval_id)
    assert asked.returncode == 0, asked.stderr

    presented = _hook(layout, "pre_tool_use.py", {
        "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": SESSION,
        "tool_use_id": "toolu_shown", "cwd": str(layout.parent),
        "tool_input": json.loads(asked.stdout),
    })

    assert presented.returncode == 0, presented.stderr
    assert _count(
        home, "SELECT count(*) FROM approval_events WHERE approval_id = ? AND event_type = 'SHOWN'", approval_id
    ) == 1
