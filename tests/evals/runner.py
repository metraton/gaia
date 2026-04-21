"""Runner for the eval framework (T2).

Dispatches a task to an agent and captures the multi-turn session
transcript plus any ``audit-*.jsonl`` slices produced during the
dispatch window.

Two backends implement :class:`DispatchBackend`:

- :class:`SubprocessBackend` shells out to the real ``claude`` CLI with
  a fixed session id, so the transcript lands at a predictable path
  under ``~/.claude/projects/<cwd-slug>/<session-id>.jsonl``.
- :class:`FakeBackend` replays canned session JSONL from
  ``tests/evals/fixtures/sessions/`` without any subprocess or network
  I/O. Used in unit tests; also consumed by T7's smoke runs.

The module MUST NOT import from ``hooks/`` or parse ``project-context``
data directly -- it only reads and writes files the agent already
produces.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol


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
# Helpers
# ---------------------------------------------------------------------------


def _cwd_slug(cwd: Path) -> str:
    """Return the CC project-slug for ``cwd``.

    Claude Code stores transcripts at
    ``~/.claude/projects/<slug>/<session-id>.jsonl`` where ``<slug>`` is
    the absolute cwd with path separators replaced by ``-``. For
    ``/home/jorge/ws/me`` the slug is ``-home-jorge-ws-me``.
    """

    return str(cwd.resolve()).replace("/", "-")


def _projects_dir() -> Path:
    """Return the CC transcripts root (``~/.claude/projects``)."""

    return Path.home() / ".claude" / "projects"


def _collect_audit_slices(
    logs_dir: Path,
    window_start: float,
    window_end: float,
) -> list[Path]:
    """Return audit-*.jsonl files whose mtime overlaps the window.

    We don't try to surgically slice by timestamp here -- graders
    downstream filter by dispatch start/end using line timestamps. The
    runner just hands over the candidate files produced during the
    dispatch window.
    """

    if not logs_dir.is_dir():
        return []

    candidates: list[Path] = []
    for path in sorted(logs_dir.glob("audit-*.jsonl")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # A file counts if its mtime is within (or just after) the window.
        # Using a generous 5 s margin absorbs filesystem clock jitter
        # without letting unrelated days leak in.
        if mtime >= window_start - 5 and mtime <= window_end + 5:
            candidates.append(path)
    return candidates


# ---------------------------------------------------------------------------
# Subprocess backend (real claude CLI)
# ---------------------------------------------------------------------------


@dataclass
class SubprocessBackend:
    """Dispatch via the real ``claude`` CLI.

    The backend pins a deterministic ``--session-id`` so the transcript
    path is predictable: ``~/.claude/projects/<cwd-slug>/<uuid>.jsonl``.
    ``audit_paths`` comes from ``<cwd>/.claude/logs/audit-YYYY-MM-DD.jsonl``
    files whose mtime falls within the dispatch window.

    Attributes:
        cwd: Working directory used as the CC project root. Defaults to
            the current working directory at backend construction time.
        claude_bin: Path or name of the ``claude`` binary. Defaults to
            whichever ``claude`` is on ``$PATH``; ``EvalError`` is raised
            at dispatch time if it cannot be found.
        output_format: Passed to ``--output-format``. ``"json"`` returns
            a single-shot JSON result; ``"text"`` returns raw text.
        permission_mode: Passed to ``--permission-mode``. Defaults to
            ``"acceptEdits"`` so Edit/Write on declarative files do not
            block the non-interactive dispatch. Callers that need strict
            behaviour (e.g. S6 approval flow) can override.
        extra_args: Additional CLI args appended verbatim to the
            ``claude`` invocation. Useful for ``--add-dir`` or
            ``--settings``.
    """

    cwd: Path = field(default_factory=Path.cwd)
    claude_bin: Optional[str] = None
    output_format: str = "json"
    permission_mode: str = "acceptEdits"
    extra_args: list[str] = field(default_factory=list)

    def dispatch(
        self,
        agent_type: str,
        task: str,
        timeout: int = 60,
    ) -> DispatchResult:
        binary = self.claude_bin or shutil.which("claude")
        if not binary:
            raise EvalError(
                "claude CLI not found on PATH; set SubprocessBackend.claude_bin"
            )

        if not agent_type or not isinstance(agent_type, str):
            raise EvalError(f"invalid agent_type: {agent_type!r}")

        session_id = str(uuid.uuid4())
        cwd = self.cwd.resolve()
        logs_dir = cwd / ".claude" / "logs"

        cmd = [
            binary,
            "--print",
            "--agent",
            agent_type,
            "--session-id",
            session_id,
            "--output-format",
            self.output_format,
            "--permission-mode",
            self.permission_mode,
            *self.extra_args,
            task,
        ]

        window_start = time.time()
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise EvalError(
                f"claude dispatch timed out after {timeout}s (agent={agent_type})"
            ) from exc
        except FileNotFoundError as exc:
            raise EvalError(f"failed to exec claude binary: {exc}") from exc
        window_end = time.time()

        # Unknown agent: CC currently exits non-zero with an error on stderr.
        # We surface that as EvalError so callers get a clean signal instead
        # of having to inspect exit codes.
        if completed.returncode != 0 and "agent" in (completed.stderr or "").lower():
            raise EvalError(
                f"claude rejected agent {agent_type!r}: "
                f"{completed.stderr.strip() or 'no stderr'}"
            )

        transcript_path = _projects_dir() / _cwd_slug(cwd) / f"{session_id}.jsonl"
        session_path: Optional[Path] = (
            transcript_path if transcript_path.exists() else None
        )

        audit_paths = _collect_audit_slices(logs_dir, window_start, window_end)

        return DispatchResult(
            stdout=completed.stdout or "",
            session_path=session_path,
            audit_paths=audit_paths,
            exit_code=completed.returncode,
        )


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
    """Return the gaia-ops repo root.

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
        repo_root: Path to the gaia-ops repo root. ``config/`` and
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
        # cannot construct the simulator (missing surface-routing.json
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
# Public dispatch function
# ---------------------------------------------------------------------------


# Default backend is constructed lazily so importing this module in test
# environments without a `claude` CLI does not fail.
_default_backend: Optional[DispatchBackend] = None


def _get_default_backend() -> DispatchBackend:
    global _default_backend
    if _default_backend is None:
        _default_backend = SubprocessBackend()
    return _default_backend


def dispatch(
    agent_type: str,
    task: str,
    timeout: int = 60,
    capture_session: bool = False,
    backend: Optional[DispatchBackend] = None,
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
        backend: Backend implementation. Defaults to a lazy
            :class:`SubprocessBackend`. Tests inject
            :class:`FakeBackend`.

    Returns:
        :class:`DispatchResult` with the agent's response and captured
        telemetry.

    Raises:
        EvalError: On timeout, missing CLI, unknown agent, missing
            fixture, or any other terminal backend failure.
    """

    _ = capture_session  # reserved for future use; see docstring
    impl = backend or _get_default_backend()
    return impl.dispatch(agent_type=agent_type, task=task, timeout=timeout)


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
    "RoutingSimBackend",
    "SubprocessBackend",
    "dispatch",
    "iso_now",
]
