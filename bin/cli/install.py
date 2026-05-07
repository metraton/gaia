"""
gaia install -- Bootstrap Gaia in this machine + workspace.

This subcommand is the Python entry point for both:
  - npm postinstall hook: bootstrap DB + .claude/ structure + symlinks for a fresh install
  - manual first-time setup (`gaia install` from any workspace)

Responsibilities (in order):
  1. Detect plugin mode (npm vs CC plugin) for diagnostic output.
  2. Invoke `scripts/bootstrap_database.sh` -- the single source of truth for
     creating/upgrading `~/.gaia/gaia.db` (schema, agent_permissions seed,
     project registration, FTS5 backfill, invariant checks).
  3. Configure workspace `.claude/settings.json` (create if missing).
  4. Merge gaia permissions, env vars, and agent identity into
     `.claude/settings.local.json`.
  5. Merge hook event entries from `hooks.json` into `.claude/settings.local.json`
     (only relevant in npm mode -- in plugin mode CC reads hooks.json directly).
  6. Create or repair `.claude/{agents,tools,hooks,commands,templates,config,skills}`
     symlinks pointing at the installed package.
  7. Write `.claude/plugin-registry.json` with the installed version.
  8. (Optional) When `--postinstall` is set on a fresh workspace, invoke
     `gaia scan --fresh --npm-postinstall` to seed `project-context.json`.

Idempotent: re-running over a populated workspace + DB never destroys
state -- bootstrap.sh uses IF NOT EXISTS / INSERT OR IGNORE, the helpers
return ``action: noop`` when nothing changed, and symlink/registry writes
detect the already-good case.

Workspace bootstrap and update logic is centralised in `_install_helpers.py`
so `gaia install` and `gaia update` share a single source of truth.

Flags:
  --postinstall      Mark this invocation as the npm postinstall path
                     (adjusts output, never returns non-zero so npm install
                     does not abort, and triggers `gaia scan --fresh` on a
                     fresh workspace).
  --quiet            Suppress informational output; only errors print.
  --verbose          Stream bootstrap.sh output verbatim and report each
                     helper individually.
  --db-path PATH     Override target DB path (default: ~/.gaia/gaia.db,
                     forwarded to bootstrap.sh via the GAIA_DB env var).
  --workspace PATH   Workspace where settings/symlinks/registry are
                     written (default: cwd).
  --skip-workspace   Bootstrap the DB only; skip workspace configuration.
                     Useful when running install just to refresh the DB
                     schema from a non-Gaia directory.
  --no-path          Skip creating the ~/.local/bin/gaia symlink. By default
                     install creates one so `gaia` is callable from any cwd.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# bin/cli/install.py -> bin/cli -> bin -> gaia/
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOTSTRAP_SCRIPT = _PACKAGE_ROOT / "scripts" / "bootstrap_database.sh"

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# Helpers shared with `gaia update`. Module-relative import works when run
# via `python bin/gaia install` because bin/ is on sys.path.
from cli import _install_helpers  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# PATH symlink (~/.local/bin/gaia -> bin/gaia)
# ---------------------------------------------------------------------------

def _create_path_symlink(
    target_path: Path,
    link_path: Path | str = "~/.local/bin/gaia",
    overwrite: bool = False,
) -> dict:
    """Create a symlink to `target_path` at `link_path`.

    Behavior:
      - If `link_path` already exists and points to `target_path`: noop.
      - If `link_path` exists and points elsewhere: replace if `overwrite=True`,
        otherwise skip with a warning.
      - If `link_path` does not exist: create the symlink (and parent dir
        if missing).

    Returns a dict with at least `action`, `path`, and `target`. `action`
    is one of: created, skipped, replaced, noop, error.
    """
    target = Path(target_path).expanduser().resolve()
    link = Path(link_path).expanduser() if isinstance(link_path, str) else link_path
    link = Path(link).expanduser()

    parent = link.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "action": "error",
            "path": str(link),
            "target": str(target),
            "details": f"failed to create parent {parent}: {exc}",
        }

    if link.is_symlink():
        try:
            current_target = Path(os.readlink(link))
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "target": str(target),
                "details": f"failed to read existing symlink: {exc}",
            }
        # Compare resolved paths so absolute and relative targets compare equal
        try:
            current_resolved = (link.parent / current_target).resolve()
        except OSError:
            current_resolved = current_target
        if current_resolved == target:
            return {
                "action": "noop",
                "path": str(link),
                "target": str(target),
                "details": "symlink already points at target",
            }
        if not overwrite:
            return {
                "action": "skipped",
                "path": str(link),
                "target": str(target),
                "details": (
                    f"symlink exists pointing at {current_target}; "
                    "use --no-path to suppress or remove manually"
                ),
            }
        try:
            link.unlink()
            link.symlink_to(target)
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "target": str(target),
                "details": f"failed to replace symlink: {exc}",
            }
        return {
            "action": "replaced",
            "path": str(link),
            "target": str(target),
            "details": f"replaced previous target {current_target}",
        }

    if link.exists():
        # Regular file or directory in the way -- never delete user content
        return {
            "action": "skipped",
            "path": str(link),
            "target": str(target),
            "details": "path exists and is not a symlink; refusing to overwrite",
        }

    try:
        link.symlink_to(target)
    except OSError as exc:
        return {
            "action": "error",
            "path": str(link),
            "target": str(target),
            "details": f"failed to create symlink: {exc}",
        }
    return {
        "action": "created",
        "path": str(link),
        "target": str(target),
        "details": "symlink created",
    }


# ---------------------------------------------------------------------------
# Plugin mode detection (best-effort, never fatal)
# ---------------------------------------------------------------------------

def _detect_plugin_mode() -> str:
    """Return 'ops', 'security', or 'unknown'. Never raises."""
    try:
        from hooks.modules.core.plugin_mode import detect_mode  # type: ignore
        return detect_mode() or "unknown"
    except Exception:
        return os.environ.get("GAIA_PLUGIN_MODE", "unknown")


# ---------------------------------------------------------------------------
# Bootstrap invocation
# ---------------------------------------------------------------------------

def _run_bootstrap(db_path: str | None, verbose: bool, quiet: bool) -> int:
    """Invoke bootstrap_database.sh and return its exit code."""
    if not _BOOTSTRAP_SCRIPT.is_file():
        print(
            f"gaia install: bootstrap script not found at {_BOOTSTRAP_SCRIPT}",
            file=sys.stderr,
        )
        return 1

    env = os.environ.copy()
    if db_path:
        env["GAIA_DB"] = str(Path(db_path).expanduser())

    cmd = ["bash", str(_BOOTSTRAP_SCRIPT)]

    if verbose or not quiet:
        try:
            result = subprocess.run(cmd, env=env, check=False)
            return result.returncode
        except OSError as exc:
            print(f"gaia install: failed to invoke bash -- {exc}", file=sys.stderr)
            return 1

    # Quiet: capture; only print on failure.
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(f"gaia install: failed to invoke bash -- {exc}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)

    return result.returncode


# ---------------------------------------------------------------------------
# Optional fresh scan (postinstall path only)
# ---------------------------------------------------------------------------

def _maybe_run_fresh_scan(workspace: Path, verbose: bool, quiet: bool) -> dict:
    """Invoke `gaia scan --fresh --npm-postinstall` for fresh workspaces.

    Detection: a workspace is "fresh" when project-context.json does not
    exist yet. On scan failure we report but never raise -- the postinstall
    flow must remain non-fatal.
    """
    ctx_path = workspace / ".claude" / "project-context" / "project-context.json"
    if ctx_path.exists():
        return {"action": "noop", "details": "project-context.json already present"}

    gaia_entry = _PACKAGE_ROOT / "bin" / "gaia"
    if not gaia_entry.is_file():
        return {"action": "skipped", "details": f"bin/gaia not found at {gaia_entry}"}

    # Use the same Python interpreter the postinstall uses.
    py = sys.executable or "python3"
    cmd = [py, str(gaia_entry), "scan", "--fresh", "--npm-postinstall"]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=os.environ.copy(),
            capture_output=not verbose,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"action": "error", "details": f"scan invocation failed: {exc}"}

    if result.returncode == 0:
        return {"action": "created", "details": "project-context.json seeded via gaia scan"}

    if not quiet and not verbose:
        # On failure surface stderr so user has a hint
        if result.stderr:
            sys.stderr.write(result.stderr)
    return {"action": "error", "details": f"gaia scan exited {result.returncode}"}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_header(*, postinstall: bool, quiet: bool, mode: str, workspace: Path) -> None:
    if quiet:
        return
    label = "postinstall" if postinstall else "first-time install"
    print(f"\n  Setting up Gaia for the first time...")
    print(f"  ({label}, plugin mode: {mode})")
    print(f"  workspace: {workspace}")
    print()


def _report_step(*, name: str, result: dict, quiet: bool, verbose: bool) -> None:
    """Print a one-line result for a helper step."""
    if quiet:
        return
    action = result.get("action", "unknown")
    details = result.get("details", "")
    if action == "noop" and not verbose:
        return
    icon = {
        "created": "+",
        "updated": "~",
        "noop": "=",
        "skipped": "-",
        "error": "!",
    }.get(action, "?")
    print(f"  [{icon}] {name}: {details}")


def _print_next_steps(*, quiet: bool, postinstall: bool) -> None:
    if quiet:
        return
    print()
    print("  Gaia ready. Next steps:")
    if postinstall:
        print("    1. Restart Claude Code to pick up new hooks/agents.")
        print("    2. Run `gaia doctor` to verify the installation.")
    else:
        print("    1. Run `gaia doctor` to verify the installation.")
        print("    2. Open Claude Code in this workspace.")
    print()


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'install' subcommand."""
    p = subparsers.add_parser(
        "install",
        help="First-time setup: bootstrap DB, configure workspace, write registry",
        description=(
            "Bootstrap or refresh Gaia for this workspace + machine.\n"
            "\n"
            "Idempotent end to end: re-running over an existing setup applies\n"
            "schema migrations, re-seeds permissions, and repairs broken symlinks\n"
            "without destroying user state.\n"
            "\n"
            "Typically called by:\n"
            "  - npm postinstall (with --postinstall) after `npm install`\n"
            "  - the user, manually, to re-bootstrap the DB or workspace\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--postinstall",
        action="store_true",
        default=False,
        help="Mark this invocation as the npm postinstall path (adjusts output)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress informational output; only errors print",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Stream bootstrap.sh output verbatim and report every step",
    )
    p.add_argument(
        "--db-path",
        dest="db_path",
        type=str,
        default=None,
        help="Override DB path (default: ~/.gaia/gaia.db, via GAIA_DB env var)",
    )
    p.add_argument(
        "--workspace",
        dest="workspace",
        type=str,
        default=None,
        help="Workspace where .claude/ is configured (default: cwd)",
    )
    p.add_argument(
        "--skip-workspace",
        dest="skip_workspace",
        action="store_true",
        default=False,
        help="Skip workspace configuration; only bootstrap the DB",
    )
    p.add_argument(
        "--no-path",
        dest="no_path",
        action="store_true",
        default=False,
        help="Skip creating the ~/.local/bin/gaia symlink",
    )
    return p


def cmd_install(args: argparse.Namespace) -> int:
    """Execute the install subcommand."""
    postinstall = bool(getattr(args, "postinstall", False))
    quiet = bool(getattr(args, "quiet", False))
    verbose = bool(getattr(args, "verbose", False))
    db_path = getattr(args, "db_path", None)
    skip_workspace = bool(getattr(args, "skip_workspace", False))
    no_path = bool(getattr(args, "no_path", False))
    workspace_arg = getattr(args, "workspace", None)

    workspace = (
        Path(workspace_arg).expanduser().resolve()
        if workspace_arg
        else Path(os.environ.get("INIT_CWD", os.getcwd())).resolve()
    )

    mode = _detect_plugin_mode()
    _print_header(postinstall=postinstall, quiet=quiet, mode=mode, workspace=workspace)

    # Step 1 -- bootstrap DB (always)
    rc = _run_bootstrap(db_path=db_path, verbose=verbose, quiet=quiet)
    if rc != 0:
        if postinstall:
            if not quiet:
                print(
                    f"\n  gaia install: bootstrap exited {rc} -- run `gaia doctor` "
                    "to diagnose.\n",
                    file=sys.stderr,
                )
            return 0
        return rc

    if skip_workspace:
        _print_next_steps(quiet=quiet, postinstall=postinstall)
        return 0

    # Steps 2-6 -- workspace configuration
    if not workspace.exists():
        if not quiet:
            print(f"  workspace {workspace} does not exist -- skipping configuration", file=sys.stderr)
        return 0

    settings_res = _install_helpers.configure_settings_json(workspace)
    _report_step(name="settings.json", result=settings_res, quiet=quiet, verbose=verbose)

    perms_res = _install_helpers.merge_local_permissions(workspace, mode=mode if mode != "unknown" else None)
    _report_step(name="permissions", result=perms_res, quiet=quiet, verbose=verbose)

    # merge_local_hooks is most relevant for npm mode but is safe in any mode
    # (it's a no-op when hooks are already merged).
    hooks_res = _install_helpers.merge_local_hooks(workspace)
    _report_step(name="hooks", result=hooks_res, quiet=quiet, verbose=verbose)

    sym_res = _install_helpers.manage_symlinks(workspace)
    _report_step(name="symlinks", result=sym_res, quiet=quiet, verbose=verbose)

    registry_source = "npm-postinstall" if postinstall else "cli-install"
    reg_res = _install_helpers.register_plugin(workspace, source=registry_source)
    _report_step(name="plugin-registry", result=reg_res, quiet=quiet, verbose=verbose)

    # Step 6.5 -- PATH symlink (~/.local/bin/gaia) unless --no-path
    if not no_path:
        gaia_bin = _PACKAGE_ROOT / "bin" / "gaia"
        path_res = _create_path_symlink(gaia_bin)
        _report_step(name="PATH symlink", result=path_res, quiet=quiet, verbose=verbose)

    # Step 7 -- optional gaia scan on fresh postinstall
    if postinstall:
        scan_res = _maybe_run_fresh_scan(workspace, verbose=verbose, quiet=quiet)
        _report_step(name="project scan", result=scan_res, quiet=quiet, verbose=verbose)

    _print_next_steps(quiet=quiet, postinstall=postinstall)
    return 0
