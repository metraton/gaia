"""
Command + artifact evidence runners.

Contract:
    run_command(shape, artifact_path) -> EvidenceResult
    run_artifact(shape, artifact_path) -> EvidenceResult

EvidenceResult:
    passed: bool
    output: str           # stdout + stderr
    artifact_path: Path   # file written -- stdout + stderr + exit for commands,
                          # raw target bytes for artifacts
    error: Optional[str]  # "timeout" | "spawn_error" | "yaml: ..." | "json: ..." | None
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .assertions import evaluate


_DEFAULT_TIMEOUT = 60
_DEFAULT_EXPECT = "exit 0"


@dataclass
class EvidenceResult:
    """Outcome of a single evidence run."""

    passed: bool
    output: str
    artifact_path: Path
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------

def _write_command_artifact(
    artifact_path: Path,
    stdout: str,
    stderr: str,
    exit_code: int,
    error: Optional[str],
) -> None:
    """Persist stdout + stderr + exit code to the artifact file."""
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "=== stdout ===",
        stdout.rstrip("\n"),
        "=== stderr ===",
        stderr.rstrip("\n"),
        f"exit_code: {exit_code}",
    ]
    if error:
        parts.append(f"error: {error}")
    artifact_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _check_expect(expect: str, stdout: str, stderr: str, exit_code: int) -> bool:
    """Evaluate the expect clause against the run output."""
    if expect == "exit 0":
        return exit_code == 0
    if expect.startswith("substring "):
        needle = expect[len("substring "):]
        return needle in stdout or needle in stderr
    # Unknown expect pattern -- fail closed.
    return False


def run_command(shape: dict, artifact_path: Path) -> EvidenceResult:
    """Execute `shape.run` through bash and evaluate against `shape.expect`."""
    run_cmd = shape.get("run", "")
    expect = shape.get("expect", _DEFAULT_EXPECT)
    timeout = int(shape.get("timeout", _DEFAULT_TIMEOUT))

    try:
        completed = subprocess.run(
            ["bash", "-c", run_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(
            exc.stdout, (bytes, bytearray)
        ) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(
            exc.stderr, (bytes, bytearray)
        ) else (exc.stderr or "")
        combined = stdout + stderr
        _write_command_artifact(artifact_path, stdout, stderr, -1, "timeout")
        return EvidenceResult(
            passed=False,
            output=combined,
            artifact_path=artifact_path,
            error="timeout",
        )
    except (FileNotFoundError, OSError) as exc:
        msg = f"spawn_error: {exc}"
        _write_command_artifact(artifact_path, "", msg, -1, "spawn_error")
        return EvidenceResult(
            passed=False,
            output=msg,
            artifact_path=artifact_path,
            error="spawn_error",
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout + stderr
    passed = _check_expect(expect, stdout, stderr, completed.returncode)

    _write_command_artifact(
        artifact_path, stdout, stderr, completed.returncode, None
    )
    return EvidenceResult(
        passed=passed,
        output=combined,
        artifact_path=artifact_path,
        error=None,
    )


# ---------------------------------------------------------------------------
# artifact
# ---------------------------------------------------------------------------

def _select_target(path_str: str, select: Optional[str]) -> Path:
    """Resolve the file to parse. If path is a directory and select=latest,
    pick the newest file in it (by mtime). Otherwise return path as-is."""
    p = Path(path_str)
    if p.is_dir() and select == "latest":
        candidates = [c for c in p.iterdir() if c.is_file()]
        if not candidates:
            return p  # Will fail downstream with a clear error.
        candidates.sort(key=lambda c: c.stat().st_mtime, reverse=True)
        return candidates[0]
    return p


def _parse_target(target: Path, kind: str) -> Any:
    """Parse the target file according to `kind`. Returns the raw Python object."""
    text = target.read_text(encoding="utf-8")
    if kind == "yaml":
        return yaml.safe_load(text)
    if kind == "json":
        return json.loads(text)
    raise ValueError(f"Unknown artifact kind: {kind!r}")


def _write_artifact_snapshot(
    artifact_path: Path,
    target: Path,
    passed: bool,
    error: Optional[str],
) -> None:
    """Persist a summary of the artifact check to the artifact file."""
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"target: {target}",
        f"passed: {passed}",
    ]
    if error:
        parts.append(f"error: {error}")
    try:
        snippet = target.read_text(encoding="utf-8")
        parts.append("=== content ===")
        parts.append(snippet)
    except OSError as exc:
        parts.append(f"(unable to read target: {exc})")
    artifact_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def run_artifact(shape: dict, artifact_path: Path) -> EvidenceResult:
    """Load a file, optionally pick latest from a directory, assert on its parsed
    contents via the assertions DSL."""
    path_str = shape.get("path", "")
    kind = shape.get("kind", "")
    select = shape.get("select")
    assert_spec = shape.get("assert") or {}

    target = _select_target(path_str, select)

    try:
        data = _parse_target(target, kind)
    except yaml.YAMLError as exc:
        err = f"yaml parse error: {exc}"
        _write_artifact_snapshot(artifact_path, target, False, err)
        return EvidenceResult(
            passed=False,
            output=err,
            artifact_path=artifact_path,
            error=err,
        )
    except json.JSONDecodeError as exc:
        err = f"json parse error: {exc}"
        _write_artifact_snapshot(artifact_path, target, False, err)
        return EvidenceResult(
            passed=False,
            output=err,
            artifact_path=artifact_path,
            error=err,
        )
    except (OSError, ValueError) as exc:
        err = f"artifact error: {exc}"
        _write_artifact_snapshot(artifact_path, target, False, err)
        return EvidenceResult(
            passed=False,
            output=err,
            artifact_path=artifact_path,
            error=err,
        )

    try:
        passed = evaluate(assert_spec, data)
    except ValueError as exc:
        err = f"assert error: {exc}"
        _write_artifact_snapshot(artifact_path, target, False, err)
        return EvidenceResult(
            passed=False,
            output=err,
            artifact_path=artifact_path,
            error=err,
        )

    _write_artifact_snapshot(artifact_path, target, passed, None)
    return EvidenceResult(
        passed=passed,
        output=f"target={target} passed={passed}",
        artifact_path=artifact_path,
        error=None,
    )
