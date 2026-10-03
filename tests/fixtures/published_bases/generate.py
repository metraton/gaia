#!/usr/bin/env python3
"""Regenerate the published-base fixtures from the release tags that shipped them.

Each fixture is the database a user of that release actually has: the tag's
own bootstrap (the Python port where the tag ships one, the shell original
otherwise) run against an empty file, plus a handful of representative rows,
dumped as SQL text. The dumps are committed so the upgrade tests need neither
the tags nor the sqlite3 CLI -- a shallow CI checkout has no tags at all.

Run from a full clone with the tags fetched:

    python3 tests/fixtures/published_bases/generate.py [--repo PATH]
"""

from __future__ import annotations

import argparse
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# (schema version, tag). v5.0.11 and v5.1.0 both shipped v21; v5.4.0-rc.1 stands
# for 5.4, which never had a stable release.
BASES = (
    (21, "v5.1.0"),
    (26, "v5.1.1"),
    (33, "v5.2.0"),
    (37, "v5.3.0"),
    (51, "v5.4.0-rc.1"),
    (57, "v5.5.0-rc.3"),
)

_ARCHIVED_PATHS = ("scripts", "gaia/store/schema.sql", "bin/cli/doctor.py")

# Rows every published schema accepts. They exist so that a migration reaching
# these tables meets real data, which is what makes the consent gate bite.
_REPRESENTATIVE_ROWS = (
    (
        "INSERT INTO memory (workspace, name, type, description, body) "
        "VALUES (?, ?, 'project', 'fixture', 'representative row')",
        ("fixture_memory_1",),
    ),
    (
        "INSERT INTO memory (workspace, name, type, description, body) "
        "VALUES (?, ?, 'feedback', 'fixture', 'representative row')",
        ("fixture_memory_2",),
    ),
    (
        "INSERT INTO briefs (workspace, name, title, objective) "
        "VALUES (?, ?, 'Fixture brief', 'representative row')",
        ("fixture-brief",),
    ),
    (
        "INSERT INTO projects (workspace, name, role, path) "
        "VALUES (?, ?, 'library', '/fixture/project')",
        ("fixture-project",),
    ),
)


def _extract_tag(repo: Path, tag: str, dest: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", tag, *_ARCHIVED_PATHS],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def _bootstrap(tree: Path, db: Path) -> None:
    workspace = tree / "fixture-ws"
    workspace.mkdir()
    env = {**os.environ, "GAIA_DB": str(db), "WORKSPACE": str(workspace)}
    python_port = tree / "scripts" / "bootstrap_database.py"
    if python_port.is_file():
        cmd = [sys.executable, str(python_port)]
    else:
        cmd = ["bash", str(tree / "scripts" / "bootstrap_database.sh")]
    subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)


def build(repo: Path, version: int, tag: str) -> str:
    """Return the SQL dump of the database release ``tag`` produces."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        _extract_tag(repo, tag, tree)
        db = tree / "gaia.db"
        _bootstrap(tree, db)
        con = sqlite3.connect(db)
        stamped = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if stamped != version:
            raise SystemExit(f"{tag} bootstrapped to v{stamped}, expected v{version}")
        workspace = con.execute("SELECT name FROM workspaces LIMIT 1").fetchone()[0]
        for sql, params in _REPRESENTATIVE_ROWS:
            con.execute(sql, (workspace, *params))
        con.commit()
        dump = "\n".join(con.iterdump()) + "\n"
        con.close()
        return dump


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=_HERE.parents[2])
    args = parser.parse_args()
    for version, tag in BASES:
        out = _HERE / f"v{version}.sql"
        out.write_text(build(args.repo, version, tag), encoding="utf-8")
        print(f"{out.name}: {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
