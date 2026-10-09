"""
gaia migrate -- the one path that moves a Gaia database between schema versions.

`plan` shows the whole pending chain; `apply` runs it. `gaia install`,
`gaia update` and `gaia dev` (through the install it runs) reach the database
only through `apply`, so every upgrade gets the same backup, the same single
transaction and the same consent rule. The work itself is done by
`scripts/bootstrap_database.py`, run as a subprocess so a failure in it can
never take the calling command down with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from gaia.paths import db_path as _resolved_db_path  # noqa: E402

ENGINE = _PACKAGE_ROOT / "scripts" / "bootstrap_database.py"

_DESCRIPTION = """\
Move the Gaia database to the schema version this code expects.

  plan    Print the pending chain vA..vB, what each migration reaches
          (structure only, or rows that already exist), where the backup
          will go, and whether apply will need consent. Writes nothing.
  apply   Run the chain.

How apply treats a database:
  - Before anything changes on an existing database, a full copy is written
    with SQLite's backup API to <database dir>/backups/.
  - The whole chain -- every migration and its schema_version seal -- runs in
    ONE transaction: an interruption at any point leaves the objects and the
    version exactly as they were.
  - A chain that only adds structure applies on its own.
  - A chain in which any migration reaches rows that already exist asks ONE
    consent for the whole chain: re-run with --consent-chain vA..vB, naming
    the chain `plan` printed. The consent approves that span and nothing
    that ships later.
  - A fresh database is built from schema.sql; there is nothing to back up
    or consent to.

gaia install, gaia update and gaia dev run `gaia migrate apply` without
consent; when they stop on a data-reaching chain, the message names the exact
`gaia migrate apply --consent-chain ...` command that continues. The plugin,
which never runs install, does the same at SessionStart when the database is
behind, and names that command in the session's startup notice.

Migrations only run forward. A database newer than this code is never moved
down. Each seal records min_code_version, the oldest code that may still write,
and only a migration marked `-- gaia-compat: breaking` raises it. While this
code's version reaches that minimum, apply has nothing to do and this Gaia keeps
reading and writing (with a warning); past it, apply refuses and every write is
refused (reads keep working) until a Gaia whose schema version is at least the
minimum is installed. SessionStart names the fix in either direction.
"""


def engine_command(action: str, consent_chain: str | None = None) -> list[str]:
    """The engine invocation for ``action`` ('plan' or 'apply')."""
    cmd = [sys.executable or "python3", str(ENGINE)]
    if action == "plan":
        cmd.append("--plan")
    if consent_chain:
        cmd += ["--consent-chain", consent_chain]
    return cmd


def run(
    action: str,
    consent_chain: str | None = None,
    db_path: str | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run the engine on ``db_path``, else on the database every gaia call resolves.

    The engine reads GAIA_DB alone, so the resolver's answer (which also honors
    GAIA_DATA_DIR) is handed to it through that variable.
    """
    env = os.environ.copy()
    env["GAIA_DB"] = (
        str(Path(db_path).expanduser().resolve()) if db_path else str(_resolved_db_path())
    )
    return subprocess.run(
        engine_command(action, consent_chain),
        env=env,
        capture_output=capture,
        text=True,
        check=False,
    )


def register(subparsers):
    """Register the 'migrate' subcommand."""
    import argparse

    p = subparsers.add_parser(
        "migrate",
        help="Plan or apply the database schema migration chain",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="migrate_action", metavar="{plan,apply}")
    actions.required = True
    for name, help_text in (
        ("plan", "Show the pending chain and what apply would need; writes nothing"),
        ("apply", "Back up, then apply the chain in one transaction"),
    ):
        sub = actions.add_parser(name, help=help_text, description=help_text)
        sub.add_argument(
            "--db",
            dest="db_path",
            default=None,
            help="Database path (default: GAIA_DB, else GAIA_DATA_DIR/gaia.db, else ~/.gaia/gaia.db)",
        )
        if name == "apply":
            sub.add_argument(
                "--consent-chain",
                dest="consent_chain",
                metavar="vA..vB",
                default=None,
                help="Consent, once, to a chain that reaches existing data; "
                "must name the chain `gaia migrate plan` prints",
            )
    return p


def cmd_migrate(args) -> int:
    if not ENGINE.is_file():
        print(f"gaia migrate: engine not found at {ENGINE}", file=sys.stderr)
        return 1
    return run(
        args.migrate_action,
        consent_chain=getattr(args, "consent_chain", None),
        db_path=args.db_path,
    ).returncode
