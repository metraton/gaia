"""Runner for the eval framework (T2).

Dispatches a case to the production machinery that answers it and returns
a uniform :class:`DispatchResult` the graders consume.

Three backends implement :class:`DispatchBackend`:

- :class:`RoutingSimBackend` runs the real ``RoutingSimulator`` in-process
  over the DB-backed ``surface_routing`` table.
- :class:`HookLogReplayBackend` replays a command through the real
  ``pre_tool_use.py`` entry point as a subprocess.
- :class:`FakeBackend` replays canned session JSONL from
  ``tests/evals/fixtures/sessions/`` without any subprocess or network
  I/O. Used by this layer's own unit tests -- never to answer a catalog
  case, which would grade the fixture instead of the system.

There is deliberately NO single-agent dispatch backend. A Gaia session is
rooted in the orchestrator (``gaia install`` writes
``agent: gaia-orchestrator`` into the workspace settings), so a
``claude --print --agent <specialist>`` dispatch measures a mode Gaia does
not route through: it skips the orchestrator, and with it the per-agent
context injection and the dispatch identity the hooks key on. The backend
that shelled out that way was removed with the two cases that used it.

The module MUST NOT import from ``hooks/`` or parse ``project-context``
data directly -- it only reads and writes files the agent already
produces.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol


# ---------------------------------------------------------------------------
# Errors and data classes
# ---------------------------------------------------------------------------


class EvalError(RuntimeError):
    """Raised when a dispatch cannot be completed.

    Covers timeouts, missing binaries, unknown agents, missing fixtures,
    and any other terminal failure of the runner. Graders and the
    catalog loader do NOT raise this -- it is specific to backend
    execution.
    """


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of dispatching a task to an agent.

    Attributes:
        stdout: Captured textual response from the agent. For routing-sim
            backends, this is the JSON-serialized ``RoutingResult``.
        session_path: Path to the session transcript JSONL, or ``None``
            when the backend does not produce transcripts (e.g. routing
            simulator).
        audit_paths: List of ``audit-YYYY-MM-DD.jsonl`` files (or slices)
            that belong to this dispatch window. Empty list when no audit
            events were captured.
        exit_code: Process exit code (0 on success). For non-subprocess
            backends this reflects internal success/failure mapping.
    """

    stdout: str
    session_path: Optional[Path]
    audit_paths: list[Path] = field(default_factory=list)
    exit_code: int = 0


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class DispatchBackend(Protocol):
    """Protocol every dispatch backend must satisfy.

    Implementations are responsible for:

    - Running the target agent against ``task`` (however they choose --
      subprocess, in-process, fixture replay).
    - Producing a :class:`DispatchResult` with ``stdout`` populated and,
      when applicable, ``session_path`` and ``audit_paths`` pointing at
      JSONL files that downstream graders can read.
    - Raising :class:`EvalError` on timeout, missing binary, unknown
      agent, or any other terminal failure.
    """

    def dispatch(
        self,
        agent_type: str,
        task: str,
        timeout: int = 60,
    ) -> DispatchResult:  # pragma: no cover - protocol definition
        ...


# ---------------------------------------------------------------------------
# Fake backend (fixture replay)
# ---------------------------------------------------------------------------


@dataclass
class FakeBackend:
    """Replay a canned session JSONL fixture without running ``claude``.

    Attributes:
        fixture_path: Path to a ``*.jsonl`` file under
            ``tests/evals/fixtures/sessions/``. The file is returned as
            ``DispatchResult.session_path`` directly; the runner does
            NOT copy it into a tmp dir.
        stdout: Canned stdout string to return. Tests typically build
            this from the fixture content so graders have both signals.
        audit_paths: Optional pre-built list of audit JSONL files to
            attach to the result. Defaults to empty.
        exit_code: Canned exit code. Defaults to 0.
        simulate_timeout: When true, ``dispatch`` raises ``EvalError``
            with a timeout message. Used in unit tests.
        simulate_bad_agent: When a non-empty string, ``dispatch`` raises
            ``EvalError`` if ``agent_type`` equals this value. Used in
            unit tests for the "bad agent" path.
    """

    fixture_path: Path
    stdout: str = ""
    audit_paths: list[Path] = field(default_factory=list)
    exit_code: int = 0
    simulate_timeout: bool = False
    simulate_bad_agent: Optional[str] = None

    def dispatch(
        self,
        agent_type: str,
        task: str,
        timeout: int = 60,
    ) -> DispatchResult:
        if self.simulate_timeout:
            raise EvalError(
                f"fake dispatch timed out after {timeout}s (agent={agent_type})"
            )

        if self.simulate_bad_agent and agent_type == self.simulate_bad_agent:
            raise EvalError(f"claude rejected agent {agent_type!r}: unknown agent")

        if not self.fixture_path.exists():
            raise EvalError(f"fake backend fixture missing: {self.fixture_path}")

        return DispatchResult(
            stdout=self.stdout,
            session_path=self.fixture_path,
            audit_paths=list(self.audit_paths),
            exit_code=self.exit_code,
        )


