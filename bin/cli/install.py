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
  1. Invoke `scripts/bootstrap_database.py` -- the cross-platform Python
     bootstrapper for creating/upgrading `~/.gaia/gaia.db` (schema,
     agent_permissions seed, project registration, FTS5 backfill, invariant
     checks). The canonical schema source is `gaia/store/schema.sql`;
     `bootstrap_database.sh` is retained as the shell/test reference.
  2. Configure workspace `.claude/settings.json` (create if missing).
  3. Merge gaia permissions, env vars, and agent identity into
     `.claude/settings.local.json`.
  4. Merge hook event entries from `hooks.json` into `.claude/settings.local.json`
     (only relevant in npm mode -- in plugin mode CC reads hooks.json directly).
  5. Create or repair `.claude/{agents,tools,hooks,config,skills}` symlinks
     (5 directories) plus a `.claude/CHANGELOG.md` file link, pointing at the
     installed package (`_SYMLINK_NAMES` + `_SYMLINK_FILES` in
     `_install_helpers.py`).
  6. Write `.claude/plugin-registry.json` with `installed[].name == "gaia"`
     (the single unified plugin registry identity).

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
                     install writes a workspace-aware launcher so `gaia` is
                     callable from any cwd: a bash launcher on POSIX, and
                     `gaia.cmd` + `gaia.ps1` on Windows.
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
_BOOTSTRAP_SCRIPT = _PACKAGE_ROOT / "scripts" / "bootstrap_database.py"

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# Helpers shared with `gaia update`. Module-relative import works when run
# via `python bin/gaia install` because bin/ is on sys.path.
from cli import _install_helpers  # type: ignore  # noqa: E402

_SEED_CONTRACT_PERMS = _PACKAGE_ROOT / "tools" / "scan" / "seed_contract_permissions.py"
_SEED_SURFACE_ROUTING = _PACKAGE_ROOT / "tools" / "scan" / "seed_surface_routing.py"


# ---------------------------------------------------------------------------
# PATH launcher (~/.local/bin/gaia -- workspace-bound launcher)
# ---------------------------------------------------------------------------
#
# Platform split (Step 6.5): the launcher form is chosen by platform. This is a
# hard guard, not a preference -- a bash script has no meaning to the Windows
# shell (PowerShell opens an "open with" dialog on an extensionless file), and
# the POSIX shim's ``{workspace}/node_modules/...`` target does not exist under
# ``npm install -g`` on any platform. So:
#
#   * POSIX  -> one bash launcher at ``<link>`` (unchanged behavior).
#   * Windows -> ``<link>.cmd`` and ``<link>.ps1``, each of which
#       (a) bakes the resolved workspace path,
#       (b) exports ``GAIA_WORKSPACE_PATH`` so `gaia doctor` resolves the
#           correct workspace from the env instead of deriving it from
#           ``__file__`` (which, via the npm global shim, lands in the npm
#           prefix and yields a false CRITICAL -- see doctor._derive_workspace),
#       (c) execs the ACTUAL installed ``bin/gaia`` dispatcher
#           (``_gaia_entrypoint()`` = ``<package_root>/bin/gaia``), which is
#           valid for BOTH a global (`-g`) and a local install, unlike the
#           POSIX shim's workspace-relative ``node_modules`` path.
#
# PATH precedence vs npm's own shim (Windows): `npm install -g @jaguilar87/gaia`
# writes its own ``gaia.cmd`` into the npm global prefix (on PATH), and that
# shim execs ``bin/gaia`` WITHOUT setting GAIA_WORKSPACE_PATH -- which is
# exactly the origin of the false-CRITICAL bug. Gaia's launcher coexists with
# and wins over npm's by being written to Gaia's own bin dir (default
# ``~/.local/bin``): when that dir precedes the npm prefix on PATH, cmd.exe /
# PowerShell resolve ``gaia`` (``gaia.cmd`` / ``gaia.ps1``) to Gaia's launcher,
# which sets GAIA_WORKSPACE_PATH before dispatching.
#
# Where Gaia's dir is NOT ahead of the npm prefix (the common case on Windows,
# where ``~/.local/bin`` is not on PATH by convention), npm's shim wins and
# execs ``bin/gaia`` WITHOUT the process-scoped GAIA_WORKSPACE_PATH export. The
# doctor `__file__` fallback does NOT save this case: with the npm global shim,
# ``__file__`` resolves into the npm prefix, so ``doctor._derive_workspace``
# derives the npm prefix as the "workspace" and emits a FALSE CRITICAL (this is
# the observed rc.2 bug, not a hypothetical). Two things close it, so the fix
# does not depend on PATH order:
#   1. `gaia install` on Windows PERSISTS GAIA_WORKSPACE_PATH to the USER
#      environment (`setx`, see `_persist_workspace_env`). The next `gaia
#      doctor` is a fresh process that inherits it, so doctor resolves the
#      workspace via the env var regardless of which `gaia` won the PATH.
#   2. `gaia install` WARNS when Gaia's launcher dir is not ahead of the npm
#      prefix on PATH (see `_launcher_path_precedence`), so the shadowed-launcher
#      condition is a visible, actionable signal instead of a silent surprise.
#
# Re-running `gaia install` from a different workspace rewrites the launcher(s)
# to point at that workspace AND re-persists GAIA_WORKSPACE_PATH -- the install
# action is what selects which workspace both the launcher and the env var
# target (last-install-wins, single-valued).

