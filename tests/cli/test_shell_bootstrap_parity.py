"""The shell bootstrap the sandbox harness runs reaches the same schema as `gaia migrate`.

bin/validate-sandbox.sh seeds its database with scripts/bootstrap_database.sh,
while installs run scripts/bootstrap_database.py. A migration only the Python
engine can apply therefore passes the suite and fails the publish gate after
the tag already exists, as 5.5.0-rc.4 did. From an empty database and from
every published base, both engines run the whole chain and must land on the
expected version with an identical schema.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "published_bases"

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_FIXTURES))
import migration_guard  # noqa: E402
from loader import load_base  # noqa: E402

BASES = sorted(int(p.stem[1:]) for p in _FIXTURES.glob("v*.sql"))

_SQLITE3_SHIM = '''
import sqlite3, sys
sql = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
con = sqlite3.connect(sys.argv[1])
try:
    rows = con.execute(sql).fetchall()
except sqlite3.ProgrammingError:
    con.executescript(sql)
    rows = []
con.commit()
for row in rows:
    print("|".join("" if v is None else str(v) for v in row))
'''


def _expected_version() -> int:
    text = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    return int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.M).group(1))


def _env(tmp: Path, db: Path) -> dict[str, str]:
    """The sqlite3 CLI is not in the own toolchain, so a stand-in on PATH plays it."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "sqlite3"
    shim.write_text(f"#!{sys.executable}\n{_SQLITE3_SHIM}", encoding="utf-8")
    shim.chmod(0o755)
    return {
        **os.environ,
        "GAIA_DB": str(db),
        "GAIA_DATA_DIR": str(tmp / "data"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }


def _run(cmd: list[str], tmp: Path, db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=_env(tmp, db), capture_output=True, text=True, timeout=300)


def _definition(sql: str) -> object:
    """An object's SQL without comments or layout; a table's clauses as an unordered set.

    On an existing database the shell engine adds pending columns and runs
    schema.sql before the chain, so column order and comments differ from the
    Python engine's by construction; every clause, CHECK included, must match.
    """
    text = " ".join(migration_guard.strip_comments(sql).split())
    head, _, body = text.partition("(")
    if not head.upper().startswith("CREATE TABLE"):
        return text
    clauses, current, depth = [], "", 0
    for ch in body.rsplit(")", 1)[0]:
        if ch == "," and depth == 0:
            clauses.append(current.strip())
            current = ""
            continue
        depth += (ch == "(") - (ch == ")")
        current += ch
    clauses.append(current.strip())
    return head.strip(), frozenset(clauses)


def _schema(db: Path) -> dict[tuple[str, str], object]:
    con = sqlite3.connect(db)
    try:
        return {
            (kind, name): _definition(sql)
            for kind, name, sql in con.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }
    finally:
        con.close()


def _ledger(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("base", [None, *BASES], ids=["empty", *(f"v{b}" for b in BASES)])
def test_shell_bootstrap_reaches_the_python_schema(base, tmp_path):
    """bootstrap_database.sh applies every migration and ends where bootstrap_database.py does."""
    expected = _expected_version()
    shell_db = tmp_path / "shell" / "gaia.db"
    python_db = tmp_path / "python" / "gaia.db"
    consent: list[str] = []
    for db in (shell_db, python_db):
        db.parent.mkdir()
        if base is not None:
            load_base(base, db)
    if base is not None:
        consent = ["--consent-chain", migration_guard.chain_label(base, expected)]

    python = _run([sys.executable, str(_REPO_ROOT / "scripts" / "bootstrap_database.py"), *consent],
                  python_db.parent, python_db)
    assert python.returncode == 0, python.stdout + python.stderr
    shell = _run(["bash", str(_REPO_ROOT / "scripts" / "bootstrap_database.sh")], shell_db.parent, shell_db)
    assert shell.returncode == 0, shell.stdout[-2000:] + shell.stderr[-2000:]

    assert _ledger(shell_db) == expected
    shell_schema, python_schema = _schema(shell_db), _schema(python_db)
    differing = sorted(
        key for key in shell_schema.keys() | python_schema.keys()
        if shell_schema.get(key) != python_schema.get(key)
    )
    assert not differing, "\n".join(
        f"{key}\n  shell:  {shell_schema.get(key)}\n  python: {python_schema.get(key)}" for key in differing
    )