# ---------------------------------------------------------------------------
# Routing simulator backend (T3d)
# ---------------------------------------------------------------------------


def _default_repo_root() -> Path:
    """Return the gaia repo root.

    The runner lives at ``<repo>/tests/evals/runner.py`` so we walk two
    parents up. Callers can override via ``RoutingSimBackend.repo_root``.
    """

    return Path(__file__).resolve().parent.parent.parent


@dataclass
class RoutingSimBackend:
    """Dispatch-compatible backend that wraps ``tools/gaia_simulator/routing_simulator``.

    Purpose (T3d / gap G4): S4 (``routing_deflect``) only needs to know
    which agent the orchestrator would pick; running a real agent for
    that question costs ~4-8k tokens per invocation. This backend calls
    :class:`~tools.gaia_simulator.routing_simulator.RoutingSimulator`
    synchronously and serialises the returned
    :class:`~tools.gaia_simulator.routing_simulator.RoutingResult` as
    JSON on ``DispatchResult.stdout`` so downstream graders (e.g.
    :func:`graders.routing_grader`) can consume it exactly like the
    other backends.

    Attributes:
        repo_root: Path to the gaia repo root. ``config/`` and
            ``agents/`` are resolved relative to this. Defaults to the
            repo inferred from this file's location so tests can run
            without any setup.
        config_dir: Overrides the ``<repo_root>/config`` default.
        agents_dir: Overrides the ``<repo_root>/agents`` default.
        simulator: Optional pre-constructed simulator. Tests inject a
            stub here; normal callers leave it ``None`` and let the
            backend build one lazily on first dispatch.
    """

    repo_root: Path = field(default_factory=_default_repo_root)
    config_dir: Optional[Path] = None
    agents_dir: Optional[Path] = None
    simulator: Optional[Any] = None

    def _get_simulator(self) -> Any:
        if self.simulator is not None:
            return self.simulator

        # Lazy import: keep the runner importable in environments that
        # cannot construct the simulator (missing DB-backed routing
        # during scaffold-only smoke tests, for instance).
        tools_dir = self.repo_root / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        try:
            from gaia_simulator.routing_simulator import RoutingSimulator
        except ImportError as exc:  # pragma: no cover - defensive
            raise EvalError(
                f"routing simulator unavailable: {exc}"
            ) from exc

        config_dir = self.config_dir or self.repo_root / "config"
        agents_dir = self.agents_dir or self.repo_root / "agents"
        if not config_dir.is_dir():
            raise EvalError(f"config dir not found: {config_dir}")
        if not agents_dir.is_dir():
            raise EvalError(f"agents dir not found: {agents_dir}")

        self.simulator = RoutingSimulator(config_dir, agents_dir)
        return self.simulator

    def dispatch(
        self,
        agent_type: str,
        task: str,
        timeout: int = 60,
    ) -> DispatchResult:
        """Return a :class:`DispatchResult` whose stdout is the routing JSON.

        ``agent_type`` is accepted for protocol symmetry with the other
        backends but is not passed through to the simulator -- routing
        cases explicitly want to know which agent the orchestrator
        *would* select from the prompt alone, not which agent the
        catalog nominated. ``timeout`` is ignored (the simulator runs
        synchronously in-process).
        """

        _ = timeout  # synchronous, nothing to interrupt
        if not agent_type or not isinstance(agent_type, str):
            raise EvalError(f"invalid agent_type: {agent_type!r}")

        sim = self._get_simulator()
        try:
            result = sim.simulate(task)
        except Exception as exc:  # pragma: no cover - defensive
            raise EvalError(f"routing simulator failed: {exc}") from exc

        # ``RoutingResult`` is a dataclass -- ``asdict`` yields a plain
        # JSON-serialisable mapping. We keep every field so graders can
        # assert on surfaces, confidence, adjacent agents, etc., not
        # just the primary agent.
        try:
            payload = asdict(result)
        except TypeError:
            # Non-dataclass shim (e.g. test double that returns a dict
            # directly). Accept it verbatim.
            payload = result if isinstance(result, dict) else {
                "primary_agent": getattr(result, "primary_agent", ""),
                "adjacent_agents": list(getattr(result, "adjacent_agents", []) or []),
                "surfaces_active": list(getattr(result, "surfaces_active", []) or []),
                "confidence": getattr(result, "confidence", 0.0),
                "multi_surface": getattr(result, "multi_surface", False),
            }

        return DispatchResult(
            stdout=json.dumps(payload, sort_keys=True, default=str),
            session_path=None,
            audit_paths=[],
            exit_code=0,
        )


