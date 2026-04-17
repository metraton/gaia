"""Tests for the command-type evidence runner.

Contract:
    run_command(shape: dict, artifact_path: Path) -> EvidenceResult

    shape keys:
      run: str               # bash command (required)
      expect: str            # "exit 0" | "substring <literal>" (default "exit 0")
      timeout: int           # seconds (default 60)

    EvidenceResult:
      passed: bool
      output: str            # stdout + stderr
      artifact_path: Path    # file written (contains stdout + stderr + exit_code)
      error: Optional[str]   # "timeout" | "spawn_error" | None
"""
from __future__ import annotations

from pathlib import Path

from hooks.modules.evidence.runner import EvidenceResult, run_command


def test_echo_hello_exits_zero_passes(tmp_path: Path) -> None:
    """`echo hello` with expect=exit 0 -> passed=True, artifact has 'hello'."""
    art = tmp_path / "AC-1.txt"
    result = run_command({"run": "echo hello", "expect": "exit 0"}, art)
    assert isinstance(result, EvidenceResult)
    assert result.passed is True
    assert "hello" in result.output
    assert art.exists()
    assert "hello" in art.read_text()


def test_false_command_fails_exit_zero(tmp_path: Path) -> None:
    """`false` with expect=exit 0 -> passed=False (non-zero exit)."""
    art = tmp_path / "AC.txt"
    result = run_command({"run": "false", "expect": "exit 0"}, art)
    assert result.passed is False


def test_substring_expect_matches(tmp_path: Path) -> None:
    """expect='substring pass' -> passed if output contains 'pass'."""
    art = tmp_path / "AC.txt"
    result = run_command({"run": "echo pass", "expect": "substring pass"}, art)
    assert result.passed is True


def test_substring_expect_does_not_match(tmp_path: Path) -> None:
    """expect='substring foo' but output says 'bar' -> passed=False."""
    art = tmp_path / "AC.txt"
    result = run_command({"run": "echo bar", "expect": "substring foo"}, art)
    assert result.passed is False


def test_timeout_marks_result_as_failed(tmp_path: Path) -> None:
    """A command exceeding timeout -> passed=False, error='timeout'."""
    art = tmp_path / "AC.txt"
    result = run_command(
        {"run": "sleep 3", "expect": "exit 0", "timeout": 1},
        art,
    )
    assert result.passed is False
    assert result.error == "timeout"


def test_artifact_file_contains_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    """Artifact persistence contract: the file holds stdout + stderr + exit_code."""
    art = tmp_path / "AC.txt"
    run_command(
        {"run": "echo out; echo err >&2; exit 3", "expect": "exit 0"},
        art,
    )
    content = art.read_text()
    assert "out" in content
    assert "err" in content
    # Exit code line in a predictable form: allow any of "exit 3", "exit_code: 3".
    assert "3" in content
    assert "exit" in content.lower()


def test_default_expect_is_exit_zero(tmp_path: Path) -> None:
    """Omitting `expect` defaults to `exit 0`."""
    art = tmp_path / "AC.txt"
    result = run_command({"run": "true"}, art)
    assert result.passed is True
