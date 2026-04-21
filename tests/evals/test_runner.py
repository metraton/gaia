"""Unit tests for :mod:`tests.evals.runner` (T2).

Four scenarios per the plan:

1. FakeBackend returns a captured session + audit path.
2. Timeout raises :class:`EvalError`.
3. Bad agent name raises :class:`EvalError`.
4. ``session_path`` points at readable JSONL.

All tests use :class:`FakeBackend` -- no subprocess, no network, no real
``claude`` CLI. Coverage of :class:`SubprocessBackend` happens in the
live-dispatch suite (T7) marked ``@pytest.mark.llm`` and skipped by
default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.runner import (
    DispatchResult,
    EvalError,
    FakeBackend,
    dispatch,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "sessions"
MINIMAL_FIXTURE = FIXTURES_DIR / "minimal.jsonl"


# ---------------------------------------------------------------------------
# (a) FakeBackend returns captured session + audit path
# ---------------------------------------------------------------------------


def test_fake_backend_returns_session_and_audit(tmp_path: Path) -> None:
    """FakeBackend replays a fixture and surfaces audit paths verbatim."""

    audit_file = tmp_path / "audit-2026-04-20.jsonl"
    audit_file.write_text(
        json.dumps({"tool_name": "Read", "ts": "2026-04-20T12:00:00Z"}) + "\n"
    )

    backend = FakeBackend(
        fixture_path=MINIMAL_FIXTURE,
        stdout="response body",
        audit_paths=[audit_file],
        exit_code=0,
    )

    result = dispatch(
        agent_type="developer",
        task="echo hello",
        backend=backend,
    )

    assert isinstance(result, DispatchResult)
    assert result.stdout == "response body"
    assert result.session_path == MINIMAL_FIXTURE
    assert result.audit_paths == [audit_file]
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# (b) Timeout raises EvalError
# ---------------------------------------------------------------------------


def test_timeout_raises_eval_error() -> None:
    """A backend configured to time out surfaces :class:`EvalError`."""

    backend = FakeBackend(
        fixture_path=MINIMAL_FIXTURE,
        simulate_timeout=True,
    )

    with pytest.raises(EvalError) as excinfo:
        dispatch(
            agent_type="developer",
            task="long task",
            timeout=1,
            backend=backend,
        )

    assert "timed out" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# (c) Bad agent raises EvalError
# ---------------------------------------------------------------------------


def test_bad_agent_raises_eval_error() -> None:
    """An unknown agent name surfaces :class:`EvalError` from the backend."""

    backend = FakeBackend(
        fixture_path=MINIMAL_FIXTURE,
        simulate_bad_agent="not-a-real-agent",
    )

    with pytest.raises(EvalError) as excinfo:
        dispatch(
            agent_type="not-a-real-agent",
            task="whatever",
            backend=backend,
        )

    assert "not-a-real-agent" in str(excinfo.value)


def test_missing_fixture_raises_eval_error(tmp_path: Path) -> None:
    """FakeBackend raises :class:`EvalError` when its fixture is absent."""

    ghost = tmp_path / "does-not-exist.jsonl"
    backend = FakeBackend(fixture_path=ghost)

    with pytest.raises(EvalError) as excinfo:
        dispatch(agent_type="developer", task="x", backend=backend)

    assert "fixture" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# (d) session_path is readable JSONL
# ---------------------------------------------------------------------------


def test_session_path_is_readable_jsonl() -> None:
    """Every line of the returned session JSONL parses as JSON."""

    backend = FakeBackend(
        fixture_path=MINIMAL_FIXTURE,
        stdout="ok",
    )

    result = dispatch(agent_type="developer", task="echo hello", backend=backend)

    assert result.session_path is not None
    assert result.session_path.exists()
    raw = result.session_path.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) >= 1, "fixture must contain at least one session event"
    for line in lines:
        # Each line must be valid JSON -- no partial/streaming artifacts.
        json.loads(line)


# ---------------------------------------------------------------------------
# DispatchResult sanity (regression guard -- shape is consumed by graders)
# ---------------------------------------------------------------------------


def test_dispatch_result_defaults() -> None:
    """DispatchResult has sensible defaults for optional fields."""

    result = DispatchResult(stdout="", session_path=None)
    assert result.audit_paths == []
    assert result.exit_code == 0
