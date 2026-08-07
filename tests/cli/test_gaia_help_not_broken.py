"""Regression: `gaia --help` and bare `gaia` must print help and exit 0.

Root cause: the `--json` global flag's `help=(...)` in `_build_parser`
(bin/gaia) was built from adjacent string literals, but the last literal
carried a trailing comma before the closing paren -- turning the whole
`help=` value into a one-element tuple instead of a `str`. argparse's
`HelpFormatter._format_action` calls `action.help.strip()` while rendering
the help text, so both `gaia --help` and bare `gaia` (which also prints help)
crashed with `AttributeError: 'tuple' object has no attribute 'strip'` and
exited 1, instead of ever reaching the orchestrator's lane epilogue.

A test that only asserts `isinstance(help_value, str)` would not catch an
equivalent regression on a DIFFERENT flag's `help=` -- this exercises the
real end-to-end help invocation via subprocess, the same way a user or the
orchestrator actually calls it, so any flag whose help value stops being a
plain string breaks this test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"

# A stable, distinctive substring of the orchestrator lane epilogue
# (`_EPILOG` in bin/gaia) -- proof the full help text rendered, not just
# that argparse exited 0.
_EPILOG_MARKER = "lanes -- what runs where, and who owns the rest:"


def _run(argv):
    return subprocess.run(
        [sys.executable, str(_GAIA_BIN), *argv],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_gaia_dash_dash_help_exits_zero_and_prints_epilogue():
    result = _run(["--help"])
    assert result.returncode == 0, (
        f"gaia --help must exit 0, got {result.returncode}. "
        f"stderr: {result.stderr}"
    )
    assert "AttributeError" not in result.stderr
    assert _EPILOG_MARKER in result.stdout, (
        "gaia --help must print the orchestrator lane epilogue; "
        f"stdout was: {result.stdout!r}"
    )


def test_bare_gaia_exits_zero_and_prints_epilogue():
    result = _run([])
    assert result.returncode == 0, (
        f"bare gaia must exit 0, got {result.returncode}. "
        f"stderr: {result.stderr}"
    )
    assert "AttributeError" not in result.stderr
    assert _EPILOG_MARKER in result.stdout, (
        "bare gaia must print the orchestrator lane epilogue; "
        f"stdout was: {result.stdout!r}"
    )


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
