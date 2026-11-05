"""The real store accepts writes from Gaia's entry points and refuses a plain process importing the store library."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAIN_WRITE = (
    f"import sys; sys.path.insert(0, {str(ROOT)!r}); from gaia.store.writer import _connect; "
    "_connect().execute('CREATE TABLE stray_probe(x)')"
)


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GAIA_", "PYTEST_", "CLAUDE_"))}
    env["HOME"] = str(home)
    return env


def _run(argv, home, **kwargs):
    return subprocess.run(argv, env=_env(home), cwd=home, capture_output=True, text=True, **kwargs)


def _count(store: Path, sql: str) -> int:
    with sqlite3.connect(store) as con:
        return con.execute(sql).fetchone()[0]


def test_cli_dispatch_and_gaia_scan_write_and_plain_process_is_refused(tmp_path):
    gaia = [sys.executable, str(ROOT / "bin" / "gaia")]
    store = tmp_path / ".gaia" / "gaia.db"
    workspace = tmp_path / "ws"
    (workspace / "repo").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(workspace / "repo")], check=True)

    declared = _run([*gaia, "workspace", "declare", "w", str(workspace)], tmp_path)
    assert declared.returncode == 0, declared.stderr
    scanned = _run([*gaia, "scan", "--workspace", "w", str(workspace)], tmp_path)
    assert scanned.returncode == 0, scanned.stderr
    assert _count(store, "SELECT count(*) FROM projects WHERE name='repo'") == 1

    plain = _run([sys.executable, "-c", PLAIN_WRITE], tmp_path)
    assert plain.returncode != 0
    assert "accidental-write wall" in plain.stderr
    assert _count(store, "SELECT count(*) FROM sqlite_master WHERE name='stray_probe'") == 0


def test_hook_entry_script_creates_the_store_a_plain_process_cannot(tmp_path):
    plain = _run([sys.executable, "-c", PLAIN_WRITE], tmp_path)
    assert "accidental-write wall" in plain.stderr
    assert not (tmp_path / ".gaia" / "gaia.db").exists()

    hook = _run([sys.executable, str(ROOT / "hooks" / "session_start.py")], tmp_path, input="{}")
    assert hook.returncode == 0, hook.stderr
    assert _count(tmp_path / ".gaia" / "gaia.db", "SELECT count(*) FROM sqlite_master WHERE type='table'") > 0
