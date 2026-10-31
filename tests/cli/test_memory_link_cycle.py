"""A supersedes link that would close a loop, or point a row at itself, is refused before anything is written."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_GAIA = Path(__file__).resolve().parents[2] / "bin" / "gaia"


@pytest.fixture
def gaia(tmp_path):
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


def _rows(gaia, *names: str) -> None:
    for name in names:
        result = gaia(
            "memory", "add", "--name", name, "--type", "decision", "--initiative", "demo",
            "--workspace", "demo", "--class", "thread", "--status", "open",
            "--description", f"Decision: {name}.", "--body", f"body of {name}",
        )
        assert result.returncode == 0, result.stderr


def _link(gaia, src: str, *dsts: str) -> subprocess.CompletedProcess:
    return gaia("memory", "link", src, *dsts, "--kind=supersedes", "--workspace", "demo")


def _edges(gaia) -> set[tuple[str, str]]:
    con = sqlite3.connect(str(gaia.db))
    try:
        return set(con.execute(
            "SELECT src_name, dst_name FROM memory_links WHERE kind = 'supersedes'"
        ).fetchall())
    finally:
        con.close()


def test_a_link_back_to_the_row_that_already_supersedes_it_is_refused(gaia):
    _rows(gaia, "decision_new", "decision_old")
    assert _link(gaia, "decision_new", "decision_old").returncode == 0

    result = _link(gaia, "decision_old", "decision_new")

    assert result.returncode != 0
    assert "decision_old" in result.stderr and "decision_new" in result.stderr
    assert _edges(gaia) == {("decision_new", "decision_old")}


def test_a_link_that_closes_a_chain_into_a_loop_is_refused(gaia):
    _rows(gaia, "decision_v1", "decision_v2", "decision_v3")
    assert _link(gaia, "decision_v2", "decision_v1").returncode == 0
    assert _link(gaia, "decision_v3", "decision_v2").returncode == 0

    result = _link(gaia, "decision_v1", "decision_v3")

    assert result.returncode != 0
    assert "decision_v1" in result.stderr and "decision_v3" in result.stderr
    assert _edges(gaia) == {("decision_v2", "decision_v1"), ("decision_v3", "decision_v2")}


def test_a_row_cannot_supersede_itself(gaia):
    _rows(gaia, "decision_solo")

    result = _link(gaia, "decision_solo", "decision_solo")

    assert result.returncode != 0
    assert "decision_solo" in result.stderr
    assert _edges(gaia) == set()


def test_one_invalid_destination_refuses_the_whole_call_and_names_the_pair(gaia):
    _rows(gaia, "decision_x", "decision_y", "decision_z", "decision_w")
    assert _link(gaia, "decision_y", "decision_x").returncode == 0

    result = _link(gaia, "decision_x", "decision_z", "decision_y", "decision_w")

    assert result.returncode != 0
    assert "decision_x" in result.stderr and "decision_y" in result.stderr
    assert _edges(gaia) == {("decision_y", "decision_x")}


def test_a_clean_call_with_several_destinations_writes_every_pair(gaia):
    _rows(gaia, "decision_new", "decision_a", "decision_b")

    result = _link(gaia, "decision_new", "decision_a", "decision_b")

    assert result.returncode == 0, result.stderr
    assert _edges(gaia) == {("decision_new", "decision_a"), ("decision_new", "decision_b")}
