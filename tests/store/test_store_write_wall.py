"""The real store accepts writes from Gaia's entry points and refuses a plain process importing the store library."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GAIA_", "PYTEST_"))}
    env["HOME"] = str(home)
    return env


def test_cli_dispatch_writes_and_plain_process_is_refused(tmp_path):
    env = _env(tmp_path)
    cli = subprocess.run([sys.executable, str(ROOT / "bin" / "gaia"), "paths"], env=env, capture_output=True, text=True)
    store = tmp_path / ".gaia" / "gaia.db"
    assert cli.returncode == 0, cli.stderr
    with sqlite3.connect(store) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0

    probe = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); from gaia.store.writer import _connect; "
        "_connect().execute('CREATE TABLE stray_probe(x)')"
    )
    plain = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
    assert plain.returncode != 0
    assert "accidental-write wall" in plain.stderr
    with sqlite3.connect(store) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='stray_probe'").fetchone()[0] == 0
