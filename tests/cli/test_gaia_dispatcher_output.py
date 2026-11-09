"""Every argv that loads all plugins writes nothing to stderr: a plugin that fails to load must not hide behind exit 0."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_BIN_GAIA = Path(__file__).resolve().parents[2] / "bin" / "gaia"


@pytest.mark.parametrize("argv", [[], ["--version"], ["--help"]], ids=["bare", "version", "help"])
def test_full_discovery_writes_nothing_to_stderr(argv, tmp_path):
    result = subprocess.run(
        [sys.executable, str(_BIN_GAIA), *argv],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
    )

    assert (result.returncode, result.stderr) == (0, "")
