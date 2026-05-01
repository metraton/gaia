"""Tests for gaia.paths.resolver -- path resolution with env override."""

from pathlib import Path

import pytest

from gaia.paths import (
    cache_dir,
    data_dir,
    db_path,
    events_dir,
    logs_dir,
    snapshot_dir,
    state_dir,
    workspaces_dir,
)


# ---------------------------------------------------------------------------
# Default (no GAIA_DATA_DIR)
# ---------------------------------------------------------------------------

def test_data_dir_default_is_home_gaia(monkeypatch):
    """Without GAIA_DATA_DIR, data_dir() returns ~/.gaia."""
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    assert data_dir() == Path.home() / ".gaia"


def test_data_dir_default_is_absolute(monkeypatch):
    """data_dir() must return an absolute Path even on default."""
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    assert data_dir().is_absolute()


# ---------------------------------------------------------------------------
# GAIA_DATA_DIR override
# ---------------------------------------------------------------------------

def test_data_dir_with_override(monkeypatch, tmp_path):
    """GAIA_DATA_DIR overrides the default."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert data_dir() == tmp_path.resolve()


def test_data_dir_override_returns_absolute(monkeypatch, tmp_path):
    """Override result must be absolute."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert data_dir().is_absolute()


def test_resolver_reads_env_each_call(monkeypatch, tmp_path):
    """resolver functions must NOT cache — env changes between calls take effect."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    first = data_dir()
    other = tmp_path.parent / "alt-gaia"
    monkeypatch.setenv("GAIA_DATA_DIR", str(other))
    second = data_dir()
    assert first != second
    assert second == other.resolve()


# ---------------------------------------------------------------------------
# Sub-paths derived from data_dir()
# ---------------------------------------------------------------------------

def test_db_path_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert db_path() == tmp_path.resolve() / "gaia.db"


def test_snapshot_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert snapshot_dir() == tmp_path.resolve() / "snapshot"


def test_state_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert state_dir() == tmp_path.resolve() / "state"


def test_workspaces_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert workspaces_dir() == tmp_path.resolve() / "workspaces"


def test_logs_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert logs_dir() == tmp_path.resolve() / "logs"


def test_events_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert events_dir() == tmp_path.resolve() / "events"


def test_cache_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    assert cache_dir() == tmp_path.resolve() / "cache"


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

def test_paths_module_exports():
    """gaia.paths must export all documented public names."""
    import gaia.paths as paths_mod

    expected = {
        "data_dir",
        "db_path",
        "snapshot_dir",
        "state_dir",
        "workspaces_dir",
        "logs_dir",
        "events_dir",
        "cache_dir",
        "ensure_layout",
        "workspace_id",
    }
    for name in expected:
        assert hasattr(paths_mod, name), f"gaia.paths missing export: {name}"
