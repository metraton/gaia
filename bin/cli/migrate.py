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
`gaia migrate apply --consent-chain ...` command that continues.
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
    """Run the engine; ``db_path`` overrides the database via GAIA_DB."""
    env = os.environ.copy()
    if db_path:
        env["GAIA_DB"] = str(Path(db_path).expanduser().resolve())
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
            help="Database path (default: GAIA_DB, else ~/.gaia/gaia.db)",
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
