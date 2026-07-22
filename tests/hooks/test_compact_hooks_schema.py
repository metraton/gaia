#!/usr/bin/env python3
"""Regression: PreCompact/PostCompact must emit schema-valid hook output.

Root cause (observed on /compact): pre_compact.py and post_compact.py both
emitted ``{"hookSpecificOutput": {"hookEventName": "PreCompact"|"PostCompact",
"additionalContext": ...}}``. Claude Code's hook-output validator only
accepts a fixed set of ``hookSpecificOutput.hookEventName`` literals
(PreToolUse, UserPromptSubmit, UserPromptExpansion, PostToolUse,
PostToolUseFailure, PostToolBatch, Stop, SubagentStop, SessionStart, Setup,
SubagentStart, PermissionDenied, PermissionRequest, Elicitation,
ElicitationResult, MessageDisplay) -- "PreCompact" and "PostCompact" are NOT
in that set, so every ``/compact`` failed validation with "(root): Invalid
input" and the post-compaction context refresh (agent roster + anomalies)
was silently dropped, never delivered.

The fix: PreCompact and PostCompact never emit hookSpecificOutput (there is
no runtime consumer for either literal even when schema-valid -- see both
hooks' module docstrings), and the real delivery path -- SessionStart fired
with ``source: "compact"`` -- carries the compact-context refresh instead
(hooks/session_start.py). These tests assert (1) pre_compact.py and
post_compact.py never emit an invalid hookEventName, and (2) session_start.py
emits a *valid* SessionStart-shaped hookSpecificOutput when source=="compact".
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
PRE_COMPACT_PATH = HOOKS_DIR / "pre_compact.py"
POST_COMPACT_PATH = HOOKS_DIR / "post_compact.py"
SESSION_START_PATH = HOOKS_DIR / "session_start.py"

# The full set of hookEventName literals Claude Code's hook-output validator
# accepts inside hookSpecificOutput, as observed in the running claude-code
# binary's response-consumption switch. "PreCompact" and "PostCompact" are
# deliberately absent -- that absence is the bug this test guards against.
ALLOWED_HOOK_EVENT_NAMES = {
    "PreToolUse",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "Setup",
    "SubagentStart",
    "PermissionDenied",
    "PermissionRequest",
    "Elicitation",
    "ElicitationResult",
    "MessageDisplay",
}


def _assert_schema_valid(response: dict) -> None:
    """Assert a hook's JSON response cannot fail Claude Code's validator.

    Every top-level field in Claude Code's Hook JSON Output schema is
    optional, so an empty object always validates. The one way a hook can
    fail validation is by emitting a ``hookSpecificOutput`` whose
    ``hookEventName`` is outside the accepted literal set (or missing the
    field entirely, which is a different, also-invalid shape).
    """
    if "hookSpecificOutput" not in response:
        return
    hso = response["hookSpecificOutput"]
    assert isinstance(hso, dict), "hookSpecificOutput must be an object"
    assert "hookEventName" in hso, (
        'hookSpecificOutput is missing required field "hookEventName"'
    )
    assert hso["hookEventName"] in ALLOWED_HOOK_EVENT_NAMES, (
        f"hookEventName {hso['hookEventName']!r} is not one of Claude Code's "
        f"accepted literals -- this is exactly the '(root): Invalid input' "
        f"validation failure observed on /compact"
    )


def _run_hook(path: Path, payload: dict, cwd: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"{path.name} exited non-zero: stderr={proc.stderr!r}"
    )
    stdout = proc.stdout.strip()
    if not stdout:
        return {}
    return json.loads(stdout)


class TestPreCompactSchemaValid:
    """PreCompact must never emit hookSpecificOutput -- Claude Code does not
    accept "PreCompact" as a hookEventName and has no consumer for it."""

    def test_no_hook_specific_output(self, tmp_path):
        payload = {"hook_event_name": "PreCompact", "session_id": "sess-precompact-test"}
        response = _run_hook(PRE_COMPACT_PATH, payload, tmp_path)
        _assert_schema_valid(response)
        assert "hookSpecificOutput" not in response, (
            "PreCompact must not emit hookSpecificOutput: Claude Code's "
            "hook-output validator rejects hookEventName='PreCompact' "
            "outright, and even a valid shape would be dropped -- there is "
            "no runtime consumer for this event."
        )

    def test_active_loop_does_not_change_the_shape(self, tmp_path):
        """Even with an active agentic-loop state file present (which used to
        populate additionalContext), the response must stay schema-valid and
        must not resurrect the invalid hookSpecificOutput shape."""
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "eval_command": "pytest",
                    "metric": "pass_rate",
                    "threshold": 1.0,
                    "iteration": 3,
                    "status": "running",
                }
            )
        )
        payload = {"hook_event_name": "PreCompact", "session_id": "sess-precompact-loop"}
        response = _run_hook(PRE_COMPACT_PATH, payload, tmp_path)
        _assert_schema_valid(response)
        assert "hookSpecificOutput" not in response


class TestPostCompactSchemaValid:
    """PostCompact must never emit hookSpecificOutput -- same platform
    limitation as PreCompact. The real delivery path is SessionStart."""

    def test_no_hook_specific_output(self, tmp_path):
        payload = {"hook_event_name": "PostCompact", "session_id": "sess-postcompact-test"}
        response = _run_hook(POST_COMPACT_PATH, payload, tmp_path)
        _assert_schema_valid(response)
        assert "hookSpecificOutput" not in response, (
            "PostCompact must not emit hookSpecificOutput: Claude Code's "
            "hook-output validator rejects hookEventName='PostCompact' "
            "outright, and even a valid shape would be dropped -- there is "
            "no runtime consumer for this event. The compact-context "
            "refresh is delivered via SessionStart(source=compact) instead."
        )


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Build a tmp workspace with an empty .claude tree so the subprocess
    cannot reach the real ~/.claude/ files. Mirrors the fixture in
    test_session_start_emits_additional_context.py."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".claude").mkdir()

    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield workspace, plugin_data


class TestSessionStartCompactDeliversRefresh:
    """SessionStart(source=compact) is the valid replacement delivery path
    for the compact-context refresh PostCompact used to (invalidly) carry."""

    def test_compact_source_emits_valid_session_start_shape(self, isolated_workspace):
        cwd, _ = isolated_workspace
        env = os.environ.copy()
        env["CLAUDE_SESSION_ID"] = "sess-compact-test"
        payload = json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-compact-test",
                "source": "compact",
            }
        )
        proc = subprocess.run(
            [sys.executable, str(SESSION_START_PATH)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd),
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"session_start.py exited non-zero: stderr={proc.stderr!r}"
        )
        response = json.loads(proc.stdout.strip())
        _assert_schema_valid(response)

        hso = response.get("hookSpecificOutput")
        assert hso is not None, (
            "source=compact must produce the compact-context refresh "
            "(orchestrator identity block is always present), so "
            "hookSpecificOutput must be emitted."
        )
        assert hso["hookEventName"] == "SessionStart", (
            "The event actually firing is SessionStart (source=compact), "
            "so hookEventName must say SessionStart, not PostCompact -- "
            "Claude Code raises 'Hook returned incorrect event name' "
            "otherwise."
        )
        ctx = hso.get("additionalContext", "")
        assert isinstance(ctx, str) and ctx
        assert "Post-Compaction Context Refresh" in ctx, (
            "source=compact must build via build_compact_context(), the "
            "same builder post_compact.py used to own -- not the full "
            "startup/resume session manifest."
        )

    def test_startup_source_still_uses_full_manifest(self, isolated_workspace):
        """Regression guard: the new compact branch must not swallow the
        existing startup/resume path."""
        cwd, _ = isolated_workspace
        env = os.environ.copy()
        env["CLAUDE_SESSION_ID"] = "sess-startup-test"
        payload = json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-startup-test",
                "source": "startup",
            }
        )
        proc = subprocess.run(
            [sys.executable, str(SESSION_START_PATH)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd),
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"session_start.py exited non-zero: stderr={proc.stderr!r}"
        )
        response = json.loads(proc.stdout.strip())
        _assert_schema_valid(response)
        hso = response.get("hookSpecificOutput", {})
        ctx = hso.get("additionalContext", "")
        assert "## Environment" in ctx, (
            "source=startup must still build the full session manifest "
            "(Environment block), not the lightweight compact refresh."
        )
        assert "Post-Compaction Context Refresh" not in ctx
