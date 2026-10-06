"""`gaia install` bootstraps and seeds one database: the one `gaia.paths.db_path` resolves.

The sandbox install gate isolates HOME, so a step that falls back to ~/.gaia
on its own writes a database no later gaia call reads, or fails outright
with "unable to open database file".
"""

import argparse
import sqlite3
import sys
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

import cli.install as install  # noqa: E402
from cli import migrate  # noqa: E402


def _scalar(db: Path, sql: str) -> int:
    with sqlite3.connect(db) as con:
        return con.execute(sql).fetchone()[0]


def _isolated_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    return home


def test_seeders_write_the_gaia_db_database_under_an_isolated_home(tmp_path, monkeypatch):
    """With no --db-path, both seeders succeed against GAIA_DB and never reach for ~/.gaia."""
    home = _isolated_home(tmp_path, monkeypatch)
    db = tmp_path / "sandbox-data" / "gaia.db"
    db.parent.mkdir()
    monkeypatch.setenv("GAIA_DB", str(db))
    assert migrate.run("apply", db_path=str(db), capture=True).returncode == 0

    permissions = install._seed_contract_permissions(db_path=None, quiet=True)
    routing = install._seed_surface_routing(db_path=None, quiet=True)

    assert permissions["action"] == "created", permissions
    assert routing["action"] == "created", routing
    assert _scalar(db, "SELECT COUNT(*) FROM agent_contract_permissions") > 0
    assert _scalar(db, "SELECT COUNT(*) FROM surface_routing") > 0
    assert not (home / ".gaia").exists()


def test_install_with_only_gaia_data_dir_bootstraps_and_seeds_the_same_file(tmp_path, monkeypatch):
    """GAIA_DATA_DIR alone: the schema'd, versioned database is <data dir>/gaia.db and it holds the seeds."""
    home = _isolated_home(tmp_path, monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    args = argparse.Namespace(skip_workspace=True, quiet=True, db_path=None)

    assert install.install_channels(args, ()) == 0

    db = data_dir / "gaia.db"
    assert _scalar(db, "SELECT COALESCE(MAX(version), 0) FROM schema_version") > 0
    assert _scalar(db, "SELECT COUNT(*) FROM surface_routing") > 0
    assert _scalar(db, "SELECT COUNT(*) FROM agent_contract_permissions") > 0
    assert not (home / ".gaia" / "gaia.db").exists()
