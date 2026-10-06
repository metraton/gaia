"""Bring a plugin install's database and seeds up to the code it runs.

The plugin channel never runs `gaia install` or `gaia update`, so SessionStart
is the only moment it can notice new code over an old database. It does what
those commands do, with their rules: a structure-only chain is applied by
`gaia migrate apply` (backup first, one transaction), a chain that reaches
existing rows is left for the user's consent, a database newer than this code
is never moved, and a new package version re-runs install's permission and
routing seeds. Hooks are not written: the plugin registers them itself.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

SEEDED_VERSION_FILE = "seeded-version"

_CONSENT_COMMAND_RE = re.compile(r"gaia migrate apply --consent-chain v\d+\.\.v\d+")
_BACKUP_RE = re.compile(r"backup written: (\S+)")


def _cli_module(name: str):
    """A `gaia` CLI module; bin/ joins a hook's path only when one is needed."""
    bin_dir = str(_PACKAGE_ROOT / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    return importlib.import_module(f"cli.{name}")


def package_version() -> str | None:
    try:
        return json.loads((_PACKAGE_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return None


def _ledger_version(db_file: Path) -> int | None:
    """The database's schema version, or None when it has no ledger; the one query."""
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _migrate(db_file: Path, live: int | None, expected: int) -> tuple[bool, str]:
    """Run `gaia migrate apply` without consent; (applied, notice)."""
    result = _cli_module("migrate").run("apply", db_path=str(db_file), capture=True)
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    start = f"v{live}" if live is not None else "an unsealed ledger"
    if result.returncode == 0:
        backup = _BACKUP_RE.search(output)
        where = f" A backup of the previous database is at {backup.group(1)}." if backup else ""
        return True, f"gaia.db was migrated from {start} to v{expected} at session start.{where}"
    consent = _CONSENT_COMMAND_RE.search(output)
    if consent:
        return False, (
            f"gaia.db is at {start}; this Gaia expects v{expected}. The pending chain "
            f"reaches rows that already exist, so it was not applied. Review it with "
            f"`gaia migrate plan`, then run `{consent.group(0)}`."
        )
    lines = [line for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
    reason = lines[-1].strip() if lines else f"exit {result.returncode}"
    return False, (
        f"gaia.db could not be migrated from {start} to v{expected} ({reason}); it was "
        f"left as it was. Run `gaia migrate plan` to see the chain."
    )


def _reseed(db_file: Path) -> list[str]:
    """Run install's permission and routing seeds; the failures, empty on success."""
    install = _cli_module("install")
    failures = []
    for name, seed in (
        ("contract permissions", install._seed_contract_permissions),
        ("surface routing", install._seed_surface_routing),
    ):
        result = seed(db_path=str(db_file), quiet=True)
        if result.get("action") != "created":
            failures.append(f"{name}: {result.get('details', 'failed')}")
    return failures


def reconcile(db_file: Path, expected: int, marker: Path, version: str | None) -> str:
    """Migrate and re-seed *db_file* for this code; the session notice, "" when silent.

    *marker* holds the package version the seeds last ran for and is rewritten
    only after they succeed, so a failed re-seed is retried next session.
    """
    if not db_file.is_file():
        return ""
    live = _ledger_version(db_file)
    if live is not None and live > expected:
        return ""
    notices = []
    if live is None or live < expected:
        applied, notice = _migrate(db_file, live, expected)
        notices.append(notice)
        if not applied:
            return "\n".join(notices)
    if version and (not marker.is_file() or marker.read_text(encoding="utf-8").strip() != version):
        failures = _reseed(db_file)
        if failures:
            notices.append(
                f"Permission and routing seeds for Gaia {version} did not complete "
                f"({'; '.join(failures)}); they are retried next session."
            )
        else:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(version, encoding="utf-8")
    return "\n".join(notices)


def reconcile_plugin_install(workspace: Path) -> str:
    """Run :func:`reconcile` for this session when the plugin channel owns *workspace*."""
    from modules.core.paths import get_plugin_data_dir
    from modules.core.plugin_setup import resolve_hook_channel

    if resolve_hook_channel(workspace, npm_copy=False) != "plugin":
        return ""
    from gaia.paths import db_path
    from gaia.store.writer import _expected_schema_version

    expected = _expected_schema_version()
    if expected is None:
        return ""
    return reconcile(
        db_path(),
        expected,
        get_plugin_data_dir() / SEEDED_VERSION_FILE,
        package_version(),
    )
