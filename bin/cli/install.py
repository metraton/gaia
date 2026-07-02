"""
gaia install -- Bootstrap Gaia in this machine + workspace.

This subcommand is the Python entry point for:
  - manual first-time setup (`gaia install` from any workspace)
  - the non-interactive `--postinstall` path, kept for callers that want the
    fail-soft behaviour described below

There is NO npm postinstall hook -- `package.json` carries no `postinstall`
script. Bootstrap is lazy: `bin/gaia` calls `_ensure_db_bootstrapped()` on
first CLI use (for any subcommand except `install`/`uninstall`) so the DB
exists before anything needs it, without npm or pnpm ever running a
lifecycle script. Workspace `.claude/` config is applied on demand by
running `gaia install` (this module) or by the SessionStart hook.

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
  6. Create or repair `.claude/{agents,tools,hooks,config,skills}` symlinks
     (5 directories) plus a `.claude/CHANGELOG.md` file link, pointing at the
     installed package (`_SYMLINK_NAMES` + `_SYMLINK_FILES` in
     `_install_helpers.py`).
  7. Write `.claude/plugin-registry.json` with `installed[].name == "gaia"`
     (the canonical registry identity; `gaia-ops` is recognized as a legacy
     name for registries written by older installs, never written fresh).

Scanning is intentionally NOT part of install. `gaia scan` is a separate,
standalone module (bin/cli/scan.py + tools/scan/**) the user runs on demand;
install never triggers it.

Idempotent: re-running over a populated workspace + DB never destroys
state -- bootstrap.sh uses IF NOT EXISTS / INSERT OR IGNORE, the helpers
return ``action: noop`` when nothing changed, and symlink/registry writes
detect the already-good case.

Workspace bootstrap and update logic is centralised in `_install_helpers.py`
so `gaia install` and `gaia update` share a single source of truth.

Flags:
  --postinstall      Mark this invocation as a non-interactive bootstrap path
                     (adjusts output, never returns non-zero so a wrapping
                     install flow does not abort). Kept for callers that want
                     the fail-soft behaviour; nothing in the npm/pnpm
                     lifecycle invokes this automatically -- bootstrap is
                     lazy (see bin/gaia:_ensure_db_bootstrapped).
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
  --no-path          Skip creating the ~/.local/bin/gaia launcher. By default
                     install writes a workspace-aware bash launcher so `gaia`
                     is callable from any cwd.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# bin/cli/install.py -> bin/cli -> bin -> gaia/
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOTSTRAP_SCRIPT = _PACKAGE_ROOT / "scripts" / "bootstrap_database.sh"

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# Helpers shared with `gaia update`. Module-relative import works when run
# via `python bin/gaia install` because bin/ is on sys.path.
from cli import _install_helpers  # type: ignore  # noqa: E402

_SEED_CONTRACT_PERMS = _PACKAGE_ROOT / "tools" / "scan" / "seed_contract_permissions.py"


# ---------------------------------------------------------------------------
# PATH launcher (~/.local/bin/gaia -- workspace-bound bash launcher)
# ---------------------------------------------------------------------------

# Hardcoded launcher template. The workspace path is resolved at install time
# (the cwd of `gaia install`) and baked into the script verbatim. No discovery,
# no env vars, no fallbacks -- the shim is a 3-line exec to a fixed path.
#
# Rationale: the previous workspace-aware launcher walked up from $PWD looking
# for node_modules/@jaguilar87/gaia/, which failed silently from any cwd
# outside the consumer workspace tree (e.g. /tmp). Same conceptual bug that
# rc.5 fixed in doctor.py for the Python layer.
#
# Re-running `gaia install` from a different workspace rewrites the shim to
# point at that workspace -- the install action is what selects which Gaia
# install ~/.local/bin/gaia targets.
_LAUNCHER_TEMPLATE = """#!/bin/bash
# gaia -- workspace-bound launcher (workspace path hardcoded at install time)
# Generated by `gaia install`. Re-run install from another workspace to retarget.
exec python3 "{workspace_path}/node_modules/@jaguilar87/gaia/bin/gaia" "$@"
"""


def _render_launcher(workspace: Path) -> str:
    """Render the launcher script with the workspace path baked in.

    The workspace must be an absolute, resolved path -- the rendered script
    references it verbatim. Quoting in the template uses double quotes so
    paths with spaces remain a single argument to ``exec``.
    """
    return _LAUNCHER_TEMPLATE.format(workspace_path=str(workspace))


def _install_path_launcher(
    target_path: Path | None = None,
    link_path: Path | str = "~/.local/bin/gaia",
    overwrite: bool = False,
    workspace: Path | str | None = None,
) -> dict:
    """Install the workspace-bound bash launcher at `link_path`.

    The launcher is a 3-line script that execs into a hardcoded absolute path
    pointing at ``<workspace>/node_modules/@jaguilar87/gaia/bin/gaia``. There
    is no discovery logic, no env-var override, no fallback chain -- the path
    is fixed at install time and only changes when ``gaia install`` runs again
    from a different workspace.

    Behavior:
      - If `link_path` is a symlink (legacy install): unlink, write launcher.
      - If `link_path` is a regular file with the expected content: noop.
      - If `link_path` is a regular file with different content and
        `overwrite=False`: skip with warning.
      - If `link_path` is a regular file with different content and
        `overwrite=True`: replace.
      - If `link_path` does not exist: write launcher (and parent dir).

    Args:
        target_path: accepted for API compatibility; the launcher embeds the
            workspace path instead. Ignored.
        link_path: where the shim is written (default ``~/.local/bin/gaia``).
        overwrite: replace existing different-content files when True.
        workspace: absolute path to the consumer workspace (the directory
            that contains ``node_modules/@jaguilar87/gaia/``). Resolved with
            ``Path.cwd().resolve()`` when None.

    Returns a dict with `action`, `path`, and `details`. `action` is one
    of: created, replaced, migrated, noop, skipped, error.
    """
    link = Path(link_path).expanduser() if isinstance(link_path, str) else link_path
    link = Path(link).expanduser()

    if workspace is None:
        workspace_resolved = Path.cwd().resolve()
    else:
        workspace_resolved = Path(workspace).expanduser().resolve()

    parent = link.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "action": "error",
            "path": str(link),
            "details": f"failed to create parent {parent}: {exc}",
        }

    expected = _render_launcher(workspace_resolved)

    def _write_launcher() -> None:
        link.write_text(expected)
        link.chmod(0o755)

    # Legacy symlink: migrate to launcher unconditionally.
    if link.is_symlink():
        try:
            link.unlink()
            _write_launcher()
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "details": f"failed to migrate symlink to launcher: {exc}",
            }
        return {
            "action": "migrated",
            "path": str(link),
            "details": "replaced legacy symlink with workspace-aware launcher",
        }

    if link.exists():
        # Regular file or directory in the way.
        if link.is_dir():
            return {
                "action": "skipped",
                "path": str(link),
                "details": "path is a directory; refusing to overwrite",
            }
        try:
            current = link.read_text()
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "details": f"failed to read existing file: {exc}",
            }
        if current == expected:
            # Ensure executable bit is set (idempotent).
            try:
                mode = link.stat().st_mode
                if not (mode & stat.S_IXUSR):
                    link.chmod(0o755)
            except OSError:
                pass
            return {
                "action": "noop",
                "path": str(link),
                "details": "launcher already up to date",
            }
        if not overwrite:
            return {
                "action": "skipped",
                "path": str(link),
                "details": (
                    "file exists with different content; "
                    "use --no-path to suppress or remove manually to refresh"
                ),
            }
        try:
            _write_launcher()
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "details": f"failed to overwrite launcher: {exc}",
            }
        return {
            "action": "replaced",
            "path": str(link),
            "details": "replaced previous launcher with current version",
        }

    # Path does not exist -- create launcher.
    try:
        _write_launcher()
    except OSError as exc:
        return {
            "action": "error",
            "path": str(link),
            "details": f"failed to write launcher: {exc}",
        }
    return {
        "action": "created",
        "path": str(link),
        "details": "workspace-aware launcher installed",
    }


# Backward-compatible alias -- existing tests/imports continue to work
# while migrating to the new name.
_create_path_symlink = _install_path_launcher


# ---------------------------------------------------------------------------
# Plugin mode detection (best-effort, never fatal)
# ---------------------------------------------------------------------------

def _detect_plugin_mode() -> str:
    """Return 'ops', 'security', or 'unknown'. Never raises."""
    try:
        from hooks.modules.core.plugin_mode import get_plugin_mode  # type: ignore
        return get_plugin_mode() or "unknown"
    except Exception:
        return os.environ.get("GAIA_PLUGIN_MODE", "unknown")


# ---------------------------------------------------------------------------
# Bootstrap invocation
# ---------------------------------------------------------------------------

def _run_bootstrap(db_path: str | None, verbose: bool, quiet: bool) -> dict:
    """Invoke bootstrap_database.sh and return a structured result.

    Returns a dict with:
      - ``rc``: int exit code (0 on success).
      - ``detail``: str -- short human-readable summary, suitable for the
        install-error marker. Empty string on success.

    Always captures stdout/stderr so the caller (cmd_install) has the
    failure detail available for ``_write_install_error_marker`` even
    under the verbose branch. Output is re-emitted to the parent's
    streams to preserve the original UX (visible bootstrap progress in
    verbose mode; failure-only spill in quiet mode).
    """
    if not _BOOTSTRAP_SCRIPT.is_file():
        msg = f"bootstrap script not found at {_BOOTSTRAP_SCRIPT}"
        print(f"gaia install: {msg}", file=sys.stderr)
        return {"rc": 1, "detail": msg}

    env = os.environ.copy()
    if db_path:
        env["GAIA_DB"] = str(Path(db_path).expanduser())

    cmd = ["bash", str(_BOOTSTRAP_SCRIPT)]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"failed to invoke bash -- {exc}"
        print(f"gaia install: {msg}", file=sys.stderr)
        return {"rc": 1, "detail": msg}

    # In verbose mode (or not quiet), surface all bootstrap output so the
    # user sees progress in real-ish time. In quiet mode, only show output
    # when bootstrap fails -- success stays silent.
    if verbose or not quiet:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
    elif result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)

    if result.returncode == 0:
        return {"rc": 0, "detail": ""}

    # Build a compact, marker-friendly detail. Prefer the last non-empty
    # stderr line (where bash + sqlite3 surface the actual error) and fall
    # back to a generic message keyed to the exit code.
    detail = _summarize_bootstrap_failure(
        rc=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
    return {"rc": result.returncode, "detail": detail}


def _seed_contract_permissions(db_path: str | None, quiet: bool) -> dict:
    """Invoke seed_contract_permissions to populate agent_contract_permissions.

    Returns a step-result dict compatible with ``_report_step``.  Never raises
    -- seeding failures are logged and reported as action='error' so the install
    continues rather than being aborted for a non-critical step.
    """
    if not _SEED_CONTRACT_PERMS.is_file():
        return {
            "action": "skipped",
            "details": f"seeder not found at {_SEED_CONTRACT_PERMS}",
        }

    env = os.environ.copy()
    resolved_db = (
        str(Path(db_path).expanduser()) if db_path else str(Path("~/.gaia/gaia.db").expanduser())
    )
    cmd = [sys.executable, str(_SEED_CONTRACT_PERMS), "--db-path", resolved_db]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"action": "error", "details": f"seeder invocation failed: {exc}"}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[:200]
        if not quiet:
            sys.stderr.write(f"  [!] contract-permissions seeder: {detail}\n")
        return {"action": "error", "details": detail}

    # Extract summary line from stdout for the step report.
    summary = (result.stdout or "").strip().split("\n")[-1]
    return {"action": "created", "details": summary}


def _summarize_bootstrap_failure(*, rc: int, stdout: str, stderr: str) -> str:
    """Build a short detail string for the install-error marker.

    The marker file is read by `gaia doctor`, which shows the detail
    inline. Keep it under ~200 chars and pull the most diagnostic line
    (typically a sqlite3 'Parse error' or a [bootstrap] check: FAIL line).
    """
    candidates: list[str] = []
    for chunk in (stderr, stdout):
        for raw in reversed(chunk.splitlines()):
            line = raw.strip()
            if not line:
                continue
            # Most informative signals: sqlite3 parse errors, FAIL checks,
            # explicit [bootstrap] ERROR lines.
            lower = line.lower()
            if (
                "error" in lower
                or "fail" in lower
                or "parse error" in lower
                or "no such" in lower
            ):
                candidates.append(line)
                break

    if candidates:
        summary = candidates[0]
    elif stderr.strip():
        # Last resort: first non-empty stderr line.
        for raw in stderr.splitlines():
            line = raw.strip()
            if line:
                summary = line
                break
        else:
            summary = f"bootstrap exited rc={rc}"
    else:
        summary = f"bootstrap exited rc={rc} (no stderr captured)"

    # Cap to keep the marker readable.
    if len(summary) > 220:
        summary = summary[:217] + "..."
    return f"bootstrap rc={rc}: {summary}"


# ---------------------------------------------------------------------------
# Install-error marker (~/.gaia/last-install-error.json)
# ---------------------------------------------------------------------------
#
# `gaia doctor` reads this file to surface install failures that happened
# under `--postinstall` (where we cannot abort npm). Interactive `gaia install`
# clears it on success and does not write it on failure (the user sees the
# error in stderr already).

_INSTALL_ERROR_MARKER = Path("~/.gaia/last-install-error.json").expanduser()


def _write_install_error_marker(*, workspace: Path, step: str, detail: str) -> None:
    """Persist a structured install-error marker for `gaia doctor` to pick up.

    Best-effort: failure to write the marker is never fatal (we are already
    in an error path; raising here would mask the original problem).
    """
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": step,
        "detail": detail,
        "workspace": str(workspace),
    }
    try:
        _INSTALL_ERROR_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _INSTALL_ERROR_MARKER.write_text(json.dumps(payload, indent=2) + "\n")
    except OSError:
        pass  # marker is advisory; never block the install path on it


def _clear_install_error_marker() -> None:
    """Remove the install-error marker if present (called on a clean install)."""
    try:
        _INSTALL_ERROR_MARKER.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


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
            "There is no npm postinstall hook -- bootstrap is lazy, triggered\n"
            "by the first `gaia` CLI invocation. Typically called by:\n"
            "  - the user, manually, to re-bootstrap the DB or workspace\n"
            "  - a non-interactive caller passing --postinstall for fail-soft output\n"
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
    bootstrap_res = _run_bootstrap(db_path=db_path, verbose=verbose, quiet=quiet)
    rc = bootstrap_res["rc"]
    if rc == 0:
        # Step 1a -- seed agent_contract_permissions from agent frontmatters.
        # Runs after bootstrap so the table is guaranteed to exist (v3 migration).
        # Non-fatal: a seeding failure should not abort an otherwise clean install.
        seed_res = _seed_contract_permissions(db_path=db_path, quiet=quiet)
        _report_step(name="contract-permissions", result=seed_res, quiet=quiet, verbose=verbose)
    if rc != 0:
        if postinstall:
            # Persist a marker so `gaia doctor` can surface the real failure.
            # Without this, the postinstall returns 0 silently and the user
            # only sees vague "missing file" hints from doctor -- never the
            # bootstrap stderr that holds the root cause (e.g. a sqlite3
            # parse error). The marker is best-effort; never blocks.
            _write_install_error_marker(
                workspace=workspace,
                step="bootstrap",
                detail=bootstrap_res.get("detail")
                or f"bootstrap exited {rc} (no detail captured)",
            )
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

    # Step 1.5 -- ensure workspace .claude/ exists BEFORE invoking helpers.
    # The first four helpers (configure_settings_json, merge_local_permissions,
    # merge_local_hooks, manage_symlinks) early-return "skipped" when .claude/
    # is missing. Only register_plugin used to mkdir it -- too late. Doing it
    # here makes all five helpers see a real directory and apply their work.
    # Placed AFTER bootstrap so a bootstrap failure does not leave behind a
    # partially-initialized workspace; placed BEFORE the helpers so they can
    # write into the directory.
    claude_dir = workspace / ".claude"
    if not claude_dir.exists():
        try:
            claude_dir.mkdir(parents=True, exist_ok=True)
            if not quiet:
                print(f"  [+] workspace: created {claude_dir}")
        except OSError as exc:
            if not quiet:
                print(
                    f"  [!] workspace: failed to create {claude_dir}: {exc}",
                    file=sys.stderr,
                )
            # Non-fatal under postinstall (parity with bootstrap behavior);
            # surface error in manual mode.
            if not postinstall:
                return 1
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

    # Step 6.5 -- PATH launcher (~/.local/bin/gaia) unless --no-path
    if not no_path:
        # Hardcode the resolved workspace path into the shim. The launcher has
        # no discovery logic -- the path baked in here is the path it execs to,
        # period. Re-running `gaia install` from a different workspace is what
        # retargets the shim.
        path_res = _install_path_launcher(workspace=workspace)
        _report_step(name="PATH launcher", result=path_res, quiet=quiet, verbose=verbose)

    # Install owns Steps 1-6 only. Workspace scanning is a separate, on-demand
    # flow (`gaia scan`); install never triggers it. A clean install clears any
    # stale install-error marker left by a prior failed bootstrap attempt.
    _clear_install_error_marker()

    _print_next_steps(quiet=quiet, postinstall=postinstall)
    return 0