# ---------------------------------------------------------------------------
# Hook-log replay backend (brief #89 AC-2)
# ---------------------------------------------------------------------------


# The security oracle vocabulary is the Claude Code PreToolUse
# permissionDecision space: {allow, ask, deny}. Mapping the hook's observed
# outcome onto it requires distinguishing two refusals that look identical
# at the exit-code level but are categorically different policy decisions:
#
#   * CONSENT-REQUIRED (oracle ``ask``) -- a T3 mutation. The security core
#     expresses this two ways depending on plugin mode: security-only mode
#     emits a native ``permissionDecision: ask`` (reason ``[T3] ...``); ops
#     mode emits an *approvable* hard block carrying an ``approval_id``
#     (reason ``[T3_BLOCKED] ... requires user approval ... Report
#     APPROVAL_REQUEST``). Both are the same decision -- "the user must
#     consent before this proceeds" -- so both map to ``ask``. The oracle is
#     therefore plugin-mode-independent, which is the point: the curated
#     decision asserts policy, not a transport detail.
#
#   * PERMANENT BLOCK (oracle ``deny``) -- a ``blocked_commands.py`` match
#     (reason ``[BLOCKED] ...``, exit 2, never approvable). This is the only
#     outcome that maps to ``deny``.
#
# Discrimination is by the reason prefix the hook stamps, not by exit code,
# because the ops-mode T3 block and a permanent block share exit code 2.
_T3_CONSENT_MARKERS = ("[T3]", "[T3_BLOCKED]")
_PERMANENT_BLOCK_MARKER = "[BLOCKED]"

# Fallback when no reason marker is present: the HookRunner's exit-code
# vocabulary ({ALLOW, BLOCK, DENY}). Only reached if the hook emitted no
# recognizable reason text.
_REPLAY_DECISION_TO_ORACLE = {
    "ALLOW": "allow",
    "BLOCK": "deny",
    "DENY": "deny",
}


@contextlib.contextmanager
def _isolated_gaia_data_dir() -> Iterator[None]:
    """Point ``GAIA_DATA_DIR`` at a throwaway dir for the duration.

    The PreToolUse hook reads/writes the approval store in ``gaia.db``
    (resolved by ``gaia.paths.resolver`` from ``GAIA_DATA_DIR`` on each
    call). Without isolation, the first replayed T3 command writes a
    pending approval grant that a later replay sees as prior state and
    escalates from ``ask`` to a hard ``deny`` -- the 18 cases would
    contaminate one another. Each dispatch runs against a fresh, empty
    data dir so every replay observes the *cold-start* security decision,
    which is exactly what the curated oracle asserts (``_connect()``
    materializes the schema lazily on first use, so an empty dir
    suffices).

    The child hook process inherits ``os.environ``, so setting it here
    propagates into the ``HookRunner`` subprocess; the prior value is
    restored on exit.

    The oracle decision is context-independent -- a T3 maps to ``ask`` whether
    it surfaces as the native consent prompt (main session) or the approvable
    nonce block (subagent under the orchestrator).
    """
    overrides = {
        "GAIA_DATA_DIR": tempfile.mkdtemp(prefix="eval_gaia_data_"),
    }
    prev = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, original in prev.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        shutil.rmtree(overrides["GAIA_DATA_DIR"], ignore_errors=True)


