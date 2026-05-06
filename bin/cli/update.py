"""
gaia update -- Refresh DB schema, .claude/ config, and symlinks after a
package upgrade.

Idempotent end-to-end. Where `gaia install` is "first-time setup",
`gaia update` is "re-sync after npm install bumped the version" -- they
share helpers but differ in orchestration and phrasing.

Order of operations:
  1. Bootstrap DB (no-op if schema already current).
  2. settings.json (create if missing).
  3. settings.local.json -- merge permissions/env/agent.
  4. settings.local.json -- merge hooks (npm mode).
  5. Symlinks under .claude/ (recreate only if broken or stale).
  6. plugin-registry.json (record current version).

Verification (the `--verify` flag) reuses the existing checks so we don't
duplicate doctor's logic. For the legacy 6-check report, see the
`_run_verification` helper preserved here for backward compatibility with
existing tests.

Flags:
  --dry-run   Detect what would change without mutating files.
  --verbose   Show all check results (including passing ones).
  --json      Machine-readable output.
  --skip-bootstrap  Don't invoke bootstrap.sh (helpful when DB is on a
                    read-only mount or already known good).
  --workspace PATH  Override workspace detection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# bin/cli/update.py -> bin/cli -> bin -> gaia/
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOTSTRAP_SCRIPT = _PACKAGE_ROOT / "scripts" / "bootstrap_database.sh"

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from cli import _install_helpers  # type: ignore  # noqa: E402


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
    """The gaia package root (where package.json lives)."""
    return _PACKAGE_ROOT


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
# Bootstrap helper (best-effort, never fatal in update mode)
# ---------------------------------------------------------------------------

def _run_bootstrap_idempotent(verbose: bool) -> dict:
    """Run bootstrap.sh; return result dict with action + details.

    Failures are reported but never abort the update flow -- the user can
    still benefit from settings/symlink fixes even if the DB is unreachable.
    """
    if not _BOOTSTRAP_SCRIPT.is_file():
        return {"action": "skipped", "details": "bootstrap script missing"}
    cmd = ["bash", str(_BOOTSTRAP_SCRIPT)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"action": "error", "details": f"bootstrap failed: {exc}"}

    if result.returncode == 0:
        return {"action": "noop", "details": "DB schema up to date"}
    return {"action": "error", "details": f"bootstrap exited {result.returncode}"}


# ---------------------------------------------------------------------------
# Backward-compat shims for existing tests (test_gaia_update.py imports these)
# ---------------------------------------------------------------------------

def _legacy_settings_shape(helper_res: dict, dry_run: bool) -> dict:
    """Convert a configure_settings_json helper result to legacy status shape."""
    action = helper_res["action"]
    if action == "skipped":
        return {"status": "skipped", "reason": helper_res.get("details", "")}
    if action == "noop":
        return {"status": "ok", "message": helper_res.get("details", "")}
    if action == "created":
        return {"status": "created", "dry_run": dry_run}
    return {"status": action, "details": helper_res.get("details", ""), "dry_run": dry_run}


def _legacy_symlinks_shape(helper_res: dict, dry_run: bool) -> dict:
    """Convert a manage_symlinks helper result to legacy status shape."""
    if helper_res["action"] == "skipped":
        return {"status": "skipped", "reason": helper_res.get("details", "")}
    return {
        "status": "fixed" if helper_res.get("fixed") or helper_res.get("failed") else "ok",
        "fixed": helper_res.get("fixed", []),
        "valid": helper_res.get("valid", []),
        "failed": helper_res.get("failed", []),
        "dry_run": dry_run,
    }


def _check_settings_json(claude_dir: Path, dry_run: bool) -> dict:
    """Compat shim that delegates to _install_helpers.configure_settings_json.

    Kept for backward compatibility with test_gaia_update.py imports.
    Internal callers should use _legacy_settings_shape() against the helper
    result directly to avoid double invocation.
    """
    workspace = claude_dir.parent
    res = _install_helpers.configure_settings_json(workspace, dry_run=dry_run)
    return _legacy_settings_shape(res, dry_run)


def _check_symlinks(claude_dir: Path, pkg_root: Path, dry_run: bool) -> dict:
    """Compat shim that delegates to _install_helpers.manage_symlinks.

    Kept for backward compatibility with test_gaia_update.py imports.
    Internal callers should use _legacy_symlinks_shape() against the helper
    result directly to avoid double invocation.
    """
    workspace = claude_dir.parent
    res = _install_helpers.manage_symlinks(workspace, plugin_root=pkg_root, dry_run=dry_run)
    return _legacy_symlinks_shape(res, dry_run)


# ---------------------------------------------------------------------------
# Verification -- legacy 6-check report
# ---------------------------------------------------------------------------

def _run_verification(claude_dir: Path) -> dict:
    """Run the legacy 6-check health report.

    Note: `gaia doctor` performs a richer set of checks (12 total). This
    function is preserved for backward compatibility with the existing
    `--verify` output shape and test_gaia_update.py expectations. New code
    should call `gaia doctor --json` for the canonical health snapshot.
    """
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
        issues.append("project-context.json not found (run `gaia scan`)")

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
        help="Sync Gaia after a package upgrade (settings, hooks, symlinks, registry)",
        description=(
            "Sync Gaia after a package upgrade. Idempotent: every step is a\n"
            "no-op when state is already current.\n"
            "\n"
            "  - Bootstrap DB (re-applies migrations only if needed)\n"
            "  - settings.json (create if missing)\n"
            "  - settings.local.json (merge permissions, env, agent, hooks)\n"
            "  - .claude/<name> symlinks (recreate broken/stale)\n"
            "  - plugin-registry.json (record current version)\n"
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
    p.add_argument(
        "--skip-bootstrap",
        dest="skip_bootstrap",
        action="store_true",
        default=False,
        help="Skip bootstrap.sh invocation (advanced; helpful for ro mounts)",
    )
    p.add_argument(
        "--workspace",
        dest="workspace",
        type=str,
        default=None,
        help="Override workspace detection (default: walk up from cwd)",
    )
    return p


def cmd_update(args) -> int:
    """Execute the update subcommand."""
    workspace_arg = getattr(args, "workspace", None)
    if workspace_arg:
        root = Path(workspace_arg).expanduser().resolve()
    else:
        root = _find_project_root()
    pkg_root = _find_package_root()
    claude_dir = root / ".claude"
    dry_run = getattr(args, "dry_run", False)
    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "json", False)
    skip_bootstrap = getattr(args, "skip_bootstrap", False)

    versions = _detect_versions(root, pkg_root)

    if not as_json:
        current = versions.get("current", "unknown")
        previous = versions.get("previous")
        if previous and previous != current:
            print(f"\nUpdating Gaia from {previous} to {current}...\n")
        else:
            print(f"\nUpdating Gaia (current: {current})...\n")
        if dry_run:
            print("  (dry-run mode -- no files will be modified)\n")

    # Step 1 -- bootstrap DB
    if skip_bootstrap or dry_run:
        bootstrap_result = {"action": "skipped", "details": "skipped (flag or dry-run)"}
    else:
        bootstrap_result = _run_bootstrap_idempotent(verbose=verbose)

    # Steps 2-6 -- workspace helpers (each idempotent + dry-run aware).
    # Order matches `gaia install` so install/update share the same sequence.
    settings_helper = _install_helpers.configure_settings_json(root, dry_run=dry_run)
    perms_helper = _install_helpers.merge_local_permissions(root, dry_run=dry_run)
    hooks_helper = _install_helpers.merge_local_hooks(root, plugin_root=pkg_root, dry_run=dry_run)
    sym_helper = _install_helpers.manage_symlinks(root, plugin_root=pkg_root, dry_run=dry_run)
    reg_helper = _install_helpers.register_plugin(
        root, plugin_root=pkg_root, source="cli-update", dry_run=dry_run,
    )

    # Compat: derive legacy shape from helper results (do NOT re-invoke).
    settings_result = _legacy_settings_shape(settings_helper, dry_run)
    symlinks_result = _legacy_symlinks_shape(sym_helper, dry_run)
    verify_result = _run_verification(claude_dir)

    result = {
        "root": str(root),
        "versions": versions,
        "dry_run": dry_run,
        "bootstrap": bootstrap_result,
        "settings_json": settings_result,
        "permissions": perms_helper,
        "hooks": hooks_helper,
        "symlinks": symlinks_result,
        "plugin_registry": reg_helper,
        "verification": verify_result,
    }

    if as_json:
        print(json.dumps(result, indent=2))
        return 0

    # Human-readable summary
    def _fmt(name: str, helper_res: dict) -> None:
        action = helper_res.get("action", "?")
        details = helper_res.get("details", "")
        if action == "noop" and not verbose:
            return
        icon = {"created": "+", "updated": "~", "noop": "=",
                "skipped": "-", "error": "!"}.get(action, "?")
        print(f"  [{icon}] {name}: {details}")

    _fmt("bootstrap", bootstrap_result)
    _fmt("settings.json", settings_helper)
    _fmt("permissions", perms_helper)
    _fmt("hooks", hooks_helper)
    _fmt("symlinks", sym_helper)
    _fmt("plugin-registry", reg_helper)

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
