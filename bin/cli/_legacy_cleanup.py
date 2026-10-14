"""Remove what Gaia 5.3 and 5.4 left in a workspace, touching only artifacts Gaia recognizes.

With CLAUDE_PLUGIN_DATA unset (every npm install), those releases kept their
runtime data under ``<workspace>/.claude/`` and their approval grants as JSON
under ``<workspace>/.claude/cache/approvals/``. Data now lives in the Gaia data
dir and grants in gaia.db, so install merges the first and drops the second.
"""

from __future__ import annotations

import filecmp
import json
import shutil
from pathlib import Path

# Workspace-relative 5.x data dir -> its path under the current data dir.
LEGACY_DATA_DIRS = {
    Path(".claude") / "logs": Path("logs"),
    Path(".claude") / "events": Path("events"),
    Path(".claude") / "memory": Path("memory"),
    Path(".claude") / "session": Path("session"),
}
LEGACY_GRANT_CACHE = Path(".claude") / "cache" / "approvals"


def _prune_empty(directory: Path, stop: Path) -> None:
    """Remove *directory* and its parents up to *stop* while they are empty; os.rmdir refuses a non-empty one."""
    for current in [*sorted(directory.rglob("*"), reverse=True), directory]:
        if current.is_dir() and not current.is_symlink():
            try:
                current.rmdir()
            except OSError:
                pass
    parent = directory.parent
    while parent != stop and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def merge_legacy_data_dirs(workspace: Path, data_dir: Path) -> dict:
    """Move each 5.x data file into the data dir, then remove the legacy dirs left empty.

    A file whose destination already holds different bytes stays where it is,
    so its legacy dir survives non-empty and nothing is lost.
    """
    moved = kept = 0
    for legacy, current in LEGACY_DATA_DIRS.items():
        source_root = workspace / legacy
        if not source_root.is_dir() or source_root.is_symlink():
            continue
        for source in sorted(p for p in source_root.rglob("*") if p.is_file() and not p.is_symlink()):
            target = data_dir / current / source.relative_to(source_root)
            if target.exists():
                if filecmp.cmp(source, target, shallow=False):
                    source.unlink()
                else:
                    kept += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved += 1
        _prune_empty(source_root, workspace / ".claude")
    if not moved and not kept:
        return {"action": "noop", "details": "no 5.x data dirs in the workspace"}
    details = f"merged {moved} file(s) into {data_dir}"
    if kept:
        details += f"; {kept} conflicting file(s) left in place"
    return {"action": "migrated", "details": details}


# Name prefix -> keys every record of that kind carried, as the last filesystem
# grant writer wrote them (hooks/modules/security/approval_grants.py before
# 24afac13 moved grants into gaia.db). "pending-index-" precedes "pending-",
# whose prefix it shares.
LEGACY_GRANT_SHAPES = (
    ("grant-", frozenset({"session_id", "approved_scope", "scope_type", "scope_signature",
                          "granted_at", "ttl_minutes", "used"})),
    ("pending-index-", frozenset({"session_id", "latest_nonce", "entries"})),
    ("pending-", frozenset({"nonce", "session_id", "command", "scope_signature", "timestamp"})),
)


def is_legacy_grant_file(path: Path) -> bool:
    """True when *path* is named and shaped like a record the 5.x filesystem grant plane wrote."""
    if path.suffix != ".json" or not path.is_file() or path.is_symlink():
        return False
    keys = next((keys for prefix, keys in LEGACY_GRANT_SHAPES if path.name.startswith(prefix)), None)
    if keys is None:
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and keys <= record.keys()


def remove_stale_grant_cache(workspace: Path) -> dict:
    """Delete the 5.x JSON approval grants, superseded by gaia.db and long expired; any other file stays."""
    cache = workspace / LEGACY_GRANT_CACHE
    if not cache.is_dir() or cache.is_symlink():
        return {"action": "noop", "details": "no 5.x grant cache"}
    removed = 0
    for grant in cache.glob("*.json"):
        if is_legacy_grant_file(grant):
            grant.unlink()
            removed += 1
    _prune_empty(cache, workspace / ".claude")
    return {"action": "removed", "details": f"removed {removed} 5.x grant file(s) from {cache}"}
