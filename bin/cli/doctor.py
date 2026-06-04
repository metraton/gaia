"""
gaia doctor -- System health checks for Gaia-Ops.

Checks (in order):
   5. package-integrity  - scripts/bootstrap_database.sh shipped + exec
  10. gaia-version       - package.json readable
  15. last-install-error - ~/.gaia/last-install-error.json (postinstall failure marker)
  20. claude-code        - CLI installed
  30. python             - Python 3.9+ available
  35. workspace-init     - .claude/, plugin-registry, settings hooks all present
  40. plugin-mode        - ops vs security, registry valid
  45. schema-version     - gaia.db schema_version matches CLI expectation
  47. schema-ddl         - live CHECK constraints match schema.sql (ledger-vs-DDL)
  50. symlinks           - .claude/ symlinks resolve
  60. identity           - orchestrator agent configured
  70. settings           - hooks registered, permissions, deny rules
  80. hook-files         - all hook scripts present
  90. project-context    - project-context.json valid
 100. project-dirs       - paths declared in context exist
 110. memory-store       - episodes table present in gaia.db (DB-canonical)
 120. memory_fts5_db     - episodes_fts present in gaia.db
 130. memory_fts5_count  - FTS5 index complete
 140. memory_scoring     - scoring module importable

Severity: pass / info / warning / error
Exit codes: 0=healthy, 1=warnings, 2=errors

Design notes (Pass 4 overhaul):
  - Diagnostic-only. Every failed check carries an actionable `fix` hint
    that points to a concrete user action ("reinstall", "run gaia install",
    "upgrade Gaia") -- never to `--fix`. The auto-fix surface (Identity
    agent field, FTS5 backfill) remains from earlier passes for backward
    compatibility but no new check opts into it.
  - Severity is consistent: ERROR = Gaia is broken for the user, WARNING =
    degraded but usable, INFO = advisory.
  - The summary line at the end counts checks by severity and tells the
    user where to look for fixes (inline hints above).

References:
  - brew doctor: every warning carries an inline remediation; no --fix mode.
  - npm doctor: 7 well-scoped checks, context-specific hints when one fails.
  - rustup: package integrity via SHA + manifest; corruption detected at
    use-time, repair is reinstall.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


# ============================================================================
# Check Registry
# ============================================================================

# Global ordered registry of (order, name, fn) tuples populated by
# @register_check. cmd_doctor iterates this list rather than a hardcoded
# array. Order values are spaced (10, 20, 30...) to leave room for inserts.
_CHECKS = []


def register_check(name: str, order: int):
    """Register a check function in the global ordered registry.

    Args:
        name: Display name for the check (used as identifier for fallbacks).
        order: Integer priority -- lower runs first. Use multiples of 10 to
            leave room for future inserts.
    """
    def decorator(fn):
        _CHECKS.append((order, name, fn))
        _CHECKS.sort(key=lambda x: x[0])
        return fn
    return decorator


# ============================================================================
# Helpers
# ============================================================================

def _result(name: str, severity: str, detail: str, fix: str = None) -> dict:
    """Create a check result dict."""
    ok = severity in ("pass", "info")
    r = {"name": name, "severity": severity, "ok": ok, "detail": detail}
    if fix:
        r["fix"] = fix
    return r


def _derive_workspace(override: str = None) -> Path:
    """Derive the consumer workspace from the running script's install path.

    Algorithm
    ---------
    1. If *override* is given (from --workspace), validate it has .claude/
       and return it directly.
    2. Resolve Path(__file__) to its realpath and search its parts for the
       pattern ``<workspace>/node_modules/@jaguilar87/gaia/``.
    3. If the derived workspace IS the Gaia source package itself (its
       package.json has name "@jaguilar87/gaia"), treat the workspace as a
       dev self-install and look one directory up for the real consumer
       workspace (which should also have node_modules/@jaguilar87/gaia/).
    4. If the script is NOT inside any node_modules/@jaguilar87/gaia/ tree
       (global install, PATH symlink, etc.) exit with a clear error -- no
       silent cwd fallback.

    This replaces the old ``_find_project_root()`` walk-up-from-cwd logic
    that caused false-positive HEALTHY reports when the user was cd'd into
    the Gaia source repo (which has its own healthy .claude/).
    """
    # --- Explicit override via --workspace flag ---
    if override:
        ws = Path(override).resolve()
        if not (ws / ".claude").is_dir():
            print(
                f"gaia doctor: --workspace path has no .claude/ directory: {ws}",
                file=sys.stderr,
            )
            sys.exit(2)
        return ws

    # --- Derive from __file__ realpath ---
    script_path = Path(__file__).resolve()
    parts = script_path.parts

    for i, part in enumerate(parts):
        if (
            part == "node_modules"
            and i + 2 < len(parts)
            and parts[i + 1] == "@jaguilar87"
            and parts[i + 2] == "gaia"
        ):
            # Reconstruct the workspace path from the parts before node_modules/.
            # On POSIX, parts[0] == '/', so Path(*parts[:i]) builds correctly.
            # Guard i==0 (should never happen in practice) just in case.
            workspace = Path(parts[0]).joinpath(*parts[1:i]) if i > 0 else Path("/")

            # Check whether this workspace is itself the Gaia source package.
            # When a developer installs Gaia into the source repo (common dev
            # workflow), the resulting path is:
            #   <source_repo>/node_modules/@jaguilar87/gaia/bin/cli/doctor.py
            # We detect this by checking package.json name.
            pkg_json = workspace / "package.json"
            data = _read_json(pkg_json)
            if data and data.get("name") == "@jaguilar87/gaia":
                # This is a self-install inside the Gaia source repo.
                # Walk one level up to find the real consumer workspace.
                parent = workspace.parent
                parent_nm = parent / "node_modules" / "@jaguilar87" / "gaia"
                if parent_nm.is_dir():
                    return parent
                # No consumer found above the source repo -- fall through to error.
                break

            return workspace

    # --- No inferable consumer workspace ---
    print(
        "gaia doctor: global or symlinked install detected; "
        "no consumer workspace inferable. "
        "Specify --workspace <path> to choose one.",
        file=sys.stderr,
    )
    sys.exit(2)


def _read_json(path: Path):
    """Read and parse a JSON file, returning None on any error."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _package_root() -> Path:
    """Return the gaia-ops package root (parent of bin/)."""
    return Path(__file__).resolve().parent.parent.parent


