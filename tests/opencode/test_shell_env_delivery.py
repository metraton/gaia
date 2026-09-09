"""Collect the native Bun identity-delivery regression in the Python CI suite."""

import subprocess
from pathlib import Path


def test_shell_env_delivery():
    """Require the identity-only controls to pass without using a database or network."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["bun", "test", str(root / "tests/opencode/shell_env_delivery.test.ts")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
