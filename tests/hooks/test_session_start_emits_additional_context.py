#!/usr/bin/env python3
"""Phase 4: SessionStart must emit hookSpecificOutput.additionalContext.

The session_manifest assembler decides what to include; this test verifies
that the SessionStart entry-point hook actually forwards that string into
the response under hookSpecificOutput, and that an empty manifest yields
NO hookSpecificOutput field (Claude Code distinguishes absence from empty).

The hook runs its logic under ``if __name__ == "__main__":``. Rather than
fight that with importlib, we drive it the way Claude Code does: pipe a
SessionStart event on stdin and parse the JSON the script writes to stdout.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HOOK_PATH = (
    Path(__file__).resolve().parents[2] / "hooks" / "session_start.py"
)


def _run_session_start(cwd: Path, extra_env: dict) -> dict:
    """Spawn session_start.py with a fake SessionStart event and return JSON.

    The hook reads stdin once, then prints a JSON response. We capture
    stdout and json-decode. stderr is left attached to ours so a crash is
    visible.

    cwd is set to a fresh tmp directory so ``find_claude_dir()`` cannot
    walk up into the real workspace. CLAUDE_PLUGIN_DATA is pinned to an
    empty subdir so the plugin registry / approval cache lookups land in
    the same isolation. Combined, those two env vars give the subprocess
    a clean view that resembles a fresh install.
    """
    env = os.environ.copy()
    env.update(extra_env)
    payload = json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-test-phase4",
            "matcher": "startup",
        }
    )
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
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
    stdout = proc.stdout.strip()
    if not stdout:
        return {}
    return json.loads(stdout)


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Build a tmp workspace with an empty .claude tree so the subprocess
    cannot reach the real ~/.claude/ files.

    Yields (cwd, plugin_data_dir). cwd is what we pass via cwd= to subprocess;
    plugin_data_dir is what we pin via CLAUDE_PLUGIN_DATA so registry /
    approvals lookups land here too.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    claude_dir = workspace / ".claude"
    claude_dir.mkdir()

    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield workspace, plugin_data


class TestSessionStartManifest:
    """SessionStart response must carry hookSpecificOutput when manifest
    is non-empty, and omit it when the manifest is empty."""

    def test_manifest_non_empty_emits_additional_context(self, isolated_workspace):
        """The Environment block is built unconditionally in ops mode and
        always produces at least cwd + machine. Run the hook in ops mode
        with a clean home and verify hookSpecificOutput is present.
        """
        cwd, _ = isolated_workspace
        result = _run_session_start(
            cwd,
            {
                "CLAUDE_SESSION_ID": "sess-test-phase4",
                "GAIA_PLUGIN_MODE": "ops",
            },
        )
        hso = result.get("hookSpecificOutput")
        assert hso is not None, (
            "ops mode with reachable cwd must produce a non-empty manifest. "
            "The hookSpecificOutput key MUST appear so Claude Code injects "
            "the manifest into the orchestrator's context."
        )
        assert hso.get("hookEventName") == "SessionStart"
        ctx = hso.get("additionalContext", "")
        assert isinstance(ctx, str) and ctx, (
            "additionalContext must be a non-empty string when "
            "hookSpecificOutput is emitted."
        )
        assert "## Environment" in ctx, (
            "The Environment block is the minimum guaranteed content of "
            "the manifest in ops mode."
        )
        assert "cwd:" in ctx

    def test_manifest_empty_in_security_mode_omits_hook_specific_output(
        self, isolated_workspace
    ):
        """build_session_context returns '' when mode != 'ops'. The hook
        must NOT emit hookSpecificOutput in that case -- an empty
        additionalContext field is meaningfully different from absence.
        """
        cwd, _ = isolated_workspace
        result = _run_session_start(
            cwd,
            {
                "CLAUDE_SESSION_ID": "sess-test-phase4",
                "GAIA_PLUGIN_MODE": "security",
            },
        )
        # Either no hookSpecificOutput key at all, or one without an
        # additionalContext payload. The brief asks for the former.
        assert "hookSpecificOutput" not in result, (
            "Empty manifest must not emit hookSpecificOutput. Including the "
            "key with an empty string would still trigger Claude Code's "
            "context-injection path with zero payload."
        )
        # Sanity: session_type still produced.
        assert result.get("session_type") == "startup"
