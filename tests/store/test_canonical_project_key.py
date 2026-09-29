"""The project_ref and initiative variants resolve to one canonical project key.

Memory rows name the same project three ways -- a git-common-dir path
(``/x/gaia/.git``), a bare name (``gaia``) and a remote identity
(``github.com/metraton/gaia``) -- plus the v32 ``initiative`` column. The key is
resolved over those columns as they are (decision D-a: no row is rewritten),
and every writer that knows a project anchor stores the canonical key.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_VARIANTS = {
    "legacy_path": "/x/gaia/.git",
    "legacy_bare": "gaia",
    "legacy_remote": "github.com/metraton/gaia",
}


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(data / "gaia.db"))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(key, raising=False)

    from gaia.paths import db_path
    from gaia.store.writer import _connect, upsert_memory

    path = db_path()
    con = _connect(path)
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.commit()
    finally:
        con.close()

    for name, ref in _VARIANTS.items():
        upsert_memory("me", name, type="project", body=f"note {name}", project_ref=ref)
    # Legacy rows were written before initiative existed: the key lives only
    # in project_ref.
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("UPDATE memory SET initiative = NULL WHERE name LIKE 'legacy_%'")
        raw.commit()
    finally:
        raw.close()
    return path


def _rows(db_path: Path, names) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        marks = ",".join("?" for _ in names)
        return con.execute(
            f"SELECT * FROM memory WHERE name IN ({marks}) ORDER BY name",
            tuple(names),
        ).fetchall()
    finally:
        con.close()


def _row(db_path: Path, name: str) -> dict:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT project_ref, initiative FROM memory WHERE name = ?", (name,)
        ).fetchone())
    finally:
        con.close()


@pytest.mark.parametrize(
    ("project_ref", "initiative"),
    [
        ("/x/gaia/.git", None),
        ("gaia", None),
        ("github.com/metraton/gaia", None),
        ("git@github.com:metraton/gaia.git", None),
        (None, "gaia"),
        (None, "Gaia"),
    ],
)
def test_every_variant_resolves_to_one_key(project_ref, initiative):
    from gaia.store.writer import canonical_project_key

    assert canonical_project_key(project_ref, initiative) == "gaia"


def test_explicit_initiative_outranks_the_anchor():
    from gaia.store.writer import canonical_project_key

    assert canonical_project_key("/x/gaia/.git", "century") == "century"
    assert canonical_project_key(None, None) is None


def test_stored_variants_resolve_without_rewriting_rows(db):
    from gaia.store.writer import canonical_project_key

    before = _rows(db, _VARIANTS)
    keys = {
        name: canonical_project_key(**_row(db, name)) for name in _VARIANTS
    }
    assert set(keys.values()) == {"gaia"}, keys
    assert _rows(db, _VARIANTS) == before


def test_new_writes_store_the_canonical_key_and_leave_old_rows_alone(db):
    from gaia.store.writer import (
        close_session_memory, reanchor_memory_project_ref, upsert_memory,
    )

    before = _rows(db, _VARIANTS)

    upsert_memory(
        "me", "fresh_upsert", type="project", body="b",
        project_ref="github.com/metraton/gaia",
    )
    close_session_memory(
        "me",
        {
            "resumen": {"name": "fresh_close", "type": "project",
                        "description": "d", "body": "b"},
            "pendientes": [{"name": "fresh_thread", "description": "d", "body": "b"}],
        },
        project_ref="/x/gaia/.git",
    )
    upsert_memory("me", "fresh_reanchor", type="project", body="b")
    reanchor_memory_project_ref("me", "fresh_reanchor", "gaia")

    for name in ("fresh_upsert", "fresh_close", "fresh_thread", "fresh_reanchor"):
        assert _row(db, name)["initiative"] == "gaia", name
    assert _rows(db, _VARIANTS) == before
