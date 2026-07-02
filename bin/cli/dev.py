"""
gaia dev -- Fast local dev loop: pack + install + wire in one command.

Collapses today's manual 3-step loop (`npm pack` -> `npm`/`pnpm add
<tarball>` -> `gaia install --workspace <target>`) into a single atomic
`gaia dev [--workspace <path>]` invocation, so testing a source change in a
real consumer workspace is one command: edit source, run `gaia dev`,
restart Claude Code, test.

Two modes:

  --mode pack (default)
    1. `npm pack` the CURRENT source tree (via `_pack_helpers.pack_tarball`,
       shared with the Phase-2 `gaia release check` gate -- one pack
       primitive, not two).
    2. Install the freshly packed tarball into the target workspace's
       `node_modules` (npm or pnpm, auto-detected from lockfile/workspace
       markers).
    3. Wire `.claude/` and bootstrap the DB by invoking the FRESHLY
       INSTALLED copy's own `gaia install --workspace <target>` as a
       subprocess. This is deliberate, not incidental: `_install_helpers`
       resolves its `plugin_root` from wherever it is physically loaded
       from, so delegating to the installed copy (rather than importing
       `_install_helpers` in-process from this source tree) makes the
       symlinks point at the packed tarball's node_modules copy -- the
       same safeguard `bin/validate-sandbox.sh` documents (never wire a
       consumer workspace's `.claude/` back to the Gaia source repo).
    Reflects a real shippable version and reuses the exact install
    machinery a real `npm install` consumer would exercise.

  --mode link
    Symlinks `<workspace>/node_modules/@jaguilar87/gaia` directly at this
    source tree (no pack, no install) and wires `.claude/` in-process by
    calling this source tree's own `cli.install.cmd_install` -- so
    `_install_helpers` naturally resolves `plugin_root` to THIS source
    tree. Edits under `gaia/`, `hooks/`, `agents/`, `skills/`, `config/`,
    `tools/` are visible on the next Claude Code restart with no pack step
    at all. Instant iteration; does not reflect what actually ships.

Both modes terminate in the same place: `cli.install.cmd_install`, so the
wiring logic (settings.json, permissions, hooks, symlinks, plugin-registry,
DB bootstrap) is never duplicated between them or against `gaia install`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# bin/cli/dev.py -> bin/cli -> bin -> gaia/ (this source tree's root)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from cli import _pack_helpers  # type: ignore  # noqa: E402
from cli import install as install_mod  # type: ignore  # noqa: E402
from cli.install import _report_step  # type: ignore  # noqa: E402

_NPM_PACKAGE_NAME = "@jaguilar87/gaia"


# ---------------------------------------------------------------------------
# Package-manager detection + tarball install (pack mode)
# ---------------------------------------------------------------------------

def detect_package_manager(workspace: Path) -> str:
    """Return "pnpm" when the workspace is pnpm-managed, else "npm".

    Detected by the presence of a pnpm lockfile or workspace manifest --
    the same signal a developer would use to pick the right add command by
    hand. Defaults to npm, the safe universal fallback.
    """
    if (workspace / "pnpm-lock.yaml").is_file() or (workspace / "pnpm-workspace.yaml").is_file():
        return "pnpm"
    return "npm"


def install_tarball(
    workspace: Path,
    tarball: Path,
    *,
    package_manager: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Install *tarball* into workspace/node_modules via npm or pnpm.

    Mirrors `bin/validate-sandbox.sh`'s `install_package()`: if the
    workspace has no package.json yet, create a minimal one first so the
    package manager has an anchor to install against.
    """
    workspace = Path(workspace).resolve()
    pm = package_manager or detect_package_manager(workspace)

    if not (workspace / "package.json").is_file():
        try:
            subprocess.run(
                ["npm", "init", "-y"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "action": "error",
                "path": str(workspace / "package.json"),
                "details": f"failed to create anchor package.json: {exc}",
                "package_manager": pm,
            }

    cmd = ["pnpm", "add", str(tarball)] if pm == "pnpm" else [
        "npm", "install", "--no-audit", "--no-fund", str(tarball),
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "action": "error",
            "path": str(workspace),
            "details": f"{pm} install failed to invoke: {exc}",
            "package_manager": pm,
        }

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-500:]
        return {
            "action": "error",
            "path": str(workspace),
            "details": f"{pm} install exited {result.returncode}: {detail}",
            "package_manager": pm,
        }

    return {
        "action": "created",
        "path": str(workspace / "node_modules" / "@jaguilar87" / "gaia"),
        "details": f"installed {tarball.name} via {pm}",
        "package_manager": pm,
    }


