"""workspace_bootstrap — fix CC v2.1.119 install bug that writes broken hook paths.

CC v2.1.119 resolves ${CLAUDE_PLUGIN_ROOT} to the workspace instead of the
plugin cache dir, so <workspace>/.claude/settings.local.json ends up with
paths like <workspace>/.claude/hooks/pre_tool_use.py that don't exist.

Workaround: on first hook fire, create <workspace>/.claude/hooks as a symlink
(POSIX) or junction (Windows) pointing to the real hooks dir inside the plugin
cache. This mirrors the pattern in bin/gaia-update.js updateSymlinks().
"""

import json
import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _read_pkg_version(pkg_root: Path) -> Optional[str]:
    """Best-effort read of ``package.json`` ``version`` at *pkg_root*.

    Returns the version string, or None on any error (missing file,
    unreadable, no version field). Never raises.
    """
    try:
        data = json.loads((pkg_root / "package.json").read_text(encoding="utf-8"))
        v = data.get("version")
        return v if isinstance(v, str) and v else None
    except Exception:
        return None


def _version_tuple(v: Optional[str]) -> tuple:
    """Coarse comparable tuple from a semver-ish string.

    Compares on MAJOR.MINOR.PATCH only (prerelease/build metadata dropped),
    which is enough to decide "is the installed package at least as new as
    the executing copy". Unknown/unreadable versions sort lowest so a missing
    version never wins the freshness comparison.
    """
    if not v:
        return (-1,)
    base = v.split("+", 1)[0].split("-", 1)[0]
    out = []
    for part in base.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out) if out else (-1,)


def _pick_fresher_hooks_dir(exec_hooks_dir: Path, nm_gaia: Path, nm_hooks: Path) -> Path:
    """Return whichever hooks dir belongs to the fresher gaia package.

    Prefers the top-level installed package (``nm_hooks``) when it exists and
    its version is >= the executing copy's version; otherwise keeps the
    executing copy (``exec_hooks_dir``). The executing copy's package root is
    ``exec_hooks_dir.parent`` (``.../gaia/hooks`` -> ``.../gaia``). Never
    raises; on any doubt it falls back to the executing copy, preserving the
    prior behaviour.
    """
    try:
        if not nm_hooks.exists():
            return exec_hooks_dir
        nm_ver = _version_tuple(_read_pkg_version(nm_gaia))
        exec_ver = _version_tuple(_read_pkg_version(exec_hooks_dir.parent))
        if nm_ver >= exec_ver:
            return nm_hooks
    except Exception:
        pass
    return exec_hooks_dir


def ensure_workspace_hooks_link() -> None:
    """Create or repair <workspace>/.claude/hooks → the FRESHEST gaia hooks dir.

    Never raises. All failures are logged as warnings so that a broken
    workspace layout never prevents the hook from running its real logic.

    Freshness fix: the desired target is the top-level installed package's
    hooks dir (``<workspace>/node_modules/@jaguilar87/gaia/hooks``) whenever
    it is present and at least as new as the EXECUTING copy. The executing
    copy (``__file__``'s hooks dir) can be an OLDER extraction -- e.g. a stale
    pnpm virtual-store entry -- so anchoring the link to "wherever this file
    runs from" pins the workspace to old code. Comparing package versions and
    re-pointing at the fresher install is what lets a new install actually
    reach the runtime.
    """
    try:
        # hooks/modules/core/workspace_bootstrap.py → up 3 levels = hooks/
        # of the EXECUTING copy (may be a stale installed extraction).
        cache_hooks_dir = Path(__file__).resolve().parent.parent.parent

        workspace = Path.cwd()
        workspace_hooks_dir = workspace / ".claude" / "hooks"

        # Prefer the top-level installed package's hooks dir when it is at
        # least as new as the executing copy -- this is the freshness anchor.
        nm_gaia = workspace / "node_modules" / "@jaguilar87" / "gaia"
        nm_hooks = nm_gaia / "hooks"
        cache_hooks_dir = _pick_fresher_hooks_dir(cache_hooks_dir, nm_gaia, nm_hooks)

        # Case 1: real directory with files — npm install placed real files,
        # nothing to do. Check via lstat to avoid following symlinks.
        try:
            st = workspace_hooks_dir.lstat()
            import stat as _stat
            is_symlink = _stat.S_ISLNK(st.st_mode)
        except FileNotFoundError:
            is_symlink = False
            st = None

        if st is not None and not is_symlink:
            # A real directory exists — check if it has files.  If it does,
            # this is the npm-install path and we must not touch it.
            if any(workspace_hooks_dir.iterdir()):
                return
            # Empty real directory — fall through and replace.

        if is_symlink:
            # Resolve what the symlink points to.
            try:
                current_target = Path(os.readlink(workspace_hooks_dir))
                if not current_target.is_absolute():
                    current_target = (workspace_hooks_dir.parent / current_target).resolve()
                # Compare CANONICAL paths: the desired target may itself be a
                # package-manager symlink (e.g. node_modules/@jaguilar87/gaia
                # under pnpm) that resolves to the same real dir the current
                # link already points at -- resolving both sides avoids a
                # needless recreate while still catching a genuinely different
                # (older) target.
                if (
                    cache_hooks_dir.exists()
                    and current_target.resolve(strict=False) == cache_hooks_dir.resolve(strict=False)
                ):
                    # Already correct — no-op.
                    return
            except OSError as exc:
                logger.warning("workspace_bootstrap: readlink failed (%s) — will recreate", exc)
            # Stale or wrong target — remove and recreate.
            try:
                workspace_hooks_dir.unlink()
            except OSError as exc:
                logger.warning("workspace_bootstrap: unlink failed (%s) — skipping", exc)
                return

        # Ensure parent .claude/ exists.
        workspace_hooks_dir.parent.mkdir(parents=True, exist_ok=True)

        # Create the symlink / junction.
        _create_link(cache_hooks_dir, workspace_hooks_dir)

    except Exception as exc:  # pragma: no cover — safety net
        logger.warning("workspace_bootstrap: unexpected error (%s) — skipping", exc)


def _create_link(target: Path, link: Path) -> None:
    """Create a directory symlink (POSIX) or junction (Windows)."""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
            )
        else:
            os.symlink(str(target), str(link), target_is_directory=True)
        logger.info("workspace_bootstrap: created hooks link %s → %s", link, target)
    except Exception as exc:
        logger.warning("workspace_bootstrap: link creation failed (%s) — skipping", exc)
