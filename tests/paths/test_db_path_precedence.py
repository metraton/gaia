"""The database-path precedence ladder, and the decoy-database defect it closes.

Two resolvers used to answer "which gaia.db?" independently and disagree:
``bin/gaia::_resolve_db_path`` honored ``GAIA_DB`` (and BOOTSTRAPPED a complete
database at whatever it returned), while ``gaia.paths.resolver.db_path`` -- the
single funnel every store read and write passes through via
``gaia.store.writer._db_path`` -- read only ``GAIA_DATA_DIR`` and ignored
``GAIA_DB`` entirely.

The measured consequence: ``GAIA_DB=<x> gaia <subcommand>`` fabricated a fully
schema'd database at ``<x>`` and then read and wrote the REAL user database,
reporting success. The fabricated database is what made it undetectable -- anyone
auditing afterwards found it populated with schema and concluded the isolation
had worked.

These tests pin the property that replaces it: asking for isolation either
isolates, or says so loudly. Never both-and-neither. They cover the ladder
(``GAIA_DB`` > ``GAIA_DATA_DIR`` > ``~/.gaia``), the file-vs-root scope of the
two variables, the conflict warning, a real store write landing in the isolated
file, and the agreement between the two resolvers that must never drift apart
again.

Matchable by ``pytest tests/paths/test_db_path_precedence.py -q``.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from gaia.paths import data_dir, db_path, evidence_dir, scratch_dir
from gaia.paths import resolver as resolver_mod

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"

# Every env combination the ladder distinguishes, as (GAIA_DB, GAIA_DATA_DIR)
# markers resolved against tmp_path by the helper below. Shared by the ladder
# tests and by the two-resolver agreement test so both exercise the SAME matrix
# -- the drift being guarded against is precisely one resolver handling a
# combination the other does not.
_ENV_MATRIX = [
    ("db-only", "sandbox/gaia.db", None),
    ("data-dir-only", None, "sandbox"),
    ("both-agreeing", "sandbox/gaia.db", "sandbox"),
    ("both-conflicting", "elsewhere/other.db", "sandbox"),
    ("neither", None, None),
]


@pytest.fixture(autouse=True)
def _reset_conflict_warnings():
    """Clear the one-warning-per-conflict memo so each test observes its own.

    ``_WARNED_CONFLICTS`` is module state deliberately kept across calls (a
    resolver on the path of every read must not reprint per access); without
    this reset, whichever conflict test ran first would silence the others.
    """
    resolver_mod._WARNED_CONFLICTS.clear()
    yield
    resolver_mod._WARNED_CONFLICTS.clear()


def _apply_env(monkeypatch, tmp_path, db_rel, data_rel):
    """Set/clear the two variables for one matrix row; return expected values."""
    if db_rel is None:
        monkeypatch.delenv("GAIA_DB", raising=False)
    else:
        target = tmp_path / db_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("GAIA_DB", str(target))
    if data_rel is None:
        monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    else:
        root = tmp_path / data_rel
        root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("GAIA_DATA_DIR", str(root))


# ---------------------------------------------------------------------------
# The ladder: GAIA_DB > GAIA_DATA_DIR > ~/.gaia
# ---------------------------------------------------------------------------

def test_gaia_db_alone_is_honored(monkeypatch, tmp_path):
    """GAIA_DB with no GAIA_DATA_DIR resolves the database to that exact file.

    This is the case the defect got wrong: the CI workflow and
    bin/validate-sandbox.sh both set GAIA_DB alone, and the store ignored it.
    """
    target = tmp_path / "sandbox" / "isolated.db"
    target.parent.mkdir(parents=True)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("GAIA_DB", str(target))
    assert db_path() == target.resolve()


def test_gaia_db_outranks_gaia_data_dir(monkeypatch, tmp_path):
    """When both are set and disagree, GAIA_DB wins -- explicitly, not by accident.

    An implicit precedence is the seed of the same class of defect, so the
    winner is pinned here rather than left to whichever branch runs first.
    """
    db_target = tmp_path / "elsewhere" / "other.db"
    db_target.parent.mkdir(parents=True)
    data_root = tmp_path / "sandbox"
    data_root.mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("GAIA_DB", str(db_target))

    assert db_path() == db_target.resolve()
    assert db_path() != data_root.resolve() / "gaia.db"


def test_gaia_data_dir_used_when_gaia_db_absent(monkeypatch, tmp_path):
    """Without GAIA_DB, the database is <GAIA_DATA_DIR>/gaia.db (unchanged)."""
    data_root = tmp_path / "sandbox"
    data_root.mkdir(parents=True)
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    assert db_path() == data_root.resolve() / "gaia.db"


def test_default_when_neither_is_set(monkeypatch):
    """With neither variable set, the database is ~/.gaia/gaia.db (unchanged)."""
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    assert db_path() == Path.home() / ".gaia" / "gaia.db"


def test_gaia_db_expands_user(monkeypatch):
    """GAIA_DB accepts a ~-relative path, like every other Gaia path input."""
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("GAIA_DB", "~/.gaia/some-sandbox.db")
    assert db_path() == (Path.home() / ".gaia" / "some-sandbox.db").resolve()


# ---------------------------------------------------------------------------
# Scope: GAIA_DB moves the FILE, GAIA_DATA_DIR moves the ROOT
# ---------------------------------------------------------------------------

def test_gaia_db_is_file_scoped_not_root_scoped(monkeypatch, tmp_path):
    """GAIA_DB relocates the database only -- never the sibling directories.

    Collapsing the two variables would silently move scratch/evidence/logs for
    every caller that sets GAIA_DB alone (CI, the sandbox validator) and expects
    them to stay put.
    """
    target = tmp_path / "sandbox" / "isolated.db"
    target.parent.mkdir(parents=True)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("GAIA_DB", str(target))

    assert db_path() == target.resolve()
    assert data_dir() == Path.home() / ".gaia"
    assert scratch_dir() == Path.home() / ".gaia" / "scratch"
    assert evidence_dir() == Path.home() / ".gaia" / "evidence"


# ---------------------------------------------------------------------------
# The conflict is announced; the established idiom stays quiet
# ---------------------------------------------------------------------------

def test_conflicting_env_warns_and_names_the_winner(monkeypatch, tmp_path, capsys):
    """A genuine disagreement warns on stderr, naming both the winner and loser.

    Silence here is exactly what let the original defect go unnoticed, so the
    ambiguous configuration must announce which of the two requests is not being
    honored.
    """
    db_target = tmp_path / "elsewhere" / "other.db"
    db_target.parent.mkdir(parents=True)
    data_root = tmp_path / "sandbox"
    data_root.mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("GAIA_DB", str(db_target))

    db_path()
    err = capsys.readouterr().err
    assert "GAIA_DB" in err
    assert "GAIA_DATA_DIR" in err
    assert str(db_target.resolve()) in err
    assert str(data_root.resolve() / "gaia.db") in err


def test_no_warning_when_both_agree(monkeypatch, tmp_path, capsys):
    """Setting both at the same file is the tree's own idiom and must stay quiet.

    ``tests/ci/windows_smoke.py`` points the store at a data dir and the
    bootstrapper at the same file on purpose; warning about that would train
    everyone to ignore the warning that matters.
    """
    data_root = tmp_path / "sandbox"
    data_root.mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("GAIA_DB", str(data_root / "gaia.db"))

    db_path()
    assert capsys.readouterr().err == ""


def test_gaia_db_alone_does_not_warn(monkeypatch, tmp_path, capsys):
    """GAIA_DB with no GAIA_DATA_DIR is unambiguous -- nothing to warn about."""
    target = tmp_path / "sandbox" / "isolated.db"
    target.parent.mkdir(parents=True)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("GAIA_DB", str(target))

    db_path()
    assert capsys.readouterr().err == ""


def test_conflict_warns_once_per_process(monkeypatch, tmp_path, capsys):
    """The warning is memoized: once per distinct conflict, not once per access.

    db_path() is called on every store read and write; a per-access warning
    would bury real output.
    """
    db_target = tmp_path / "elsewhere" / "other.db"
    db_target.parent.mkdir(parents=True)
    data_root = tmp_path / "sandbox"
    data_root.mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("GAIA_DB", str(db_target))

    db_path()
    first = capsys.readouterr().err
    db_path()
    db_path()
    assert first != ""
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# The anti-decoy property: a real store write lands in the isolated file
# ---------------------------------------------------------------------------

def test_store_write_lands_in_the_isolated_database(monkeypatch, tmp_path):
    """A genuine store connection materializes the ISOLATED file, not the default.

    The store resolves its connection through ``writer._db_path`` ->
    ``gaia.paths.db_path``, so this is the end-to-end statement of the property
    the defect violated: under GAIA_DB the bytes land in the requested file, and
    the data_dir default is never created alongside it as a decoy.
    """
    from gaia.store import writer

    data_root = tmp_path / "home_gaia"
    data_root.mkdir(parents=True)
    isolated = tmp_path / "sandbox" / "isolated.db"
    isolated.parent.mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("GAIA_DB", str(isolated))

    assert writer._db_path() == isolated.resolve()

    conn = writer._connect()
    try:
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert isolated.exists(), "the requested database was never written"
    assert tables > 0, "the isolated database has no schema"
    assert not (data_root / "gaia.db").exists(), (
        "a decoy database was created at the GAIA_DATA_DIR default while the "
        "isolated file was in use -- this is the defect being guarded against"
    )


def test_store_read_sees_only_the_isolated_database(monkeypatch, tmp_path):
    """A row written under GAIA_DB is invisible to the GAIA_DATA_DIR default.

    The complement of the write test: isolation that leaks on the read side is
    not isolation.
    """
    from gaia.store import writer

    data_root = tmp_path / "home_gaia"
    data_root.mkdir(parents=True)
    isolated = tmp_path / "sandbox" / "isolated.db"
    isolated.parent.mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("GAIA_DB", str(isolated))

    conn = writer._connect()
    try:
        conn.execute("CREATE TABLE _isolation_probe (marker TEXT)")
        conn.execute("INSERT INTO _isolation_probe VALUES ('isolated')")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.delenv("GAIA_DB", raising=False)
    default_db = data_root / "gaia.db"
    assert writer._db_path() == default_db.resolve()

    if default_db.exists():
        probe = sqlite3.connect(str(default_db))
        try:
            leaked = probe.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='_isolation_probe'"
            ).fetchone()[0]
        finally:
            probe.close()
        assert leaked == 0, "the isolated write leaked into the default database"


# ---------------------------------------------------------------------------
# The two resolvers must agree -- this is the structural fix
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gaia_bin():
    """Load the suffix-less ``bin/gaia`` dispatcher as a module.

    ``spec_from_file_location`` returns None for a path with no recognized
    suffix, so an explicit SourceFileLoader is required (same approach as
    tests/cli/test_gaia_bin_json_dispatch.py).
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    if str(_BIN_DIR) not in sys.path:
        sys.path.insert(0, str(_BIN_DIR))
    loader = SourceFileLoader("_gaia_bin_db_path", str(_BIN_DIR / "gaia"))
    spec = importlib.util.spec_from_loader("_gaia_bin_db_path", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("label", "db_rel", "data_rel"),
    _ENV_MATRIX,
    ids=[row[0] for row in _ENV_MATRIX],
)
def test_bin_gaia_resolves_the_same_database_as_the_store(
    gaia_bin, monkeypatch, tmp_path, label, db_rel, data_rel
):
    """bin/gaia must resolve the database to exactly what the store resolves.

    bin/gaia CREATES a database at whatever ``_resolve_db_path`` returns, so any
    divergence from the store's resolver is a decoy factory by construction --
    which is what the original defect was. Pinned across the whole env matrix
    because the drift that caused it was one resolver handling a combination the
    other did not.
    """
    _apply_env(monkeypatch, tmp_path, db_rel, data_rel)
    assert gaia_bin._resolve_db_path() == db_path()


def test_bin_gaia_bootstrap_target_is_the_store_database(
    gaia_bin, monkeypatch, tmp_path
):
    """Under GAIA_DB alone, bin/gaia would bootstrap the file the store then uses.

    The original defect in one assertion: bin/gaia targeted $GAIA_DB while the
    store targeted the data_dir default, so the bootstrapped database and the
    used database were different files.
    """
    isolated = tmp_path / "sandbox" / "isolated.db"
    isolated.parent.mkdir(parents=True)
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("GAIA_DB", str(isolated))

    assert gaia_bin._resolve_db_path() == isolated.resolve()
    assert gaia_bin._resolve_db_path() == db_path()
