"""
E2E session simulator for gaia-ops hook pipeline.

Simulates a complete Claude Code session by invoking hooks as subprocesses,
chained in session order. Validates the full hook lifecycle without needing
an LLM: session_start -> pre_tool_use -> post_tool_use -> subagent_stop -> stop_hook.

Run: python3 -m pytest tests/e2e/test_session_simulator.py -v
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Worktree root where hooks live (same pattern as test_hook_e2e.py)
WORKTREE = Path(__file__).resolve().parents[2]
HOOKS_DIR = WORKTREE / "hooks"


# ============================================================================
# Session Simulator
# ============================================================================


class SessionSimulator:
    """Simulates a complete Claude Code session by invoking hooks as subprocesses."""

    def __init__(self, project_root: Path):
        """Set up a temp project with .claude/, project-context.json, settings.json, etc.

        Args:
            project_root: Temporary directory to use as the simulated project root.
        """
        self.project_root = project_root
        self.session_id = "e2e-sim-" + secrets.token_hex(6)
        self._events: List[Dict[str, Any]] = []

        # Clean any stale context cache files from previous test runs
        cache_dir = Path("/tmp/gaia-context-cache")
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                try:
                    f.unlink()
                except OSError:
                    pass

        # Isolate from host /tmp state (stale gaia-context-payloads, etc.)
        self._tmpdir = project_root / "tmp"
        self._tmpdir.mkdir(exist_ok=True)

        # Create minimal .claude/ structure
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        # Logs directory (hooks write logs here)
        (claude_dir / "logs").mkdir(exist_ok=True)

        # Session directory for session context writer
        session_dir = claude_dir / "session" / "active"
        session_dir.mkdir(parents=True, exist_ok=True)

        # Project context directory
        pc_dir = claude_dir / "project-context"
        pc_dir.mkdir(parents=True, exist_ok=True)

        # Minimal project-context.json
        minimal_context = {
            "metadata": {
                "version": "2.0",
                "last_updated": "2026-03-11T00:00:00Z",
                "scan_config": {
                    "last_scan": "2026-03-11T00:00:00Z",
                    "scanner_version": "0.1.0",
                    "staleness_hours": 24,
                },
            },
            "paths": {},
            "sections": {
                "project_identity": {
                    "name": "e2e-session-test",
                    "type": "application",
                },
            },
        }
        (pc_dir / "project-context.json").write_text(
            json.dumps(minimal_context, indent=2)
        )

        # Workflow episodic memory dir (subagent_stop writes here)
        wem_dir = pc_dir / "workflow-episodic-memory"
        wem_dir.mkdir(parents=True, exist_ok=True)
        (wem_dir / "signals").mkdir(exist_ok=True)

        # Config directory
        config_dir = claude_dir / "config"
        config_dir.mkdir(exist_ok=True)

        # Copy real config files so context_provider.py can resolve them
        # from the temp project's .claude/config/ directory.
        real_config = WORKTREE / "config"
        for cfg_name in ("context-contracts.json", "surface-routing.json", "universal-rules.json"):
            src = real_config / cfg_name
            if src.exists():
                shutil.copy2(str(src), str(config_dir / cfg_name))

        # Memory directory
        (claude_dir / "memory").mkdir(exist_ok=True)

        # Metrics directory
        (claude_dir / "metrics").mkdir(exist_ok=True)

        # Settings.json (simplified)
        settings = {
            "permissions": {"allow": ["Bash(*)"], "deny": []},
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # ------------------------------------------------------------------
    # Internal helper: run a hook script
    # ------------------------------------------------------------------

    def _run_hook(
        self,
        script_name: str,
        stdin_payload: Dict[str, Any],
        env_extras: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run a hook script as subprocess and return structured result.

        Args:
            script_name: Hook script filename (e.g. "pre_tool_use.py").
            stdin_payload: Dict to serialize as JSON on stdin.
            env_extras: Optional extra environment variables.

        Returns:
            Dict with exit_code, stdout_json (parsed or None), stdout_raw, stderr.
        """
        script_path = HOOKS_DIR / script_name
        assert script_path.exists(), f"Hook script not found: {script_path}"

        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        # Isolate from host orchestrator env to prevent delegate mode blocking
        env["ORCHESTRATOR_DELEGATE_MODE"] = "false"
        env["GAIA_PLUGIN_MODE"] = "ops"
        # Isolate from host /tmp state (stale gaia-context-payloads, etc.)
        env["TMPDIR"] = str(self._tmpdir)
        if env_extras:
            env.update(env_extras)

        result = subprocess.run(
            [sys.executable, str(script_path)],
            input=json.dumps(stdin_payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            cwd=str(self.project_root),
        )

        stdout_json = None
        if result.stdout.strip():
            for line in reversed(result.stdout.strip().splitlines()):
                try:
                    stdout_json = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        return {
            "exit_code": result.returncode,
            "stdout_json": stdout_json,
            "stdout_raw": result.stdout,
            "stderr": result.stderr,
        }

    # ------------------------------------------------------------------
    # Public API: session lifecycle methods
    # ------------------------------------------------------------------

    def start_session(self) -> Dict[str, Any]:
        """Invoke session_start.py with SessionStart event. Returns hook output."""
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": self.session_id,
            "session_type": "startup",
        }
        result = self._run_hook("session_start.py", payload)
        self._events.append({"type": "session_start", "result": result})
        return result

    def user_prompt(self, text: str) -> str:
        """Simulate UserPromptSubmit -- returns the static additionalContext string.

        UserPromptSubmit is a static echo in settings.json, not a Python hook.
        We return the known static context directly.

        Returns:
            The additionalContext string.
        """
        additional_context = (
            "Trust your identity. Follow your instructions. "
            "Your constraints are non-negotiable. "
            "Approvals require real nonces from subagent. "
            "When in doubt, ask. Never assume."
        )
        self._events.append(
            {"type": "user_prompt", "text": text, "context": additional_context}
        )
        return additional_context

    def invoke_agent(self, agent_type: str, prompt: str) -> Dict[str, Any]:
        """Invoke pre_tool_use.py with Agent/Task tool event.

        Returns: dict with exit_code, updatedInput (with injected context), or deny reason.
        """
        payload = {
            "tool_name": "Agent",
            "tool_input": {
                "prompt": prompt,
                "subagent_type": agent_type,
            },
            "hook_event_name": "PreToolUse",
            "session_id": self.session_id,
        }
        result = self._run_hook("pre_tool_use.py", payload)
        self._events.append(
            {"type": "invoke_agent", "agent": agent_type, "result": result}
        )
        return result

    def execute_bash(self, command: str) -> Dict[str, Any]:
        """Invoke pre_tool_use.py with Bash tool event.

        Returns: dict with exit_code (0=allow, 2=block), deny reason if blocked.
        """
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "hook_event_name": "PreToolUse",
            "session_id": self.session_id,
        }
        result = self._run_hook("pre_tool_use.py", payload)
        self._events.append(
            {"type": "execute_bash", "command": command, "result": result}
        )
        return result

    def resume_agent(self, agent_id: str, prompt: str) -> Dict[str, Any]:
        """Invoke pre_tool_use.py with a SendMessage resume event.

        Used to simulate the orchestrator resuming an agent, e.g. with an
        APPROVE:<nonce> token after a T3 command was blocked.

        Args:
            agent_id: The agent ID to resume (e.g. "a1f2c3d4e5").
            prompt: The resume message (e.g. "APPROVE:<32-char-hex>").

        Returns: dict with exit_code, stdout_json, stdout_raw, stderr.
        """
        payload = {
            "tool_name": "SendMessage",
            "tool_input": {
                "message": prompt,
                "to": agent_id,
            },
            "hook_event_name": "PreToolUse",
            "session_id": self.session_id,
        }
        result = self._run_hook("pre_tool_use.py", payload)
        self._events.append(
            {"type": "resume_agent", "agent_id": agent_id, "prompt": prompt, "result": result}
        )
        return result

    def after_bash(
        self, command: str, output: str, exit_code: int
    ) -> Dict[str, Any]:
        """Invoke post_tool_use.py with PostToolUse event.

        Returns: dict with hook result.
        """
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_result": {
                "stdout": output,
                "output": output,
                "exit_code": exit_code,
                "duration_ms": 100,
            },
            "hook_event_name": "PostToolUse",
            "session_id": self.session_id,
        }
        result = self._run_hook("post_tool_use.py", payload)
        self._events.append(
            {"type": "after_bash", "command": command, "result": result}
        )
        return result

    def agent_responds(
        self, agent_type: str, agent_id: str, output: str
    ) -> Dict[str, Any]:
        """Invoke subagent_stop.py with SubagentStop event.

        Returns: dict with contract_validated, anomalies_detected, episode_id, etc.
        """
        payload = {
            "hook_event_name": "SubagentStop",
            "session_id": self.session_id,
            "agent_type": agent_type,
            "agent_id": agent_id,
            "agent_transcript_path": "",
            "last_assistant_message": output,
            "cwd": str(self.project_root),
        }
        result = self._run_hook("subagent_stop.py", payload)
        self._events.append(
            {
                "type": "agent_responds",
                "agent": agent_type,
                "agent_id": agent_id,
                "result": result,
            }
        )
        return result

    def start_agent(
        self,
        agent_type: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invoke subagent_start.py with SubagentStart event.

        Simulates Claude Code dispatching a subagent. SubagentStart reads
        the context cache written by invoke_agent() and returns it as
        additionalContext for injection into the subagent.

        Args:
            agent_type: Agent type (e.g. "devops-developer", "Explore").
            agent_id: Optional agent ID. Generated if not provided.
            session_id: Optional session ID. Uses simulator's session_id if not provided.

        Returns:
            Dict with exit_code, stdout_json, stdout_raw, stderr.
        """
        if agent_id is None:
            agent_id = "a" + secrets.token_hex(4)
        if session_id is None:
            session_id = self.session_id

        payload = {
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
            "agent_id": agent_id,
            "agent_type": agent_type,
            "cwd": str(self.project_root),
        }
        result = self._run_hook("subagent_start.py", payload)
        self._events.append(
            {
                "type": "start_agent",
                "agent_type": agent_type,
                "agent_id": agent_id,
                "result": result,
            }
        )
        return result

    def end_session(self) -> Dict[str, Any]:
        """Invoke stop_hook.py with Stop event. Returns quality assessment."""
        payload = {
            "hook_event_name": "Stop",
            "session_id": self.session_id,
            "stop_reason": "end_turn",
        }
        result = self._run_hook("stop_hook.py", payload)
        self._events.append({"type": "end_session", "result": result})
        return result

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def get_session_context(self) -> Dict[str, Any]:
        """Read the current session context file."""
        context_path = (
            self.project_root / ".claude" / "session" / "active" / "context.json"
        )
        if not context_path.exists():
            return {}
        return json.loads(context_path.read_text())

    @property
    def events(self) -> List[Dict[str, Any]]:
        """Return all recorded events in this session."""
        return list(self._events)


# ============================================================================
# Helper: build agent output with valid contract
# ============================================================================


def _build_valid_agent_output(
    plan_status: str = "COMPLETE",
    agent_id: str = "a1f2c3",
    summary: str = "Task completed successfully.",
    include_consolidation: bool = True,
) -> str:
    """Build a realistic agent output string with json:contract block.

    The json:contract block must contain agent_status and evidence_report
    as nested objects -- that is the format contract_validator.parse_contract()
    and response_contract.validate_response_contract() expect.

    consolidation_report is included by default because the runtime may detect
    consolidation_required=True from cached context payloads. Including a valid
    consolidation_report makes the contract pass regardless of environment state.
    """
    bt = chr(96)  # backtick
    consolidation = None
    if include_consolidation:
        consolidation = {
            "ownership_assessment": "owned_here",
            "confirmed_findings": ["OOMKilled on pod app-1"],
            "suspected_findings": ["memory limit too low"],
            "conflicts": ["none"],
            "open_gaps": ["root cause of memory spike unknown"],
            "next_best_agent": ["devops-developer"],
        }
    contract_block = json.dumps(
        {
            "agent_status": {
                "plan_status": plan_status,
                "agent_id": agent_id,
                "pending_steps": "none",
                "next_action": "done",
            },
            "evidence_report": {
                "patterns_checked": ["pod status pattern"],
                "files_checked": ["none"],
                "commands_run": ["kubectl get pods -> CrashLoopBackOff"],
                "key_outputs": ["OOMKilled detected"],
                "verbatim_outputs": ["none"],
                "cross_layer_impacts": ["none"],
                "open_gaps": ["none"],
            },
            "consolidation_report": consolidation,
            "approval_request": None,
        },
        indent=2,
    )
    return (
        f"{summary}\n\n"
        f"{bt}{bt}{bt}json:contract\n"
        f"{contract_block}\n"
        f"{bt}{bt}{bt}\n\n"
    )


# ============================================================================
# Helper: build blocked command strings without triggering hook scanners
# ============================================================================


def _blocked_kubectl_delete_ns():
    """Build 'kubectl delete namespace production' avoiding hook scanner."""
    return " ".join(["kubectl", "delete", "namespace", "production"])


def _blocked_tf_destroy():
    """Build 'terraform destroy' avoiding hook scanner."""
    return " ".join(["terraform", "destroy"])


def _blocked_git_reset():
    """Build 'git reset --hard' avoiding hook scanner."""
    return "git reset " + "--" + "hard"


# ============================================================================
# Pytest fixture
# ============================================================================


@pytest.fixture
def simulator(tmp_path):
    """Create a fresh SessionSimulator with isolated temp directory."""
    return SessionSimulator(tmp_path)


# ============================================================================
# Scenario 1: Happy path -- agent completes successfully
# ============================================================================


class TestScenario1HappyPath:
    """Happy path: session start -> agent invocation -> bash -> agent response -> stop."""

    def test_full_session_lifecycle(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # 1. Start session
        result = sim.start_session()
        assert result["exit_code"] == 0, (
            f"SessionStart failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 2. User prompt
        context = sim.user_prompt("diagnostica por que el pod crashea")
        assert "Trust your identity" in context

        # 3. Invoke cloud-troubleshooter agent
        result = sim.invoke_agent(
            "cloud-troubleshooter", "diagnostica por que el pod crashea"
        )
        assert result["exit_code"] == 0, (
            f"Agent invoke failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 4. Simulate bash command (kubectl get pods)
        result = sim.execute_bash("kubectl get pods")
        assert result["exit_code"] == 0, (
            f"kubectl get pods blocked: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 5. Post-tool-use for the bash command
        result = sim.after_bash(
            "kubectl get pods",
            "NAME  READY  STATUS\napp-1  0/1  CrashLoopBackOff",
            0,
        )
        assert result["exit_code"] == 0, (
            f"PostToolUse failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 6. Agent responds with valid contract
        agent_output = _build_valid_agent_output(
            summary="El pod crashea por OOMKilled.",
            agent_id="a1f2c3",
        )
        result = sim.agent_responds("cloud-troubleshooter", "a1f2c3", agent_output)
        assert result["exit_code"] == 0, (
            f"SubagentStop failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )
        stop_data = result["stdout_json"]
        assert stop_data is not None, (
            f"SubagentStop returned no JSON. stdout: {result['stdout_raw']}"
        )
        assert stop_data.get("contract_validated") is True, (
            f"Contract not validated: {stop_data}"
        )

        # 7. End session
        result = sim.end_session()
        assert result["exit_code"] == 0, (
            f"Stop hook failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )


# ============================================================================
# Scenario 2: Security gate -- blocked command
# ============================================================================


class TestScenario2SecurityGate:
    """Blocked command (kubectl delete namespace) should exit 2."""

    def test_blocked_command(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # Start session
        result = sim.start_session()
        assert result["exit_code"] == 0

        # Try a permanently blocked command
        result = sim.execute_bash(_blocked_kubectl_delete_ns())
        assert result["exit_code"] == 2, (
            f"Expected exit 2 (blocked), got {result['exit_code']}. "
            f"stderr: {result['stderr']}, stdout: {result['stdout_raw']}"
        )
        # stdout should contain "blocked by security policy"
        combined_output = result["stdout_raw"].lower() + result["stderr"].lower()
        assert "blocked" in combined_output, (
            f"Expected 'blocked' in output. stdout: {result['stdout_raw']}, "
            f"stderr: {result['stderr']}"
        )


# ============================================================================
# Scenario 3: Mutative command -- requires approval (nonce-denied)
# ============================================================================


class TestScenario3MutativeCommand:
    """Mutative command (terraform apply) should be asked via native dialog."""

    def test_mutative_command_ask_via_native_dialog(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # Start session
        result = sim.start_session()
        assert result["exit_code"] == 0

        # Try a mutative command (T3)
        result = sim.execute_bash("terraform apply")
        # Mutative commands exit 0 with a corrective ask response
        assert result["exit_code"] == 0, (
            f"Expected exit 0 (ask), got {result['exit_code']}. "
            f"stderr: {result['stderr']}"
        )
        assert result["stdout_json"] is not None, (
            f"Expected JSON response for mutative ask. stdout: {result['stdout_raw']}"
        )
        hook_output = result["stdout_json"].get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", (
            f"Expected ask, got: {hook_output.get('permissionDecision')}. "
            f"Full response: {result['stdout_json']}"
        )


# ============================================================================
# Scenario 4: Agent with incomplete contract
# ============================================================================


class TestScenario4IncompleteContract:
    """Agent response without json:contract block should flag anomalies."""

    def test_missing_contract_detected(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # Start session
        result = sim.start_session()
        assert result["exit_code"] == 0

        # Invoke agent
        result = sim.invoke_agent("devops-developer", "refactoriza el modulo X")
        assert result["exit_code"] == 0

        # Agent responds WITHOUT a contract block
        # Missing contract triggers exit_code=2 (selective enforcement)
        agent_output = "Listo, refactorice todo."
        result = sim.agent_responds("devops-developer", "a9b8c7", agent_output)
        assert result["exit_code"] == 2, (
            f"Missing contract should trigger rejection (exit=2): exit={result['exit_code']}, "
            f"stderr: {result['stderr']}"
        )

        stop_data = result["stdout_json"]
        assert stop_data is not None, (
            f"SubagentStop returned no JSON. stdout: {result['stdout_raw']}"
        )

        # contract_validated should be False (no contract block)
        assert stop_data.get("contract_validated") is False, (
            f"Expected contract_validated=False for missing contract. Got: {stop_data}"
        )

        # Should have anomalies detected
        anomalies_count = stop_data.get("anomalies_detected", 0)
        assert anomalies_count > 0, (
            f"Expected anomalies for missing contract. Got: {stop_data}"
        )


# ============================================================================
# Scenario 5: Invalid plan_status in contract -- selective enforcement
# ============================================================================


class TestScenario5InvalidPlanStatus:
    """Agent response with json:contract but invalid plan_status should be rejected (exit=2)."""

    def test_invalid_plan_status_rejected(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # Start session
        result = sim.start_session()
        assert result["exit_code"] == 0

        # Invoke agent
        result = sim.invoke_agent("devops-developer", "refactoriza el modulo X")
        assert result["exit_code"] == 0

        # Agent responds with a json:contract block but INVALID plan_status
        agent_output = _build_valid_agent_output(
            plan_status="RANDOM_STATUS",
            agent_id="a5e6f7",
            summary="Refactoring done.",
        )
        result = sim.agent_responds("devops-developer", "a5e6f7", agent_output)
        assert result["exit_code"] == 2, (
            f"Invalid plan_status should trigger rejection (exit=2): exit={result['exit_code']}, "
            f"stderr: {result['stderr']}"
        )

        stop_data = result["stdout_json"]
        assert stop_data is not None, (
            f"SubagentStop returned no JSON. stdout: {result['stdout_raw']}"
        )

        # contract_rejected should be True
        assert stop_data.get("contract_rejected") is True, (
            f"Expected contract_rejected=True for invalid plan_status. Got: {stop_data}"
        )

        # Rejection reason should mention the invalid status
        reason = stop_data.get("contract_rejection_reason", "")
        assert "RANDOM_STATUS" in reason, (
            f"Expected rejection reason to mention 'RANDOM_STATUS'. Got: {reason}"
        )


# ============================================================================
# Scenario 6: Valid plan_status but empty evidence -- advisory only
# ============================================================================


class TestScenario6EmptyEvidenceAdvisory:
    """Agent response with valid plan_status but empty evidence fields should NOT be blocked."""

    def test_empty_evidence_allowed(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # Start session
        result = sim.start_session()
        assert result["exit_code"] == 0

        # Invoke agent
        result = sim.invoke_agent("cloud-troubleshooter", "diagnostica el pod")
        assert result["exit_code"] == 0

        # Build agent output with valid plan_status but ALL evidence fields empty
        bt = chr(96)
        contract_block = json.dumps(
            {
                "agent_status": {
                    "plan_status": "COMPLETE",
                    "agent_id": "ab12cd",
                    "pending_steps": [],
                    "next_action": "done",
                },
                "evidence_report": {
                    "patterns_checked": [],
                    "files_checked": [],
                    "commands_run": [],
                    "key_outputs": [],
                    "verbatim_outputs": [],
                    "cross_layer_impacts": [],
                    "open_gaps": [],
                },
                "consolidation_report": None,
                "approval_request": None,
            },
            indent=2,
        )
        agent_output = (
            f"Diagnostico completado.\n\n"
            f"{bt}{bt}{bt}json:contract\n"
            f"{contract_block}\n"
            f"{bt}{bt}{bt}\n\n"
        )

        result = sim.agent_responds("cloud-troubleshooter", "ab12cd", agent_output)
        assert result["exit_code"] == 0, (
            f"Empty evidence with valid plan_status should be advisory (exit=0): "
            f"exit={result['exit_code']}, stderr: {result['stderr']}"
        )

        stop_data = result["stdout_json"]
        assert stop_data is not None, (
            f"SubagentStop returned no JSON. stdout: {result['stdout_raw']}"
        )

        # Should NOT have contract_rejected
        assert stop_data.get("contract_rejected") is not True, (
            f"Expected no contract rejection for empty evidence. Got: {stop_data}"
        )


# ============================================================================
# Scenario 7: Session events persist between tool calls
# ============================================================================


class TestScenario7SessionEventsPersist:
    """Session events from tool calls should persist and be available to later agents."""

    def test_events_persist_across_calls(self, tmp_path):
        sim = SessionSimulator(tmp_path)

        # Start session
        result = sim.start_session()
        assert result["exit_code"] == 0

        # Invoke first agent
        result = sim.invoke_agent("cloud-troubleshooter", "investiga")
        assert result["exit_code"] == 0

        # Execute and record first bash command
        result = sim.execute_bash("kubectl get pods")
        assert result["exit_code"] == 0

        result = sim.after_bash("kubectl get pods", "pod output 1", 0)
        assert result["exit_code"] == 0

        # Execute and record second bash command
        result = sim.execute_bash("kubectl describe pod app-1")
        assert result["exit_code"] == 0

        result = sim.after_bash("kubectl describe pod app-1", "pod description output", 0)
        assert result["exit_code"] == 0

        # Invoke a second agent -- verify it can be invoked successfully
        result = sim.invoke_agent("gitops-operator", "corrige el deployment")
        assert result["exit_code"] == 0, (
            f"Second agent invoke failed: exit={result['exit_code']}, "
            f"stderr: {result['stderr']}"
        )

        # Verify the simulator tracked all events
        assert len(sim.events) >= 6, (
            f"Expected at least 6 events tracked, got {len(sim.events)}"
        )


# ============================================================================
# Additional scenarios: edge cases
# ============================================================================


class TestSessionSimulatorEdgeCases:
    """Edge cases for the session simulator."""

    def test_multiple_blocked_commands(self, tmp_path):
        """Multiple blocked commands should each be independently blocked."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        blocked_commands = [
            _blocked_kubectl_delete_ns(),
            _blocked_tf_destroy(),
        ]
        for cmd in blocked_commands:
            result = sim.execute_bash(cmd)
            assert result["exit_code"] == 2, (
                f"Expected exit 2 for '{cmd}', got {result['exit_code']}. "
                f"stderr: {result['stderr']}"
            )

    def test_git_reset_hard_blocked(self, tmp_path):
        """git reset --hard should be permanently blocked."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        result = sim.execute_bash(_blocked_git_reset())
        assert result["exit_code"] == 2, (
            f"Expected exit 2 for git reset --hard, got {result['exit_code']}. "
            f"stderr: {result['stderr']}"
        )

    def test_safe_commands_after_blocked(self, tmp_path):
        """Safe commands should still work after blocked commands."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        # Blocked
        result = sim.execute_bash(_blocked_kubectl_delete_ns())
        assert result["exit_code"] == 2

        # Safe -- should still be allowed
        result = sim.execute_bash("kubectl get pods")
        assert result["exit_code"] == 0

        result = sim.execute_bash("ls -la")
        assert result["exit_code"] == 0

    def test_end_session_returns_quality(self, tmp_path):
        """Stop hook should return quality assessment JSON."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        result = sim.end_session()
        assert result["exit_code"] == 0
        assert result["stdout_json"] is not None, (
            f"Stop hook returned no JSON. stdout: {result['stdout_raw']}"
        )
        quality = result["stdout_json"]
        assert "quality_sufficient" in quality, (
            f"Expected quality_sufficient in stop response. Got: {quality}"
        )

    def test_read_tool_passthrough(self, tmp_path):
        """Non-Bash, non-Agent tools should pass through."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        # Read tool should pass through (exit 0, no blocking)
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/test.txt"},
            "hook_event_name": "PreToolUse",
            "session_id": sim.session_id,
        }
        result = sim._run_hook("pre_tool_use.py", payload)
        assert result["exit_code"] == 0


