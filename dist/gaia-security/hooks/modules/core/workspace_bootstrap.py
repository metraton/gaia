"""workspace_bootstrap — fix CC v2.1.119 install bug that writes broken hook paths.

CC v2.1.119 resolves ${CLAUDE_PLUGIN_ROOT} to the workspace instead of the
plugin cache dir, so <workspace>/.claude/settings.local.json ends up with
paths like <workspace>/.claude/hooks/pre_tool_use.py that don't exist.

Workaround: on first hook fire, create <workspace>/.claude/hooks as a symlink
(POSIX) or junction (Windows) pointing to the real hooks dir inside the plugin
cache. This mirrors the pattern in bin/gaia-update.js updateSymlinks().
"""

import logging
import os
import platform
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_workspace_hooks_link() -> None:
    """Create or repair <workspace>/.claude/hooks → plugin cache hooks dir.

    Never raises. All failures are logged as warnings so that a broken
    workspace layout never prevents the hook from running its real logic.
    """
    try:
        # hooks/modules/core/workspace_bootstrap.py → up 3 levels = hooks/
        cache_hooks_dir = Path(__file__).resolve().parent.parent.parent

        workspace_hooks_dir = Path.cwd() / ".claude" / "hooks"

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
                if current_target == cache_hooks_dir and cache_hooks_dir.exists():
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