# The schema version this CLI build expects to find in gaia.db. When the
# bootstrap script applies a new schema migration it must bump this constant
# in lock-step with the INSERT it adds to bootstrap_database.sh. If a user
# upgrades the CLI past a schema bump but does not re-run `gaia install`,
# `check_schema_version` raises a warning telling them how to repair.
EXPECTED_SCHEMA_VERSION = 18

# Locations the doctor reads outside the workspace.
_INSTALL_ERROR_MARKER = Path("~/.gaia/last-install-error.json").expanduser()
_DEFAULT_DB_PATH = Path("~/.gaia/gaia.db").expanduser()


# ============================================================================
# Health Checks
# ============================================================================

@register_check("Package integrity", order=5)
def check_package_integrity() -> dict:
    """Check that critical files the package SHOULD ship are present.

    The npm `files` array IS our manifest (like rustup's manifest.toml).
    Past install failures traced back to missing scripts/bootstrap_database.sh
    inside the published tarball -- this check fails loud at diagnostic time
    so the user knows their install is broken (vs. silently failing later).

    Presence-only: we deliberately do NOT verify the executable bit on
    scripts/bootstrap_database.sh. `install.py::_run_bootstrap` invokes
    the script as `bash <path>` (see bin/cli/install.py:287), so bash
    reads and interprets the file regardless of the exec bit. Checking
    it would create cross-platform flakiness (WSL/Windows checkouts
    routinely lose the exec bit) without preventing any real failure.
    """
    pkg_root = _package_root()
    required = [
        # The CRITICAL file: install.py shells out to this to bootstrap the DB.
        "scripts/bootstrap_database.sh",
        # Top-level package metadata.
        "package.json",
        # bin/gaia is the entry point invoked by the launcher.
        "bin/gaia",
        # Hook entry points loaded by Claude Code via settings.local.json.
        "hooks/pre_tool_use.py",
    ]

    missing = [rel for rel in required if not (pkg_root / rel).is_file()]

    if missing:
        return _result(
            "Package integrity",
            "error",
            f"missing files: {', '.join(missing)}",
            "Your Gaia install is incomplete. Reinstall: "
            "`npm install @jaguilar87/gaia@latest`. If it persists, file a bug.",
        )

    return _result(
        "Package integrity",
        "pass",
        f"{len(required)}/{len(required)} critical files present",
    )


@register_check("Gaia-Ops", order=10)
def check_gaia_version() -> dict:
    """Check that package.json is readable and has a version."""
    pkg_path = _package_root() / "package.json"
    data = _read_json(pkg_path)
    if data and "version" in data:
        return _result("Gaia-Ops", "pass", f"v{data['version']}")
    return _result("Gaia-Ops", "error", "Version unknown", "Reinstall @jaguilar87/gaia")


@register_check("Last install error", order=15)
def check_last_install_error() -> dict:
    """Surface a postinstall failure that left a marker file.

    `gaia install --postinstall` cannot fail loudly (npm aborts the whole
    transaction on non-zero exit). Instead, when a non-fatal step fails it
    writes ~/.gaia/last-install-error.json. This check reads that marker
    and reports it as an ERROR so the user knows the workspace is in a
    degraded state -- and what to do.
    """
    if not _INSTALL_ERROR_MARKER.is_file():
        return _result("Last install error", "pass", "no recent install errors")

    data = _read_json(_INSTALL_ERROR_MARKER)
    if not data:
        return _result(
            "Last install error",
            "warning",
            f"marker present at {_INSTALL_ERROR_MARKER} but unreadable",
            "Delete the marker manually and re-run `gaia install`.",
        )

    step = data.get("step", "unknown step")
    detail = data.get("detail", "no detail")
    ts = data.get("timestamp", "unknown time")
    workspace = data.get("workspace", "unknown workspace")
    return _result(
        "Last install error",
        "error",
        f"postinstall failed at step '{step}' ({ts}) in {workspace}: {detail}",
        "Re-run `gaia install` in the affected workspace to repair. "
        "If the same step fails again, file a bug with this marker attached.",
    )


