"""
Hook executor for gaia-ops replay testing.

Runs hooks as subprocesses with ReplayEvent payloads and compares results
against expected outcomes. Completely decoupled from log parsing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from gaia_simulator.extractor import ReplayEvent


@dataclass(frozen=True)
class ReplayResult:
    """Result of replaying a single event against the current hooks."""

    event: ReplayEvent
    actual_exit_code: int
    actual_stdout: str
    actual_stderr: str
    actual_decision: str  # "ALLOW", "BLOCK", "DENY", "ERROR"
    actual_tier: str  # parsed from stdout if available
    matched: bool  # expected_decision == actual_decision
    regression_type: Optional[str]  # None, "allow_to_block", "block_to_allow", "tier_change", "exit_code_change"
    actual_metadata: dict[str, Any] = field(default_factory=dict)


_RE_TIER = re.compile(r"\bT[0-3]\b")


def _parse_decision_from_output(
    exit_code: int, stdout: str
) -> tuple[str, str]:
    """Parse the hook decision and tier from stdout/exit_code.

    Returns:
        (decision, tier) tuple.
    """
    decision = "ALLOW"
    tier = ""

    if exit_code == 2:
        decision = "BLOCK"
    elif exit_code != 0:
        decision = "ERROR"

    # Try to parse structured JSON from stdout
    stdout_stripped = stdout.strip()
    if stdout_stripped:
        # Hook output may have multiple lines; find the last JSON line
        for line in reversed(stdout_stripped.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                # Check for deny via hookSpecificOutput
                hook_output = data.get("hookSpecificOutput", {})
                perm_decision = hook_output.get("permissionDecision", "")
                if perm_decision == "deny":
                    decision = "DENY"
                break
            except json.JSONDecodeError:
                continue

    return decision, tier


def _extract_tier_from_text(*texts: str) -> str:
    """Return the first security tier found in the provided texts."""
    for text in texts:
        if not text:
            continue
        match = _RE_TIER.search(text)
        if match:
            return match.group(0)
    return ""


def _parse_last_json_line(stdout: str) -> Optional[dict[str, Any]]:
    """Parse the last JSON object emitted on stdout, if any."""
    for line in reversed(stdout.strip().splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None


def _classify_regression(
    expected_decision: str,
    actual_decision: str,
    expected_exit_code: int,
    actual_exit_code: int,
    expected_tier: str,
    actual_tier: str,
    expected_metadata: Optional[dict[str, Any]] = None,
    actual_metadata: Optional[dict[str, Any]] = None,
    compare_tier: bool = True,
) -> Optional[str]:
    """Classify the type of regression, if any.

    Returns:
        None if no regression, or a string describing the regression type.
    """
    expected_metadata = expected_metadata or {}
    actual_metadata = actual_metadata or {}

    if expected_decision == actual_decision and expected_exit_code == actual_exit_code:
        if compare_tier and expected_tier and actual_tier and expected_tier != actual_tier:
            return "tier_change"
        for key, expected_value in expected_metadata.items():
            if key not in actual_metadata:
                return f"{key}_missing"
            if actual_metadata[key] != expected_value:
                return f"{key}_change"
        return None

    if expected_decision == "ALLOW" and actual_decision == "BLOCK":
        return "allow_to_block"
    if expected_decision == "ALLOW" and actual_decision == "DENY":
        return "allow_to_t3"
    if expected_decision == "BLOCK" and actual_decision == "ALLOW":
        return "block_to_allow"
    if expected_decision == "DENY" and actual_decision == "ALLOW":
        return "deny_to_allow"
    if expected_exit_code != actual_exit_code:
        return "exit_code_change"

    return "decision_change"


class HookRunner:
    """Executes hooks as subprocesses for replay testing.

    Creates an isolated temporary project directory for each batch run,
    mimicking the .claude/ directory structure that hooks expect.
    """

    def __init__(self, hooks_dir: Path, project_root: Optional[Path] = None):
        """Initialize the runner.

        Args:
            hooks_dir: Path to the directory containing hook .py files.
            project_root: Optional path to use as the simulated project root.
                         If None, a temporary directory is created per batch.
        """
        self.hooks_dir = hooks_dir
        self.project_root = project_root
        self._timeout = 30

    def _state_file_path(self, work_dir: Path) -> Path:
        """Return the hook state file path for a replay work directory."""
        return work_dir / ".claude" / ".hooks_state.json"

    def _load_hook_state(self, work_dir: Path) -> dict[str, Any]:
        """Load hook state written by pre_tool_use, if present."""
        path = self._state_file_path(work_dir)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _prime_post_tool_use_state(self, event: ReplayEvent, work_dir: Path) -> None:
        """Seed the pre-hook state so post_tool_use can replay faithfully."""
        tool_input = event.stdin_payload.get("tool_input", {})
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command", ""))

        state = {
            "tool_name": event.tool_name,
            "command": command,
            "tier": event.expected_tier or "unknown",
            "start_time": "2026-01-01T00:00:00",
            "start_time_epoch": 0.0,
            "session_id": event.stdin_payload.get("session_id", "replay"),
            "pre_hook_result": "allowed",
            "metadata": {},
        }
        self._state_file_path(work_dir).write_text(json.dumps(state))

    def _read_latest_audit_record(self, work_dir: Path) -> dict[str, Any]:
        """Read the most recent audit record emitted during replay, if any."""
        logs_dir = work_dir / ".claude" / "logs"
        audit_files = sorted(logs_dir.glob("audit-*.jsonl"))
        if not audit_files:
            return {}
        lines = audit_files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return {}

    def _parse_pre_tool_use_result(
        self,
        exit_code: int,
        stdout: str,
        stderr: str,
        work_dir: Path,
    ) -> tuple[str, str, dict[str, Any]]:
        """Parse pre_tool_use results, including tier from hook state/log artifacts."""
        decision, tier = _parse_decision_from_output(exit_code, stdout)
        payload = _parse_last_json_line(stdout) or {}
        hook_output = payload.get("hookSpecificOutput", {}) if isinstance(payload, dict) else {}

        state = self._load_hook_state(work_dir)
        if not tier:
            tier = str(state.get("tier", "") or "")
        if not tier:
            tier = _extract_tier_from_text(
                str(hook_output.get("permissionDecisionReason", "")),
                stdout,
                stderr,
            )

        actual_metadata: dict[str, Any] = {}
        if "updatedInput" in hook_output:
            actual_metadata["updated_input"] = hook_output["updatedInput"]
        if "permissionDecisionReason" in hook_output:
            actual_metadata["permission_reason"] = hook_output["permissionDecisionReason"]
        return decision, tier, actual_metadata

    def _parse_post_tool_use_result(
        self,
        exit_code: int,
        stdout: str,
        work_dir: Path,
    ) -> tuple[str, str, dict[str, Any]]:
        """Parse post_tool_use results using the audit record it just emitted."""
        decision = "PASS" if exit_code == 0 else "ERROR"
        audit_record = self._read_latest_audit_record(work_dir)
        actual_tier = str(audit_record.get("tier", "") or "")
        actual_metadata = {}
        if audit_record:
            actual_metadata["tool_exit_code"] = audit_record.get("exit_code")
            actual_metadata["duration_ms"] = audit_record.get("duration_ms")
        return decision, actual_tier, actual_metadata

    def _parse_stop_hook_result(
        self,
        exit_code: int,
        stdout: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Parse stop_hook results from its JSON stdout payload."""
        decision = "PASS" if exit_code == 0 else "ERROR"
        payload = _parse_last_json_line(stdout) or {}
        actual_metadata: dict[str, Any] = {}
        if payload:
            for key in ("quality_sufficient", "score", "recommendation"):
                if key in payload:
                    actual_metadata[key] = payload[key]
        return decision, "", actual_metadata

    def _parse_result(
        self,
        event: ReplayEvent,
        exit_code: int,
        stdout: str,
        stderr: str,
        work_dir: Path,
    ) -> tuple[str, str, dict[str, Any]]:
        """Dispatch hook-specific result parsing."""
        if event.hook_name == "pre_tool_use":
            return self._parse_pre_tool_use_result(exit_code, stdout, stderr, work_dir)
        if event.hook_name == "post_tool_use":
            return self._parse_post_tool_use_result(exit_code, stdout, work_dir)
        if event.hook_name == "stop_hook":
            return self._parse_stop_hook_result(exit_code, stdout)
        return ("PASS" if exit_code == 0 else "ERROR", "", {})

    def _setup_project_dir(self, base_dir: Path) -> Path:
        """Create a minimal .claude/ directory structure for hooks.

        Args:
            base_dir: Directory to set up as the project root.

        Returns:
            The base_dir path.
        """
        claude_dir = base_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        # Logs directory
        (claude_dir / "logs").mkdir(exist_ok=True)

        # Session directory
        session_dir = claude_dir / "session" / "active"
        session_dir.mkdir(parents=True, exist_ok=True)

        # Project context directory
        pc_dir = claude_dir / "project-context"
        pc_dir.mkdir(parents=True, exist_ok=True)

        # Minimal project-context.json
        minimal_context = {
            "metadata": {
                "version": "2.0",
                "last_updated": "2026-01-01T00:00:00Z",
                "scan_config": {
                    "last_scan": "2026-01-01T00:00:00Z",
                    "scanner_version": "0.1.0",
                    "staleness_hours": 24,
                },
            },
            "paths": {},
            "sections": {
                "project_identity": {
                    "name": "replay-test",
                    "type": "application",
                },
            },
        }
        (pc_dir / "project-context.json").write_text(
            json.dumps(minimal_context, indent=2)
        )

        # Workflow episodic memory dir
        wem_dir = pc_dir / "workflow-episodic-memory"
        wem_dir.mkdir(parents=True, exist_ok=True)
        (wem_dir / "signals").mkdir(exist_ok=True)

        # Config, memory, metrics directories
        (claude_dir / "config").mkdir(exist_ok=True)
        (claude_dir / "memory").mkdir(exist_ok=True)
        (claude_dir / "metrics").mkdir(exist_ok=True)

        # Settings.json
        settings = {
            "permissions": {"allow": ["Bash(*)"], "deny": []},
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

        return base_dir

    def _resolve_hook_script(self, hook_name: str) -> Path:
        """Resolve hook name to script path.

        Args:
            hook_name: Hook name like "pre_tool_use" or "subagent_stop".

        Returns:
            Path to the hook script.
        """
        script_name = f"{hook_name}.py"
        return self.hooks_dir / script_name

    def run(self, event: ReplayEvent, project_dir: Optional[Path] = None) -> ReplayResult:
        """Run the hook with the event's stdin_payload and compare results.

        Args:
            event: The ReplayEvent to replay.
            project_dir: Optional project directory to use. If None, uses
                        self.project_root or creates a temporary one.

        Returns:
            ReplayResult with actual vs expected comparison.
        """
        work_dir = project_dir or self.project_root
        if work_dir is None:
            tmp = tempfile.mkdtemp(prefix="replay_")
            work_dir = Path(tmp)
        self._setup_project_dir(work_dir)

        script_path = self._resolve_hook_script(event.hook_name)
        if not script_path.exists():
            return ReplayResult(
                event=event,
                actual_exit_code=-1,
                actual_stdout="",
                actual_stderr=f"Hook script not found: {script_path}",
                actual_decision="ERROR",
                actual_tier="",
                matched=False,
                regression_type="missing_hook",
            )

        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)

        if event.hook_name == "post_tool_use":
            self._prime_post_tool_use_state(event, work_dir)

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                input=json.dumps(event.stdin_payload),
                capture_output=True,
                text=True,
                env=env,
                timeout=self._timeout,
                cwd=str(work_dir),
            )
        except subprocess.TimeoutExpired:
            return ReplayResult(
                event=event,
                actual_exit_code=-1,
                actual_stdout="",
                actual_stderr="Timeout",
                actual_decision="ERROR",
                actual_tier="",
                matched=False,
                regression_type="timeout",
            )
        except OSError as exc:
            return ReplayResult(
                event=event,
                actual_exit_code=-1,
                actual_stdout="",
                actual_stderr=str(exc),
                actual_decision="ERROR",
                actual_tier="",
                matched=False,
                regression_type="os_error",
            )

        actual_decision, actual_tier, actual_metadata = self._parse_result(
            event,
            result.returncode,
            result.stdout,
            result.stderr,
            work_dir,
        )

        regression = _classify_regression(
            event.expected_decision,
            actual_decision,
            event.expected_exit_code,
            result.returncode,
            event.expected_tier,
            actual_tier,
            expected_metadata=event.expected_metadata,
            actual_metadata=actual_metadata,
            compare_tier=event.compare_tier,
        )

        matched = regression is None

        return ReplayResult(
            event=event,
            actual_exit_code=result.returncode,
            actual_stdout=result.stdout,
            actual_stderr=result.stderr,
            actual_decision=actual_decision,
            actual_tier=actual_tier,
            matched=matched,
            regression_type=regression,
            actual_metadata=actual_metadata,
        )

    def run_batch(
        self,
        events: list[ReplayEvent],
        progress_callback=None,
    ) -> list[ReplayResult]:
        """Run all events and return all results.

        Creates a single isolated project directory for the batch to
        share session state across sequential hook calls.

        Args:
            events: List of ReplayEvents to replay.
            progress_callback: Optional callable(current, total) for progress.

        Returns:
            List of ReplayResult instances in the same order as events.
        """
        results: list[ReplayResult] = []

        # Create a shared project directory for the batch
        if self.project_root:
            work_dir = self.project_root
        else:
            tmp = tempfile.mkdtemp(prefix="replay_batch_")
            work_dir = Path(tmp)

        self._setup_project_dir(work_dir)

        total = len(events)
        for idx, event in enumerate(events):
            result = self.run(event, project_dir=work_dir)
            results.append(result)

            if progress_callback:
                progress_callback(idx + 1, total)

        return results
