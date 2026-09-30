"""A dispatched subagent can read the write-side memory help but still cannot write memory.

Specialists propose memory rows through their contract, so they need the
shape `gaia memory add --help` and `gaia memory link --help` teach, in the
forms an agent actually types (stderr folded in, output paged). A write
remains refused, including one riding on the same line as a help request.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO / "hooks"))

from modules.security.subagent_memory_write_guard import check  # noqa: E402

_AGENT = "developer"


@pytest.mark.parametrize(
    "command",
    [
        "gaia memory add --help",
        "gaia memory add --help 2>&1",
        "gaia memory link --help | head -40",
        "/opt/gaia/bin/gaia memory add -h 2>&1 | head -80",
    ],
)
def test_a_subagent_reads_the_write_help(command):
    """The help of a write verb reaches a non-curator subagent."""
    allowed, reason = check(command, is_subagent=True, agent_type=_AGENT)

    assert allowed, reason


@pytest.mark.parametrize(
    "command",
    [
        "gaia memory add --name atom_x --type atom --body y",
        "gaia memory add --help; gaia memory add --name atom_x --type atom --body y",
        "gaia memory add --help && gaia memory link a b --kind=supersedes",
        "gaia memory add --help $(gaia memory add --name atom_x --type atom --body y)",
        "gaia memory add --help;gaia memory add --name atom_x --type atom --body y",
    ],
)
def test_a_subagent_write_stays_blocked(command):
    """A write is refused alone and when it shares a line with a help request."""
    allowed, _ = check(command, is_subagent=True, agent_type=_AGENT)

    assert not allowed


def test_the_cli_itself_prints_help_and_refuses_the_write_for_a_non_curator(tmp_path):
    """Past the hook, the CLI under a non-curator dispatch identity shows help and writes nothing."""
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DATA_DIR": str(tmp_path / "data"),
        "GAIA_DB": str(tmp_path / "data" / "gaia.db"),
        "GAIA_DISPATCH_AGENT": _AGENT,
    }
    gaia = [sys.executable, str(_REPO / "bin" / "gaia"), "memory"]

    def run(*argv):
        return subprocess.run(
            [*gaia, *argv], env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    assert run("add", "--help").returncode == 0
    assert run("link", "--help").returncode == 0
    write = run("add", "--name", "atom_x", "--type", "atom", "--initiative", "demo",
                "--workspace", "demo", "--body", "y")
    assert write.returncode != 0
    assert run("show", "atom_x", "--workspace", "demo").returncode != 0