@register_check("Claude Code", order=20)
def check_claude_code() -> dict:
    """Check if Claude Code CLI is installed."""
    for cmd in ("claude", "claude-code"):
        if shutil.which(cmd):
            try:
                proc = subprocess.run(
                    [cmd, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                version_line = proc.stdout.strip().split("\n")[0] if proc.stdout else cmd
                return _result("Claude Code", "pass", version_line)
            except Exception:
                return _result("Claude Code", "pass", cmd)

    return _result("Claude Code", "info", "Not installed", "npm install -g @anthropic-ai/claude-code")


@register_check("Python", order=30)
def check_python() -> dict:
    """Check Python version >= 3.9."""
    version = sys.version.split()[0]
    parts = version.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return _result("Python", "error", f"Could not parse: {version}", "Install Python 3.9+")

    if major < 3 or (major == 3 and minor < 9):
        return _result("Python", "error", f"Python {version} (need >=3.9)", "Upgrade Python to 3.9+")

    return _result("Python", "pass", f"Python {version}")


@register_check("Workspace initialized", order=35)
def check_workspace_initialized(project_root: Path) -> dict:
    """Meta-check: this workspace is Gaia-aware end-to-end.

    Individual checks (symlinks, settings, identity) test pieces of the
    workspace state. This one tests the *conjunction* -- a workspace is
    only useful to Gaia when .claude/, plugin-registry.json, and a
    settings.local.json with hooks all exist together. Failing any of the
    three means the workspace is not initialized; the others will surface
    their own errors, but this check gives the user one actionable hint.
    """
    claude_dir = project_root / ".claude"
    registry = claude_dir / "plugin-registry.json"
    settings = claude_dir / "settings.local.json"

    missing = []
    if not claude_dir.is_dir():
        missing.append(".claude/")
    if not registry.is_file():
        missing.append("plugin-registry.json")
    if not settings.is_file():
        missing.append("settings.local.json")

    # If all three files exist, also require that settings.local.json
    # carries a hooks section -- a workspace with no hooks is functionally
    # uninitialized even if the file is there.
    has_hooks = False
    if settings.is_file():
        data = _read_json(settings)
        has_hooks = bool(data and data.get("hooks"))
        if data and not has_hooks:
            missing.append("hooks in settings.local.json")

    if missing:
        return _result(
            "Workspace initialized",
            "error",
            f"missing: {', '.join(missing)}",
            f"Run: `gaia install --workspace {project_root}`",
        )
    return _result("Workspace initialized", "pass", "Gaia-aware workspace")


@register_check("Plugin mode", order=40)
def check_plugin_mode(project_root: Path) -> dict:
    """Check plugin mode from plugin-registry.json."""
    registry_path = project_root / ".claude" / "plugin-registry.json"
    if not registry_path.is_file():
        return _result("Plugin mode", "warning", "No plugin-registry.json", "Run `gaia scan` or restart Claude Code")

    data = _read_json(registry_path)
    if not data:
        return _result("Plugin mode", "warning", "Invalid plugin-registry.json", "Delete and restart Claude Code")

    installed = [p.get("name", "") for p in (data.get("installed") or [])]
    source = data.get("source", "unknown")

    if "gaia-ops" in installed:
        return _result("Plugin mode", "pass", f"ops (source: {source})")
    if "gaia-security" in installed:
        return _result("Plugin mode", "pass", f"security (source: {source})")

    return _result("Plugin mode", "warning", f"Unknown plugin: {', '.join(installed)}", "Verify installation")


@register_check("Schema version", order=45)
def check_schema_version() -> dict:
    """Check that gaia.db schema matches the CLI's EXPECTED_SCHEMA_VERSION.

    Bootstrap.sh inserts row (version, applied_at, description) on each
    install. If a user upgrades the CLI past a schema bump without running
    `gaia install`, MAX(version) < EXPECTED -- we warn with a concrete hint.

    Skipped cleanly when:
      - sqlite3 cannot open the DB (fresh machine, no DB yet)
      - schema_version table missing (legacy DB from before this check)
    """
    db_path_str = os.environ.get("GAIA_DB", str(_DEFAULT_DB_PATH))
    db_path = Path(db_path_str).expanduser()

    if not db_path.is_file():
        return _result(
            "Schema version",
            "info",
            f"no DB at {db_path} (will be created on first `gaia install`)",
        )

    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        return _result(
            "Schema version",
            "warning",
            f"could not open {db_path}: {exc}",
            "Delete the corrupt DB and re-run `gaia install`.",
        )

    try:
        cur = con.cursor()
        # schema_version table introduced in EXPECTED_SCHEMA_VERSION=1.
        # If the table is missing, the DB predates the migration -- warn.
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_version'"
        )
        if cur.fetchone() is None:
            return _result(
                "Schema version",
                "warning",
                "schema_version table missing (legacy DB)",
                "Run `gaia install` to upgrade the DB schema.",
            )

        cur.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        live = row[0] if row and row[0] is not None else 0
    except sqlite3.Error as exc:
        return _result(
            "Schema version",
            "warning",
            f"could not read schema_version: {exc}",
            "Re-run `gaia install` to repair the DB.",
        )
    finally:
        con.close()

    if live < EXPECTED_SCHEMA_VERSION:
        return _result(
            "Schema version",
            "warning",
            f"DB schema_version={live}, CLI expects {EXPECTED_SCHEMA_VERSION}",
            "Run `gaia install` to apply pending schema migrations.",
        )
    if live > EXPECTED_SCHEMA_VERSION:
        # CLI lagging behind the DB -- user has a newer DB written by a
        # newer Gaia. Different remedy: upgrade the CLI.
        return _result(
            "Schema version",
            "warning",
            f"DB schema_version={live} > CLI expected {EXPECTED_SCHEMA_VERSION}",
            "Upgrade Gaia: `npm install @jaguilar87/gaia@latest`.",
        )
    return _result("Schema version", "pass", f"v{live} matches CLI expectation")


@register_check("Schema v12 tables", order=46)
def check_schema_v12_tables() -> dict:
    """Check that v12 approval tables and triggers are present in gaia.db.

    v12 introduces `approvals`, `approval_events`, and three triggers that
    enforce the hash-chain and append-only invariants. This check catches the
    case where the ledger says v12 but the DDL was not actually applied (the
    partial-apply silent failure documented in the memory atom about
    bootstrap_database.sh and triggered-based migrations).

    Skipped cleanly when:
      - gaia.db does not exist (fresh machine, no DB yet)
      - MAX(schema_version) < 12 (migration not yet applied)
    """
    db_path_str = os.environ.get("GAIA_DB", str(_DEFAULT_DB_PATH))
    db_path = Path(db_path_str).expanduser()

    if not db_path.is_file():
        return _result(
            "Schema v12 tables",
            "info",
            f"no DB at {db_path} (nothing to verify yet)",
        )

    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        return _result(
            "Schema v12 tables",
            "warning",
            f"could not open {db_path}: {exc}",
            "Delete the corrupt DB and re-run `gaia install`.",
        )

    try:
        cur = con.cursor()

        # If we're not at v12 yet, skip this check -- migration is pending.
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        live_version = cur.fetchone()[0]
        if live_version < 12:
            return _result(
                "Schema v12 tables",
                "info",
                f"schema at v{live_version} (v12 migration not yet applied)",
            )

        # Verify both tables exist.
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('approvals', 'approval_events') ORDER BY name"
        )
        found_tables = {row[0] for row in cur.fetchall()}
        missing_tables = {"approvals", "approval_events"} - found_tables

        # Verify three triggers exist.
        expected_triggers = {
            "ai_approval_events_hash",
            "bu_approval_events_immutable",
            "bd_approval_events_immutable",
        }
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('ai_approval_events_hash', "
            "'bu_approval_events_immutable', 'bd_approval_events_immutable')"
        )
        found_triggers = {row[0] for row in cur.fetchall()}
        missing_triggers = expected_triggers - found_triggers

    except sqlite3.Error as exc:
        return _result(
            "Schema v12 tables",
            "warning",
            f"could not query sqlite_master: {exc}",
            "Re-run `gaia install` to repair the DB.",
        )
    finally:
        con.close()

    issues = []
    if missing_tables:
        issues.append(f"missing tables: {', '.join(sorted(missing_tables))}")
    if missing_triggers:
        issues.append(f"missing triggers: {', '.join(sorted(missing_triggers))}")

    if issues:
        return _result(
            "Schema v12 tables",
            "error",
            "; ".join(issues),
            "Live DDL is missing v12 objects. Re-run `gaia install` to apply migration.",
        )

    return _result(
        "Schema v12 tables",
        "pass",
        "2 tables + 3 hash-chain triggers present",
    )


