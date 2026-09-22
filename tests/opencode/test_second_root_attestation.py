"""Collect the native Bun second-root attestation regression in the Python CI suite."""

import subprocess
from pathlib import Path


def test_second_root_attestation():
    """A root opened after the first still reaches the control plane; a host-recorded child never does."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["bun", "test", str(root / "tests/opencode/second_root_attestation.test.ts")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
