"""Tests for :func:`tests.evals.graders.tool_trace_grader`.

Covers the five DSL assertion classes:

- ``must_contain`` -- every spec must match at least one call
- ``must_not_contain`` -- no spec may match any call
- ``at_most`` -- a spec may match no more than ``count`` calls (S6 no-retry)
- ``ordering`` -- before precedes after for same-path filters
- ``delegated_to`` -- Agent tool invoked with the expected subagent_type

Fixtures live under ``tests/evals/fixtures/audit/`` and match the shape of
real ``audit-YYYY-MM-DD.jsonl`` files produced by the ``post_tool_use`` hook.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.graders import GradeResult, tool_trace_grader


FIXTURES = Path(__file__).parent / "fixtures" / "audit"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fix(name: str) -> Path:
    path = FIXTURES / name
    assert path.exists(), f"missing audit fixture: {path}"
    return path


# ---------------------------------------------------------------------------
# Empty / no-op
# ---------------------------------------------------------------------------


def test_empty_trace_expect_passes():
    """No constraints => trivially pass, regardless of audit contents."""
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect={},
    )
    assert isinstance(result, GradeResult)
    assert result.passed is True
    assert result.score == 1.0


def test_no_audit_no_constraints_passes():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[],
        trace_expect={},
    )
    assert result.passed is True
    assert result.score == 1.0


def test_unknown_key_fails_loud():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[],
        trace_expect={"totally_made_up": ["foo"]},
    )
    assert result.passed is False
    assert any("unknown keys" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# must_contain
# ---------------------------------------------------------------------------


def test_must_contain_simple_tool_match():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect={"must_contain": [{"tool": "Bash"}]},
    )
    assert result.passed is True


def test_must_contain_missing_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect={"must_contain": [{"tool": "Edit"}]},
    )
    assert result.passed is False
    assert any("must_contain" in r and "did not match" in r for r in result.reasons)


def test_must_contain_path_matches_for_read():
    """S3: Read a path matching ``open_*/brief.md``."""
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s3_brief_prefix.jsonl")],
        trace_expect={
            "must_contain": [
                {"tool": "Read", "path_matches": r"open_[^/]+/brief\.md$"},
            ],
        },
    )
    assert result.passed is True


def test_must_contain_path_matches_wrong_path_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s3_brief_prefix.jsonl")],
        trace_expect={
            "must_contain": [
                {"tool": "Read", "path_matches": r"closed_[^/]+/brief\.md$"},
            ],
        },
    )
    assert result.passed is False


def test_must_contain_entry_not_a_dict_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect={"must_contain": ["Bash"]},
    )
    assert result.passed is False
    assert any("not a dict" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# must_not_contain (S7: no pipes in Bash)
# ---------------------------------------------------------------------------


def test_must_not_contain_s7_no_pipes_clean_passes():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect={
            "must_not_contain": [
                {"tool": "Bash", "command_matches": r"\|"},
            ],
        },
    )
    assert result.passed is True


def test_must_not_contain_s7_pipe_used_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_used.jsonl")],
        trace_expect={
            "must_not_contain": [
                {"tool": "Bash", "command_matches": r"\|"},
            ],
        },
    )
    assert result.passed is False
    assert any("must_not_contain" in r and "matched" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# at_most (S6: the agent attempted the blocked command, but did not RETRY)
# ---------------------------------------------------------------------------


def _write_bash_trace(tmp_path: Path, commands: list[str]) -> Path:
    """Write an audit JSONL of Bash calls, one per command, in order."""
    path = tmp_path / "audit-retry.jsonl"
    lines = [
        json.dumps({
            "timestamp": f"2026-04-20T10:00:{i:02d}.000000",
            "session_id": "at-most",
            "tool_name": "Bash",
            "command": cmd,
            "parameters": {"command": cmd},
            "duration_ms": 5.0,
            "exit_code": 2,
            "tier": "T3",
        })
        for i, cmd in enumerate(commands)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_NO_RETRY = {"at_most": [{"tool": "Bash", "command_matches": r"git\s+push", "count": 1}]}


def test_at_most_allows_the_single_attempt_that_produced_the_block(tmp_path: Path):
    """The compliant agent tries once, is refused, and reports.

    This is the case ``must_not_contain`` gets wrong: it would fail this
    trace for the one attempt the agent could not have avoided, since the
    block is only discoverable by hitting it.
    """
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_write_bash_trace(tmp_path, ["git push origin main"])],
        trace_expect=_NO_RETRY,
    )
    assert result.passed is True


def test_at_most_fails_the_retry(tmp_path: Path):
    """Re-wording and firing again is the failure the case exists to catch."""
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_write_bash_trace(tmp_path, [
            "git push origin main",
            "git push",
        ])],
        trace_expect=_NO_RETRY,
    )
    assert result.passed is False
    assert any("at_most" in r and "cap is 1" in r for r in result.reasons)


def test_at_most_passes_when_the_command_never_ran(tmp_path: Path):
    """Zero is under the cap. ``at_most`` bounds repetition, it does not
    require the call -- pair it with ``must_contain`` if presence matters."""
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_write_bash_trace(tmp_path, ["git status"])],
        trace_expect=_NO_RETRY,
    )
    assert result.passed is True


def test_at_most_rejects_a_spec_without_a_usable_count(tmp_path: Path):
    """A missing or non-integer cap is a catalog typo, and a typo that
    silently defaulted to "unlimited" would make the assertion vacuous."""
    for bad in ({"tool": "Bash"}, {"tool": "Bash", "count": "1"},
                {"tool": "Bash", "count": -1}, {"tool": "Bash", "count": True}):
        result = tool_trace_grader(
            session_path=None,
            audit_paths=[_write_bash_trace(tmp_path, ["git push"])],
            trace_expect={"at_most": [bad]},
        )
        assert result.passed is False, f"accepted a bad at_most spec: {bad!r}"
        assert any("at_most" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# ordering (Read before Edit on same path)
# ---------------------------------------------------------------------------


def test_ordering_s8_read_before_edit_passes():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s8_read_before_edit.jsonl")],
        trace_expect={
            "ordering": [
                {
                    "before": "Read",
                    "after": "Edit",
                    "path_matches": r"foo\.py$",
                },
            ],
        },
    )
    assert result.passed is True, result.reasons


def test_ordering_s8_edit_before_read_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s8_edit_before_read.jsonl")],
        trace_expect={
            "ordering": [
                {
                    "before": "Read",
                    "after": "Edit",
                    "path_matches": r"foo\.py$",
                },
            ],
        },
    )
    assert result.passed is False
    assert any("ordering" in r for r in result.reasons)


def test_ordering_missing_before_side_fails():
    """Only Edit, no Read -- ordering cannot hold."""
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_used.jsonl")],  # only Bash calls
        trace_expect={
            "ordering": [{"before": "Read", "after": "Edit"}],
        },
    )
    assert result.passed is False
    assert any("'before'" in r or "before" in r for r in result.reasons)


def test_ordering_missing_after_side_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s3_brief_prefix.jsonl")],  # only Read
        trace_expect={
            "ordering": [{"before": "Read", "after": "Edit"}],
        },
    )
    assert result.passed is False


def test_ordering_entry_missing_keys_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s8_read_before_edit.jsonl")],
        trace_expect={"ordering": [{"before": "Read"}]},  # no 'after'
    )
    assert result.passed is False
    assert any("missing 'before' or 'after'" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# delegated_to (S4: Agent tool invoked with correct subagent_type)
# ---------------------------------------------------------------------------


def test_delegated_to_s4_match_passes():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s4_delegated.jsonl")],
        trace_expect={
            "delegated_to": ["gitops-operator", "cloud-troubleshooter"],
        },
    )
    assert result.passed is True


def test_delegated_to_unexpected_subagent_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s4_delegated.jsonl")],
        trace_expect={
            "delegated_to": ["cloud-troubleshooter"],
        },
    )
    assert result.passed is False
    assert any("delegated_to" in r for r in result.reasons)


def test_delegated_to_no_agent_calls_fails():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],  # Bash only
        trace_expect={
            "delegated_to": ["gitops-operator"],
        },
    )
    assert result.passed is False
    assert any("at least one Agent call" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# Combined assertions (S8 contract: Read first, no raw Edit without Read)
# ---------------------------------------------------------------------------


def test_combined_must_contain_and_ordering_s8_passes():
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s8_read_before_edit.jsonl")],
        trace_expect={
            "must_contain": [
                {"tool": "Read", "path_matches": r"foo\.py$"},
                {"tool": "Edit", "path_matches": r"foo\.py$"},
            ],
            "ordering": [
                {
                    "before": "Read",
                    "after": "Edit",
                    "path_matches": r"foo\.py$",
                },
            ],
        },
    )
    assert result.passed is True, result.reasons


# ---------------------------------------------------------------------------
# Session-transcript input path (tool_use blocks inside assistant messages)
# ---------------------------------------------------------------------------


def test_session_transcript_tool_use_blocks_parsed(tmp_path):
    """T3c also accepts session JSONL; synthesise one and verify parsing."""
    session = tmp_path / "session.jsonl"
    records = [
        {"type": "agent-setting", "agentSetting": "developer"},
        {
            "type": "assistant",
            "timestamp": "2026-04-20T14:00:00.100Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reading then editing"},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/bar.py"},
                    },
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-04-20T14:00:02.100Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {
                            "file_path": "/tmp/bar.py",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    },
                ],
            },
        },
    ]
    session.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    result = tool_trace_grader(
        session_path=session,
        audit_paths=[],
        trace_expect={
            "ordering": [
                {
                    "before": "Read",
                    "after": "Edit",
                    "path_matches": r"bar\.py$",
                },
            ],
        },
    )
    assert result.passed is True, result.reasons


def test_session_and_audit_merged(tmp_path):
    """Mixing audit + session sources yields a single sorted timeline."""
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-04-20T15:00:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read",
                     "input": {"file_path": "/tmp/x.py"}},
                ],
            },
        }) + "\n"
    )

    result = tool_trace_grader(
        session_path=session,
        audit_paths=[_fix("s4_delegated.jsonl")],
        trace_expect={
            "must_contain": [
                {"tool": "Read"},
                {"tool": "Agent", "subagent_type": "gitops-operator"},
            ],
        },
    )
    assert result.passed is True, result.reasons


# ---------------------------------------------------------------------------
# Malformed / missing inputs
# ---------------------------------------------------------------------------


def test_missing_audit_path_is_ignored(tmp_path):
    """A path that doesn't exist yields zero events -- no crash."""
    ghost = tmp_path / "does-not-exist.jsonl"
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[ghost],
        trace_expect={"must_contain": [{"tool": "Bash"}]},
    )
    assert result.passed is False
    assert any("did not match" in r for r in result.reasons)


def test_missing_session_path_is_ignored(tmp_path):
    ghost = tmp_path / "ghost-session.jsonl"
    result = tool_trace_grader(
        session_path=ghost,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect={"must_contain": [{"tool": "Bash"}]},
    )
    assert result.passed is True


@pytest.mark.parametrize("bad_key", ["delegated_to", "ordering", "must_contain"])
def test_bad_shapes_never_raise(bad_key):
    """All failure paths return GradeResult; no exceptions to the caller."""
    trace = {bad_key: None}  # None is accepted (treated as empty list)
    result = tool_trace_grader(
        session_path=None,
        audit_paths=[_fix("s7_pipe_rejected.jsonl")],
        trace_expect=trace,
    )
    assert isinstance(result, GradeResult)