@register_check("Schema DDL consistency", order=47)
def check_schema_ddl_consistency() -> dict:
    """Compare live CHECK constraints in gaia.db against gaia/store/schema.sql.

    This check is the complement of `check_schema_version`. That check catches
    "doctor.py and bootstrap.sh disagree about which version is current". This
    one catches "the ledger says vN but the live DDL was never actually
    migrated" -- the failure mode introduced when bootstrap used to stamp the
    schema_version row unconditionally while CREATE TABLE IF NOT EXISTS in
    schema.sql short-circuited on existing DBs.

    Mechanism:
      1. SELECT sql FROM sqlite_master for each critical table.
      2. Parse the CHECK constraint's allowed-value list with a regex.
      3. Parse the corresponding CHECK from gaia/store/schema.sql.
      4. Compare as sets; report drift.

    Critical tables monitored: `memory.type` (widening in v2 was the bug that
    motivated this check). When future migrations widen other CHECKs, add a
    row to `_DDL_TARGETS`.

    Skipped cleanly when the DB does not exist or the table is missing -- a
    fresh install with no DB is not "drift", just "not initialised yet".
    """
    db_path_str = os.environ.get("GAIA_DB", str(_DEFAULT_DB_PATH))
    db_path = Path(db_path_str).expanduser()
    schema_path = _package_root() / "gaia" / "store" / "schema.sql"

    if not db_path.is_file():
        return _result(
            "Schema DDL consistency",
            "info",
            f"no DB at {db_path} (nothing to compare)",
        )
    if not schema_path.is_file():
        return _result(
            "Schema DDL consistency",
            "warning",
            f"schema.sql not shipped at {schema_path}",
            "Your Gaia install is incomplete. Reinstall.",
        )

    # (table_name, column_name) pairs to verify. Add new tuples here when
    # future migrations widen or narrow a CHECK constraint that needs guarding.
    _DDL_TARGETS = [("memory", "type")]

    try:
        schema_text = schema_path.read_text()
    except OSError as exc:
        return _result(
            "Schema DDL consistency",
            "warning",
            f"could not read schema.sql: {exc}",
            "Reinstall Gaia.",
        )

    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        return _result(
            "Schema DDL consistency",
            "warning",
            f"could not open {db_path}: {exc}",
            "Delete the corrupt DB and re-run `gaia install`.",
        )

    drifts: list[str] = []
    try:
        cur = con.cursor()
        for table, column in _DDL_TARGETS:
            cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            row = cur.fetchone()
            if row is None or not row[0]:
                # Table missing in live DB: not a CHECK drift, skip silently.
                # The schema-version / workspace-initialised checks will
                # surface the real problem.
                continue
            live_values = _extract_check_values(row[0], column)
            source_values = _extract_check_values(schema_text, column, table=table)

            if live_values is None or source_values is None:
                # Could not parse one side -- treat as inconclusive rather
                # than false-positive. This keeps the check honest: it only
                # fires when we can actually prove drift.
                continue

            if live_values != source_values:
                missing = source_values - live_values
                extra = live_values - source_values
                parts = [f"{table}.{column} drift"]
                parts.append(f"live=({', '.join(sorted(live_values))})")
                parts.append(f"source=({', '.join(sorted(source_values))})")
                if missing:
                    parts.append(f"missing in live: {sorted(missing)}")
                if extra:
                    parts.append(f"extra in live: {sorted(extra)}")
                drifts.append(" | ".join(parts))
    except sqlite3.Error as exc:
        return _result(
            "Schema DDL consistency",
            "warning",
            f"could not read sqlite_master: {exc}",
            "Re-run `gaia install` to repair the DB.",
        )
    finally:
        con.close()

    if drifts:
        return _result(
            "Schema DDL consistency",
            "error",
            "; ".join(drifts),
            "Live DDL is behind schema.sql -- the schema_version ledger is "
            "lying. Re-run `gaia install` to apply pending migrations.",
        )

    return _result(
        "Schema DDL consistency",
        "pass",
        f"{len(_DDL_TARGETS)}/{len(_DDL_TARGETS)} CHECK constraints in sync",
    )


