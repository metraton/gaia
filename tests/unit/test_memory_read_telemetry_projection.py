"""gaia.store.writer.get_memory / list_memory -- v48 telemetry projection.

telemetria-de-uso-en-memoria-curada: the write side (record_memory_access)
already landed in an earlier increment (see test_memory_telemetry_writer.py).
This module covers the READ side that increment left unbuilt: before this,
neither get_memory nor list_memory projected injection_count/deliberate_count/
last_injected_at/last_deliberate_at, so no CLI verb could show them. Also
covers list_memory's new order_by (name/injection/deliberate, never a
combined score) and its class/status projection+filter (the carry_forward
gap: no verb projected class/status in aggregate, only gaia memory show did
for one row at a time).

Builds its own disposable DB from the REAL gaia/store/schema.sql, in pytest's
tmp_path -- same construction as test_memory_telemetry_writer.py.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "gaia" / "store" / "schema.sql"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaia.store import writer  # noqa: E402

_WORKSPACE = "me"


def _seed_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("INSERT INTO workspaces (name) VALUES (?)", (_WORKSPACE,))
    rows = [
        # name, type, class, status, injection_count, deliberate_count
        ("row_low_usage", "project", "log", None, 0, 0),
        ("row_mid_injection", "project", "anchor", None, 5, 1),
        ("row_high_injection", "project", "anchor", None, 9, 2),
        ("row_high_deliberate", "project", "thread", "carry_forward", 1, 7),
    ]
    for name, type_, cls, status, inj, delib in rows:
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, class, status, "
            "injection_count, deliberate_count, last_injected_at, "
            "last_deliberate_at, updated_at) "
            "VALUES (?, ?, ?, 'body', ?, ?, ?, ?, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (_WORKSPACE, name, type_, cls, status, inj, delib),
        )
    con.commit()
    con.close()


class TestGetMemoryProjectsTelemetry:
    def test_get_memory_returns_all_four_telemetry_fields(self, tmp_path: Path) -> None:
        db_path = tmp_path / "get.db"
        _seed_db(db_path)

        row = writer.get_memory(_WORKSPACE, "row_high_injection", db_path=db_path)

        assert row is not None
        assert row["injection_count"] == 9
        assert row["deliberate_count"] == 2
        assert row["last_injected_at"] == "2026-01-01T00:00:00Z"
        assert row["last_deliberate_at"] == "2026-01-01T00:00:00Z"

    def test_get_memory_never_bumps_either_counter(self, tmp_path: Path) -> None:
        """get_memory is a pure read -- it must never itself be the thing
        that increments a counter (only record_memory_access does)."""
        db_path = tmp_path / "no_bump.db"
        _seed_db(db_path)

        for _ in range(3):
            writer.get_memory(_WORKSPACE, "row_low_usage", db_path=db_path)

        row = writer.get_memory(_WORKSPACE, "row_low_usage", db_path=db_path)
        assert row["injection_count"] == 0
        assert row["deliberate_count"] == 0


class TestListMemoryProjectsTelemetryAndLifecycle:
    def test_list_memory_default_order_is_unchanged_by_name(self, tmp_path: Path) -> None:
        db_path = tmp_path / "default_order.db"
        _seed_db(db_path)

        rows = writer.list_memory(_WORKSPACE, db_path=db_path)

        assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)

    def test_list_memory_projects_class_status_and_counters(self, tmp_path: Path) -> None:
        db_path = tmp_path / "projection.db"
        _seed_db(db_path)

        rows = {r["name"]: r for r in writer.list_memory(_WORKSPACE, db_path=db_path)}

        assert rows["row_high_deliberate"]["class"] == "thread"
        assert rows["row_high_deliberate"]["status"] == "carry_forward"
        assert rows["row_high_deliberate"]["injection_count"] == 1
        assert rows["row_high_deliberate"]["deliberate_count"] == 7

    def test_order_by_injection_sorts_desc_by_injection_count_only(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "order_injection.db"
        _seed_db(db_path)

        rows = writer.list_memory(_WORKSPACE, order_by="injection", db_path=db_path)

        assert [r["name"] for r in rows] == [
            "row_high_injection", "row_mid_injection",
            "row_high_deliberate", "row_low_usage",
        ]

    def test_order_by_deliberate_sorts_desc_by_deliberate_count_only(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "order_deliberate.db"
        _seed_db(db_path)

        rows = writer.list_memory(_WORKSPACE, order_by="deliberate", db_path=db_path)

        assert [r["name"] for r in rows] == [
            "row_high_deliberate", "row_high_injection",
            "row_mid_injection", "row_low_usage",
        ]

    def test_invalid_order_by_raises_value_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bad_order.db"
        _seed_db(db_path)

        with pytest.raises(ValueError):
            writer.list_memory(_WORKSPACE, order_by="bogus", db_path=db_path)

    def test_filter_by_class_and_status(self, tmp_path: Path) -> None:
        db_path = tmp_path / "filter.db"
        _seed_db(db_path)

        rows = writer.list_memory(
            _WORKSPACE, class_="thread", status="carry_forward", db_path=db_path,
        )

        assert [r["name"] for r in rows] == ["row_high_deliberate"]
