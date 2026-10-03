"""A supersedes link points from the new row to the old one, and the CLI says so when it is written.

With the arrow backwards the new row leaves every injection and the stale one
stays, so the corpus reads as if the change never happened. `link` echoes
which row replaces which, warns when the row it retires is the newer one, and
its own help example retires the row it calls old.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GAIA = _REPO / "bin" / "gaia"


@pytest.fixture
def gaia(tmp_path):
    """Run the real CLI against a throwaway HOME, data dir and database."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    env = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DATA_DIR": str(data),
        "GAIA_DB": str(data / "gaia.db"),
    }
    env.pop("GAIA_DISPATCH_AGENT", None)

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_GAIA), *argv],
            env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    run.db = data / "gaia.db"
    return run


def _thread(gaia, name: str, *, born: str) -> None:
    """Write a live decision thread of project demo and date its birth."""
    result = gaia(
        "memory", "add", "--name", name, "--type", "decision", "--initiative", "demo",
        "--workspace", "demo", "--class", "thread", "--status", "open",
        "--description", f"Decisión: {name}.", "--body", f"body of {name}",
    )
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(str(gaia.db))
    try:
        con.execute(
            "UPDATE memory SET created_at = ?, updated_at = ? WHERE workspace = 'demo' AND name = ?",
            (born, born, name),
        )
        con.commit()
    finally:
        con.close()


def _live(gaia) -> set[str]:
    """The project's live-pending set, as `get-relevant --initiative` injects it."""
    result = gaia("memory", "get-relevant", "--initiative", "demo", "--workspace", "demo", "--json")
    assert result.returncode == 0, result.stderr
    return {item["name"] for item in json.loads(result.stdout)["items"]}


def _link_warnings(result) -> set[str]:
    assert result.returncode == 0, result.stdout + result.stderr
    return {w["code"] for w in json.loads(result.stdout).get("warnings", [])}


def _link(gaia, src: str, dst: str, *extra: str) -> subprocess.CompletedProcess:
    return gaia("memory", "link", src, dst, "--kind=supersedes", "--workspace", "demo", *extra)


def test_link_new_old_says_new_replaces_old_and_retires_old(gaia):
    """The echo names the replacement, and the old row leaves the live set."""
    _thread(gaia, "decision_x_old", born="2026-01-01T00:00:00Z")
    _thread(gaia, "decision_x_new", born="2026-02-01T00:00:00Z")

    result = _link(gaia, "decision_x_new", "decision_x_old")

    assert result.returncode == 0, result.stderr
    assert "decision_x_new reemplaza a decision_x_old" in result.stdout
    assert _live(gaia) == {"decision_x_new"}


def test_a_supersedes_link_that_retires_the_newer_row_warns_of_a_reversed_arrow(gaia):
    """Retiring the newer row is flagged; retiring the older one is not."""
    _thread(gaia, "decision_x_old", born="2026-01-01T00:00:00Z")
    _thread(gaia, "decision_x_new", born="2026-02-01T00:00:00Z")

    reversed_link = _link(gaia, "decision_x_old", "decision_x_new", "--json")
    _link(gaia, "decision_x_old", "decision_x_new", "--delete")
    forward_link = _link(gaia, "decision_x_new", "decision_x_old", "--json")

    assert "supersedes_reversed" in _link_warnings(reversed_link)
    assert "supersedes_reversed" not in _link_warnings(forward_link)


def test_the_help_example_run_as_written_retires_the_row_it_calls_old(gaia):
    """Copying the supersedes example out of `link --help` retires the old row, not the new one."""
    help_text = gaia("memory", "link", "--help").stdout
    example = re.search(r"gaia memory link (\S+) (\S+) --kind=supersedes", help_text)
    assert example, help_text
    first, second = example.groups()
    old, new = sorted((first, second), key=lambda name: "new" in name)
    assert "old" in old and "new" in new
    _thread(gaia, old, born="2026-01-01T00:00:00Z")
    _thread(gaia, new, born="2026-02-01T00:00:00Z")

    result = _link(gaia, first, second, "--json")

    assert "supersedes_reversed" not in _link_warnings(result)
    assert _live(gaia) == {new}
