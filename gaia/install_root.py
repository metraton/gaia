"""The folder Gaia was installed in: resolving it from beneath and scanning it once.

Every channel (plugin, npm, pnpm) treats the installed folder as the workspace.
Writes that belong to the workspace anchor to that folder rather than to the
current directory, so a session opened in a subfolder reuses the installed
`.claude` instead of seeding its own.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from gaia.paths import db_path

INIT_MARKER = Path(".claude") / ".plugin-initialized"


def registered_roots(database: Path | None = None) -> dict[Path, str]:
    """Map every recorded workspace root to its workspace name; empty when unreadable."""
    database = database or db_path()
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT name, root_path FROM workspaces "
                "WHERE root_path IS NOT NULL AND root_path != ''"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {Path(root).resolve(): name for name, root in rows}


def owning_root(path: Path, roots: dict[Path, str]) -> Path | None:
    """The nearest recorded root at or above *path*, or None."""
    for candidate in (path, *path.parents):
        if candidate in roots:
            return candidate
    return None


def installed_root(start: Path | None = None, *, database: Path | None = None) -> Path:
    """The installed workspace root containing *start*; *start* itself when none does.

    A root recorded in the registry wins over the on-disk init marker, because a
    `.claude` seeded by an earlier cwd-anchored write carries the same marker.
    The home directory is never a candidate: `~/.claude` is Claude Code's user
    directory, not an install.
    """
    here = (start or Path.cwd()).resolve()
    recorded = owning_root(here, registered_roots(database))
    if recorded is not None:
        return recorded
    home = Path.home().resolve()
    for candidate in (here, *here.parents):
        if candidate == home:
            break
        if (candidate / INIT_MARKER).exists():
            return candidate
    return here


def first_scan(root: Path, *, database: Path | None = None) -> dict:
    """Register *root* as the workspace named after it and index the repos beneath it.

    Returns ``{"action": "noop" | "created" | "error", "details": str}``. A root
    already recorded is a noop (re-scanning is `gaia scan`'s job), and a name
    already recorded for another root is an error rather than a rebind, so no
    existing workspace changes its root or its projects.
    """
    from gaia.store.writer import set_workspace_last_scan_at
    from tools.scan.classify import scan
    from tools.scan.promote import promote_workspace

    root = root.resolve()
    name = root.name
    roots = registered_roots(database)
    if root in roots:
        return {"action": "noop", "details": f"{root} is already workspace {roots[root]!r}"}
    taken = {workspace: path for path, workspace in roots.items()}
    if name in taken:
        return {
            "action": "error",
            "details": (
                f"workspace name {name!r} already records root {taken[name]}; "
                f"run `gaia scan --workspace <another-name> {root}`"
            ),
        }

    report = scan(root, name, db_path=database)
    set_workspace_last_scan_at(name, db_path=database, root_path=str(root))
    if report.resolved_workspace:
        promote_workspace(name, db_path=database)
    return {
        "action": "created",
        "details": f"workspace {name!r} at {root}: {len(report.projects)} project(s) indexed",
    }


def start_first_scan(root: Path) -> None:
    """Run :func:`first_scan` for *root* in a detached process, so a session never waits on it."""
    subprocess.Popen(
        [sys.executable, "-m", "gaia.install_root", str(root)],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    print(first_scan(Path(sys.argv[1]))["details"])
