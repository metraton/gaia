#!/usr/bin/env python3
"""Layer D: Claude Code protected-path deny, proven on its own terms.

Task 508, gate 968. The Claude Code PreToolUse chain is an independent
enforcement point from the OpenCode plugin seam (different trigger, different
process); no OpenCode result is cited or inherited here.

The proof drives a Write attempt at a controlled protected target through the
REAL entrypoint file (hooks/pre_tool_use.py) as a subprocess and requires a
categorical deny with the target byte-identical, naming the hooks.json matcher
that fired and the resolved entrypoint that executed so the installed copy and
the source checkout are distinguished rather than assumed to agree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_HOOKS = REPO_ROOT / "hooks"
SOURCE_MANIFEST = SOURCE_HOOKS / "hooks.json"
SOURCE_ENTRYPOINT = SOURCE_HOOKS / "pre_tool_use.py"

HARNESS_HOOKS_LITERAL = Path.home() / ".claude" / "hooks"

EXPECTED_MATCHER = "Read|Edit|Write|Glob|Grep|WebSearch|WebFetch|NotebookEdit"

SUBAGENT_AGENT_ID = "a1b2c3d4e5f60718"
SUBAGENT_AGENT_TYPE = "developer"


def _digest_and_size(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _worktree_marks(target: Path) -> tuple[str, str]:
    diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--stat", "--", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--short", "--", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    assert diff.returncode == 0, diff.stderr
    assert status.returncode == 0, status.stderr
    return diff.stdout.strip(), status.stdout.strip()


def _matching_pretooluse_entry(tool_name: str) -> dict:
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["hooks"]["PreToolUse"]
    return next(
        entry
        for entry in entries
        if re.fullmatch(entry["matcher"], tool_name)
    )


@pytest.fixture()
def probe_env(tmp_path, bootstrapped_db_template) -> dict:
    from tests.conftest import copy_bootstrapped_db

    root = tmp_path / "layer-d-data"
    root.mkdir()
    database = copy_bootstrapped_db(bootstrapped_db_template, root / "gaia.db")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GAIA_DB"] = str(database)
    env["GAIA_DATA_DIR"] = str(root)
    return env


def test_file_tool_matcher_names_pre_tool_use_entrypoint():
    entry = _matching_pretooluse_entry("Write")
    assert entry["matcher"] == EXPECTED_MATCHER
    assert len(entry["hooks"]) == 1
    command = entry["hooks"][0]["command"]
    assert command.endswith("hooks/pre_tool_use.py"), command

    executed = SOURCE_ENTRYPOINT.resolve()
    installed_literal = HARNESS_HOOKS_LITERAL / "pre_tool_use.py"
    installed_resolved = Path(os.path.realpath(installed_literal))
    print(f"matcher={entry['matcher']}")
    print(f"entrypoint_command={command}")
    print(f"executed={executed}")
    print(f"installed_literal={installed_literal}")
    print(f"installed_resolved={installed_resolved}")
    assert SOURCE_ENTRYPOINT.is_file()
    assert executed == Path(os.path.realpath(SOURCE_ENTRYPOINT))


def test_protected_write_denied_through_real_entrypoint_byte_identical(probe_env):
    target = SOURCE_ENTRYPOINT
    digest_before, size_before = _digest_and_size(target)
    diff_before, status_before = _worktree_marks(target)

    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(target)},
        "session_id": "sess-layer-d-proof",
        "agent_id": SUBAGENT_AGENT_ID,
        "agent_type": SUBAGENT_AGENT_TYPE,
    }
    executed = str(Path(os.path.realpath(SOURCE_ENTRYPOINT)))
    completed = subprocess.run(
        [sys.executable, executed],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=120, env=probe_env,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"entrypoint emitted no verdict\nstderr:\n{completed.stderr}"
    verdict = json.loads(lines[-1])
    specific = verdict.get("hookSpecificOutput", {})
    decision = specific.get("permissionDecision", "")
    reason = specific.get("permissionDecisionReason", "")
    print(f"matcher={EXPECTED_MATCHER}")
    print(f"executed={executed}")
    print(f"installed_resolved={os.path.realpath(HARNESS_HOOKS_LITERAL / 'pre_tool_use.py')}")
    print(f"decision={decision}")
    print(f"exit_code={completed.returncode}")
    assert completed.returncode == 0, completed.stderr
    assert decision == "deny", f"expected a categorical deny, got {decision!r}"
    assert re.search(r"P-[a-f0-9]{32}", reason), (
        "the deny must carry the subagent protected-path approval_id"
    )
    assert "PROTECTED" in reason.upper() or "T3_BLOCKED" in reason, (
        "the deny must come from protected-path policy, not a session-wide guard"
    )

    digest_after, size_after = _digest_and_size(target)
    diff_after, status_after = _worktree_marks(target)
    print(f"digest_before={digest_before}")
    print(f"digest_after={digest_after}")
    print(f"bytes_before={size_before}")
    print(f"bytes_after={size_after}")
    assert (digest_after, size_after) == (digest_before, size_before)
    assert (diff_after, status_after) == (diff_before, status_before) == ("", "")