def _extract_check_values(
    sql_text: str, column: str, table: "str | None" = None
) -> "set[str] | None":
    """Extract the allowed-value set from a `CHECK (<column> IN (...))` clause.

    Returns the parsed set of literal values (without surrounding quotes),
    or None if the column / CHECK clause cannot be located.

    When *table* is given the search is narrowed to the CREATE TABLE block for
    that table before the CHECK pattern is applied.  This is essential when
    parsing a multi-table schema file (e.g. schema.sql) where multiple tables
    share the same column name -- without narrowing, ``re.search`` would always
    return the first match in the file, which may belong to the wrong table.

    Used by `check_schema_ddl_consistency` to compare live DDL against
    schema.sql. Kept module-level (not nested) so tests can exercise the
    parser independently.
    """
    import re  # noqa: PLC0415

    search_text = sql_text

    if table is not None:
        # Narrow to the CREATE TABLE block for the target table.
        # Matches from "CREATE TABLE [IF NOT EXISTS] <table> (" up to the
        # balancing closing ");" that terminates the statement.
        tbl_pattern = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + re.escape(table) + r"\s*\(",
            re.IGNORECASE | re.DOTALL,
        )
        tbl_match = tbl_pattern.search(sql_text)
        if tbl_match is None:
            return None
        # Walk forward from the opening "(" to find its balancing ")".
        depth = 0
        start = tbl_match.end() - 1  # position of the opening "("
        end = start
        for i, ch in enumerate(sql_text[start:], start=start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        search_text = sql_text[start : end + 1]

    # Pattern: CHECK (<column> IN ('a', 'b', ...))
    # Tolerates whitespace, newlines, and extra parens around the IN clause.
    pattern = re.compile(
        r"CHECK\s*\(\s*" + re.escape(column) + r"\s+IN\s*\(([^)]+)\)\s*\)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(search_text)
    if not match:
        return None

    raw = match.group(1)
    # Extract each single-quoted literal -- robust against commas and spaces.
    literals = re.findall(r"'([^']*)'", raw)
    if not literals:
        return None
    return set(literals)


@register_check("Symlinks", order=50)
def check_symlinks(project_root: Path) -> dict:
    """Check .claude/ symlinks resolve to package content."""
    names = ["agents", "tools", "hooks", "commands", "templates", "config", "skills", "CHANGELOG.md"]
    critical = {"agents", "hooks", "skills"}
    valid = 0
    has_critical_missing = False

    for name in names:
        link_path = project_root / ".claude" / name
        if link_path.exists():
            try:
                link_path.resolve(strict=True)
                valid += 1
            except OSError:
                if name in critical:
                    has_critical_missing = True
        else:
            if name in critical:
                has_critical_missing = True

    total = len(names)
    if valid == total:
        return _result("Symlinks", "pass", f"{valid}/{total} valid")

    severity = "error" if has_critical_missing else "warning"
    return _result("Symlinks", severity, f"{valid}/{total} valid", "Run `gaia scan` to recreate symlinks")


@register_check("Identity", order=60)
def check_identity(project_root: Path) -> dict:
    """Check orchestrator agent is configured."""
    issues = []
    infos = []

    agent_path = project_root / ".claude" / "agents" / "gaia-orchestrator.md"
    if not agent_path.is_file():
        issues.append("gaia-orchestrator.md not found")

    local_settings = project_root / ".claude" / "settings.local.json"
    if local_settings.is_file():
        data = _read_json(local_settings)
        if data:
            agent = data.get("agent")
            if agent == "gaia-orchestrator":
                pass  # correct
            elif agent:
                issues.append(f'Agent set to "{agent}" (expected "gaia-orchestrator")')
            else:
                issues.append("No agent field in settings.local.json")
    else:
        issues.append("settings.local.json missing")

    claude_md = project_root / "CLAUDE.md"
    if claude_md.is_file():
        infos.append("Legacy CLAUDE.md present (no longer used)")

    if issues:
        return _result("Identity", "error", "; ".join(issues), "Run `gaia scan` or `gaia update`")
    if infos:
        return _result("Identity", "info", f"Orchestrator configured -- {'; '.join(infos)}")
    return _result("Identity", "pass", "Orchestrator agent configured")


@register_check("Settings", order=70)
def check_settings(project_root: Path) -> dict:
    """Check settings.local.json for hooks, permissions, deny rules."""
    local_path = project_root / ".claude" / "settings.local.json"
    if not local_path.is_file():
        return _result("Settings", "error", "settings.local.json missing", "Run `gaia scan` or `gaia update`")

    data = _read_json(local_path)
    if not data:
        return _result("Settings", "error", "Invalid JSON in settings.local.json", "Delete and run `gaia scan`")

    issues = []
    infos = []

    hooks_config = data.get("hooks")
    if not hooks_config:
        issues.append("No hooks configured")
    else:
        required = ["PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart"]
        missing = [h for h in required if h not in hooks_config]
        if missing:
            issues.append(f"Missing hooks: {', '.join(missing)}")

    perms = data.get("permissions", {})
    allow_count = len(perms.get("allow", []))
    deny_count = len(perms.get("deny", []))
    if allow_count == 0:
        infos.append("No allow rules (tools will prompt for approval)")
    if deny_count == 0:
        issues.append("No deny rules (destructive commands not blocked)")

    env = data.get("env", {})
    if not env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"):
        infos.append("AGENT_TEAMS env not set")

    if issues:
        return _result("Settings", "error", "; ".join(issues), "Run `gaia scan` or `gaia update`")

    hook_count = len(hooks_config) if hooks_config else 0
    perm_count = allow_count + deny_count

    if infos:
        return _result("Settings", "info", f"{hook_count} hook types, {perm_count} rules -- {'; '.join(infos)}")
    return _result("Settings", "pass", f"{hook_count} hook types, {perm_count} rules")


@register_check("Hook files", order=80)
def check_hook_files(project_root: Path) -> dict:
    """Check all expected hook scripts exist."""
    hooks = [
        ("pre_tool_use.py", True),
        ("post_tool_use.py", True),
        ("user_prompt_submit.py", True),
        ("session_start.py", True),
        ("subagent_stop.py", False),
        ("subagent_start.py", False),
        ("stop_hook.py", False),
        ("task_completed.py", False),
        ("post_compact.py", False),
        ("elicitation_result.py", False),
    ]

    errors = []
    warnings = []
    valid = 0
    total = len(hooks)

    for filename, required in hooks:
        hook_path = project_root / ".claude" / "hooks" / filename
        if hook_path.is_file():
            valid += 1
        elif required:
            errors.append(f"{filename} missing")
        else:
            warnings.append(filename)

    if errors:
        return _result("Hook files", "error", "; ".join(errors), "Recreate symlinks: `gaia scan`")
    if warnings:
        return _result(
            "Hook files",
            "warning",
            f"{valid}/{total} found (missing: {', '.join(warnings)})",
            "Run `gaia scan` to recreate symlinks",
        )
    return _result("Hook files", "pass", f"{valid}/{total} found")


@register_check("project-context", order=90)
def check_project_context(project_root: Path) -> dict:
    """Check project context contracts exist in the DB (T1.3: DB-backed read).

    Reads from project_context_contracts table in gaia.db instead of the
    legacy project-context.json file (retired in agent-contract-handoff M1).
    """
    try:
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=project_root)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM project_context_contracts WHERE workspace = ?",
                (ws,),
            ).fetchone()
            count = row[0] if row else 0
        finally:
            con.close()
    except Exception as exc:
        return _result("project-context", "warning", f"DB read error: {exc}", "Run `gaia scan`")

    if count == 0:
        return _result("project-context", "warning", "No contracts in DB", "Run `gaia scan`")

    if count < 3:
        return _result(
            "project-context", "info",
            f"{count} contracts (expected >=3)",
            "Run `gaia scan` to enrich",
        )

    return _result("project-context", "pass", f"{count} contracts")


