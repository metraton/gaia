"""The plugin's SessionStart keeps the database and seeds in step with the code.

The plugin channel never runs `gaia install`, so without this a plugin user
kept new code over an old database with nothing telling them. The chain cases
drive `reconcile` over a staged copy of the real migration engine, because the
chain a test needs (structure only, or reaching rows) cannot be taken from the
shipped migrations without breaking the next time one ships. The re-seed case
drives the real hook as Claude Code does, on the plugin channel.

Every case builds its own database and data dirs under tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
HOOK_PATH = _REPO / "hooks" / "session_start.py"
_SCHEMA_SQL = _REPO / "gaia" / "store" / "schema.sql"
for _p in (_REPO / "hooks", _REPO / "bin", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cli import migrate  # noqa: E402
from cli.doctor import EXPECTED_SCHEMA_VERSION  # noqa: E402

EXPECTED = EXPECTED_SCHEMA_VERSION


def _plugin_upgrade():
    from modules.session import plugin_upgrade

    return plugin_upgrade


def _database(path: Path, version: int, minimum: int | None = None) -> Path:
    """A user-shaped database: the real schema, one curated row, stamped at *version*."""
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("DELETE FROM schema_version")
    con.execute(
        "INSERT INTO schema_version (version, applied_at, description, min_code_version) "
        "VALUES (?, '2026-01-01T00:00:00Z', 'fixture', ?)",
        (version, minimum),
    )
    con.execute("INSERT INTO workspaces (name, identity) VALUES ('me', 'me')")
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body) "
        "VALUES ('me', 'kept', 'project', 'original', 'body')"
    )
    con.commit()
    con.close()
    return path


def _ledger(db: Path) -> int:
    with sqlite3.connect(db) as con:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def staged_engine(tmp_path, monkeypatch):
    """The real engine copied beside a migrations dir the test owns."""
    scripts = tmp_path / "pkg" / "scripts"
    (scripts / "migrations").mkdir(parents=True)
    for name in ("bootstrap_database.py", "migration_guard.py"):
        (scripts / name).write_text((_REPO / "scripts" / name).read_text(encoding="utf-8"))
    doctor = tmp_path / "pkg" / "bin" / "cli"
    doctor.mkdir(parents=True)
    (doctor / "doctor.py").write_text(f"EXPECTED_SCHEMA_VERSION = {EXPECTED}\n")
    monkeypatch.setattr(migrate, "ENGINE", scripts / "bootstrap_database.py")
    monkeypatch.setenv("SCHEMA_FILE", str(_SCHEMA_SQL))
    return scripts / "migrations"


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "gaia-data"
    path.mkdir()
    return path


def test_structure_only_chain_is_applied_after_a_backup(staged_engine, data_dir, tmp_path):
    (staged_engine / f"v{EXPECTED - 1}_to_v{EXPECTED}.sql").write_text(
        "CREATE TABLE IF NOT EXISTS plugin_probe (a TEXT);\n"
    )
    db = _database(data_dir / "gaia.db", EXPECTED - 1)
    marker = tmp_path / "seeded-version"
    marker.write_text("1.0.0")

    notice = _plugin_upgrade().reconcile(db, EXPECTED, marker, "1.0.0")

    assert _ledger(db) == EXPECTED
    assert len(list((data_dir / "backups").glob("*.db"))) == 1
    assert f"v{EXPECTED - 1}" in notice and f"v{EXPECTED}" in notice


def test_data_reaching_chain_is_not_applied_and_names_the_consent_command(
    staged_engine, data_dir, tmp_path
):
    (staged_engine / f"v{EXPECTED - 1}_to_v{EXPECTED}.sql").write_text(
        "UPDATE memory SET description = 'clobbered';\n"
    )
    db = _database(data_dir / "gaia.db", EXPECTED - 1)
    before = _digest(db)

    notice = _plugin_upgrade().reconcile(db, EXPECTED, tmp_path / "seeded-version", "1.0.0")

    assert _digest(db) == before
    assert _ledger(db) == EXPECTED - 1
    assert f"gaia migrate apply --consent-chain v{EXPECTED - 1}..v{EXPECTED}" in notice
    assert not (tmp_path / "seeded-version").exists()


def test_nothing_pending_costs_one_query_and_writes_nothing(data_dir, tmp_path, monkeypatch):
    db = _database(data_dir / "gaia.db", EXPECTED)
    marker = tmp_path / "seeded-version"
    marker.write_text("1.0.0")
    before = _digest(db)
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(sqlite3, "connect", traced)
    monkeypatch.setattr(migrate, "run", lambda *a, **k: pytest.fail("migrate must not run"))

    notice = _plugin_upgrade().reconcile(db, EXPECTED, marker, "1.0.0")

    assert notice == ""
    assert len(statements) == 1, statements
    assert _digest(db) == before
    assert not (data_dir / "backups").exists()


def test_database_ahead_outside_the_window_is_not_migrated(data_dir, tmp_path, monkeypatch):
    db = _database(data_dir / "gaia.db", EXPECTED + 1, minimum=EXPECTED + 1)
    marker = tmp_path / "seeded-version"
    marker.write_text("1.0.0")
    before = _digest(db)
    monkeypatch.setattr(migrate, "run", lambda *a, **k: pytest.fail("migrate must not run"))

    _plugin_upgrade().reconcile(db, EXPECTED, marker, "1.0.0")

    assert _digest(db) == before
    assert not (data_dir / "backups").exists()


def test_new_package_version_reseeds_without_registering_hooks(tmp_path):
    db = _database(tmp_path / "gaia.db", EXPECTED)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM agent_contract_permissions")
        con.execute("DELETE FROM surface_routing")
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    (plugin_data / "seeded-version").write_text("0.0.1")
    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "GAIA_DB": str(db),
            "HOME": str(tmp_path),
            "CLAUDE_PLUGIN_DATA": str(plugin_data),
            "CLAUDE_PLUGIN_ROOT": str(_REPO),
        }
    )

    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(
            {"hook_event_name": "SessionStart", "session_id": "s-plugin", "source": "startup"}
        ),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workspace),
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    package_version = json.loads((_REPO / "package.json").read_text())["version"]
    assert (plugin_data / "seeded-version").read_text().strip() == package_version
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM agent_contract_permissions").fetchone()[0] > 0
        assert con.execute("SELECT COUNT(*) FROM surface_routing").fetchone()[0] > 0
    settings = workspace / ".claude" / "settings.local.json"
    registered = json.loads(settings.read_text()).get("hooks", {}) if settings.exists() else {}
    assert registered == {}
