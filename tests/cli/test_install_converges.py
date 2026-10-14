"""A bare `gaia install` converges a 5.3 or 5.4 install in one run (task 933, D140).

The 5.3 and 5.4 databases are the published bases v37 (v5.3.0) and v51
(v5.4.0-rc.1). Every case works under tmp_path; the user's ~/.gaia is never read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA = _REPO_ROOT / "bin" / "gaia"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "published_bases"
for _path in (_REPO_ROOT / "scripts", _FIXTURES, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    sys.path.insert(0, str(_path))

import bootstrap_database  # noqa: E402
import migration_guard  # noqa: E402
from cli import doctor, install  # noqa: E402
from loader import load_base  # noqa: E402

_BACKUP_LINE = re.compile(r"backup written and verified: (\S+\.db)")


def _expected_version() -> int:
    text = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    return int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.M).group(1))


def _env(tmp: Path, db: Path) -> dict:
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "GAIA_DB": str(db),
            "GAIA_DATA_DIR": str(tmp / "data")}


def _bare_install(tmp: Path, db: Path, workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GAIA), "install", "--workspace", str(workspace), "--db-path", str(db)],
        env=_env(tmp, db), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )


def _census(db: Path) -> dict[str, int]:
    con = sqlite3.connect(db)
    try:
        return migration_guard.take_census(con)
    finally:
        con.close()


def _ledger(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("base", (37, 51), ids=("gaia-5.3", "gaia-5.4"))
def test_bare_install_migrates_after_a_verified_backup_and_a_second_run_changes_nothing(tmp_path, base):
    db = tmp_path / "data" / "gaia.db"
    db.parent.mkdir()
    load_base(base, db)
    before = _census(db)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    first = _bare_install(tmp_path, db, workspace)

    assert first.returncode == 0, first.stdout + first.stderr
    assert _ledger(db) == _expected_version()
    backup = Path(_BACKUP_LINE.search(first.stdout + first.stderr).group(1))
    con = sqlite3.connect(backup)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert migration_guard.take_census(con) == before
    finally:
        con.close()
    settings = (workspace / ".claude" / "settings.local.json").read_bytes()

    second = _bare_install(tmp_path, db, workspace)

    assert second.returncode == 0, second.stdout + second.stderr
    assert not _BACKUP_LINE.search(second.stdout + second.stderr)
    assert (workspace / ".claude" / "settings.local.json").read_bytes() == settings
    assert sorted(p.name for p in (tmp_path / "home").iterdir() if p.name.startswith(".")) \
        in ([], [".claude"], [".gaia"], [".claude", ".gaia"])


def test_bare_install_refuses_a_newer_database_and_leaves_it_byte_identical(tmp_path):
    db = tmp_path / "data" / "gaia.db"
    db.parent.mkdir()
    load_base(57, db)
    newer = _expected_version() + 5
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE schema_version ADD COLUMN min_code_version INTEGER")
    con.execute("INSERT INTO schema_version (version, applied_at, description, min_code_version) "
                "VALUES (?, '2030-01-01T00:00:00Z', 'future', ?)", (newer, newer))
    con.commit()
    con.close()
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = _bare_install(tmp_path, db, workspace)

    assert result.returncode != 0
    assert "is NEWER" in result.stderr
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest


def test_a_failure_mid_chain_leaves_the_original_after_the_backup(tmp_path, monkeypatch, capsys):
    db = tmp_path / "gaia.db"
    load_base(51, db)
    before, ledger = _census(db), _ledger(db)
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    migrations = tmp_path / "migrations"
    shutil.copytree(bootstrap_database.MIG_DIR, migrations)
    expected = _expected_version()
    (migrations / f"v{expected - 1}_to_v{expected}.sql").write_text("CREATE TABLE schema_version (x);\n")
    monkeypatch.setattr(bootstrap_database, "GAIA_DB", db)
    monkeypatch.setattr(bootstrap_database, "MIG_DIR", migrations)

    rc = bootstrap_database.main(["--apply-after-backup"])

    out = capsys.readouterr()
    assert rc == 1
    assert "rolled back" in out.err
    assert Path(_BACKUP_LINE.search(out.out).group(1)).is_file()
    assert _ledger(db) == ledger
    assert _census(db) == before
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest


def test_a_failure_on_an_unversioned_database_restores_the_verified_backup(tmp_path, monkeypatch, capsys):
    db = tmp_path / "gaia.db"
    load_base(51, db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("DELETE FROM schema_version")
    con.commit()
    con.close()
    before = _census(db)
    migrations = tmp_path / "migrations"
    shutil.copytree(bootstrap_database.MIG_DIR, migrations)
    expected = _expected_version()
    (migrations / f"v{expected - 1}_to_v{expected}.sql").write_text("CREATE TABLE schema_version (x);\n")
    monkeypatch.setattr(bootstrap_database, "GAIA_DB", db)
    monkeypatch.setattr(bootstrap_database, "MIG_DIR", migrations)

    rc = bootstrap_database.main(["--apply-after-backup"])

    out = capsys.readouterr()
    assert rc == 1
    assert "restored from" in out.err
    assert _census(db) == before
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        con.close()


_5X_CMD = ('@echo off\nREM gaia -- workspace-bound launcher (generated by `gaia install`)\n'
           'REM Re-run install from another workspace to retarget. Exports GAIA_WORKSPACE_PATH\n'
           'REM so `gaia doctor` resolves this workspace instead of deriving from __file__.\n'
           'set "GAIA_WORKSPACE_PATH={ws}"\npython "{bin}" %*\n')
_5X_PS1 = ('# gaia -- workspace-bound launcher (generated by `gaia install`)\n'
           '# Re-run install from another workspace to retarget. Exports GAIA_WORKSPACE_PATH\n'
           '# so `gaia doctor` resolves this workspace instead of deriving from __file__.\n'
           '$env:GAIA_WORKSPACE_PATH = "{ws}"\n& python "{bin}" @args\nexit $LASTEXITCODE\n')


def test_the_5x_windows_launchers_are_recognized_and_replaced_and_a_foreign_one_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    workspace = tmp_path / "ws"
    link = tmp_path / "bin" / "gaia"
    link.parent.mkdir()
    old_bin = r"C:\Users\u\AppData\Roaming\npm\node_modules\@jaguilar87\gaia\bin\gaia"
    link.with_suffix(".cmd").write_text(_5X_CMD.format(ws=workspace, bin=old_bin).replace("\n", "\r\n"))
    link.with_suffix(".ps1").write_text(_5X_PS1.format(ws=workspace, bin=old_bin))
    new_bin = tmp_path / "pkg" / "bin" / "gaia"

    assert install._legacy_launcher(workspace, str(link))
    install._install_windows_launchers(link, workspace, new_bin)

    assert link.with_suffix(".cmd").read_text() == install._render_cmd_launcher(workspace, new_bin)
    assert link.with_suffix(".ps1").read_text(encoding="utf-8-sig") == install._render_ps1_launcher(workspace, new_bin)
    link.with_suffix(".cmd").write_text("@echo off\nREM mine\n")
    install._install_windows_launchers(link, workspace, new_bin)
    assert link.with_suffix(".cmd").read_text() == "@echo off\nREM mine\n"
    assert not install._legacy_launcher(tmp_path / "other", str(link))


def _seed_legacy_workspace(workspace: Path) -> None:
    claude = workspace / ".claude"
    (claude / "logs").mkdir(parents=True)
    (claude / "logs" / "hooks.log").write_text("5.4 log\n")
    (claude / "session" / "active").mkdir(parents=True)
    (claude / "session" / "active" / "s1.json").write_text("{}\n")
    (claude / "cache" / "approvals").mkdir(parents=True)
    (claude / "cache" / "approvals" / "grant-s1-1718000000000-abcd1234.json").write_text(json.dumps({
        "session_id": "s1", "approved_verbs": ["push"], "approved_scope": "git push",
        "scope_type": "semantic_signature", "scope_signature": {}, "granted_at": 1718000000.0,
        "ttl_minutes": 5, "used": True, "confirmed": True, "multi_use": False,
    }))


def test_only_files_shaped_like_5x_grants_are_removed_from_the_grant_cache(tmp_path):
    from cli import _legacy_cleanup

    workspace = tmp_path / "ws"
    _seed_legacy_workspace(workspace)
    cache = workspace / ".claude" / "cache" / "approvals"
    (cache / "pending-n1.json").write_text(json.dumps(
        {"nonce": "n1", "session_id": "s1", "command": "git push", "scope_signature": {}, "timestamp": 1.0}))
    (cache / "pending-index-s1.json").write_text(json.dumps({"session_id": "s1", "latest_nonce": "n1", "entries": []}))
    kept = {"grant-mine.json": "{}", "pending-notes.json": '{"nonce": "n2"}', "settings.json": '{"a": 1}'}
    for name, body in kept.items():
        (cache / name).write_text(body)

    result = _legacy_cleanup.remove_stale_grant_cache(workspace)

    assert "removed 3 " in result["details"]
    assert sorted(p.name for p in cache.iterdir()) == sorted(kept)


def test_bare_install_merges_5x_data_dirs_and_drops_the_grant_cache(tmp_path):
    db = tmp_path / "data" / "gaia.db"
    db.parent.mkdir()
    load_base(51, db)
    workspace = tmp_path / "ws"
    _seed_legacy_workspace(workspace)

    result = _bare_install(tmp_path, db, workspace)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "data" / "logs" / "hooks.log").read_text() == "5.4 log\n"
    assert (tmp_path / "data" / "session" / "active" / "s1.json").is_file()
    for gone in ("logs", "session", "cache"):
        assert not (workspace / ".claude" / gone).exists(), gone


def test_a_conflicting_legacy_file_is_kept_and_its_dir_survives(tmp_path):
    from cli import _legacy_cleanup

    workspace, data = tmp_path / "ws", tmp_path / "data"
    _seed_legacy_workspace(workspace)
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "hooks.log").write_text("current log\n")

    result = _legacy_cleanup.merge_legacy_data_dirs(workspace, data)

    assert "1 conflicting" in result["details"]
    assert (workspace / ".claude" / "logs" / "hooks.log").read_text() == "5.4 log\n"
    assert (data / "logs" / "hooks.log").read_text() == "current log\n"
    assert _legacy_cleanup.merge_legacy_data_dirs(workspace, data)["action"] == "migrated"
    unknown = workspace / ".claude" / "cache" / "approvals" / "notes.txt"
    unknown.write_text("user file\n")
    _legacy_cleanup.remove_stale_grant_cache(workspace)
    assert unknown.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="the 5.x wrapper is the POSIX launcher")
def test_bare_install_migrates_the_5x_launcher_and_leaves_a_foreign_one(tmp_path):
    db = tmp_path / "data" / "gaia.db"
    db.parent.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    launcher = tmp_path / "home" / ".local" / "bin" / "gaia"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(install._render_launcher(workspace.resolve()))

    result = _bare_install(tmp_path, db, workspace)

    assert result.returncode == 0, result.stdout + result.stderr
    assert launcher.is_symlink()
    launcher.unlink()
    launcher.write_text("#!/bin/sh\necho mine\n")
    _bare_install(tmp_path, db, workspace)
    assert launcher.read_text() == "#!/bin/sh\necho mine\n"


def test_a_second_concurrent_install_is_refused_while_the_lock_is_held(tmp_path, monkeypatch, capsys):
    db = tmp_path / "data" / "gaia.db"
    db.parent.mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path[:0] = [sys.argv[1], sys.argv[3]];"
         "from modules.core.filelock import exclusive_file_lock\n"
         "with exclusive_file_lock(sys.argv[2]):\n print('held', flush=True); time.sleep(30)",
         str(_REPO_ROOT / "hooks"), str(db.parent / install.INSTALL_LOCK_NAME), str(_REPO_ROOT)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(install, "INSTALL_LOCK_TIMEOUT_SECONDS", 0.3)
        args = argparse.Namespace(db_path=str(db), workspace=str(tmp_path), skip_workspace=True)

        rc = install.install_channels(args, ())
    finally:
        holder.kill()
        holder.wait()

    assert rc == 1
    assert "another install holds" in capsys.readouterr().err
    assert not db.exists()


@pytest.mark.parametrize(
    ("recorded", "plugin", "wired"),
    ((("npm",), False, ("npm",)), (("opencode",), False, ("opencode",)), (("npm",), True, ())),
    ids=("npm", "opencode", "plugin"),
)
def test_launcher_fix_is_the_bare_install_that_picks_the_channel_itself(
    tmp_path, monkeypatch, recorded, plugin, wired
):
    monkeypatch.setattr(install, "recorded_channels", lambda _ws: recorded)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    if plugin:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.local.json").write_text(
            '{"enabledPlugins": {"gaia@gaia-marketplace": true}}')
    launcher = tmp_path / "bin" / "gaia.cmd"
    launcher.parent.mkdir()
    launcher.write_text("@echo off\n")
    monkeypatch.setattr(doctor, "_WINDOWS_LAUNCHER", launcher)

    assert install.detect_channels(tmp_path) == wired
    assert install.channel_conflict(tmp_path, "npm") is None or "npm" not in wired
    fix = doctor.check_launcher_python()["fix"]
    assert "`gaia install --path`" in fix and "--channel" not in fix
