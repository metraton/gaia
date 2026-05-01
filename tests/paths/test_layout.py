"""Tests for gaia.paths.layout -- directory creation with mode 0700."""

import os
import stat

from gaia.paths import (
    cache_dir,
    data_dir,
    ensure_layout,
    events_dir,
    logs_dir,
    workspaces_dir,
)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_ensure_layout_creates_all_directories(monkeypatch, tmp_path):
    """ensure_layout creates the full canonical tree."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-test"))
    ensure_layout()
    assert data_dir().is_dir()
    assert workspaces_dir().is_dir()
    assert logs_dir().is_dir()
    assert events_dir().is_dir()
    assert cache_dir().is_dir()


def test_ensure_layout_mode_is_0700(monkeypatch, tmp_path):
    """Every created directory must have mode 0700."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-test"))
    ensure_layout()
    for d in (data_dir(), workspaces_dir(), logs_dir(), events_dir(), cache_dir()):
        assert _mode(d) == 0o700, f"{d} has mode {oct(_mode(d))}, expected 0o700"


def test_ensure_layout_idempotent(monkeypatch, tmp_path):
    """Calling ensure_layout twice must not raise and not change permissions."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-test"))
    ensure_layout()
    first_modes = {d: _mode(d) for d in (data_dir(), workspaces_dir(), logs_dir(), events_dir(), cache_dir())}
    ensure_layout()  # Second call must not raise
    second_modes = {d: _mode(d) for d in (data_dir(), workspaces_dir(), logs_dir(), events_dir(), cache_dir())}
    assert first_modes == second_modes


def test_ensure_layout_forces_mode_even_if_pre_existing_with_wrong_perms(monkeypatch, tmp_path):
    """If a directory pre-exists with mode 0755, ensure_layout must chmod it to 0700."""
    target = tmp_path / "gaia-test"
    target.mkdir(mode=0o755)
    assert _mode(target) == 0o755
    monkeypatch.setenv("GAIA_DATA_DIR", str(target))
    ensure_layout()
    assert _mode(target) == 0o700
