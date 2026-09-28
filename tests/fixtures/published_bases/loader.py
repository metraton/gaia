"""Rebuild a published-base database from its committed dump.

The dumps come from ``sqlite3.Connection.iterdump``, which registers an FTS5
table by writing ``sqlite_master`` directly and then inserts rows into it in
the same script -- before any connection has re-read the schema, so the
insert fails with "no such table". Those rows are redundant: the FTS index
lives in the shadow tables (``<name>_data``, ``_idx``, ...), which the dump
restores verbatim. So the loader skips inserts into virtual tables, and the
index is intact once a fresh connection reads the schema.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[2] / "scripts"))
import migration_guard  # noqa: E402

_VIRTUAL_TABLE = re.compile(
    r"^INSERT INTO sqlite_master\(type,name,tbl_name,rootpage,sql\)VALUES\('table','(\w+)'"
)


def load_base(version: int, db: Path) -> None:
    """Write the v``version`` published base into the new file ``db``."""
    script = (_HERE / f"v{version}.sql").read_text(encoding="utf-8")
    statements = migration_guard.split_statements(script)
    virtual = {m.group(1) for s in statements if (m := _VIRTUAL_TABLE.match(s))}
    skipped = tuple(f'INSERT INTO "{name}" ' for name in virtual)
    con = sqlite3.connect(db, isolation_level=None)
    try:
        for statement in statements:
            if not statement.startswith(skipped):
                con.execute(statement)
    finally:
        con.close()
