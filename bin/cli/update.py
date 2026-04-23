"""
gaia update -- mirror of gaia-update.js

Checks and updates the gaia installation:
- Verify settings.json exists (create if missing)
- Merge permissions into settings.local.json
- Check symlinks (recreate if missing or broken)
- Run verification checks (hooks, python, project-context, config, agents)

Flags:
  --dry-run   Detect what would change without mutating files
  --verbose   Show all check results (including passing ones)
  --json      Machine-readable output
"""

import json
import os
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    start = Path(os.environ.get("INIT_CWD", "")) if os.environ.get("INIT_CWD") else None
    if start and (start / ".claude").exists():
        return start

    current = Path.cwd()
    while True:
        if (current / ".claude").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    return Path(os.environ.get("INIT_CWD", str(Path.cwd())))


def _find_package_root() -> Path:
    """The gaia-ops package root (where package.json lives)."""
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def _read_package_version(pkg_path: Path) -> str:
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
        return data.get("version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def _detect_versions(cwd: Path, pkg_root: Path) -> dict:
    current = _read_package_version(pkg_root / "package.json")
    previous = None

    lock_path = cwd / "package-lock.json"
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            dep = (
                (lock.get("packages") or {}).get("node_modules/@jaguilar87/gaia")
                or (lock.get("dependencies") or {}).get("@jaguilar87/gaia")
            )
            if dep:
                previous = dep.get("version")
        except (json.JSONDecodeError, OSError):
            pass

    return {"current": current, "previous": previous}


# ---------------------------------------------------------------------------
# Update steps
# ---------------------------------------------------------------------------

def _check_settings_json(claude_dir: Path, dry_run: bool) -> dict:
    """Create settings.json if missing (non-invasive, never overwrites)."""
    if not claude_dir.exists():
        return {"status": "skipped", "reason": ".claude/ not found"}

    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        return {"status": "ok", "message": "settings.json already exists"}

    if not dry_run:
        settings_path.write_text("{}\n", encoding="utf-8")

    return {"status": "created", "dry_run": dry_run}


def _check_symlinks(claude_dir: Path, pkg_root: Path, dry_run: bool) -> dict:
    """Check symlinks exist and are not broken; recreate if needed."""
    if not claude_dir.exists():
        return {"status": "skipped", "reason": ".claude/ not found"}

    symlink_names = ["agents", "tools", "hooks", "commands", "templates", "config", "skills"]
    fixed = []
    valid = []
    failed = []

    for name in symlink_names:
        link = claude_dir / name
        target = pkg_root / name

        if not link.exists() and not link.is_symlink():
            if not dry_run:
                try:
                    link.symlink_to(target)
                    fixed.append(name)
                except OSError as exc:
                    failed.append({"name": name, "error": str(exc)})
            else:
                fixed.append(name)
        else:
            # Check if broken
            try:
                link.resolve(strict=True)
                valid.append(name)
            except OSError:
                if not dry_run:
                    try:
                        link.unlink()
                        link.symlink_to(target)
                        fixed.append(name)
                    except OSError as exc:
                        failed.append({"name": name, "error": str(exc)})
                else:
                    fixed.append(name)

    # CHANGELOG.md
    changelog_link = claude_dir / "CHANGELOG.md"
    changelog_src = pkg_root / "CHANGELOG.md"
    if not changelog_link.exists() and not changelog_link.is_symlink():
        if not dry_run:
            try:
                changelog_link.symlink_to(changelog_src)
                fixed.append("CHANGELOG.md")
            except OSError as exc:
                failed.append({"name": "CHANGELOG.md", "error": str(exc)})
        else:
            fixed.append("CHANGELOG.md")
    else:
        valid.append("CHANGELOG.md")

    return {
        "status": "ok" if not fixed and not failed else "fixed",
        "fixed": fixed,
        "valid": valid,
        "failed": failed,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Verification checks
# ---------------------------------------------------------------------------

def _run_verification(claude_dir: Path) -> dict:
    """Run installation health checks (mirrors runVerification in gaia-update.js)."""
    checks = []
    issues = []

    # 1. Hook files
    hook_files = ["pre_tool_use.py", "post_tool_use.py", "subagent_stop.py"]
    for hook in hook_files:
        path = claude_dir / "hooks" / hook
        ok = path.exists()
        checks.append({"name": hook, "ok": ok})
        if not ok:
            issues.append(f"Hook missing: .claude/hooks/{hook}")

    # 2. Python available
    py_cmd = None
    for candidate in ["python3", "python"]:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                py_cmd = candidate
                detail = (result.stdout or result.stderr).strip()
                checks.append({"name": "python3", "ok": True, "detail": detail})
                break
        except (OSError, subprocess.TimeoutExpired):
            pass
    if py_cmd is None:
        checks.append({"name": "python3", "ok": False})
        issues.append("Python 3 not found (required for hooks)")

    # 3. project-context.json
    ctx_path = claude_dir / "project-context" / "project-context.json"
    if ctx_path.exists():
        try:
            ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
            sections = len(ctx.get("sections", {}))
            ok = sections >= 3
            checks.append({"name": "project-context.json", "ok": ok, "detail": f"{sections} sections"})
            if not ok:
                issues.append("project-context.json has fewer than 3 sections")
        except (json.JSONDecodeError, OSError):
            checks.append({"name": "project-context.json", "ok": False})
            issues.append("project-context.json is invalid JSON")
    else:
        checks.append({"name": "project-context.json", "ok": False})
        issues.append("project-context.json not found (run gaia-scan)")

    # 4. Config files
    config_files = ["git_standards.json", "universal-rules.json", "surface-routing.json"]
    for cfg in config_files:
        path = claude_dir / "config" / cfg
        ok = path.exists()
        checks.append({"name": cfg, "ok": ok})

    # 5. Agent definitions
    agent_files = [
        "gaia-orchestrator.md", "gaia-operator.md", "terraform-architect.md",
        "gitops-operator.md", "cloud-troubleshooter.md", "developer.md",
        "gaia-system.md", "gaia-planner.md",
    ]
    agents_ok = sum(1 for a in agent_files if (claude_dir / "agents" / a).exists())
    checks.append({
        "name": "agent definitions",
        "ok": agents_ok == len(agent_files),
        "detail": f"{agents_ok}/{len(agent_files)}",
    })
    if agents_ok < len(agent_files):
        issues.append(f"{len(agent_files) - agents_ok} agent definition(s) missing")

    # 6. hooks.json
    hooks_json_path = claude_dir / "hooks" / "hooks.json"
    if hooks_json_path.exists():
        try:
            hdata = json.loads(hooks_json_path.read_text(encoding="utf-8"))
            has_hooks = bool(hdata.get("hooks") and hdata["hooks"])
            checks.append({"name": "hooks.json", "ok": has_hooks})
            if not has_hooks:
                issues.append("hooks.json has no hooks configured")
        except (json.JSONDecodeError, OSError):
            checks.append({"name": "hooks.json", "ok": False})
            issues.append("hooks.json is invalid")
    else:
        checks.append({"name": "hooks.json", "ok": False})
        issues.append("hooks.json not found (hooks symlink may be broken)")

    passed = sum(1 for c in checks if c["ok"])
    return {"checks": checks, "issues": issues, "passed": passed, "total": len(checks)}


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers):
    """Register the 'update' subcommand."""
    p = subparsers.add_parser(
        "update",
        help="Check and update the gaia installation (settings, symlinks, verification)",
        description=(
            "Update the gaia installation:\n"
            "  - Check settings.json (create if missing)\n"
            "  - Check symlinks (recreate missing/broken)\n"
            "  - Verify installation health (hooks, python, project-context)\n"
            "\n"
            "--dry-run: print what would change without modifying files.\n"
        ),
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Detect what would change without mutating files",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show all check results (including passing ones)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    return p


def cmd_update(args) -> int:
    """Execute the update subcommand."""
    root = _find_project_root()
    pkg_root = _find_package_root()
    claude_dir = root / ".claude"
    dry_run = getattr(args, "dry_run", False)
    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "json", False)

    versions = _detect_versions(root, pkg_root)

    if not as_json:
        current = versions.get("current", "unknown")
        previous = versions.get("previous")
        if previous and previous != current:
            print(f"\ngaia-ops update  {previous} -> {current}\n")
        else:
            print(f"\ngaia-ops update  {current}\n")
        if dry_run:
            print("  (dry-run mode -- no files will be modified)\n")

    settings_result = _check_settings_json(claude_dir, dry_run)
    symlinks_result = _check_symlinks(claude_dir, pkg_root, dry_run)
    verify_result = _run_verification(claude_dir)

    result = {
        "root": str(root),
        "versions": versions,
        "dry_run": dry_run,
        "settings_json": settings_result,
        "symlinks": symlinks_result,
        "verification": verify_result,
    }

    if as_json:
        print(json.dumps(result, indent=2))
        return 0

    # Settings
    s = settings_result
    if s["status"] == "skipped":
        if verbose:
            print(f"  settings.json: skipped ({s.get('reason', '')})")
    elif s["status"] == "ok":
        if verbose:
            print("  settings.json: already exists")
    elif s["status"] == "created":
        verb = "Would create" if dry_run else "Created"
        print(f"  settings.json: {verb}")

    # Symlinks
    sl = symlinks_result
    if sl.get("status") == "skipped":
        if verbose:
            print(f"  Symlinks: skipped ({sl.get('reason', '')})")
    elif sl.get("fixed"):
        verb = "Would fix" if dry_run else "Fixed"
        print(f"  Symlinks: {verb} {len(sl['fixed'])} ({', '.join(sl['fixed'])})")
    else:
        total = len(sl.get("valid", [])) + len(sl.get("fixed", []))
        if verbose:
            print(f"  Symlinks: {total}/{total} valid")

    # Verification
    v = verify_result
    print()
    if v["issues"]:
        print(f"  Health: {v['passed']}/{v['total']} checks passed, {len(v['issues'])} issue(s)")
        for issue in v["issues"]:
            print(f"    - {issue}")
    else:
        print(f"  Health: {v['passed']}/{v['total']} checks passed -- everything up to date")

    if verbose:
        for check in v["checks"]:
            status = "pass" if check["ok"] else "FAIL"
            detail = f"  ({check.get('detail', '')})" if check.get("detail") else ""
            print(f"    [{status}] {check['name']}{detail}")

    print()
    return 0
