"""Texts on who applies a migration state MIGRATION_RULE verbatim; package.json installs pin a version."""

from __future__ import annotations

import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GAIA = _REPO / "bin" / "gaia"
_FIXTURES = _REPO / "tests" / "fixtures" / "published_bases"

for _path in (_REPO, _REPO / "bin", _REPO / "hooks", _REPO / "scripts", _FIXTURES):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import migration_guard  # noqa: E402
from cli import _install_helpers  # noqa: E402
from cli.doctor import EXPECTED_SCHEMA_VERSION  # noqa: E402
from cli.migrate import MIGRATION_RULE  # noqa: E402
from loader import load_base  # noqa: E402
from modules.session import session_manifest  # noqa: E402

RULE_TEXTS = ("README.md", "INSTALL.md", "scripts/migrations/README.md", "skills/gaia-release/SKILL.md")
PINNED_TEXTS = ("README.md", "INSTALL.md", "bin/README.md")
DATA_REACHING_BASE = 37

_INSTALL = re.compile(r"\b(?:npm|pnpm)\s+(?:install|i|add)\b[^`\n#()]*")
_EXACT = re.compile(r"@jaguilar87/gaia@(?:\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?|<[a-z ]*version>)(?=\s|$|,)")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _gaia(tmp: Path, db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GAIA_DB": str(db), "GAIA_DATA_DIR": str(tmp)}
    return subprocess.run([sys.executable, str(_GAIA), *args], env=env, capture_output=True, text=True, check=False)


def _unpinned(text: str) -> list[str]:
    bad = []
    for match in _INSTALL.finditer(text):
        command = match.group(0).strip()
        words = [word.strip(",;.") for word in command.split()]
        if "@jaguilar87/gaia" not in command or "-g" in words or "--global" in words:
            continue
        if "--save-exact" not in words or not _EXACT.search(command) or "@latest" in command:
            bad.append(command)
    return bad


@pytest.mark.parametrize("name", RULE_TEXTS)
def test_the_text_states_the_rule_word_for_word(name):
    assert _flat(MIGRATION_RULE) in _flat((_REPO / name).read_text(encoding="utf-8")), name


def test_gaia_migrate_help_states_the_rule(tmp_path):
    result = _gaia(tmp_path, tmp_path / "gaia.db", "migrate", "--help")

    assert result.returncode == 0, result.stderr
    assert _flat(MIGRATION_RULE) in _flat(result.stdout)


def test_the_session_start_block_names_a_command_that_moves_a_data_reaching_store(tmp_path, monkeypatch):
    db = tmp_path / "gaia.db"
    load_base(DATA_REACHING_BASE, db)
    con = sqlite3.connect(db)
    census = migration_guard.take_census(con)
    con.close()
    assert any(
        migration_guard.scan((_REPO / "scripts" / "migrations" / f"v{n - 1}_to_v{n}.sql").read_text(encoding="utf-8"),
                             census)
        for n in range(DATA_REACHING_BASE + 1, EXPECTED_SCHEMA_VERSION + 1)
    ), "the fixture's pending chain must reach existing rows"
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    block = session_manifest.build_schema_direction_block()

    assert block.startswith("## Database schema\n")
    assert _flat(MIGRATION_RULE) in _flat(block)
    named = re.search(r"`(gaia migrate apply [^`]*)`", block.replace(MIGRATION_RULE, ""))
    assert named, block
    plain = _gaia(tmp_path, db, "migrate", "apply", "--db", str(db))
    assert plain.returncode == 1 and "--consent-chain" in plain.stderr, "a plain apply stops, as the rule says"

    moved = _gaia(tmp_path, db, *shlex.split(named.group(1))[1:], "--db", str(db))

    assert moved.returncode == 0, moved.stdout + moved.stderr
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == EXPECTED_SCHEMA_VERSION
    finally:
        con.close()


@pytest.mark.parametrize("name", PINNED_TEXTS)
def test_documented_installs_pin_the_exact_version(name):
    assert _unpinned((_REPO / name).read_text(encoding="utf-8")) == []


def test_printed_installs_pin_the_exact_version(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    own = workspace / "node_modules" / "@jaguilar87" / "gaia"
    own.mkdir(parents=True)
    (workspace / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(session_manifest, "_own_package_root", lambda: own)
    cache = tmp_path / "home" / ".claude" / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.8.0-rc.3"
    cache.mkdir(parents=True)

    opencode_workspace = tmp_path / "oc"
    opencode_workspace.mkdir()

    printed = [
        session_manifest.install_behind_line(80, 73, refused=True),
        _install_helpers.configure_opencode_plugin(opencode_workspace, cache)["details"],
    ]

    assert all("@jaguilar87/gaia@" in line for line in printed), printed
    assert [_unpinned(line) for line in printed] == [[], []]
