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
  1. Run `gaia migrate apply` (`cli.migrate`, engine
     `scripts/bootstrap_database.py`) -- the cross-platform Python
     bootstrapper for creating/upgrading `~/.gaia/gaia.db` (schema,
     agent_permissions seed, project registration, FTS5 backfill, invariant
     checks). The canonical schema source is `gaia/store/schema.sql`;
     `bootstrap_database.sh` is retained as the shell/test reference.
  2. Configure workspace `.claude/settings.json` (create if missing).
  3. Merge gaia permissions, env vars, and agent identity into
     `.claude/settings.local.json`.
  4. Register Gaia's hooks in `.claude/settings.local.json` through the single
     writer (`plugin_setup.sync_workspace_hooks`): npm mode writes each
     (event, matcher, command) `hooks.json` ships and prunes retired ones;
     plugin mode (or a workspace enabling the Gaia plugin) writes none, since
     CC reads the plugin's hooks.json directly. User entries are kept.
  5. Create or repair `.claude/{agents,tools,hooks,config,skills}` symlinks
     (5 directories) plus a `.claude/CHANGELOG.md` file link, pointing at the
     installed package (`_SYMLINK_NAMES` + `_SYMLINK_FILES` in
     `_install_helpers.py`).
  6. Write `.claude/plugin-registry.json` with `installed[].name == "gaia"`
     (the single unified plugin registry identity).

  7. First scan (`gaia.install_root.first_scan`): the workspace is the folder
     install ran in, registered under its basename with that folder as its
     root, and the repos beneath it are indexed. A root already recorded is
     left alone -- re-indexing is `gaia scan` -- and repos inside another
     workspace's recorded root stay with that workspace. Non-fatal.

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
   --channel CHANNEL  Required: npm wires Claude Code through this package,
                      opencode wires OpenCode. npm refuses while the Claude
                      Code plugin is enabled for the workspace; opencode
                      joins either. `--host claude_code|opencode` is the
                      older alias.
  --skip-workspace   Bootstrap the DB only; skip workspace configuration.
                     Useful when running install just to refresh the DB
                     schema from a non-Gaia directory.
  --path             Opt in to the writes outside the workspace: the
                     ~/.local/bin/gaia launcher (a symlink to this package's
                     bin/gaia; `gaia.cmd` + `gaia.ps1` on Windows) and, on
                     Windows, `setx GAIA_WORKSPACE_PATH`. Without it install
                     writes nothing outside the workspace. `--no-path` is
                     still accepted and does nothing.

Every file and settings key install writes is recorded in the workspace's
manifest, `.claude/gaia-manifest.json` (`cli/_manifest.py`), which
`gaia uninstall` reverts exactly. A workspace wired by an install that
predates the manifest is adopted on the next install. `gaia update` is an
alias of this command.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# bin/cli/install.py -> bin/cli -> bin -> gaia/
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# Helpers shared with `gaia update`. Module-relative import works when run
# via `python bin/gaia install` because bin/ is on sys.path.
from cli import _install_helpers  # type: ignore  # noqa: E402
from cli import migrate  # type: ignore  # noqa: E402
from cli import _manifest  # type: ignore  # noqa: E402

_BOOTSTRAP_SCRIPT = migrate.ENGINE

_SEED_CONTRACT_PERMS = _PACKAGE_ROOT / "tools" / "scan" / "seed_contract_permissions.py"
_SEED_SURFACE_ROUTING = _PACKAGE_ROOT / "tools" / "scan" / "seed_surface_routing.py"