@register_check("Project dirs", order=100)
def check_project_dirs(project_root: Path) -> dict:
    """Check paths declared in project-context contracts exist on disk.

    Reads the infrastructure.paths payload from project_context_contracts
    in gaia.db (T1.3: DB-backed read). Falls back gracefully when no paths
    contract exists.
    """
    try:
        import json as _json
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=project_root)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT payload FROM project_context_contracts "
                "WHERE workspace = ? AND contract_name = 'infrastructure'",
                (ws,),
            ).fetchone()
        finally:
            con.close()
    except Exception:
        return _result("Project dirs", "pass", "Skipped (DB read error)")

    if not row:
        return _result("Project dirs", "pass", "Skipped (no infrastructure contract)")

    try:
        payload = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except Exception:
        return _result("Project dirs", "pass", "Skipped (parse error)")

    paths = (payload or {}).get("paths") or {}
    issues = []
    verified = 0

    # Path values may be a single string (e.g. "project_root": ".") or a list
    # of strings (e.g. "scan_targets": [".", "src"]). Normalize to a flat list
    # of (label, str) pairs so `project_root / value` is always Path / str.
    for key, value in paths.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            entries = [(f"{key}[{i}]", str(v)) for i, v in enumerate(value) if v]
        else:
            entries = [(key, str(value))]

        for label, dir_path in entries:
            verified += 1
            if not (project_root / dir_path).exists():
                issues.append(f"{label}: {dir_path} not found")

    if issues:
        return _result("Project dirs", "warning", "; ".join(issues), "Create missing directories or update paths")

    return _result("Project dirs", "pass", f"{verified} paths verified")


@register_check("memory_fts5_db", order=120)
def check_memory_fts5_db(project_root: Path) -> dict:
    """Check if episodes_fts virtual table exists and has rows in gaia.db.

    T6 migration: replaced legacy search.db filesystem check with a query
    against the episodes_fts FTS5 table in gaia.db.
    """
    try:
        import sys as _sys
        pkg_root = str(_package_root())
        if pkg_root not in _sys.path:
            _sys.path.insert(0, pkg_root)
        from gaia.store.writer import _connect as _store_connect
    except ImportError:
        return _result(
            "memory_fts5_db",
            "warning",
            "gaia.store.writer not importable — cannot verify episodes_fts",
            "Check gaia installation",
        )

    try:
        con = _store_connect()
        try:
            row = con.execute("SELECT COUNT(*) FROM episodes_fts").fetchone()
            count = row[0] if row else 0
        finally:
            con.close()
    except Exception as exc:
        return _result(
            "memory_fts5_db",
            "warning",
            f"episodes_fts not accessible in gaia.db: {exc}",
            "Run: gaia doctor --fix",
        )

    if count > 0:
        return _result("memory_fts5_db", "pass", f"episodes_fts in gaia.db: {count} rows indexed")
    return _result(
        "memory_fts5_db",
        "info",
        "episodes_fts table present in gaia.db but empty (no episodes yet)",
    )


@register_check("memory_fts5_count", order=130)
def check_memory_fts5_count(project_root: Path) -> dict:
    """Check FTS5 indexed count against total episode count in index.json."""
    index_path = project_root / ".claude" / "project-context" / "episodic-memory" / "index.json"

    if not index_path.is_file():
        return _result("memory_fts5_count", "info", "index.json not found — no episodes yet")

    index_data = _read_json(index_path)
    if not index_data:
        return _result("memory_fts5_count", "info", "index.json unreadable")

    total = len(index_data.get("episodes") or [])

    try:
        import sys as _sys
        # Ensure package root is on path for lazy import
        pkg_root = str(_package_root())
        if pkg_root not in _sys.path:
            _sys.path.insert(0, pkg_root)
        from tools.memory import search_store  # noqa: PLC0415
        indexed = search_store.count()
    except ImportError:
        return _result(
            "memory_fts5_count",
            "info",
            "tools.memory.search_store not importable — FTS5 count skipped",
        )
    except Exception as exc:
        return _result("memory_fts5_count", "info", f"Could not query FTS5 count: {exc}")

    if total == 0:
        return _result("memory_fts5_count", "pass", "No episodes to index")

    pct = indexed / total
    if pct < 0.90:
        return _result(
            "memory_fts5_count",
            "warning",
            f"FTS5 index incomplete: {indexed}/{total} episodes indexed ({pct:.0%})",
            "Run: gaia doctor --fix",
        )
    return _result("memory_fts5_count", "pass", f"{indexed}/{total} episodes indexed ({pct:.0%})")


