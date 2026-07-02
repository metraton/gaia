"""Production-faithful harness for exercising the T3 grant cycle.

The approval grant cycle (block -> activate -> retry) is session-scoped: a
grant created under one ``session_id`` must only satisfy a retry resolved to
the same id. In production Claude Code does NOT reliably export
``CLAUDE_SESSION_ID`` into the hook subprocess; it ALWAYS pipes the real
``session_id`` inside the JSON event on stdin. A faithful test must therefore
resolve the session from the EVENT, never from the environment.

This harness deliberately runs ``pre_tool_use.py`` as a real subprocess with a
sanitized environment (``CLAUDE_SESSION_ID`` stripped) and feeds the event via
stdin -- exactly the production entry point at the bottom of ``pre_tool_use.py``.
It is the anti-trap counterpart to the e2e fixtures that ``monkeypatch.setenv``
the session id: those mask the session-scoping bug because both the grant and
the retry resolve to the same env value.

Importable from any test module:

    from tests.fixtures.grant_cycle_harness import run_pre_tool_use_event
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
PRE_TOOL_USE = HOOKS_DIR / "pre_tool_use.py"


class GrantCycleResult:
    """Outcome of one ``pre_tool_use`` subprocess invocation.

    Attributes:
        exit_code: Process exit code (0 = allow/ask, 2 = hard block).
        stdout: Raw stdout (the hook prints its JSON decision here).
        stderr: Raw stderr (human-readable BLOCKED/T3 summary lines).
        output: Parsed ``stdout`` JSON when stdout is a JSON object, else None.
    """

    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.output: Optional[Dict[str, Any]] = None
        stripped = stdout.strip()
        if stripped.startswith("{"):
            try:
                self.output = json.loads(stripped)
            except json.JSONDecodeError:
                self.output = None

    @property
    def permission_decision(self) -> Optional[str]:
        """The ``permissionDecision`` from a structured hook response, if any."""
        if not isinstance(self.output, dict):
            return None
        return self.output.get("hookSpecificOutput", {}).get("permissionDecision")

    @property
    def is_allowed(self) -> bool:
        """True when the command was allowed through (no block, no ask)."""
        if self.exit_code != 0:
            return False
        decision = self.permission_decision
        return decision in (None, "allow")


def run_pre_tool_use_event(
    event: Dict[str, Any],
    *,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> GrantCycleResult:
    """Run ``pre_tool_use.py`` as a production subprocess fed via stdin.

    The environment is sanitized to match production: ``CLAUDE_SESSION_ID`` is
    removed so the hook can only learn the session from the event payload. The
    ``session_id`` the caller wants the hook to use MUST be present in ``event``
    (this is how real Claude Code delivers it).

    Args:
        event: The hook event dict (``tool_name``, ``tool_input``, ``session_id``,
            and any other Claude Code fields). Serialized to stdin as JSON.
        cwd: Working directory for the subprocess -- the project root whose
            ``.claude`` dir holds the isolated state/DB.
        extra_env: Optional env overrides merged on top of the sanitized base
            (used to point ``GAIA_DATA_DIR`` at the test isolation).

    Returns:
        A ``GrantCycleResult`` capturing exit code, stdout, stderr, and the
        parsed decision.
    """
    env = dict(os.environ)
    # The trap this harness exists to avoid: a session id leaking via env.
    env.pop("CLAUDE_SESSION_ID", None)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(HOOKS_DIR), str(REPO_ROOT), env.get("PYTHONPATH", "")) if p
    )
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        [sys.executable, str(PRE_TOOL_USE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=60,
    )
    return GrantCycleResult(proc.returncode, proc.stdout, proc.stderr)
