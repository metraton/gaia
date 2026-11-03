"""`gaia context query` reads the store and can never change it, whatever the statement hides after its first word."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.context import _cmd_query  # noqa: E402


@pytest.fixture()
def scratch_db(tmp_path, monkeypatch):
    db = tmp_path / "scratch.db"
    monkeypatch.setenv("GAIA_DB", str(db))
    from gaia.store.writer import _connect

    con = _connect(db)
    con.executemany(
        "INSERT INTO workspaces (name, identity) VALUES (?, ?)",
        [("alpha", "alpha"), ("beta", "beta")],
    )
    con.commit()
    con.close()
    return db


def _count(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
    finally:
        con.close()


def _query(sql: str, capsys) -> tuple[int, list]:
    rc = _cmd_query(argparse.Namespace(sql=sql))
    out = capsys.readouterr().out
    return rc, json.loads(out) if rc == 0 else []


def _reads_work(capsys) -> None:
    rc, rows = _query("SELECT name FROM workspaces ORDER BY name", capsys)
    assert (rc, [r["name"] for r in rows]) == (0, ["alpha", "beta"])
    rc, rows = _query("PRAGMA table_info(workspaces)", capsys)
    assert rc == 0 and any(r["name"] == "identity" for r in rows)


@pytest.mark.parametrize("sql", [
    "WITH x AS (SELECT 1) DELETE FROM workspaces",
    "PRAGMA user_version = 99",
])
def test_write_behind_a_read_head_fails_and_keeps_rows(sql, scratch_db, capsys):
    _reads_work(capsys)
    before = _count(scratch_db)

    rc, _ = _query(sql, capsys)

    assert rc != 0
    assert _count(scratch_db) == before == 2
    con = sqlite3.connect(scratch_db)
    assert con.execute("PRAGMA user_version").fetchone()[0] != 99
    con.close()
    _reads_work(capsys)
