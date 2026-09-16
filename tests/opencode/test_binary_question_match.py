"""Collect the native Bun question-correlation regression in the Python CI suite."""

import subprocess
from pathlib import Path


def test_binary_question_match():
    """The control correlates the host's question structurally, never by serialized bytes."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["bun", "test", str(root / "tests/opencode/binary_question_match.test.ts")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