# POSIX bash launcher. The workspace path is resolved at install time and baked
# in verbatim. No discovery, no env vars, no fallbacks -- a 3-line exec.
_LAUNCHER_TEMPLATE = """#!/bin/bash
# gaia -- workspace-bound launcher (workspace path hardcoded at install time)
# Generated by `gaia install`. Re-run install from another workspace to retarget.
exec python3 "{workspace_path}/node_modules/@jaguilar87/gaia/bin/gaia" "$@"
"""

# Windows launchers. Both bake the resolved workspace, export GAIA_WORKSPACE_PATH,
# and dispatch to the ACTUAL installed bin/gaia (global- or local-install safe).
_CMD_LAUNCHER_TEMPLATE = """@echo off
REM gaia -- workspace-bound launcher (generated by `gaia install`)
REM Re-run install from another workspace to retarget. Exports GAIA_WORKSPACE_PATH
REM so `gaia doctor` resolves this workspace instead of deriving from __file__.
set "GAIA_WORKSPACE_PATH={workspace_path}"
python "{gaia_bin}" %*
"""

_PS1_LAUNCHER_TEMPLATE = """# gaia -- workspace-bound launcher (generated by `gaia install`)
# Re-run install from another workspace to retarget. Exports GAIA_WORKSPACE_PATH
# so `gaia doctor` resolves this workspace instead of deriving from __file__.
$env:GAIA_WORKSPACE_PATH = "{workspace_path}"
& python "{gaia_bin}" @args
exit $LASTEXITCODE
"""


def _is_windows() -> bool:
    """True on Windows. Isolated so the platform guard is trivially patchable."""
    return sys.platform == "win32"


def _gaia_entrypoint() -> Path:
    """Absolute path to the installed ``bin/gaia`` dispatcher.

    This is ``<package_root>/bin/gaia`` -- the real location of the running
    Gaia package, which resolves correctly under both a global (`npm i -g`)
    and a local install. The POSIX shim's ``{workspace}/node_modules/...``
    assumption breaks under `-g` (there is no workspace-relative node_modules);
    the Windows launchers bake this resolved path instead.
    """
    return _PACKAGE_ROOT / "bin" / "gaia"


def _render_launcher(workspace: Path) -> str:
    """Render the POSIX bash launcher with the workspace path baked in.

    The workspace must be an absolute, resolved path -- the rendered script
    references it verbatim. Quoting in the template uses double quotes so
    paths with spaces remain a single argument to ``exec``.
    """
    return _LAUNCHER_TEMPLATE.format(workspace_path=str(workspace))


