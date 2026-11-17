"""
gaia migrate -- the one path that moves a Gaia database between schema versions.

`plan` shows the whole pending chain; `apply` runs it. `gaia install`,
`gaia update` and `gaia dev` (through the install it runs) reach the database
only through `apply`, so every upgrade gets the same backup and the same single
transaction; MIGRATION_RULE says who applies which chain. The work itself is
done by `scripts/bootstrap_database.py`, run as a subprocess so a failure in it
can never take the calling command down with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from gaia.paths import db_path as _resolved_db_path  # noqa: E402

ENGINE = _PACKAGE_ROOT / "scripts" / "bootstrap_database.py"

# Repeated word for word by the texts tests/docs/test_migration_rule_parity.py lists.
MIGRATION_RULE = (
    "Every upgrade of gaia.db first writes a verified backup, then runs the whole chain in one "
    "transaction. A chain that only adds structure applies on its own. A chain that reaches rows "
    "that already exist applies on its own under `gaia install` and `gaia update` once the backup "
    "verifies; the plugin's session start and a plain `gaia migrate apply` stop on it and name "
    "`gaia migrate apply --consent-chain vA..vB`. A chain with a breaking migration is refused by "
    "`gaia install` and `gaia update` while another Gaia copy on the machine would be left unable "
    "to write the database; the refusal names each copy and the `gaia doctor --target <version>` "
    "steps that upgrade it, and `--accept-stranding` migrates anyway."
)

_WRAPPED_RULE = textwrap.fill(
    MIGRATION_RULE, width=78, initial_indent="  ", subsequent_indent="  ",
    break_long_words=False, break_on_hyphens=False,
)

_DESCRIPTION = f"""\
Move the Gaia database to the schema version this code expects.

  plan    Print the pending chain vA..vB, what each migration reaches
          (structure only, or rows that already exist), where the backup
          will go, whether apply will need consent, and the backups on hand.
          Writes nothing. --json prints the same as one object.
  apply   Run the chain.
  restore Put a backup back as the database (--from <backup>). The database
          it replaces is first kept as a verified pre-restore backup, and
          nothing changes unless the backup passes integrity_check.
  backup  Write one verified copy of the database now, read through a
          read-only connection. Manual backups are never pruned; `plan` lists
          them and `restore --from` takes them.
  diagnose
          When a migration failed: copy the database, read-only, into scratch,
          verify the copy, replay the pending chain on it in a transaction that
          is always rolled back, and print the failing step (vN-1->vN and file),
          the SQLite error and the commands that remain. The database itself is
          never written. Exits non-zero when the chain cannot apply. --json
          prints the same as one object.

How apply treats a database:
  - Before anything changes on an existing database, a full copy is written
    with SQLite's backup API to <database dir>/backups/, verified, gzipped;
    the newest 3 pre-migrate (and 3 pre-restore) copies are kept.
  - The whole chain -- every migration and its schema_version seal -- runs in
    ONE transaction: an interruption at any point leaves the objects and the
    version exactly as they were.
  - Where consent is needed, it is ONE consent for the whole chain: re-run
    with --consent-chain vA..vB, naming the chain `plan` printed. The consent
    approves that span and nothing that ships later.
  - A fresh database is built from schema.sql; there is nothing to back up
    or consent to.

Who applies a chain:
{_WRAPPED_RULE}

When apply, install or update fails on the database: `diagnose` names the
failing step and the route left, with no signature; `backup` keeps a verified
copy; then run the route diagnose prints: apply, with the consent it names, or
a newer Gaia when the store is ahead of this one. `restore --from <backup>`
puts a backup back where a failure names it. When diagnose finds no automatic
route, keep that backup and the ones `plan` lists: no verb recovers such a
store yet.

Migrations only run forward. A database newer than this code is never moved
down. Each seal records min_code_version, the oldest code that may still write,
and only a migration marked `-- gaia-compat: breaking` raises it. While this
code's version reaches that minimum, apply has nothing to do and this Gaia keeps
reading and writing (with a warning); past it, apply refuses and every write is
refused (reads keep working) until a Gaia whose schema version is at least the
minimum is installed. SessionStart names the fix in either direction.
"""


def engine_command(
    action: str,
    consent_chain: str | None = None,
    after_backup: bool = False,
    as_json: bool = False,
    backup: str | None = None,
) -> list[str]:
    """The engine invocation for ``action`` ('plan', 'apply', 'restore', 'backup' or 'diagnose').

    *after_backup* lets a verified backup stand in for the chain consent; only
    `gaia install` passes it (D140).
    """
    cmd = [sys.executable or "python3", str(ENGINE)]
    if action in ("plan", "diagnose", "backup"):
        cmd.append(f"--{action}")
        if as_json:
            cmd.append("--json")
    if action == "restore":
        cmd += ["--restore", str(Path(backup).expanduser().resolve())]
    if consent_chain:
        cmd += ["--consent-chain", consent_chain]
    if after_backup:
        cmd.append("--apply-after-backup")
    return cmd


def run(
    action: str,
    consent_chain: str | None = None,
    db_path: str | None = None,
    capture: bool = False,
    after_backup: bool = False,
    as_json: bool = False,
    backup: str | None = None,
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
        engine_command(action, consent_chain, after_backup, as_json, backup),
        env=env,
        capture_output=capture,
        text=True,
        check=False,
    )


def plan_report(db_path: str | None = None) -> dict:
    """What `gaia migrate apply` would do to the database, read-only, as the dict `plan --json` prints."""
    result = run("plan", db_path=db_path, capture=True, as_json=True)
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"gaia migrate plan --json printed no report: {result.stderr.strip()}") from exc


def register(subparsers):
    """Register the 'migrate' subcommand."""
    import argparse

    p = subparsers.add_parser(
        "migrate",
        help="Plan or apply the database schema migration chain",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="migrate_action", metavar="{plan,apply,restore,backup,diagnose}")
    actions.required = True
    for name, help_text in (
        ("plan", "Show the pending chain and what apply would need; writes nothing"),
        ("apply", "Back up, then apply the chain in one transaction"),
        ("restore", "Put a backup back as the database, keeping the current one as a verified backup"),
        ("backup", "Write one verified backup of the database now; manual backups are never pruned"),
        ("diagnose", "Replay the pending chain on a verified scratch copy, always rolled back, and print the "
                     "failing step, its SQLite error and the commands that remain; the database is only read"),
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
        if name == "plan":
            sub.add_argument(
                "--json", dest="as_json", action="store_true",
                help="Print the plan as one JSON object (direction, chain, migrations, backups)",
            )
        if name == "diagnose":
            sub.add_argument(
                "--json", dest="as_json", action="store_true",
                help="Print the diagnosis as one JSON object (failed_step, error, steps, route, note)",
            )
        if name == "restore":
            sub.add_argument(
                "--from", dest="backup", required=True, metavar="BACKUP",
                help="The backup to put back: a .db.gz `plan` lists, or a plain .db",
            )
    return p


def cmd_migrate(args) -> int:
    if not ENGINE.is_file():
        try:
            version = json.loads((_PACKAGE_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            version = "<version>"
        print(f"gaia migrate: engine not found at {ENGINE}; this Gaia package is incomplete. Reinstall it: "
              f"`npm install -g @jaguilar87/gaia@{version}`, then run `gaia doctor`.", file=sys.stderr)
        return 1
    return run(
        args.migrate_action,
        consent_chain=getattr(args, "consent_chain", None),
        db_path=args.db_path,
        as_json=getattr(args, "as_json", False),
        backup=getattr(args, "backup", None),
    ).returncode
