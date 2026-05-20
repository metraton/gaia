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
  50. symlinks           - .claude/ symlinks resolve
  60. identity           - orchestrator agent configured
  70. settings           - hooks registered, permissions, deny rules
  80. hook-files         - all hook scripts present
  90. project-context    - project-context.json valid
 100. project-dirs       - paths declared in context exist
 110. memory-dirs        - episodic memory dirs present
 120. memory_fts5_db     - FTS5 search.db present
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


def _find_project_root() -> Path:
    """Walk up from cwd until .claude/ is found."""
    init_cwd = os.environ.get("INIT_CWD")
    if init_cwd and (Path(init_cwd) / ".claude").is_dir():
        return Path(init_cwd)

    current = Path.cwd()
    root = Path(current.anchor)
    while current != root:
        if (current / ".claude").is_dir():
            return current
        current = current.parent

    return Path(init_cwd) if init_cwd else Path.cwd()


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
EXPECTED_SCHEMA_VERSION = 1

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
    """Check project-context.json is valid and enriched."""
    path = project_root / ".claude" / "project-context" / "project-context.json"
    if not path.is_file():
        return _result("project-context", "warning", "Missing", "Run `gaia scan`")

    data = _read_json(path)
    if not data:
        return _result("project-context", "warning", "Invalid JSON", "Regenerate with `gaia scan`")

    warnings = []
    infos = []

    if not data.get("metadata"):
        warnings.append("Missing metadata section")
    if not data.get("sections"):
        warnings.append("Missing sections")

    is_v2 = (data.get("metadata") or {}).get("version") == "2.0"

    has_paths = bool((data.get("sections") or {}).get("infrastructure", {}).get("paths")) if is_v2 else bool(data.get("paths"))
    if not has_paths:
        infos.append("No paths section")

    sections = data.get("sections")
    if sections:
        section_count = len(sections)
        if section_count < 3:
            infos.append(f"Only {section_count} sections (expected >=3)")
    else:
        section_count = 0

    if warnings:
        detail = "; ".join(warnings + infos)
        return _result("project-context", "warning", detail, "Run `gaia scan` to enrich")

    if infos:
        return _result("project-context", "info", f"{section_count} sections -- {'; '.join(infos)}")

    return _result("project-context", "pass", f"{section_count} sections")


@register_check("Project dirs", order=100)
def check_project_dirs(project_root: Path) -> dict:
    """Check paths declared in project-context exist on disk."""
    context_path = project_root / ".claude" / "project-context" / "project-context.json"
    if not context_path.is_file():
        return _result("Project dirs", "pass", "Skipped (no context)")

    data = _read_json(context_path)
    if not data:
        return _result("Project dirs", "pass", "Skipped (parse error)")

    sections = data.get("sections") or {}
    paths = sections.get("infrastructure", {}).get("paths") or data.get("paths") or {}
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
    """Check if the FTS5 search.db exists for episodic memory."""
    db_path = project_root / ".claude" / "project-context" / "episodic-memory" / "search.db"
    if db_path.is_file():
        return _result("memory_fts5_db", "pass", f"search.db present ({db_path.stat().st_size} bytes)")
    return _result(
        "memory_fts5_db",
        "info",
        "search.db not found (created on first use)",
        "Run: gaia doctor --fix",
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


@register_check("Memory dirs", order=110)
def check_memory_dirs(project_root: Path) -> dict:
    """Check episodic memory directories are present."""
    checks = [
        (
            project_root / ".claude" / "project-context" / "workflow-episodic-memory",
            "workflow-episodic-memory",
            "warning",
            "Run `gaia scan` to create workflow memory directory",
        ),
        (
            project_root / ".claude" / "project-context" / "episodic-memory",
            "episodic-memory",
            "info",
            "Created automatically on first agent run",
        ),
    ]

    warnings = []
    infos = []
    found = 0

    for path, label, severity, fix in checks:
        if path.is_dir():
            found += 1
        elif severity == "info":
            infos.append({"label": label, "fix": fix})
        else:
            warnings.append({"label": label, "fix": fix})

    total = len(checks)

    if warnings:
        detail = "; ".join(f"{w['label']} missing" for w in warnings)
        return _result("Memory dirs", "warning", detail, warnings[0]["fix"])

    if infos:
        info_parts = ["{}: {}".format(i["label"], i["fix"]) for i in infos]
        detail = "{}/{} present ({})".format(found, total, "; ".join(info_parts))
        return _result("Memory dirs", "info", detail)

    return _result("Memory dirs", "pass", f"{found}/{total} present")


# ============================================================================
# Severity display
# ============================================================================

_SEVERITY_ICONS = {
    "pass": "PASS",
    "info": "INFO",
    "warning": "WARN",
    "error": "FAIL",
}


def _print_human(results: list, version_detail: str = "") -> None:
    """Print human-readable doctor output.

    Format follows brew/npm doctor conventions: one line per check,
    severity tag, inline `Fix:` hint when actionable. Summary line at
    the end counts checks by severity (errors / warnings / info / pass)
    so the user can see at a glance whether the install needs attention.
    """
    version_tag = f" ({version_detail})" if version_detail else ""
    print(f"\n  Gaia-Ops Health Check{version_tag}\n")

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


def cmd_doctor(args) -> int:
    """Handler for `gaia doctor`."""
    project_root = _find_project_root()

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
        _print_human(results, version_detail)
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
