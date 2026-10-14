"""The CLI resolves itself on a Windows-shaped host: launchers, npm shims, doctor, convergence.

Every layout below is the one npm and the Windows Python installers produce,
built under tmp_path so the probes run on Linux CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for entry in (REPO_ROOT / "bin", REPO_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from cli import _converge, doctor, install  # noqa: E402

import gaia.hook_shell as hook_shell  # noqa: E402

PACKAGE_VERSION = "9.9.9"
STORE_STUB = r"C:\Users\jorge\AppData\Local\Microsoft\WindowsApps\python3.exe"
REAL_PYTHON = r"C:\Users\jorge\AppData\Local\Programs\Python\Python313\python.exe"


def _npm_prefix(tmp_path: Path) -> Path:
    """%APPDATA%\\npm as `npm install -g` leaves it: shims at the root, the package under node_modules."""
    appdata = tmp_path / "AppData" / "Roaming"
    prefix = appdata / "npm"
    package = prefix / "node_modules" / "@jaguilar87" / "gaia"
    (package / "bin").mkdir(parents=True)
    (package / "bin" / "gaia").write_text("#!/usr/bin/env python3\n")
    (package / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": PACKAGE_VERSION}))
    (prefix / "gaia.cmd").write_text('@"%~dp0\\node_modules\\@jaguilar87\\gaia\\bin\\gaia" %*\n')
    (appdata / "package.json").write_text(json.dumps({"name": "unrelated", "version": "0.0.1"}))
    return prefix


def test_npm_cmd_shim_on_path_resolves_to_the_installed_package(tmp_path, monkeypatch):
    prefix = _npm_prefix(tmp_path)
    monkeypatch.setattr(_converge.shutil, "which", lambda name: str(prefix / "gaia.cmd"))

    surface = _converge.inspect_path_gaia(PACKAGE_VERSION)

    assert surface["state"] == _converge.STATE_ALIGNED, surface
    assert surface["version"] == PACKAGE_VERSION


def test_global_npm_finds_the_windows_cmd_shim(tmp_path):
    surface = _converge.inspect_global_npm(_npm_prefix(tmp_path), PACKAGE_VERSION)

    assert surface["state"] == _converge.STATE_ALIGNED, surface


def test_windows_npm_prefix_honours_the_configured_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(tmp_path / "nodejs"))

    assert install._npm_global_prefix() == tmp_path / "nodejs"


def test_plugin_channel_settings_without_hooks_is_not_stale(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {}}))

    surface = _converge.inspect_hooks_settings(tmp_path, plugin_channel=True)

    assert surface["state"] == _converge.STATE_ALIGNED, surface
    assert _converge.inspect_hooks_settings(tmp_path)["state"] == _converge.STATE_STALE


def test_windows_launchers_run_the_installing_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    gaia_bin = tmp_path / "pkg" / "bin" / "gaia"

    install._install_path_launcher(link_path=tmp_path / "bin" / "gaia", workspace=tmp_path, gaia_bin=gaia_bin)

    cmd = (tmp_path / "bin" / "gaia.cmd").read_text()
    ps1 = (tmp_path / "bin" / "gaia.ps1").read_text(encoding="utf-8-sig")
    assert f'"{sys.executable}" "{gaia_bin}" %*' in cmd
    assert f'& "{sys.executable}" "{gaia_bin}" @args' in ps1


def _probe_python(monkeypatch, on_path: dict) -> None:
    """Resolve names through *on_path*; the Store stub exits 9009 the way the real alias does."""
    monkeypatch.setattr(hook_shell.shutil, "which", lambda name: on_path.get(name))

    def run(args, **_kwargs):
        code = 9009 if args[0] == STORE_STUB else 0
        return subprocess.CompletedProcess(args, code, "", "")

    monkeypatch.setattr(hook_shell, "run_text", run)


def test_r2_store_alias_answering_python3_is_skipped_for_the_real_python(monkeypatch):
    _probe_python(monkeypatch, {"python3": STORE_STUB, "python": REAL_PYTHON})

    assert hook_shell.hook_python() == [REAL_PYTHON]
    result = doctor.check_hook_python()
    assert result["severity"] == "pass" and REAL_PYTHON in result["detail"]


def test_doctor_errors_when_only_the_store_alias_answers(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    _probe_python(monkeypatch, {"python3": STORE_STUB})

    result = doctor.check_hook_python()

    assert result["severity"] == "error"
    assert "App execution aliases" in result["fix"]


def test_doctor_names_the_sh_hooks_start_with(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hook_shell.shutil, "which", lambda name: r"C:\Program Files\Git\usr\bin\sh.exe")

    assert r"C:\Program Files\Git\usr\bin\sh.exe" in doctor.check_hook_shell()["detail"]


def test_doctor_reports_the_gaia_that_wins_on_path(tmp_path, monkeypatch):
    prefix = _npm_prefix(tmp_path)
    monkeypatch.setattr(_converge.shutil, "which", lambda name: str(prefix / "gaia.cmd"))
    monkeypatch.setattr(doctor, "_package_root", lambda: prefix / "node_modules" / "@jaguilar87" / "gaia")

    result = doctor.check_path_gaia()

    assert result["severity"] == "pass", result
    assert str(prefix / "gaia.cmd") in result["detail"]


def test_doctor_warns_when_another_gaia_wins_on_path_with_a_windows_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    prefix = _npm_prefix(tmp_path)
    monkeypatch.setattr(_converge.shutil, "which", lambda name: str(prefix / "gaia.cmd"))
    monkeypatch.setattr(doctor, "_package_root", lambda: tmp_path / "checkout")

    result = doctor.check_path_gaia()

    assert result["severity"] == "warning", result
    assert "[Environment]::SetEnvironmentVariable" in result["fix"]


def _checkout(tmp_path: Path, version: str) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": version}))
    return checkout


def test_doctor_reports_a_checkout_beside_its_installed_copy_as_info(tmp_path, monkeypatch):
    prefix = _npm_prefix(tmp_path)
    checkout = _checkout(tmp_path, PACKAGE_VERSION)
    monkeypatch.setattr(_converge.shutil, "which", lambda name: str(prefix / "gaia.cmd"))
    monkeypatch.setattr(doctor, "_package_root", lambda: checkout)

    result = doctor.check_path_gaia()

    assert result["severity"] == "info", result
    assert PACKAGE_VERSION in result["detail"]


def test_doctor_warns_when_the_path_gaia_is_another_version(tmp_path, monkeypatch):
    prefix = _npm_prefix(tmp_path)
    checkout = _checkout(tmp_path, "10.0.0")
    monkeypatch.setattr(_converge.shutil, "which", lambda name: str(prefix / "gaia.cmd"))
    monkeypatch.setattr(doctor, "_package_root", lambda: checkout)

    assert doctor.check_path_gaia()["severity"] == "warning"


def _write_launcher(tmp_path, monkeypatch, python: str) -> Path:
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    monkeypatch.setattr(install.sys, "executable", python)
    install._install_path_launcher(link_path=tmp_path / "bin" / "gaia", workspace=tmp_path,
                                   gaia_bin=tmp_path / "pkg" / "bin" / "gaia")
    monkeypatch.setattr(doctor, "_WINDOWS_LAUNCHER", tmp_path / "bin" / "gaia.cmd")
    return tmp_path / "bin" / "gaia.cmd"


def test_doctor_passes_when_the_launcher_python_still_runs(tmp_path, monkeypatch):
    _write_launcher(tmp_path, monkeypatch, sys.executable)

    assert doctor.check_launcher_python()["severity"] == "pass"


def test_doctor_warns_when_the_launcher_python_was_removed(tmp_path, monkeypatch):
    removed = str(tmp_path / "Python312" / "python.exe")
    _write_launcher(tmp_path, monkeypatch, removed)

    result = doctor.check_launcher_python()

    assert result["severity"] == "warning", result
    assert removed in result["detail"]
    assert "gaia install --path" in result["fix"]


def test_doctor_fix_commands_quote_paths_for_windows_shells(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    workspace = Path(r"C:\Users\Jorge Aguilar\ws")

    fix = doctor._hook_fix(workspace, {"plugin": False, "npm": workspace / "node_modules"})

    assert f'--workspace "{workspace}"' in fix
    assert "'" not in fix