def _render_cmd_launcher(workspace: Path, gaia_bin: Path) -> str:
    """Render the Windows ``gaia.cmd`` launcher (workspace + bin baked in)."""
    return _CMD_LAUNCHER_TEMPLATE.format(
        workspace_path=str(workspace), gaia_bin=str(gaia_bin)
    )


def _render_ps1_launcher(workspace: Path, gaia_bin: Path) -> str:
    """Render the Windows ``gaia.ps1`` launcher (workspace + bin baked in)."""
    return _PS1_LAUNCHER_TEMPLATE.format(
        workspace_path=str(workspace), gaia_bin=str(gaia_bin)
    )


def _install_path_launcher(
    target_path: Path | None = None,
    link_path: Path | str = "~/.local/bin/gaia",
    overwrite: bool = False,
    workspace: Path | str | None = None,
    gaia_bin: Path | str | None = None,
) -> dict:
    """Install the workspace-bound launcher at `link_path`.

    Platform-guarded (Step 6.5): on Windows this writes ``<link>.cmd`` and
    ``<link>.ps1`` (see ``_install_windows_launchers``); on POSIX it writes a
    single bash launcher at ``<link>``. The POSIX behavior below is unchanged.

    The POSIX launcher is a 3-line script that execs into a hardcoded absolute
    path pointing at ``<workspace>/node_modules/@jaguilar87/gaia/bin/gaia``.
    There is no discovery logic, no env-var override, no fallback chain -- the
    path is fixed at install time and only changes when ``gaia install`` runs
    again from a different workspace.

    Behavior (POSIX):
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
            On Windows, ``.cmd``/``.ps1`` suffixes are derived from this base.
        overwrite: replace existing different-content files when True.
        workspace: absolute path to the consumer workspace (the directory
            that contains ``node_modules/@jaguilar87/gaia/``). Resolved with
            ``Path.cwd().resolve()`` when None.
        gaia_bin: Windows only -- the ``bin/gaia`` dispatcher the launchers
            exec. Defaults to ``_gaia_entrypoint()`` (the installed package's
            ``bin/gaia``, valid for global and local installs). Ignored on POSIX.

    Returns a dict with `action`, `path`, and `details`. `action` is one
    of: created, replaced, migrated, noop, skipped, error.
    """
    link = Path(link_path).expanduser() if isinstance(link_path, str) else link_path
    link = Path(link).expanduser()

    if workspace is None:
        workspace_resolved = Path.cwd().resolve()
    else:
        workspace_resolved = Path(workspace).expanduser().resolve()

    # Windows: emit gaia.cmd + gaia.ps1 instead of a bash shim. The POSIX path
    # below is left entirely unchanged.
    if _is_windows():
        entry = (
            Path(gaia_bin).expanduser() if gaia_bin is not None
            else _gaia_entrypoint()
        )
        return _install_windows_launchers(
            link=link,
            workspace=workspace_resolved,
            gaia_bin=entry,
            overwrite=overwrite,
        )

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