def wire_workspace_via_installed_gaia(
    workspace: Path,
    *,
    quiet: bool = True,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run the FRESHLY INSTALLED copy's own `gaia install --workspace`.

    Deliberately delegates to `<workspace>/node_modules/@jaguilar87/gaia/bin/gaia`
    rather than importing `_install_helpers` from this source tree in-process
    -- see the module docstring for why plugin_root must resolve to the
    installed copy, not this dev source tree.
    """
    installed_gaia = (
        workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
    )
    if not installed_gaia.is_file():
        return {
            "action": "error",
            "path": str(installed_gaia),
            "details": "installed gaia entrypoint not found -- tarball install may have failed",
        }

    cmd = [sys.executable or "python3", str(installed_gaia), "install", "--workspace", str(workspace)]
    if quiet:
        cmd.append("--quiet")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "action": "error",
            "path": str(installed_gaia),
            "details": f"gaia install invocation failed: {exc}",
        }

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-500:]
        return {
            "action": "error",
            "path": str(installed_gaia),
            "details": f"gaia install exited {result.returncode}: {detail}",
        }

    tail = (result.stdout or "").strip().splitlines()
    return {
        "action": "created",
        "path": str(workspace / ".claude"),
        "details": tail[-1] if tail else "workspace wired",
    }


# ---------------------------------------------------------------------------
# Link mode: symlink this source tree directly into the workspace
# ---------------------------------------------------------------------------

def link_source_into_workspace(workspace: Path, source_root: Path) -> dict[str, Any]:
    """Symlink `<workspace>/node_modules/@jaguilar87/gaia` -> *source_root*.

    Makes the just-edited source tree visible under the workspace's own
    `node_modules` (needed by the `~/.local/bin/gaia` PATH launcher, which
    execs `<workspace>/node_modules/@jaguilar87/gaia/bin/gaia` verbatim)
    without ever running `npm pack`/`npm install` -- edits to the source
    tree are visible on the next Claude Code restart, no repack needed.

    Idempotent: re-running when already linked to the same source is a
    noop. Refuses to clobber a real (non-symlink) install already present
    -- that is either a prior `--mode pack` run or a real npm install, and
    either way `--mode link` must not silently destroy it.
    """
    target_dir = workspace / "node_modules" / "@jaguilar87" / "gaia"
    source_root = source_root.resolve()

    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"action": "error", "path": str(target_dir), "details": f"failed to create {target_dir.parent}: {exc}"}

    if target_dir.is_symlink():
        try:
            current = target_dir.resolve()
        except OSError:
            current = None
        if current == source_root:
            return {"action": "noop", "path": str(target_dir), "details": "already linked to source"}
        try:
            target_dir.unlink()
        except OSError as exc:
            return {"action": "error", "path": str(target_dir), "details": f"failed to replace stale link: {exc}"}
    elif target_dir.exists():
        return {
            "action": "skipped",
            "path": str(target_dir),
            "details": "a real (non-symlink) install exists there -- remove it or use --mode pack",
        }

    try:
        target_dir.symlink_to(source_root, target_is_directory=True)
    except OSError as exc:
        return {"action": "error", "path": str(target_dir), "details": f"failed to symlink: {exc}"}

    return {"action": "created", "path": str(target_dir), "details": f"linked -> {source_root}"}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _run_link_mode(workspace: Path, *, quiet: bool, verbose: bool) -> int:
    link_res = link_source_into_workspace(workspace, _PACKAGE_ROOT)
    _report_step(name="node_modules link", result=link_res, quiet=quiet, verbose=verbose)
    if link_res["action"] == "error":
        return 1

    ns = argparse.Namespace(
        postinstall=False,
        quiet=quiet,
        verbose=verbose,
        db_path=None,
        workspace=str(workspace),
        skip_workspace=False,
        no_path=False,
    )
    rc = install_mod.cmd_install(ns)
    if rc == 0 and not quiet:
        print(
            "\n  gaia dev (link): workspace wired to the live source tree.\n"
            "  Restart Claude Code, then test.\n"
        )
    return rc


def _run_pack_mode(
    workspace: Path,
    *,
    quiet: bool,
    verbose: bool,
    keep_tarball: bool,
    pack_dest: str | None,
) -> int:
    with tempfile.TemporaryDirectory(prefix="gaia-dev-pack-") as tmp:
        dest_dir = Path(pack_dest).expanduser().resolve() if pack_dest else Path(tmp)

        pack_res = _pack_helpers.pack_tarball(_PACKAGE_ROOT, dest_dir=dest_dir)
        _report_step(name="npm pack", result=pack_res, quiet=quiet, verbose=verbose)
        if pack_res["action"] == "error":
            return 1

        tarball = pack_res["tarball"]
        try:
            install_res = install_tarball(workspace, tarball)
            pm = install_res.get("package_manager", "npm")
            _report_step(name=f"{pm} install", result=install_res, quiet=quiet, verbose=verbose)
            if install_res["action"] == "error":
                return 1

            wire_res = wire_workspace_via_installed_gaia(workspace, quiet=quiet)
            _report_step(name="gaia install (wire)", result=wire_res, quiet=quiet, verbose=verbose)
            if wire_res["action"] == "error":
                return 1
        finally:
            if keep_tarball or pack_dest is not None:
                pass  # caller-managed destination or explicit keep -- leave it in place
            elif tarball.exists():
                try:
                    tarball.unlink()
                except OSError:
                    pass

    if not quiet:
        print(
            f"\n  gaia dev: packed {pack_res.get('name')}@{pack_res.get('version')} "
            f"into {workspace}.\n  Restart Claude Code, then test.\n"
        )
    return 0


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'dev' subcommand."""
    p = subparsers.add_parser(
        "dev",
        help="Fast local dev loop: pack/link + install + wire in one command",
        description=(
            "Collapse the manual pack+add+install loop into one command.\n"
            "\n"
            "  --mode pack (default): npm pack this source tree, install the\n"
            "  tarball into the target workspace's node_modules (npm or pnpm),\n"
            "  then wire .claude/ + bootstrap the DB via the freshly installed\n"
            "  copy's own `gaia install`. Reflects a real shippable version.\n"
            "\n"
            "  --mode link: symlink node_modules/@jaguilar87/gaia straight at\n"
            "  this source tree (no pack, no install) for instant iteration.\n"
            "\n"
            "After it returns, restart Claude Code in the target workspace and test.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--workspace",
        dest="workspace",
        type=str,
        default=None,
        help="Target workspace to install/link into (default: cwd)",
    )
    p.add_argument(
        "--mode",
        dest="mode",
        choices=["pack", "link"],
        default="pack",
        help="pack (default): npm pack + install + wire. link: symlink source for instant iteration.",
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
        help="Show noop steps too (default: only changes are printed)",
    )
    p.add_argument(
        "--keep-tarball",
        dest="keep_tarball",
        action="store_true",
        default=False,
        help="Do not delete the packed tarball after install (ignored in --mode link)",
    )
    p.add_argument(
        "--pack-dest",
        dest="pack_dest",
        type=str,
        default=None,
        help="Directory to write the packed tarball into (default: a tmp dir, auto-cleaned)",
    )
    return p


def cmd_dev(args: argparse.Namespace) -> int:
    """Execute the dev subcommand."""
    quiet = bool(getattr(args, "quiet", False))
    verbose = bool(getattr(args, "verbose", False))
    mode = getattr(args, "mode", "pack")
    keep_tarball = bool(getattr(args, "keep_tarball", False))
    pack_dest = getattr(args, "pack_dest", None)
    workspace_arg = getattr(args, "workspace", None)

    workspace = (
        Path(workspace_arg).expanduser().resolve()
        if workspace_arg
        else Path(os.environ.get("INIT_CWD", os.getcwd())).resolve()
    )

    if not workspace.exists():
        print(f"gaia dev: workspace {workspace} does not exist", file=sys.stderr)
        return 1

    if not quiet:
        print(f"\n  gaia dev ({mode} mode)")
        print(f"  source:    {_PACKAGE_ROOT}")
        print(f"  workspace: {workspace}\n")

    if mode == "link":
        return _run_link_mode(workspace, quiet=quiet, verbose=verbose)

    return _run_pack_mode(
        workspace,
        quiet=quiet,
        verbose=verbose,
        keep_tarball=keep_tarball,
        pack_dest=pack_dest,
    )