def _last_json_line(stdout: str) -> Optional[dict]:
    """Return the last JSON object emitted on ``stdout``, or ``None``.

    The PreToolUse hook may print human-readable lines before its JSON
    payload; the operative ``hookSpecificOutput`` is always the last
    ``{...}`` line. Mirrors ``gaia_simulator.runner._parse_last_json_line``
    without importing a private symbol across the tool boundary.
    """
    for line in reversed((stdout or "").strip().splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class HookLogReplayBackend:
    """Replay a command through the real ``pre_tool_use.py`` entry point.

    Purpose (brief #89 AC-2): the golden security catalog
    (``catalogs/security_decisions.yaml``) pairs each ``(tool, command)``
    with a human-curated ``expected_decision``. This backend feeds the
    command to the *real* PreToolUse hook -- as a subprocess, never an
    in-process import, so the runner's "MUST NOT import from ``hooks/``"
    contract holds -- and reports the observed ``permissionDecision`` so a
    grader can compare it to the curated oracle.

    The subprocess plumbing (isolated work dir with a minimal ``.claude/``
    tree, ``CLAUDE_PLUGIN_ROOT`` scrubbing, stdin payload, exit-code
    mapping) is delegated to
    :class:`tools.gaia_simulator.runner.HookRunner` so this backend stays
    consistent with the existing replay path instead of duplicating it.
    On top of that, the backend re-reads the hook's own stdout JSON to
    recover the literal ``permissionDecision`` -- the HookRunner only
    distinguishes ALLOW/BLOCK/DENY, but the oracle needs ``ask`` (T3
    consent, exit 0) kept distinct from ``allow``.

    ``DispatchResult.stdout`` is a JSON object
    ``{"decision": "<allow|ask|deny>", "reason": "...",
       "exit_code": N, "raw_decision": "<ALLOW|BLOCK|DENY|ERROR>"}``
    so a decision grader can parse it the same way ``RoutingSimBackend``
    emits routing JSON.

    Attributes:
        repo_root: gaia repo root. ``hooks/`` and ``tools/`` resolve
            relative to this. Defaults to the repo inferred from this
            file's location.
        hooks_dir: Overrides the ``<repo_root>/hooks`` default.
        runner: Optional pre-constructed ``HookRunner``. Tests inject a
            stub here; normal callers leave it ``None`` and let the
            backend build one lazily on first dispatch.
    """

    repo_root: Path = field(default_factory=_default_repo_root)
    hooks_dir: Optional[Path] = None
    runner: Optional[Any] = None

    def _get_runner(self) -> Any:
        if self.runner is not None:
            return self.runner

        # Lazy import: keep the runner module importable in environments
        # that never exercise hook replay. We import the gaia_simulator
        # *tool* (a sibling subprocess driver), not ``hooks/`` itself --
        # the hook is only ever executed as a child process.
        tools_dir = self.repo_root / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        try:
            from gaia_simulator.runner import HookRunner
        except ImportError as exc:  # pragma: no cover - defensive
            raise EvalError(f"hook replay runner unavailable: {exc}") from exc

        hooks_dir = self.hooks_dir or self.repo_root / "hooks"
        if not hooks_dir.is_dir():
            raise EvalError(f"hooks dir not found: {hooks_dir}")

        self.runner = HookRunner(hooks_dir=hooks_dir)
        return self.runner

    @staticmethod
    def _oracle_decision(replay_result: Any) -> tuple[str, str]:
        """Map a HookRunner ReplayResult to the (oracle_decision, reason).

        Decision precedence (most specific signal first):

        1. The reason marker the security core stamps. ``[BLOCKED]`` is a
           permanent, never-approvable refusal -> ``deny``. ``[T3]`` /
           ``[T3_BLOCKED]`` are consent-required mutations (the ops-mode
           hard block is still approvable via an ``approval_id``) -> ``ask``.
           This is what makes the oracle plugin-mode-independent.
        2. A literal native ``permissionDecision`` of ``allow``/``ask``/
           ``deny`` on the hook's stdout JSON. ``ask`` here is the
           security-only-mode native consent prompt.
        3. The HookRunner's exit-code-derived ALLOW/BLOCK/DENY fallback,
           used only when neither a marker nor a literal decision is present.

        The reason text is collected from both the structured stdout JSON
        and stderr -- the ops-mode hard block prints its ``[T3_BLOCKED]``
        message on stderr, while the native ask carries it in
        ``permissionDecisionReason``.
        """
        reason = ""
        literal: Optional[str] = None
        payload = _last_json_line(replay_result.actual_stdout)
        if isinstance(payload, dict):
            hook_output = payload.get("hookSpecificOutput")
            if isinstance(hook_output, dict):
                literal = hook_output.get("permissionDecision")
                reason = str(hook_output.get("permissionDecisionReason", "") or "")

        # Combine every text channel the hook may have used to stamp its
        # reason marker so the classification does not depend on transport.
        marker_text = " ".join(
            t for t in (reason, replay_result.actual_stdout, replay_result.actual_stderr)
            if t
        )

        # 1. Reason-marker classification (mode-independent, most specific).
        if _PERMANENT_BLOCK_MARKER in marker_text:
            return "deny", reason or _PERMANENT_BLOCK_MARKER
        if any(marker in marker_text for marker in _T3_CONSENT_MARKERS):
            return "ask", reason or _T3_CONSENT_MARKERS[0]

        # 2. Native permissionDecision literal.
        if literal in ("allow", "ask", "deny"):
            return literal, reason

        # 3. Exit-code fallback.
        raw = replay_result.actual_decision
        mapped = _REPLAY_DECISION_TO_ORACLE.get(raw)
        if mapped is not None:
            return mapped, reason or raw
        # ERROR or anything unexpected -- surface verbatim so the grader
        # records a clear mismatch rather than silently coercing to allow.
        return raw.lower(), reason or (replay_result.actual_stderr or raw)

    def dispatch(
        self,
        agent_type: str,
        task: str,
        timeout: int = 60,
    ) -> DispatchResult:
        """Replay ``task`` (a Bash command) through the PreToolUse hook.

        ``agent_type`` is accepted for protocol symmetry and stamped into
        the payload as the ``agent_id`` so delegate-mode classification
        sees subagent context (matching how the HookRunner primes
        ``agent_id`` for replayed tool calls). ``timeout`` is forwarded to
        the HookRunner subprocess.
        """

        _ = timeout  # HookRunner owns its own subprocess timeout
        if not agent_type or not isinstance(agent_type, str):
            raise EvalError(f"invalid agent_type: {agent_type!r}")
        if not task or not isinstance(task, str):
            raise EvalError(f"invalid task (command): {task!r}")

        runner = self._get_runner()

        from gaia_simulator.extractor import ReplayEvent

        event = ReplayEvent(
            timestamp=iso_now(),
            hook_name="pre_tool_use",
            tool_name="Bash",
            stdin_payload={
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": task},
                "session_id": "eval-hook-replay",
                "agent_id": agent_type,
            },
            expected_decision="",  # we grade against the catalog oracle, not this
            expected_exit_code=0,
            expected_tier="",
            source_file="<security_decisions catalog>",
        )

        try:
            with _isolated_gaia_data_dir():
                replay_result = runner.run(event)
        except Exception as exc:  # pragma: no cover - defensive
            raise EvalError(f"hook replay failed: {exc}") from exc

        decision, reason = self._oracle_decision(replay_result)

        payload = {
            "decision": decision,
            "reason": reason,
            "exit_code": replay_result.actual_exit_code,
            "raw_decision": replay_result.actual_decision,
        }

        return DispatchResult(
            stdout=json.dumps(payload, sort_keys=True),
            session_path=None,
            audit_paths=[],
            exit_code=replay_result.actual_exit_code,
        )


# ---------------------------------------------------------------------------
# Public dispatch function
# ---------------------------------------------------------------------------


def dispatch(
    agent_type: str,
    task: str,
    timeout: int = 60,
    capture_session: bool = False,
    *,
    backend: DispatchBackend,
) -> DispatchResult:
    """Dispatch ``task`` to the agent identified by ``agent_type``.

    Args:
        agent_type: Target agent name (e.g. ``"developer"``,
            ``"gaia-orchestrator"``).
        task: Natural-language prompt to send to the agent.
        timeout: Wall-clock timeout in seconds.
        capture_session: When true, callers expect a populated
            ``session_path``. This flag is advisory -- the
            ``SubprocessBackend`` always captures the transcript
            because CC writes it unconditionally. Kept for API
            symmetry with v1 callers and future backends that might
            need to explicitly opt in.
        backend: Backend implementation. Required, and keyword-only:
            there is no default. The former default shelled out to
            ``claude --print --agent <specialist>``, a dispatch shape Gaia
            does not route through (see the module docstring); leaving a
            default in place would make that shape the one a forgetful
            caller silently gets.

    Returns:
        :class:`DispatchResult` with the agent's response and captured
        telemetry.

    Raises:
        EvalError: On timeout, unknown agent, missing fixture, or any
            other terminal backend failure.
    """

    _ = capture_session  # reserved for future use; see docstring
    return backend.dispatch(agent_type=agent_type, task=task, timeout=timeout)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def iso_now() -> str:
    """Return an ISO-8601 UTC timestamp used by session payloads."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DispatchBackend",
    "DispatchResult",
    "EvalError",
    "FakeBackend",
    "HookLogReplayBackend",
    "RoutingSimBackend",
    "dispatch",
    "iso_now",
]
