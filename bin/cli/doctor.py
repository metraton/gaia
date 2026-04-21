"""
gaia doctor -- Health check for Gaia-Ops installation.

Mirrors the checks in gaia-doctor.js:
  1. gaia-version     - package.json readable
  2. claude-code      - CLI installed
  3. python           - Python 3.9+ available
  4. plugin-mode      - ops vs security, registry valid
  5. symlinks         - .claude/ symlinks resolve
  6. identity         - orchestrator agent configured
  7. settings         - hooks registered, permissions, deny rules
  8. hook-files       - all hook scripts present
  9. project-context  - project-context.json valid
 10. project-dirs     - paths declared in context exist
 11. memory-dirs      - episodic memory dirs present

Severity: pass / info / warning / error
Exit codes: 0=healthy, 1=warnings, 2=errors
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


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


# ============================================================================
# Health Checks
# ============================================================================

def check_gaia_version() -> dict:
    """Check that package.json is readable and has a version."""
    pkg_path = _package_root() / "package.json"
    data = _read_json(pkg_path)
    if data and "version" in data:
        return _result("Gaia-Ops", "pass", f"v{data['version']}")
    return _result("Gaia-Ops", "error", "Version unknown", "Reinstall @jaguilar87/gaia-ops")


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


def check_plugin_mode(project_root: Path) -> dict:
    """Check plugin mode from plugin-registry.json."""
    registry_path = project_root / ".claude" / "plugin-registry.json"
    if not registry_path.is_file():
        return _result("Plugin mode", "warning", "No plugin-registry.json", "Run gaia-scan or restart Claude Code")

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
    return _result("Symlinks", severity, f"{valid}/{total} valid", "Run gaia-scan to recreate symlinks")


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
        return _result("Identity", "error", "; ".join(issues), "Run gaia-scan or gaia update")
    if infos:
        return _result("Identity", "info", f"Orchestrator configured -- {'; '.join(infos)}")
    return _result("Identity", "pass", "Orchestrator agent configured")


def check_settings(project_root: Path) -> dict:
    """Check settings.local.json for hooks, permissions, deny rules."""
    local_path = project_root / ".claude" / "settings.local.json"
    if not local_path.is_file():
        return _result("Settings", "error", "settings.local.json missing", "Run gaia-scan or gaia update")

    data = _read_json(local_path)
    if not data:
        return _result("Settings", "error", "Invalid JSON in settings.local.json", "Delete and run gaia-scan")

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
        return _result("Settings", "error", "; ".join(issues), "Run gaia-scan or gaia update")

    hook_count = len(hooks_config) if hooks_config else 0
    perm_count = allow_count + deny_count

    if infos:
        return _result("Settings", "info", f"{hook_count} hook types, {perm_count} rules -- {'; '.join(infos)}")
    return _result("Settings", "pass", f"{hook_count} hook types, {perm_count} rules")


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
        return _result("Hook files", "error", "; ".join(errors), "Recreate symlinks: gaia-scan")
    if warnings:
        return _result(
            "Hook files",
            "warning",
            f"{valid}/{total} found (missing: {', '.join(warnings)})",
            "Run gaia-scan to recreate symlinks",
        )
    return _result("Hook files", "pass", f"{valid}/{total} found")


def check_project_context(project_root: Path) -> dict:
    """Check project-context.json is valid and enriched."""
    path = project_root / ".claude" / "project-context" / "project-context.json"
    if not path.is_file():
        return _result("project-context", "warning", "Missing", "Run gaia-scan")

    data = _read_json(path)
    if not data:
        return _result("project-context", "warning", "Invalid JSON", "Regenerate with gaia-scan")

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
        return _result("project-context", "warning", detail, "Run gaia-scan to enrich")

    if infos:
        return _result("project-context", "info", f"{section_count} sections -- {'; '.join(infos)}")

    return _result("project-context", "pass", f"{section_count} sections")


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

    for key, dir_path in paths.items():
        if dir_path and not (project_root / dir_path).exists():
            issues.append(f"{key}: {dir_path} not found")

    if issues:
        return _result("Project dirs", "warning", "; ".join(issues), "Create missing directories or update paths")

    return _result("Project dirs", "pass", f"{len(paths)} paths verified")


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


def check_memory_dirs(project_root: Path) -> dict:
    """Check episodic memory directories are present."""
    checks = [
        (
            project_root / ".claude" / "project-context" / "workflow-episodic-memory",
            "workflow-episodic-memory",
            "warning",
            "Run gaia-scan to create workflow memory directory",
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
    """Print human-readable doctor output."""
    version_tag = f" ({version_detail})" if version_detail else ""
    print(f"\n  Gaia-Ops Health Check{version_tag}\n")

    for r in results:
        icon = _SEVERITY_ICONS.get(r["severity"], "????")
        print(f"    [{icon}] {r['name']:<18} {r['detail']}")
        if r["severity"] in ("warning", "error") and r.get("fix"):
            print(f"           Fix: {r['fix']}")

    print()

    has_errors = any(r["severity"] == "error" for r in results)
    has_warnings = any(r["severity"] == "warning" for r in results)

    if has_errors:
        print("  Status: CRITICAL\n")
    elif has_warnings:
        print("  Status: ISSUES FOUND\n")
    else:
        print("  Status: HEALTHY\n")


# ============================================================================
# Command interface
# ============================================================================

def register(subparsers):
    """Register the doctor subcommand."""
    sub = subparsers.add_parser("doctor", help="Run Gaia-Ops health checks")
    sub.add_argument("--json", action="store_true", default=False, help="Output as JSON")
    sub.add_argument("--fix", action="store_true", default=False, help="Attempt auto-fix for common issues")


def cmd_doctor(args) -> int:
    """Handler for `gaia doctor`."""
    project_root = _find_project_root()

    check_fns = [
        lambda: check_gaia_version(),
        lambda: check_claude_code(),
        lambda: check_python(),
        lambda: check_plugin_mode(project_root),
        lambda: check_symlinks(project_root),
        lambda: check_identity(project_root),
        lambda: check_settings(project_root),
        lambda: check_hook_files(project_root),
        lambda: check_project_context(project_root),
        lambda: check_project_dirs(project_root),
        lambda: check_memory_dirs(project_root),
        lambda: check_memory_fts5_db(project_root),
        lambda: check_memory_fts5_count(project_root),
        lambda: check_memory_scoring(project_root),
    ]

    results = []
    for fn in check_fns:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(_result(fn.__name__, "error", f"Error: {exc}"))

    has_errors = any(r["severity"] == "error" for r in results)
    has_warnings = any(r["severity"] == "warning" for r in results)

    # --fix: run auto-fixers for triggered checks
    fixes = []
    if getattr(args, "fix", False):
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

                # Recompute summary flags after re-checks
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
