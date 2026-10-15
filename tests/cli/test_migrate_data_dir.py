"""`gaia migrate` resolves its database like every other gaia call.

The engine (`scripts/bootstrap_database.py`) reads GAIA_DB alone, so
`bin/cli/migrate.py::run` has to hand it the resolver's answer; without that,
GAIA_DATA_DIR was ignored and the plan or apply targeted `~/.gaia/gaia.db`.
`plan` writes nothing, which keeps the case safe to run against any home.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "bin"))
sys.path.insert(0, str(_REPO_ROOT))

from cli import migrate  # noqa: E402


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    return home


def _reported_database(result) -> str:
    for line in result.stdout.splitlines():
        if line.startswith("database: "):
            return line[len("database: "):]
    raise AssertionError(f"plan printed no database line:\n{result.stdout}")


def test_plan_follows_gaia_data_dir(tmp_path, monkeypatch, isolated_home):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

    result = migrate.run("plan", capture=True)

    assert result.returncode == 0, result.stderr
    assert _reported_database(result) == str(data_dir.resolve() / "gaia.db")


def test_gaia_db_outranks_gaia_data_dir(tmp_path, monkeypatch, isolated_home):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pinned = tmp_path / "pinned" / "gaia.db"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GAIA_DB", str(pinned))

    result = migrate.run("plan", capture=True)

    assert result.returncode == 0, result.stderr
    assert _reported_database(result) == str(pinned)


def test_explicit_db_flag_outranks_gaia_data_dir(tmp_path, monkeypatch, isolated_home):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    flagged = tmp_path / "flagged" / "gaia.db"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

    result = migrate.run("plan", db_path=str(flagged), capture=True)

    assert result.returncode == 0, result.stderr
    assert _reported_database(result) == str(flagged.resolve())


def test_default_is_home_gaia_db(isolated_home):
    result = migrate.run("plan", capture=True)

    assert result.returncode == 0, result.stderr
    assert _reported_database(result) == str(isolated_home / ".gaia" / "gaia.db")