# ---------------------------------------------------------------------------
# Channel selection
# ---------------------------------------------------------------------------
#
# The hosts the package channels wire. One point of truth for this parser AND
# `gaia dev`'s, which imports these names rather than repeating them.
#
# Deliberately NOT derived from `hooks/adapters/registry.py::_REGISTRY` at
# runtime, for two reasons that hold at import time:
#   * `hooks/` ships no `__init__.py`, so it is not an importable package from
#     the source root; only tests reach it, via the sys.path insert in
#     `tests/conftest.py`.
#   * `bin/gaia` imports ONLY the single plugin module argv names, precisely so
#     an invocation does not pay for unrelated modules. Reaching the registry
#     would pull `hooks/adapters/claude_code.py` (>5k lines) into every
#     `gaia install --help`.
# The divergence that duplication invites is caught instead by the parity
# tripwire in `tests/cli/test_host_multi_install.py`, which does import the
# registry and fails if the two sets stop matching.
SUPPORTED_HOSTS = ("claude_code", "opencode")
PACKAGE_CHANNELS = {"npm": "claude_code", "opencode": "opencode"}
HOST_ALIASES = {host: channel for channel, host in PACKAGE_CHANNELS.items()}
CHANNEL_SUMMARIES = {
    "npm": "Claude Code, wired through this package into .claude/",
    "plugin": "Claude Code plugin gaia@gaia-dev, served from this build into the workspace",
    "opencode": "OpenCode, wired through this package into opencode.json; joins either Claude Code channel",
}
_PLUGIN_SCOPES = {"settings.local.json": "local", "settings.json": "project", "user settings": "user"}


def require_supported_host(host: str) -> None:
    """Raise ValueError unless *host* is one the package channels wire."""
    if host not in SUPPORTED_HOSTS:
        raise ValueError(f"unsupported host {host!r}; choose from {', '.join(SUPPORTED_HOSTS)}")


def missing_channel_message(choices: Sequence[str]) -> str:
    """The error for a command run without a channel, listing *choices*."""
    lines = ["name a channel with --channel; there is no default:"]
    lines += [f"  {name:<9} {CHANNEL_SUMMARIES[name]}" for name in choices]
    if "plugin" not in choices:
        lines.append("The Claude Code plugin is installed by Claude Code: claude plugin install "
                     "gaia@gaia-marketplace, or gaia dev --channel plugin for a source build.")
    return "\n".join(lines)


def resolve_channel(channel: str | None, host: str | None, choices: Sequence[str]) -> str:
    """The channel --channel names, or its --host alias; ValueError listing *choices* when neither is named."""
    if channel is None and host is None:
        raise ValueError(missing_channel_message(choices))
    resolved = channel if channel is not None else HOST_ALIASES.get(host)
    if resolved not in choices:
        raise ValueError(f"unsupported channel {channel or host!r}; choose from {', '.join(choices)}")
    return resolved


def channel_conflict(workspace: Path, channel: str) -> str | None:
    """Why *channel* cannot be installed in *workspace*, or None when nothing excludes it.

    npm and plugin both register Gaia's hooks with Claude Code, so each refuses
    while the other is present; opencode writes no Claude Code wiring and joins either.
    """
    # Imported here: plugin sessions load this module, and the hooks package must stay optional at import.
    from modules.core.plugin_setup import enabled_gaia_plugins, workspace_registers_gaia_hooks  # type: ignore

    beside = "OpenCode can still be added beside it: --channel opencode"
    plugins = enabled_gaia_plugins(workspace) if channel == "npm" else []
    if plugins:
        key, label = plugins[0]
        return (f"the plugin channel is present in {workspace}: {key} is enabled in {label}, "
                "and the npm channel would register Gaia's hooks a second time.\n"
                f"Remove it first: claude plugin uninstall {key} --scope {_PLUGIN_SCOPES[label]}\n{beside}")
    if channel == "plugin" and workspace_registers_gaia_hooks(workspace):
        return (f"the npm channel is present in {workspace}: Gaia's hooks are registered in "
                ".claude/settings.local.json, and the plugin would run them a second time.\n"
                f"Remove it first: {_manifest.uninstall_command(workspace, 'npm')}\n{beside}")
    return None


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
#   * POSIX  -> one symlink at ``<link>`` to the selected package's declared
#       ``bin/gaia`` entry. Following that link preserves package provenance
#       for the orchestrator's trusted-binary check.
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
# On Windows, re-running `gaia install` from a different workspace rewrites the
# launchers to point at that workspace and re-persists GAIA_WORKSPACE_PATH
# (last-install-wins, single-valued). POSIX launchers select a package, not a
# workspace.