def _install_windows_launchers(
    link: Path,
    workspace: Path,
    gaia_bin: Path,
    overwrite: bool = False,
) -> dict:
    """Write the Windows ``gaia.cmd`` + ``gaia.ps1`` launchers.

    Both are derived from ``link`` by suffix: ``<link>.cmd`` and ``<link>.ps1``
    (so a default ``~/.local/bin/gaia`` yields ``gaia.cmd`` / ``gaia.ps1``).
    Each bakes the resolved ``workspace``, exports ``GAIA_WORKSPACE_PATH``, and
    dispatches to ``gaia_bin`` (the actual installed ``bin/gaia``).

    Idempotent and non-destructive, mirroring the POSIX branch:
      - missing            -> created
      - present, matches   -> noop
      - present, drifted   -> replaced (overwrite=True) / skipped (overwrite=False)
      - a directory in the way -> skipped

    The aggregate ``action`` is the "strongest" over the two files: error >
    skipped > replaced > created > noop. ``path`` lists both files.
    """
    parent = link.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "action": "error",
            "path": str(link),
            "details": f"failed to create parent {parent}: {exc}",
        }

    targets = [
        (link.with_suffix(".cmd"), _render_cmd_launcher(workspace, gaia_bin)),
        (link.with_suffix(".ps1"), _render_ps1_launcher(workspace, gaia_bin)),
    ]

    # Rank so the aggregate reports the most significant per-file outcome.
    rank = {"noop": 0, "created": 1, "replaced": 2, "skipped": 3, "error": 4}
    per_file: list[str] = []
    worst = "noop"

    for path, content in targets:
        action = _write_windows_launcher_file(path, content, overwrite=overwrite)
        per_file.append(f"{path.name}={action}")
        if rank[action] > rank[worst]:
            worst = action

    return {
        "action": worst,
        "path": ", ".join(str(p) for p, _ in targets),
        "details": "; ".join(per_file),
    }


def _write_windows_launcher_file(path: Path, content: str, overwrite: bool) -> str:
    """Write one Windows launcher file idempotently. Returns the action string."""
    if path.is_dir():
        return "skipped"
    if path.exists():
        try:
            current = path.read_text()
        except OSError:
            return "error"
        if current == content:
            return "noop"
        if not overwrite:
            return "skipped"
        try:
            path.write_text(content)
        except OSError:
            return "error"
        return "replaced"
    try:
        path.write_text(content)
    except OSError:
        return "error"
    return "created"


# Backward-compatible alias -- existing tests/imports continue to work
# while migrating to the new name.
_create_path_symlink = _install_path_launcher


# ---------------------------------------------------------------------------
# Windows: persist GAIA_WORKSPACE_PATH + PATH-shadow warning
# ---------------------------------------------------------------------------
#
# On Windows the launcher only exports GAIA_WORKSPACE_PATH PROCESS-scoped (see
# the launcher templates). If npm's own `gaia.cmd` wins the PATH lookup, Gaia's
# launcher never runs, the env var is never set, and doctor derives the npm
# prefix as the workspace -> false CRITICAL. Persisting the var at USER scope
# (`setx`) makes doctor resolve the workspace regardless of which `gaia` wins,
# because the next `gaia doctor` is a NEW process that inherits the user env.