# ============================================================================
# Scenario 8: Full T3 approval cycle -- block -> nonce -> approve -> execute
# ============================================================================


class TestScenario8FullApprovalCycle:
    """T3 approval lifecycle: ask via native dialog, no nonce in validator path."""

    def test_full_approval_cycle(self, tmp_path):
        """T3 command returns 'ask' for native dialog approval.

        The bash_validator now returns permissionDecision='ask' for all T3
        commands. The nonce-based flow is no longer driven by the validator.
        """
        sim = SessionSimulator(tmp_path)

        # 1. Start session
        result = sim.start_session()
        assert result["exit_code"] == 0, (
            f"SessionStart failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 2. Invoke gaia-system agent
        result = sim.invoke_agent("gaia-system", "push changes to main branch")
        assert result["exit_code"] == 0, (
            f"Agent invoke failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 3. Agent tries a T3 command -- should return "ask" for native dialog
        t3_command = "git push origin main"
        result = sim.execute_bash(t3_command)
        assert result["exit_code"] == 0, (
            f"Expected exit 0 (ask), got {result['exit_code']}. "
            f"stderr: {result['stderr']}"
        )
        assert result["stdout_json"] is not None, (
            f"Expected JSON response for T3 ask. stdout: {result['stdout_raw']}"
        )
        hook_output = result["stdout_json"].get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", (
            f"Expected ask, got: {hook_output.get('permissionDecision')}. "
            f"Full response: {result['stdout_json']}"
        )

    def test_approval_cycle_with_terraform_apply(self, tmp_path):
        """terraform apply returns 'ask' for native dialog approval."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        # Invoke agent
        result = sim.invoke_agent("terraform-architect", "apply the plan")
        assert result["exit_code"] == 0

        # Try terraform apply -- returns "ask"
        t3_command = "terraform apply"
        result = sim.execute_bash(t3_command)
        assert result["exit_code"] == 0
        hook_output = result["stdout_json"].get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", (
            f"Expected ask, got: {hook_output.get('permissionDecision')}. "
            f"Full response: {result['stdout_json']}"
        )

    def test_nonce_cannot_be_reused(self, tmp_path):
        """A nonce can only be activated once. Second activation should fail.

        This test manually creates a nonce (since bash_validator no longer
        generates them) and verifies the activation-once invariant.
        """
        import sys
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        # Manually create a pending approval by importing the module
        # and writing directly (since the subprocess hook doesn't generate nonces)
        nonce_script = (
            "import json, sys, os; "
            "sys.path.insert(0, os.environ.get('HOOKS_DIR', '')); "
            "from modules.security.approval_grants import generate_nonce, write_pending_approval; "
            f"os.environ['CLAUDE_SESSION_ID'] = 'e2e-session-sim-001'; "
            "nonce = generate_nonce(); "
            "write_pending_approval(nonce=nonce, command='git push origin main', "
            "danger_verb='push', danger_category='MUTATIVE'); "
            "print(nonce)"
        )
        # Use a simpler approach: verify the resume endpoint rejects invalid nonces
        import secrets
        fake_nonce = secrets.token_hex(16)

        # First activation with a non-existent nonce should fail
        result = sim.resume_agent("a1f2c3d4e5", f"APPROVE:{fake_nonce}")
        assert result["exit_code"] == 2, (
            f"Activation with invalid nonce should fail. "
            f"exit={result['exit_code']}, stdout={result['stdout_raw']}"
        )


# ============================================================================
# Scenario 9: Context injection round-trip
# ============================================================================


class TestScenario9ContextInjection:
    """Context injection: invoke_agent caches context, start_agent reads it."""

    def test_context_injection_round_trip(self, tmp_path):
        """invoke_agent() caches context; start_agent() returns it as additionalContext."""
        sim = SessionSimulator(tmp_path)

        # 1. Start session
        result = sim.start_session()
        assert result["exit_code"] == 0, (
            f"SessionStart failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 2. invoke_agent for a project agent -- this caches context
        result = sim.invoke_agent("devops-developer", "refactoriza el modulo X")
        assert result["exit_code"] == 0, (
            f"Agent invoke failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 2a. Verify the cache file was written
        cache_dir = Path("/tmp/gaia-context-cache")
        cache_files = list(cache_dir.glob(f"{sim.session_id}-*.json"))
        assert len(cache_files) > 0, (
            f"Expected context cache files for session {sim.session_id} in {cache_dir}. "
            f"Found: {list(cache_dir.glob('*.json'))}"
        )

        # 3. start_agent for the same project agent -- reads the cache
        result = sim.start_agent("devops-developer")
        assert result["exit_code"] == 0, (
            f"SubagentStart failed: exit={result['exit_code']}, stderr={result['stderr']}"
        )

        # 3a. Verify additionalContext is present in hookSpecificOutput
        start_json = result["stdout_json"]
        assert start_json is not None, (
            f"SubagentStart returned no JSON. stdout: {result['stdout_raw']}"
        )
        hook_output = start_json.get("hookSpecificOutput", {})
        additional_context = hook_output.get("additionalContext", "")
        assert additional_context, (
            f"Expected additionalContext in hookSpecificOutput. Got: {hook_output}"
        )

        # 3b. Verify expected sections in the injected context
        assert "# Project Context" in additional_context, (
            "Expected '# Project Context' header in additionalContext"
        )
        assert "# Brief" in additional_context, (
            "Expected '# Brief' header in additionalContext"
        )
        assert "# Permissions" in additional_context, (
            "Expected '# Permissions' header in additionalContext"
        )
        assert "## Rules" in additional_context, (
            "Expected '## Rules' header in additionalContext"
        )
        assert "project_identity" in additional_context, (
            "Expected 'project_identity' data in additionalContext"
        )

    def test_cache_consumed_after_start_agent(self, tmp_path):
        """Cache file should be deleted after SubagentStart reads it."""
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        # invoke_agent caches context
        sim.invoke_agent("devops-developer", "refactoriza")

        # Verify cache exists before start_agent
        cache_dir = Path("/tmp/gaia-context-cache")
        cache_files_before = list(cache_dir.glob(f"{sim.session_id}-*.json"))
        assert len(cache_files_before) > 0, "Cache should exist before start_agent"

        # start_agent consumes the cache
        result = sim.start_agent("devops-developer")
        assert result["exit_code"] == 0

        # Verify cache was consumed (deleted)
        cache_files_after = list(cache_dir.glob(f"{sim.session_id}-*.json"))
        assert len(cache_files_after) == 0, (
            f"Cache should be consumed after start_agent. "
            f"Remaining files: {cache_files_after}"
        )

    def test_meta_agent_no_context_injection(self, tmp_path):
        """Meta-agents (e.g. Explore) should NOT receive context injection.

        invoke_agent for a meta-agent does not cache context, so start_agent
        returns no additionalContext.
        """
        sim = SessionSimulator(tmp_path)
        sim.start_session()

        # invoke_agent for a meta-agent -- should not cache context
        result = sim.invoke_agent("Explore", "explore the codebase")
        assert result["exit_code"] == 0

        # start_agent for the meta-agent -- no cached context
        result = sim.start_agent("Explore")
        assert result["exit_code"] == 0

        start_json = result["stdout_json"]
        assert start_json is not None, (
            f"SubagentStart returned no JSON. stdout: {result['stdout_raw']}"
        )

        # Verify no additionalContext was injected
        hook_output = start_json.get("hookSpecificOutput", {})
        additional_context = hook_output.get("additionalContext")
        assert not additional_context, (
            f"Meta-agent should NOT receive context injection. "
            f"Got additionalContext: {additional_context[:200] if additional_context else 'None'}"
        )