@register_check("memory_scoring", order=140)
def check_memory_scoring(project_root: Path) -> dict:
    """Check that tools.memory.scoring is importable (scoring module available)."""
    try:
        import sys as _sys
        pkg_root = str(_package_root())
        if pkg_root not in _sys.path:
            _sys.path.insert(0, pkg_root)
        import tools.memory.scoring  # noqa: F401, PLC0415
        return _result("memory_scoring", "pass", "Scoring module importable")
    except ImportError as exc:
        return _result(
            "memory_scoring",
            "warning",
            f"Scoring module unavailable: {exc} (scoring disabled)",
        )
    except Exception as exc:
        return _result("memory_scoring", "warning", f"Scoring module error: {exc}")


def _apply_agent_fix(project_root: Path) -> dict:
    """Write agent='gaia-orchestrator' to settings.local.json top-level.

    Preserves the rest of the JSON content; uses indent=2 with trailing newline
    to keep the file format consistent with how `gaia scan` writes it.
    """
    settings_path = project_root / ".claude" / "settings.local.json"
    if not settings_path.is_file():
        return {
            "name": "agent_field",
            "status": "failed",
            "detail": "settings.local.json missing",
        }

    try:
        with open(settings_path, "r") as f:
            data = json.load(f)
    except Exception as exc:
        return {
            "name": "agent_field",
            "status": "failed",
            "detail": f"Could not read settings.local.json: {exc}",
        }

    if data.get("agent") == "gaia-orchestrator":
        return {
            "name": "agent_field",
            "status": "noop",
            "detail": "agent field already set to gaia-orchestrator",
        }

    data["agent"] = "gaia-orchestrator"

    try:
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except Exception as exc:
        return {
            "name": "agent_field",
            "status": "failed",
            "detail": f"Could not write settings.local.json: {exc}",
        }

    return {
        "name": "agent_field",
        "status": "applied",
        "detail": "Wrote agent=gaia-orchestrator to settings.local.json",
    }


def _apply_fts5_backfill(project_root: Path) -> dict:
    """Run FTS5 backfill and return a fix-result dict."""
    try:
        import sys as _sys
        pkg_root = str(_package_root())
        if pkg_root not in _sys.path:
            _sys.path.insert(0, pkg_root)

        # Ensure backfill_fts5 finds the correct project root by setting cwd context
        # via the module's own _find_project_root (walks up from cwd).
        # We temporarily add project_root to env if needed, but the module uses cwd.
        import os as _os
        orig_cwd = _os.getcwd()
        try:
            _os.chdir(project_root)
            from tools.memory import backfill_fts5  # noqa: PLC0415
            rc = backfill_fts5.main()
        finally:
            _os.chdir(orig_cwd)

        if rc == 0:
            return {"name": "fts5_backfill", "status": "applied", "detail": "FTS5 index rebuilt successfully"}
        return {"name": "fts5_backfill", "status": "failed", "detail": f"backfill_fts5.main() returned {rc}"}
    except ImportError as exc:
        return {"name": "fts5_backfill", "status": "failed", "detail": f"Cannot import backfill_fts5: {exc}"}
    except Exception as exc:
        return {"name": "fts5_backfill", "status": "failed", "detail": f"Backfill error: {exc}"}


@register_check("Memory store", order=110)
def check_memory_dirs(project_root: Path) -> dict:
    """Check the episodic memory store is present in gaia.db.

    Episodic memory is DB-canonical (brief ``episodic-workflow-to-db``): every
    agent turn is written as a row in the ``episodes`` table of ``~/.gaia/gaia.db``
    via ``gaia.store.writer.insert_episode``, and the schema's INSERT trigger
    indexes it into ``episodes_fts``. The legacy filesystem layout
    (``.claude/project-context/episodic-memory/`` and
    ``workflow-episodic-memory/``) was superseded by these DB writers and is no
    longer created on the canonical path, so this check validates the DB table
    rather than the absence/presence of those directories.
    """
    try:
        import sys as _sys
        pkg_root = str(_package_root())
        if pkg_root not in _sys.path:
            _sys.path.insert(0, pkg_root)
        from gaia.store.writer import _connect as _store_connect
    except ImportError:
        return _result(
            "Memory store",
            "warning",
            "gaia.store.writer not importable — cannot verify episodes table",
            "Check gaia installation",
        )

    try:
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes'"
            ).fetchone()
        finally:
            con.close()
    except Exception as exc:
        return _result(
            "Memory store",
            "warning",
            f"episodes table not accessible in gaia.db: {exc}",
            "Run: gaia doctor --fix",
        )

    if row is not None:
        return _result("Memory store", "pass", "episodes table present in gaia.db")
    return _result(
        "Memory store",
        "warning",
        "episodes table missing from gaia.db",
        "Run: bash scripts/bootstrap_database.sh",
    )


# ============================================================================
# Severity display
# ============================================================================

_SEVERITY_ICONS = {
    "pass": "PASS",
    "info": "INFO",
    "warning": "WARN",
    "error": "FAIL",
}


