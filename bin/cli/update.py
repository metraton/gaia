"""
gaia update -- an alias of `gaia install`.

`gaia install` is the only reconciler: it migrates the DB, runs the permission
and routing seeds, wires the workspace, records the manifest, and exits
non-zero when a step fails. `gaia update` takes the same flags and runs
exactly that (`cli.install.cmd_install`), so the two can never drift.

The one thing update adds is `--dry-run` (with `--json`): a read-only preview
of what the workspace helpers would change and the legacy health report
(`_run_verification`). It never touches the DB or the filesystem.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# bin/cli/update.py -> bin/cli -> bin -> gaia/
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from cli import _install_helpers  # type: ignore  # noqa: E402
from cli import install  # type: ignore  # noqa: E402
from cli import migrate  # type: ignore  # noqa: E402

_BOOTSTRAP_SCRIPT = migrate.ENGINE


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
    """Run bootstrap_database.py; return result dict with action + details.

    Failures are reported but never abort the update flow -- the user can
    still benefit from settings/symlink fixes even if the DB is unreachable.
    """
    if not _BOOTSTRAP_SCRIPT.is_file():
        return {"action": "skipped", "details": "bootstrap script missing"}
    try:
        result = migrate.run("apply", capture=not verbose)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"action": "error", "details": f"bootstrap failed: {exc}"}

    if result.returncode == 0:
        return {"action": "noop", "details": "DB schema up to date"}

    # A swallowed failure is a dead end: the migration consent gate refuses on
    # this path and its whole message -- what stopped, and the command that
    # continues deliberately -- lives in stderr.
    if not verbose and result.stderr:
        sys.stderr.write(result.stderr)
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

    # 3. project-context contracts (T1.3: DB-backed read).
    # Reads from project_context_contracts table in gaia.db instead of
    # the legacy project-context.json file.
    try:
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=claude_dir.parent)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM project_context_contracts WHERE workspace = ?",
                (ws,),
            ).fetchone()
            contract_count = row[0] if row else 0
        finally:
            con.close()
        ok = contract_count >= 3
        checks.append({"name": "project-context", "ok": ok, "detail": f"{contract_count} contracts"})
        if not ok:
            issues.append("project-context has fewer than 3 contracts in DB (run `gaia scan`)")
    except Exception as exc:
        checks.append({"name": "project-context", "ok": False})
        issues.append(f"project-context DB read error: {exc}")

    # 4. Surface routing (DB-backed). Routing moved from
    # config/surface-routing.json to the surface_routing table, seeded from
    # each agent's `routing:` frontmatter block at install time. Verify the
    # table carries at least the core surfaces rather than a config file.
    try:
        from gaia.store.writer import _connect as _store_connect
        con = _store_connect()
        try:
            row = con.execute("SELECT COUNT(*) FROM surface_routing").fetchone()
            surface_count = row[0] if row else 0
        finally:
            con.close()
        ok = surface_count >= 6
        checks.append({"name": "surface-routing", "ok": ok, "detail": f"{surface_count} surfaces"})
        if not ok:
            issues.append("surface_routing has fewer than 6 surfaces in DB (run `gaia install`)")
    except Exception as exc:
        checks.append({"name": "surface-routing", "ok": False})
        issues.append(f"surface_routing DB read error: {exc}")

    # 5. Agent definitions
    agent_files = [
        "gaia-orchestrator.md", "gaia-operator.md", "platform-architect.md",
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
        help="Alias of `gaia install` (migrate, seed, wire, record the manifest)",
        description=(
            "Alias of `gaia install`: same flags, same steps, same exit code --\n"
            "non-zero when the DB migration or a required step fails. The\n"
            "workspace defaults to the nearest directory holding .claude/.\n"
            "Without --channel it re-wires the channels `gaia install` recorded\n"
            "in the workspace manifest, and fails naming `gaia install --channel`\n"
            "when none is recorded.\n"
            "\n"
            "--dry-run [--json]: preview what the workspace helpers would change\n"
            "and the health report, without touching the DB or any file.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    install.add_install_arguments(p)
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Preview what would change; runs nothing",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="With --dry-run: output the preview as JSON",
    )
    return p


def cmd_update(args) -> int:
    """Re-run `gaia install` for the channels the workspace recorded, or the read-only preview with --dry-run.

    A --channel (or --host) names the channel instead, exactly as `gaia install` takes it.
    """
    if not getattr(args, "workspace", None):
        args.workspace = str(_find_project_root())
    if getattr(args, "dry_run", False):
        return _preview(args)
    if getattr(args, "channel", None) or getattr(args, "host", None):
        return install.cmd_install(args)
    workspace = Path(args.workspace).expanduser().resolve()
    channels = install.recorded_channels(workspace)
    if not channels:
        print(f"gaia update: no install channel is recorded for {workspace}; install one first: "
              f"gaia install --channel npm|opencode --workspace {workspace}", file=sys.stderr)
        return 1
    return install.install_channels(args, channels, command="gaia update")


def _preview(args) -> int:
    """Report what the workspace helpers would change and the health checks; mutates nothing."""
    root = Path(args.workspace).expanduser().resolve()
    pkg_root = _find_package_root()
    claude_dir = root / ".claude"
    dry_run = True
    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "json", False)

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

    bootstrap_result = {"action": "skipped", "details": "skipped (dry-run)"}

    # Steps 2-7 -- workspace helpers (each idempotent + dry-run aware).
    # Order matches `gaia install` so install/update share the same sequence.
    settings_helper = _install_helpers.configure_settings_json(root, dry_run=dry_run)
    perms_helper = _install_helpers.merge_local_permissions(root, dry_run=dry_run)
    hooks_helper = _install_helpers.merge_local_hooks(root, plugin_root=pkg_root, dry_run=dry_run)
    worktree_helper = _install_helpers.merge_worktree_settings(root, dry_run=dry_run)
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
        "worktree": worktree_helper,
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
    _fmt("worktree", worktree_helper)
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