# Legacy POSIX launcher, retained only to recognize and migrate regular wrappers
# written by earlier versions. New POSIX installs use a package-provenance
# symlink instead.
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
    """Render the legacy POSIX wrapper for migration detection.

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
    """Install the selected package's Gaia launcher at `link_path`.

    Platform-guarded (Step 6.5): on Windows this writes ``<link>.cmd`` and
    ``<link>.ps1`` (see ``_install_windows_launchers``); on POSIX it writes a
    symlink to the actual ``bin/gaia`` of the package running the installer.
    Resolving the PATH winner therefore reaches the package manifest's declared
    executable rather than an unprovable forwarding wrapper.

    Behavior (POSIX):
      - If `link_path` is the expected symlink: noop.
      - If `link_path` is another symlink: retarget it (legacy migration).
      - If `link_path` is a legacy generated wrapper: migrate it to the symlink.
      - If `link_path` is a regular file with different content and
        `overwrite=False`: skip with warning.
      - If `link_path` is a regular file with different content and
        `overwrite=True`: replace.
      - If `link_path` does not exist: create the symlink (and parent dir).

    Args:
        target_path: legacy name for the POSIX package entrypoint target.
        link_path: where the shim is written (default ``~/.local/bin/gaia``).
            On Windows, ``.cmd``/``.ps1`` suffixes are derived from this base.
        overwrite: replace existing different-content files when True.
        workspace: consumer workspace, used by Windows launchers and to
            recognize wrappers generated by older POSIX installs.
        gaia_bin: the selected package's ``bin/gaia`` dispatcher. Defaults to
            ``_gaia_entrypoint()`` for both platforms.

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

    entry = gaia_bin if gaia_bin is not None else target_path
    expected_target = (
        Path(entry).expanduser() if entry is not None else _gaia_entrypoint()
    ).resolve()
    if not expected_target.is_file():
        return {
            "action": "error",
            "path": str(link),
            "details": f"package entrypoint is not a file: {expected_target}",
        }
    legacy_wrapper = _render_launcher(workspace_resolved)

    def _write_symlink() -> None:
        link.symlink_to(expected_target)

    if link.is_symlink():
        try:
            if link.resolve() == expected_target:
                return {
                    "action": "noop",
                    "path": str(link),
                    "details": "launcher already targets selected package entrypoint",
                }
        except OSError:
            pass
        try:
            link.unlink()
            _write_symlink()
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "details": f"failed to retarget launcher symlink: {exc}",
            }
        return {
            "action": "migrated",
            "path": str(link),
            "details": "retargeted launcher symlink to selected package entrypoint",
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
        is_legacy_wrapper = current == legacy_wrapper
        if not is_legacy_wrapper and not overwrite:
            return {
                "action": "skipped",
                "path": str(link),
                "details": (
                    "file exists with different content; "
                    "use --no-path to suppress or remove manually to refresh"
                ),
            }
        try:
            link.unlink()
            _write_symlink()
        except OSError as exc:
            return {
                "action": "error",
                "path": str(link),
                "details": f"failed to replace launcher: {exc}",
            }
        return {
            "action": "migrated" if is_legacy_wrapper else "replaced",
            "path": str(link),
            "details": (
                "migrated generated wrapper to package-provenance symlink"
                if is_legacy_wrapper
                else "replaced previous launcher with package-provenance symlink"
            ),
        }

    # Path does not exist -- create launcher.
    try:
        _write_symlink()
    except OSError as exc:
        return {
            "action": "error",
            "path": str(link),
            "details": f"failed to create launcher symlink: {exc}",
        }
    return {
        "action": "created",
        "path": str(link),
        "details": "package-provenance launcher symlink installed",
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


def _launcher_manifest_paths() -> list[Path]:
    """Every path `--path` may create outside the workspace, parents included."""
    link = Path("~/.local/bin/gaia").expanduser()
    if _is_windows():
        return [link.with_name("gaia.cmd"), link.with_name("gaia.ps1"), link.parent, link.parent.parent]
    return [link, link.parent, link.parent.parent]


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


def _npm_config_prefix_posix() -> "Path | None":
    """Best-effort npm global prefix on POSIX (the dir whose ``bin/`` holds the
    global ``gaia`` shim under ``npm install -g``/``npm link``).

    Resolution order (first hit wins): the ``NPM_CONFIG_PREFIX`` env var, then
    ``npm config get prefix`` (offline, no network -- it reads local config),
    then the ``~/.npm-global`` convention the drift-free design names as the
    global surface. Feeds only an ADVISORY warning, so a heuristic is
    acceptable; a missing/unresolvable prefix returns None (the precedence check
    then only verifies Gaia's own dir is present).
    """
    for var in ("NPM_CONFIG_PREFIX", "npm_config_prefix"):
        val = os.environ.get(var)
        if val:
            return Path(val).expanduser()
    try:
        result = subprocess.run(
            ["npm", "config", "get", "prefix"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        out = (result.stdout or "").strip()
        if result.returncode == 0 and out and out.lower() not in ("undefined", "null"):
            return Path(out).expanduser()
    except (OSError, subprocess.TimeoutExpired):
        pass
    default = Path("~/.npm-global").expanduser()
    return default if default.is_dir() else None


def _npm_global_prefix() -> "Path | None":
    """Best-effort directory where npm writes the global ``gaia`` shim.

    Cross-platform: on Windows npm writes ``gaia.cmd`` directly into
    ``%APPDATA%\\npm``; on POSIX npm writes the ``gaia`` shim into
    ``<prefix>/bin`` (see ``_npm_config_prefix_posix``). Returns None when the
    location cannot be resolved (then the precedence check only verifies Gaia's
    own dir is present). Feeds only an ADVISORY warning, so a heuristic is fine.
    """
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "npm" if appdata else None
    prefix = _npm_config_prefix_posix()
    return prefix / "bin" if prefix is not None else None


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
    """Emit an actionable warning when the launcher dir will not win ``gaia``
    name resolution against a global npm shim -- on BOTH Windows and POSIX.

    The plain ``PATH launcher: ...=created`` step line is misleading when the
    launcher is shadowed on PATH (it reports creation, not effectiveness); this
    converts that into a visible, actionable signal. Returns the warning message
    (also printed to stderr unless quiet) or None when not shadowed.

    Windows: npm writes ``gaia.cmd`` into its prefix under ``npm install -g``, so
    the check runs on dir precedence alone (the shim is assumed present in the
    install context -- unchanged behavior).

    POSIX (new, per the drift-free design): a bare dir-precedence check would
    over-warn, because npm's prefix (e.g. ``/usr/local/bin``) commonly precedes
    ``~/.local/bin`` even when NO global ``gaia`` exists there. So on POSIX the
    check first confirms npm's global bin actually holds a ``gaia`` shim
    DISTINCT from Gaia's own launcher; only then does it evaluate precedence.
    This makes the reconcile of the global surface (surface 4) an actionable
    signal without a spurious warning on every install.
    """
    gaia_bin_dir = Path(link).expanduser().parent
    npm_prefix = _npm_global_prefix()

    if not _is_windows():
        # Only meaningful when npm's global bin actually holds a distinct `gaia`
        # shim -- otherwise nothing shadows Gaia's launcher.
        if npm_prefix is None:
            return None
        npm_gaia = npm_prefix / "gaia"
        try:
            if not npm_gaia.exists():
                return None
            launcher = Path(link).expanduser()
            if launcher.exists() and os.path.samefile(npm_gaia, launcher):
                # npm's `gaia` IS Gaia's launcher (e.g. same file via npm link) --
                # aligned, nothing to warn about.
                return None
        except OSError:
            return None

    warning = _launcher_path_precedence(
        gaia_bin_dir=gaia_bin_dir,
        npm_prefix=npm_prefix,
        path_dirs=os.environ.get("PATH", "").split(os.pathsep),
    )
    if warning and not quiet:
        print(f"  [!] PATH launcher: {warning}", file=sys.stderr)
    return warning


def _warn_launcher_dir_absent(link: "Path | str", quiet: bool) -> "str | None":
    """Warn when the launcher's own directory is entirely absent from PATH, so
    a bare ``gaia`` cannot resolve at all -- on BOTH Windows and POSIX.

    This is orthogonal to ``_warn_launcher_shadowed`` (which covers the case
    where a DISTINCT npm global ``gaia`` shim WINS the name lookup). Here the
    condition is unambiguous: the launcher file was written, but its directory
    (default ``~/.local/bin``) is on no PATH entry, so the shell will never find
    ``gaia`` regardless of any npm shim. This is exactly the wrinkle the docs
    assumed away -- ``gaia install`` is expected to make a bare ``gaia`` work,
    but on a fresh local install (``npm install`` / ``pnpm add``) the CLI lands
    in ``node_modules/.bin`` (not on PATH) and ``~/.local/bin`` is frequently not
    on PATH either. Without this signal, the ``PATH launcher: ...=created`` step
    line misleads the user into believing a bare ``gaia`` now works.

    Deliberately narrow: it fires ONLY on the dir-absent case, never on npm
    precedence (that is ``_warn_launcher_shadowed``'s concern), so it does not
    reintroduce the over-warning the POSIX shadow check was tuned to avoid.
    Comparison is case-insensitive and path-normalized (mirrors
    ``_launcher_path_precedence``). Returns the warning (also printed to stderr
    unless quiet) or None when the launcher dir is already on PATH.
    """
    launcher_dir = Path(link).expanduser().parent

    def _norm(p) -> str:
        return os.path.normcase(os.path.normpath(str(p)))

    path_dirs = {
        _norm(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p
    }
    if _norm(launcher_dir) in path_dirs:
        return None

    warning = (
        f"{launcher_dir} is not on PATH -- a bare `gaia` will not resolve yet. "
        f'Add it to PATH (e.g. add `export PATH="{launcher_dir}:$PATH"` to your '
        "shell profile), or invoke Gaia through your package manager "
        "(`npx gaia ...`) until you do."
    )
    if not quiet:
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

    try:
        result = migrate.run("apply", db_path=db_path, capture=True)
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


def _first_scan(workspace: Path, db_path: str | None) -> dict:
    """Register and scan *workspace* once; a failure is reported, never fatal."""
    from gaia.install_root import first_scan

    try:
        return first_scan(
            workspace, database=Path(db_path).expanduser().resolve() if db_path else None
        )
    except Exception as exc:
        return {"action": "error", "details": f"{type(exc).__name__}: {exc}"}


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


def _configure_host(
    host: str,
    *,
    workspace: Path,
    postinstall: bool,
    quiet: bool,
    verbose: bool,
    strict: bool = False,
) -> bool:
    """Wire one host into *workspace*; return whether it was configured.

    Host-scoped only: everything global (DB bootstrap, permission and routing
    seeds) runs once in `cmd_install` before this is called, so wiring N hosts
    re-runs none of it. Returns False instead of an exit code because with
    several hosts requested one host's failure must not decide the command's
    outcome.
    """
    if host == "opencode":
        opencode_res = _install_helpers.configure_opencode_plugin(workspace)
        _report_step(name="OpenCode plugin", result=opencode_res, quiet=quiet, verbose=verbose)
        if strict:
            return opencode_res.get("action") in ("created", "updated", "noop")
        return opencode_res.get("action") != "error"

    identity_res = _install_helpers.verify_orchestrator_artifact()
    if identity_res["action"] == "error":
        print(f"gaia install: {identity_res['details']}", file=sys.stderr)
        return False

    # Step 1.5 -- ensure workspace .claude/ exists BEFORE invoking helpers.
    # The first four helpers early-return when .claude/ is missing, so it
    # must exist before any Claude-specific configuration runs.
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
            return False

    settings_res = _install_helpers.configure_settings_json(workspace)
    _report_step(name="settings.json", result=settings_res, quiet=quiet, verbose=verbose)
    if strict and settings_res.get("action") not in ("created", "updated", "noop"):
        return False

    perms_res = _install_helpers.merge_local_permissions(workspace)
    _report_step(name="permissions", result=perms_res, quiet=quiet, verbose=verbose)
    if strict and perms_res.get("action") not in ("created", "updated", "noop"):
        return False

    hooks_res = _install_helpers.merge_local_hooks(workspace)
    _report_step(name="hooks", result=hooks_res, quiet=quiet, verbose=verbose)
    if strict and hooks_res.get("action") not in ("created", "updated", "noop"):
        return False

    worktree_res = _install_helpers.merge_worktree_settings(workspace)
    _report_step(name="worktree", result=worktree_res, quiet=quiet, verbose=verbose)
    if strict and worktree_res.get("action") not in ("created", "updated", "noop"):
        return False

    sym_res = _install_helpers.manage_symlinks(workspace)
    _report_step(name="symlinks", result=sym_res, quiet=quiet, verbose=verbose)
    if strict and sym_res.get("action") not in ("created", "updated", "noop"):
        return False

    registry_source = "npm-postinstall" if postinstall else "cli-install"
    reg_res = _install_helpers.register_plugin(workspace, source=registry_source)
    _report_step(name="plugin-registry", result=reg_res, quiet=quiet, verbose=verbose)
    if strict:
        return reg_res.get("action") in ("created", "updated", "noop")
    return True


def _print_next_steps(
    *,
    quiet: bool,
    postinstall: bool,
    hosts: Sequence[str],
) -> None:
    """Print the post-install steps for the wired hosts, `gaia doctor` once."""
    if quiet:
        return
    restart_steps: list[str] = []
    open_steps: list[str] = []
    for host in hosts:
        if host == "opencode":
            restart_steps.append("Restart OpenCode to load the Gaia plugin.")
            open_steps.append(
                "To let OpenCode run subagents in the background, add this line to "
                f"your shell profile: {_manifest.OPENCODE_BACKGROUND_SUBAGENTS_EXPORT}")
        elif postinstall:
            restart_steps.append("Restart Claude Code to pick up new hooks/agents.")
        else:
            open_steps.append("Open Claude Code in this workspace.")

    verify = "Run `gaia doctor` to verify the installation."
    # A restart is what makes freshly installed hooks live, so it leads when
    # there is one; with nothing to restart, verifying leads instead. `gaia
    # doctor` covers the whole install, so it is listed once however many hosts
    # were wired.
    if restart_steps:
        steps = [*restart_steps, *open_steps, verify]
    else:
        steps = [verify, *open_steps]

    print()
    print("  Gaia ready. Next steps:")
    for index, step in enumerate(steps, start=1):
        print(f"    {index}. {step}")
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
            "The database step runs `gaia migrate apply`: a backup first, the\n"
            "whole chain in one transaction, structure-only chains on their own.\n"
            "A chain that reaches existing rows stops here and names the\n"
            "`gaia migrate apply --consent-chain vA..vB` command that continues.\n"
            "\n"
            "The workspace is the folder install runs in (or --workspace): the\n"
            "first install registers it under its folder name and scans the\n"
            "repos beneath it. Later runs leave it to `gaia scan`.\n"
            "\n"
            "There is no npm postinstall hook -- bootstrap is lazy, triggered\n"
            "by the first `gaia` CLI invocation. Typically called by:\n"
            "  - the user, manually, to re-bootstrap the DB or workspace\n"
            "  - a non-interactive caller passing --postinstall for fail-soft output\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_install_arguments(p)
    return p


def add_install_arguments(p: argparse.ArgumentParser) -> None:
    """The flags of `gaia install`, shared by its alias `gaia update`."""
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
    selection = p.add_mutually_exclusive_group()
    selection.add_argument(
        "--channel",
        choices=tuple(PACKAGE_CHANNELS),
        default=None,
        help=(
            "Channel to wire, required unless --skip-workspace: npm wires Claude Code through this package, "
            "opencode wires OpenCode. npm refuses while the Claude Code plugin is "
            "enabled for the workspace and names how to remove it; opencode joins "
            "either. The Claude Code plugin itself is installed with `claude plugin "
            "install gaia@gaia-marketplace`."
        ),
    )
    selection.add_argument(
        "--host",
        choices=SUPPORTED_HOSTS,
        default=None,
        help="Alias kept for compatibility: claude_code = --channel npm, opencode = --channel opencode",
    )
    p.add_argument(
        "--skip-workspace",
        dest="skip_workspace",
        action="store_true",
        default=False,
        help="Skip workspace configuration; only bootstrap the DB",
    )
    p.add_argument(
        "--path",
        dest="path",
        action="store_true",
        default=False,
        help=(
            "Also write the `gaia` launcher to ~/.local/bin (gaia.cmd/gaia.ps1 "
            "on Windows) and persist GAIA_WORKSPACE_PATH with setx on Windows. "
            "Recorded in the manifest, so `gaia uninstall` takes them back."
        ),
    )
    # Retired opt-out, accepted so existing callers keep working: nothing is
    # written outside the workspace unless --path asks for it.
    p.add_argument("--no-path", dest="no_path", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--strict-wiring",
        action="store_true",
        help="Fail if any requested host or required wiring step fails or is skipped",
    )


def recorded_channels(workspace: Path) -> tuple[str, ...]:
    """The package channels `gaia install` recorded in *workspace*'s manifest, in wiring order."""
    recorded = (_manifest.load(workspace) or {}).get("package_channels", ())
    return tuple(channel for channel in PACKAGE_CHANNELS if channel in recorded)


def cmd_install(args: argparse.Namespace) -> int:
    """Execute the install subcommand; --skip-workspace alone needs no channel, since it wires nothing."""
    no_channel = getattr(args, "channel", None) is None and getattr(args, "host", None) is None
    if no_channel and getattr(args, "skip_workspace", False):
        return install_channels(args, ())
    try:
        channel = resolve_channel(getattr(args, "channel", None), getattr(args, "host", None),
                                  tuple(PACKAGE_CHANNELS))
    except ValueError as exc:
        print(f"gaia install: {exc}", file=sys.stderr)
        return 1
    return install_channels(args, (channel,))


def install_channels(args: argparse.Namespace, channels: Sequence[str], *, command: str = "gaia install") -> int:
    """Bootstrap the DB once, then wire each of *channels* into the workspace and record them."""
    postinstall = bool(getattr(args, "postinstall", False))
    quiet = bool(getattr(args, "quiet", False))
    verbose = bool(getattr(args, "verbose", False))
    db_path = getattr(args, "db_path", None)
    skip_workspace = bool(getattr(args, "skip_workspace", False))
    opt_path = bool(getattr(args, "path", False))
    strict_wiring = bool(getattr(args, "strict_wiring", False))
    workspace_arg = getattr(args, "workspace", None)
    hosts = tuple(PACKAGE_CHANNELS[channel] for channel in channels)

    workspace = (
        Path(workspace_arg).expanduser().resolve()
        if workspace_arg
        else Path(os.environ.get("INIT_CWD", os.getcwd())).resolve()
    )
    if not skip_workspace:
        for channel in channels:
            conflict = channel_conflict(workspace, channel)
            if conflict:
                print(f"{command}: {conflict}", file=sys.stderr)
                return 1

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
        _print_next_steps(quiet=quiet, postinstall=postinstall, hosts=hosts)
        return 0

    # Steps 2-6 -- workspace configuration
    if not workspace.exists():
        if not quiet:
            print(f"  workspace {workspace} does not exist -- skipping configuration", file=sys.stderr)
        return 0

    outside = _launcher_manifest_paths() if opt_path else []
    baseline, baseline_source = _manifest.baseline_for(workspace, outside)
    if baseline_source == "adopted" and not quiet:
        print("  [~] manifest: adopting an install that predates the manifest")

    wired: list[str] = []
    failed: list[str] = []
    for channel in channels:
        configured = _configure_host(
            PACKAGE_CHANNELS[channel],
            workspace=workspace,
            postinstall=postinstall,
            quiet=quiet,
            verbose=verbose,
            strict=strict_wiring,
        )
        (wired if configured else failed).append(channel)

    if failed and not quiet:
        print(f"  [!] channel configuration failed: {', '.join(failed)}", file=sys.stderr)

    if strict_wiring and failed:
        return 1

    if not wired:
        # postinstall stays fail-soft: a non-zero exit aborts the consumer's package install.
        return 0 if postinstall else 1

    # Step 6.5 -- PATH launcher (~/.local/bin/gaia), only with --path: the one
    # write outside the workspace, so the user asks for it.
    if opt_path:
        # Link directly to this installed package's bin/gaia so PATH resolution
        # preserves the provenance consumed by the orchestrator guard.
        path_res = _install_path_launcher(workspace=workspace)
        _report_step(name="PATH launcher", result=path_res, quiet=quiet, verbose=verbose)
        # Windows: the "created" line above reports the launcher was WRITTEN,
        # not that it will WIN `gaia` resolution. Warn when Gaia's launcher dir
        # is not ahead of the npm prefix on PATH -- an actionable signal, not a
        # false all-clear. No-op on POSIX.
        shadow_warning = _warn_launcher_shadowed(link="~/.local/bin/gaia", quiet=quiet)
        # If the shadow check found nothing, the launcher may still be
        # unreachable because its dir is entirely absent from PATH -- the common
        # fresh-local-install case (~/.local/bin not on PATH). Surface that
        # unambiguous condition so a bare `gaia` failing is a visible signal
        # instead of a silent surprise. Skipped when the shadow check already
        # warned, to avoid a double message (Windows dir-absent is covered
        # there via _launcher_path_precedence).
        if shadow_warning is None:
            _warn_launcher_dir_absent(link="~/.local/bin/gaia", quiet=quiet)

    env_prior: dict = {}
    if opt_path and _is_windows():
        env_prior = _manifest.windows_env_prior()
        env_res = _persist_workspace_env(workspace)
        _report_step(name="workspace-env", result=env_res, quiet=quiet, verbose=verbose)

    manifest = _manifest.record(
        workspace,
        baseline,
        channel=_install_helpers.resolve_hook_channel(
            workspace, npm_copy="npm" in {*recorded_channels(workspace), *wired}
        ) or _manifest.OPENCODE_CHANNEL,
        version=_install_helpers._read_plugin_version(_PACKAGE_ROOT) or "unknown",
        extra=outside,
        env=env_prior,
        package_channels=wired,
    )
    _report_step(
        name="manifest",
        result={"action": "updated", "details": f"{len(manifest['entries'])} entries recorded"},
        quiet=quiet,
        verbose=verbose,
    )

    _report_step(
        name="first scan",
        result=_first_scan(workspace, db_path),
        quiet=quiet,
        verbose=verbose,
    )

    # A clean install clears any stale install-error marker left by a prior
    # failed bootstrap attempt.
    _clear_install_error_marker()

    _print_next_steps(quiet=quiet, postinstall=postinstall,
                      hosts=[PACKAGE_CHANNELS[channel] for channel in wired])
    return 0