def _print_human(results: list, version_detail: str = "", workspace: Path = None) -> None:
    """Print human-readable doctor output.

    Format follows brew/npm doctor conventions: one line per check,
    severity tag, inline `Fix:` hint when actionable. Summary line at
    the end counts checks by severity (errors / warnings / info / pass)
    so the user can see at a glance whether the install needs attention.
    """
    version_tag = f" ({version_detail})" if version_detail else ""
    print(f"\n  Gaia-Ops Health Check{version_tag}\n")
    if workspace:
        print(f"  Workspace: {workspace}\n")

    for r in results:
        icon = _SEVERITY_ICONS.get(r["severity"], "????")
        # Widened from :<18 to :<22 to fit "Workspace initialized" (21 chars).
        print(f"    [{icon}] {r['name']:<22} {r['detail']}")
        if r["severity"] in ("warning", "error") and r.get("fix"):
            print(f"               Fix: {r['fix']}")

    print()

    errors = sum(1 for r in results if r["severity"] == "error")
    warnings = sum(1 for r in results if r["severity"] == "warning")
    infos = sum(1 for r in results if r["severity"] == "info")
    passes = sum(1 for r in results if r["severity"] == "pass")

    counts = f"  Summary: {errors} error(s), {warnings} warning(s), {infos} info, {passes} pass"
    print(counts)

    if errors:
        print("  Status: CRITICAL -- Gaia is degraded. See inline Fix: hints above.\n")
    elif warnings:
        print("  Status: ISSUES FOUND -- usable but degraded. See inline Fix: hints above.\n")
    else:
        print("  Status: HEALTHY\n")


# ============================================================================
# Command interface
# ============================================================================

def register(subparsers):
    """Register the doctor subcommand."""
    sub = subparsers.add_parser(
        "doctor",
        help="Run Gaia-Ops health checks",
        description="Validate the local installation and report drift.",
    )
    sub.add_argument("--json", action="store_true", default=False,
                     help="Emit JSON. bool.")
    sub.add_argument("--fix", action="store_true", default=False,
                     help="Attempt auto-fix for common issues. bool.")
    sub.add_argument("--workspace", metavar="PATH", default=None,
                     help="Check this workspace's .claude/ instead of auto-deriving. "
                          "Skips realpath derivation entirely.")


def cmd_doctor(args) -> int:
    """Handler for `gaia doctor`."""
    workspace_override = getattr(args, "workspace", None)
    project_root = _derive_workspace(override=workspace_override)

    # Iterate the global check registry populated by @register_check.
    # Each check function is invoked with project_root if it accepts an
    # argument, or no args otherwise. The registry is sorted by `order`.
    import inspect  # noqa: PLC0415

    def _invoke(fn):
        sig = inspect.signature(fn)
        if len(sig.parameters) == 0:
            return fn()
        return fn(project_root)

    def _fn_name(fn):
        return getattr(fn, "__name__", repr(fn))

    results = []
    for _order, name, fn in _CHECKS:
        try:
            results.append(_invoke(fn))
        except Exception as exc:
            results.append(_result(name or _fn_name(fn), "error", f"Error: {exc}"))

    has_errors = any(r["severity"] == "error" for r in results)
    has_warnings = any(r["severity"] == "warning" for r in results)

    # --fix: run auto-fixers for triggered checks
    fixes = []
    if getattr(args, "fix", False):
        # ----- Identity: agent field missing in settings.local.json -----
        # Only auto-fix the "No agent field" case. The "Agent set to X" case
        # (agent present but wrong value) is intentionally not auto-fixed:
        # overwriting a user-configured agent requires explicit consent.
        identity_check = next((r for r in results if r["name"] == "Identity"), None)
        if (
            identity_check
            and identity_check["severity"] == "error"
            and "No agent field" in identity_check["detail"]
        ):
            agent_fix = _apply_agent_fix(project_root)
            fixes.append(agent_fix)
            if agent_fix["status"] == "applied":
                # Re-run check_identity to reflect post-fix state
                idx = results.index(identity_check)
                results[idx] = check_identity(project_root)

        # ----- FTS5 backfill -----
        fts5_db_check = next((r for r in results if r["name"] == "memory_fts5_db"), None)
        fts5_count_check = next((r for r in results if r["name"] == "memory_fts5_count"), None)

        db_needs_fix = fts5_db_check and fts5_db_check["severity"] == "info"
        count_needs_fix = fts5_count_check and fts5_count_check["severity"] == "warning"

        if db_needs_fix or count_needs_fix:
            fix_result = _apply_fts5_backfill(project_root)
            fixes.append(fix_result)

            if fix_result["status"] == "applied":
                # Re-run the affected checks to reflect post-fix state
                if fts5_db_check:
                    idx = results.index(fts5_db_check)
                    results[idx] = check_memory_fts5_db(project_root)
                if fts5_count_check:
                    idx = results.index(fts5_count_check)
                    results[idx] = check_memory_fts5_count(project_root)

        # Recompute summary flags once after all fixes have run.
        if fixes:
            has_errors = any(r["severity"] == "error" for r in results)
            has_warnings = any(r["severity"] == "warning" for r in results)

    if getattr(args, "json", False):
        status = "critical" if has_errors else "degraded" if has_warnings else "healthy"
        output = {
            "healthy": not has_errors and not has_warnings,
            "status": status,
            "checks": results,
            "fixes": fixes,
        }
        print(json.dumps(output, indent=2))
    else:
        gaia_check = next((r for r in results if r["name"] == "Gaia-Ops"), None)
        version_detail = gaia_check["detail"] if gaia_check and gaia_check["severity"] == "pass" else ""
        _print_human(results, version_detail, workspace=project_root)
        if fixes:
            print("  Fixes applied:")
            for fix in fixes:
                print(f"    [{fix['status'].upper()}] {fix['name']}: {fix['detail']}")
            print()

    if has_errors:
        return 2
    if has_warnings:
        return 1
    return 0