def _persist_workspace_env(workspace: Path) -> dict:
    """Windows only: persist GAIA_WORKSPACE_PATH to the USER environment.

    Uses ``setx GAIA_WORKSPACE_PATH "<workspace>"`` -- a documented, built-in
    Windows command that writes the value under HKCU\\Environment and broadcasts
    WM_SETTINGCHANGE. Chosen over a direct ``winreg.SetValueEx`` because it is
    a single self-contained call (no manual broadcast, no HKCU key handling),
    and it mirrors the subprocess pattern the rest of this module already uses
    (bootstrap, seeders). ``setx`` truncates at 1024 chars, which a workspace
    path never approaches.

    Semantics: last-install-wins, single-valued -- coherent with the launcher,
    which bakes exactly one workspace. ``setx`` applies to FUTURE processes
    (the current shell keeps its old value), which is precisely what doctor
    needs: the next `gaia doctor` invocation is a new process.

    Returns a step-result dict (``action``/``details``) compatible with
    ``_report_step``. Never raises -- a failure here is advisory (the
    process-scoped launcher export still covers the launcher path).
    """
    if not _is_windows():
        return {"action": "noop", "details": "not Windows -- no env persistence needed"}

    value = str(workspace)
    try:
        result = subprocess.run(
            ["setx", "GAIA_WORKSPACE_PATH", value],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {"action": "error", "details": f"setx invocation failed: {exc}"}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[:200]
        return {"action": "error", "details": f"setx exited {result.returncode}: {detail}"}

    return {
        "action": "created",
        "details": f"GAIA_WORKSPACE_PATH persisted (user env) -> {value}",
    }


def _npm_global_prefix() -> "Path | None":
    """Best-effort npm global prefix on Windows (where npm writes its shim).

    Under ``npm install -g``, npm writes ``gaia.cmd`` into ``%APPDATA%\\npm``.
    We use that convention rather than shelling out to ``npm config get prefix``
    to keep the check offline and fast -- it feeds only an ADVISORY warning, so
    a heuristic is acceptable. Returns None when APPDATA is unset (then the
    precedence check only verifies Gaia's dir is present at all).
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "npm"
    return None


def _launcher_path_precedence(
    gaia_bin_dir: Path,
    npm_prefix: "Path | None",
    path_dirs: "list[str]",
) -> "str | None":
    """Return an actionable warning when Gaia's launcher will NOT win the
    ``gaia`` name resolution against npm's own shim -- else None.

    Pure and platform-agnostic (every input is passed in), so it is unit-
    testable on any OS. Comparison is case-insensitive and path-normalized
    (Windows PATH entries vary in case and separators).

    Two shadowing conditions produce a warning:
      1. Gaia's launcher dir is not on PATH at all -> npm's shim always wins.
      2. The npm prefix precedes Gaia's dir on PATH -> npm's shim wins.
    """
    def _norm(p) -> str:
        return os.path.normcase(os.path.normpath(str(p)))

    normalized = [_norm(p) for p in path_dirs if p]
    gaia_norm = _norm(gaia_bin_dir)

    gaia_idx = normalized.index(gaia_norm) if gaia_norm in normalized else None

    if gaia_idx is None:
        return (
            f"{gaia_bin_dir} is not on PATH -- npm's own `gaia` shim will run "
            "instead of Gaia's workspace-bound launcher. Add that dir to PATH "
            "(ahead of the npm prefix) so `gaia` resolves to Gaia's launcher."
        )

    if npm_prefix is not None:
        npm_norm = _norm(npm_prefix)
        npm_idx = normalized.index(npm_norm) if npm_norm in normalized else None
        if npm_idx is not None and npm_idx < gaia_idx:
            return (
                f"the npm prefix ({npm_prefix}) precedes Gaia's launcher dir "
                f"({gaia_bin_dir}) on PATH -- npm's `gaia` shim wins, so the "
                "workspace-bound launcher will not run. Move Gaia's dir ahead "
                "of the npm prefix on PATH."
            )

    return None


def _warn_launcher_shadowed(link: "Path | str", quiet: bool) -> "str | None":
    """Windows only: emit an actionable warning when the launcher dir will not
    win ``gaia`` resolution against npm's shim.

    The plain ``PATH launcher: gaia.cmd=created`` step line is misleading when
    the launcher is shadowed on PATH (it reports creation, not effectiveness);
    this converts that into a visible, actionable signal. Returns the warning
    message (also printed to stderr unless quiet) or None when not shadowed.
    """
    if not _is_windows():
        return None

    gaia_bin_dir = Path(link).expanduser().parent
    warning = _launcher_path_precedence(
        gaia_bin_dir=gaia_bin_dir,
        npm_prefix=_npm_global_prefix(),
        path_dirs=os.environ.get("PATH", "").split(os.pathsep),
    )
    if warning and not quiet:
        print(f"  [!] PATH launcher: {warning}", file=sys.stderr)
    return warning


# ---------------------------------------------------------------------------
# Bootstrap invocation
# ---------------------------------------------------------------------------

def _run_bootstrap(db_path: str | None, verbose: bool, quiet: bool) -> dict:
    """Invoke bootstrap_database.py and return a structured result.

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
        env["GAIA_DB"] = str(Path(db_path).expanduser().resolve())

    cmd = [sys.executable or "python3", str(_BOOTSTRAP_SCRIPT)]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"failed to invoke python bootstrapper -- {exc}"
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
        str(Path(db_path).expanduser().resolve())
        if db_path
        else str(Path("~/.gaia/gaia.db").expanduser().resolve())
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


def _seed_surface_routing(db_path: str | None, quiet: bool) -> dict:
    """Invoke seed_surface_routing to populate the surface_routing table.

    Mirror of ``_seed_contract_permissions``: reads each agent's ``routing:``
    frontmatter block and seeds the DB-backed routing table that
    surface_router.py reads (replacing config/surface-routing.json). Returns a
    step-result dict compatible with ``_report_step``. Never raises -- seeding
    failures are logged and reported as action='error' so the install continues.
    """
    if not _SEED_SURFACE_ROUTING.is_file():
        return {
            "action": "skipped",
            "details": f"seeder not found at {_SEED_SURFACE_ROUTING}",
        }

    env = os.environ.copy()
    resolved_db = (
        str(Path(db_path).expanduser().resolve())
        if db_path
        else str(Path("~/.gaia/gaia.db").expanduser().resolve())
    )
    cmd = [sys.executable, str(_SEED_SURFACE_ROUTING), "--db-path", resolved_db]

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
            sys.stderr.write(f"  [!] surface-routing seeder: {detail}\n")
        return {"action": "error", "details": detail}

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

def _print_header(*, postinstall: bool, quiet: bool, workspace: Path) -> None:
    if quiet:
        return
    label = "postinstall" if postinstall else "first-time install"
    print(f"\n  Setting up Gaia for the first time...")
    print(f"  ({label})")
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
        help="Skip creating the ~/.local/bin/gaia launcher",
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

    _print_header(postinstall=postinstall, quiet=quiet, workspace=workspace)

    # Step 1 -- bootstrap DB (always)
    bootstrap_res = _run_bootstrap(db_path=db_path, verbose=verbose, quiet=quiet)
    rc = bootstrap_res["rc"]
    if rc == 0:
        # Step 1a -- seed agent_contract_permissions from agent frontmatters.
        # Runs after bootstrap so the table is guaranteed to exist (v3 migration).
        # Non-fatal: a seeding failure should not abort an otherwise clean install.
        seed_res = _seed_contract_permissions(db_path=db_path, quiet=quiet)
        _report_step(name="contract-permissions", result=seed_res, quiet=quiet, verbose=verbose)
        # Step 1b -- seed surface_routing from agent `routing:` frontmatter
        # blocks (mirror of 1a). Populates the DB-backed routing table that
        # replaced config/surface-routing.json. Non-fatal on failure.
        routing_res = _seed_surface_routing(db_path=db_path, quiet=quiet)
        _report_step(name="surface-routing", result=routing_res, quiet=quiet, verbose=verbose)
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

    perms_res = _install_helpers.merge_local_permissions(workspace)
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
        # Windows: the "created" line above reports the launcher was WRITTEN,
        # not that it will WIN `gaia` resolution. Warn when Gaia's launcher dir
        # is not ahead of the npm prefix on PATH -- an actionable signal, not a
        # false all-clear. No-op on POSIX.
        _warn_launcher_shadowed(link="~/.local/bin/gaia", quiet=quiet)

    # Step 6.6 -- Windows: persist GAIA_WORKSPACE_PATH to the user environment
    # so `gaia doctor` resolves THIS workspace regardless of which `gaia` wins
    # PATH. The launcher only exports it process-scoped; without this, when
    # npm's shim wins, doctor derives the npm prefix and emits a false CRITICAL
    # (the rc.2 bug). Runs even under --no-path: the env var, not the launcher,
    # is what makes doctor's derivation correct. No-op on POSIX.
    if _is_windows():
        env_res = _persist_workspace_env(workspace)
        _report_step(name="workspace-env", result=env_res, quiet=quiet, verbose=verbose)

    # Install owns Steps 1-6 only. Workspace scanning is a separate, on-demand
    # flow (`gaia scan`); install never triggers it. A clean install clears any
    # stale install-error marker left by a prior failed bootstrap attempt.
    _clear_install_error_marker()

    _print_next_steps(quiet=quiet, postinstall=postinstall)
    return 0
