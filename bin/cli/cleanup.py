"""
gaia cleanup -- Remove Gaia's workspace footprint and apply data retention.

The full-cleanup footprint mirrors what `gaia install` writes, in reverse:
  - CLAUDE.md and .claude/settings.json (removed outright -- Gaia-owned)
  - .claude/ symlinks incl. skills (removed -- Gaia-owned)
  - .claude/.plugin-initialized marker (removed -- Gaia-owned)
  - .claude/plugin-registry.json (surgical: only Gaia's installed[] entry is
    removed; the file is shared with Claude Code's plugin system)
  - .claude/settings.local.json (surgical: only Gaia-injected keys are removed
    -- agent, two env vars, Gaia's permission entries, and Gaia's hook commands
    -- the mirror of merge_local_hooks; user config preserved)
The user DB at ~/.gaia/gaia.db is never touched here.

Modes:
  --prune / --retain  Apply data retention policy only (no footprint removal)
  (default)           Remove the footprint above + run retention

Flags:
  --dry-run           Print what would be pruned/removed without modifying files
  --json              Machine-readable output
"""

import copy
import json
import os
import sys
from pathlib import Path

# bin/cli/cleanup.py -> bin/cli -> bin -> gaia/
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


# ---------------------------------------------------------------------------
# Project root detection (mirrors JS findProjectRoot)
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk upward from cwd until .claude/ is found."""
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


# ---------------------------------------------------------------------------
# Retention policy -- per-target rules for what gets pruned and when.
# ---------------------------------------------------------------------------

RETENTION_POLICY = [
    {
        "key": "auditLogs",
        "type": "files",
        "pattern": "audit-*.jsonl",
        "dir": ".claude/logs",
        "max_days": 30,
        "label": "Audit logs",
    },
    {
        "key": "hookLogs",
        "type": "files",
        "pattern": "hooks-*.log",
        "dir": ".claude/logs",
        "max_days": 14,
        "label": "Hook logs",
    },
    {
        "key": "responseContract",
        "type": "dirs",
        "dir": ".claude/session/active/response-contract",
        "max_days": 7,
        "label": "Response contract sessions",
    },
    {
        "key": "episodicEpisodes",
        "type": "files",
        "pattern": "*.json",
        "dir": ".claude/project-context/episodic-memory/episodes",
        "max_days": 90,
        "label": "Episodic memory episodes",
    },
    {
        "key": "legacyLogs",
        "type": "legacy",
        "dir": ".claude/logs",
        "patterns": ["pre_tool_use_v2-*.log", "post_tool_use_v2-*.log", "subagent_stop-*.log"],
        "label": "Legacy logs",
    },
    {
        "key": "anomalyFlag",
        "type": "flag-ttl",
        "file": ".claude/project-context/workflow-episodic-memory/signals/needs_analysis.flag",
        "max_hours": 1,
        "label": "Anomaly signal flag",
    },
]


def _matches_pattern(filename: str, pattern: str) -> bool:
    """Glob-style pattern match supporting * wildcard."""
    import fnmatch
    return fnmatch.fnmatch(filename, pattern)


def _prune_old_files(root: Path, dir_rel: str, pattern: str, max_days: int, label: str, dry_run: bool) -> list:
    """Return list of action dicts for files matching pattern older than max_days."""
    actions = []
    full_dir = root / dir_rel
    if not full_dir.exists():
        return actions

    import time
    cutoff = time.time() - max_days * 86400

    for entry in full_dir.iterdir():
        if not _matches_pattern(entry.name, pattern):
            continue
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                actions.append({
                    "action": "delete-file",
                    "path": str(entry.relative_to(root)),
                    "label": label,
                })
                if not dry_run:
                    entry.unlink()
        except OSError:
            pass

    return actions


def _prune_old_dirs(root: Path, dir_rel: str, max_days: int, label: str, dry_run: bool) -> list:
    """Return list of action dicts for directories older than max_days."""
    import shutil
    import time
    actions = []
    full_dir = root / dir_rel
    if not full_dir.exists():
        return actions

    cutoff = time.time() - max_days * 86400

    for entry in full_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                actions.append({
                    "action": "delete-dir",
                    "path": str(entry.relative_to(root)),
                    "label": label,
                })
                if not dry_run:
                    shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass

    return actions


def _truncate_jsonl(root: Path, file_rel: str, max_days: int, label: str, dry_run: bool) -> list:
    """Remove JSONL lines with timestamp older than max_days."""
    import time
    actions = []
    full_path = root / file_rel
    if not full_path.exists():
        return actions

    cutoff = time.time() - max_days * 86400
    removed = 0

    try:
        lines = full_path.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("timestamp")
                if ts_str:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    if ts < cutoff:
                        removed += 1
                        continue
            except (json.JSONDecodeError, ValueError):
                pass
            kept.append(line)

        if removed > 0:
            actions.append({
                "action": "truncate-jsonl",
                "path": file_rel,
                "removed": removed,
                "label": label,
            })
            if not dry_run:
                content = "\n".join(kept) + ("\n" if kept else "")
                full_path.write_text(content, encoding="utf-8")
    except OSError:
        pass

    return actions


def _prune_legacy_logs(root: Path, dir_rel: str, patterns: list, label: str, dry_run: bool) -> list:
    """Remove legacy log files matching any of the patterns (no age check)."""
    actions = []
    full_dir = root / dir_rel
    if not full_dir.exists():
        return actions

    for entry in full_dir.iterdir():
        if not entry.is_file():
            continue
        if any(_matches_pattern(entry.name, p) for p in patterns):
            actions.append({
                "action": "delete-legacy",
                "path": str(entry.relative_to(root)),
                "label": label,
            })
            if not dry_run:
                try:
                    entry.unlink()
                except OSError:
                    pass

    return actions


def _prune_flag_by_ttl(root: Path, file_rel: str, max_hours: int, label: str, dry_run: bool) -> list:
    """Remove a flag file if older than max_hours (by mtime or created_at field)."""
    import time
    actions = []
    full_path = root / file_rel
    if not full_path.exists():
        return actions

    cutoff = time.time() - max_hours * 3600
    expired = False

    try:
        if full_path.stat().st_mtime < cutoff:
            expired = True
        else:
            try:
                data = json.loads(full_path.read_text(encoding="utf-8"))
                created_str = data.get("created_at") or data.get("timestamp")
                if created_str:
                    from datetime import datetime
                    created_ts = datetime.fromisoformat(created_str.replace("Z", "+00:00")).timestamp()
                    if created_ts < cutoff:
                        expired = True
            except (json.JSONDecodeError, ValueError, OSError):
                pass
    except OSError:
        return actions

    if expired:
        actions.append({
            "action": "expire-flag",
            "path": file_rel,
            "label": label,
        })
        if not dry_run:
            try:
                full_path.unlink()
            except OSError:
                pass

    return actions


def _apply_retention_policy(root: Path, dry_run: bool) -> list:
    """Apply all retention policy rules and return list of action dicts."""
    all_actions = []

    for policy in RETENTION_POLICY:
        ptype = policy["type"]
        if ptype == "files":
            all_actions.extend(
                _prune_old_files(root, policy["dir"], policy["pattern"], policy["max_days"], policy["label"], dry_run)
            )
        elif ptype == "dirs":
            all_actions.extend(
                _prune_old_dirs(root, policy["dir"], policy["max_days"], policy["label"], dry_run)
            )
        elif ptype == "truncate-jsonl":
            all_actions.extend(
                _truncate_jsonl(root, policy["file"], policy["max_days"], policy["label"], dry_run)
            )
        elif ptype == "legacy":
            all_actions.extend(
                _prune_legacy_logs(root, policy["dir"], policy["patterns"], policy["label"], dry_run)
            )
        elif ptype == "flag-ttl":
            all_actions.extend(
                _prune_flag_by_ttl(root, policy["file"], policy["max_hours"], policy["label"], dry_run)
            )

    return all_actions


# ---------------------------------------------------------------------------
# Symlink / file removal helpers
# ---------------------------------------------------------------------------

SYMLINKS_TO_REMOVE = [
    ".claude/agents",
    ".claude/tools",
    ".claude/hooks",
    ".claude/commands",
    ".claude/config",
    ".claude/skills",
    ".claude/CHANGELOG.md",
    ".claude/README.en.md",
    ".claude/README.md",
]


def _remove_claude_md(root: Path, dry_run: bool) -> dict:
    path = root / "CLAUDE.md"
    if not path.exists():
        return {"found": False}
    if not dry_run:
        path.unlink()
    return {"found": True, "removed": not dry_run, "dry_run": dry_run}


def _remove_settings_json(root: Path, dry_run: bool) -> dict:
    path = root / ".claude" / "settings.json"
    if not path.exists():
        return {"found": False}
    if not dry_run:
        path.unlink()
    return {"found": True, "removed": not dry_run, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# Gaia-owned data-dir markers (.plugin-initialized, plugin-registry.json)
#
# Both live in get_plugin_data_dir(), which falls back to .claude/ when
# CLAUDE_PLUGIN_DATA is unset (the common npm-install case). The marker is a
# pure Gaia artifact -- removed outright. The registry is shared with Claude
# Code's plugin system, so only Gaia's own entry is removed surgically.
# ---------------------------------------------------------------------------

# Plugin names Gaia registers in plugin-registry.json. "gaia" is the sole
# canonical identity written by _read_plugin_name in _install_helpers.py.
_GAIA_PLUGIN_NAMES = {"gaia"}


def _remove_plugin_initialized(root: Path, dry_run: bool) -> dict:
    """Remove the .plugin-initialized first-run marker.

    Written by plugin_setup.mark_initialized() into get_plugin_data_dir().
    A pure Gaia artifact (timestamp + mode), safe to delete outright.
    """
    path = root / ".claude" / ".plugin-initialized"
    if not path.exists():
        return {"found": False}
    if not dry_run:
        try:
            path.unlink()
        except OSError as exc:
            return {"found": True, "removed": False, "error": str(exc)}
    return {"found": True, "removed": not dry_run, "dry_run": dry_run}


def _remove_plugin_registry_entry(root: Path, dry_run: bool) -> dict:
    """Remove Gaia's entry from plugin-registry.json, preserving other plugins.

    plugin-registry.json is shared with Claude Code's plugin system. Install
    (register_plugin / ensure_plugin_registry) writes Gaia's entry into the
    ``installed`` list. Uninstall is symmetric: drop only Gaia-owned entries
    from ``installed``. If that empties the registry of all plugins, the file
    is equivalent to its pre-Gaia state and is removed; otherwise it is
    rewritten with the surviving entries.

    Idempotent: a registry with no Gaia entry returns found=False.
    """
    path = root / ".claude" / "plugin-registry.json"
    if not path.exists():
        return {"found": False}

    data = _read_json_file(path)
    if not isinstance(data, dict):
        # Malformed/unexpected shape -- leave it untouched rather than risk
        # destroying a file Gaia did not write.
        return {"found": False, "skipped": "registry not a JSON object"}

    installed = data.get("installed")
    if not isinstance(installed, list):
        return {"found": False, "skipped": "no installed[] array"}

    kept = [
        e for e in installed
        if not (isinstance(e, dict) and e.get("name") in _GAIA_PLUGIN_NAMES)
    ]
    removed_entries = [
        e for e in installed
        if isinstance(e, dict) and e.get("name") in _GAIA_PLUGIN_NAMES
    ]

    if not removed_entries:
        return {"found": False}

    # Only Gaia keys present (installed + source) and nothing survives ->
    # the file existed solely for Gaia; remove it entirely.
    only_gaia_keys = set(data.keys()) <= {"installed", "source"}
    delete_file = not kept and only_gaia_keys

    result = {
        "found": True,
        "removed_entries": [e.get("name") for e in removed_entries],
        "file_removed": delete_file and not dry_run,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    try:
        if delete_file:
            path.unlink()
        else:
            data["installed"] = kept
            _write_json_file(path, data)
    except OSError as exc:
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# settings.local.json -- surgical removal of Gaia-injected config
#
# Install (merge_local_permissions) MERGES into a user-owned file: it sets
# agent=gaia-orchestrator, adds two env vars, and authoritative-merges its
# permission entries. Uninstall must mirror that injection key-for-key and
# leave everything the user added intact -- never delete the whole file.
#
# Note on permissions: install's authoritative merge already overwrote any
# user-scoped variants of tool names Gaia manages (e.g. a prior Edit(/tmp/*)
# became Edit). That loss is not recoverable at uninstall time; the symmetric
# action is to drop the tool names Gaia manages and its deny rules, leaving
# only entries for tools Gaia never touched.
# ---------------------------------------------------------------------------

_GAIA_ENV_KEYS = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}


def _gaia_managed_permission_sets():
    """Return (managed_tool_names, deny_entries) Gaia injects into settings.

    Pulled from the canonical PERMISSIONS constant in plugin_setup.py (the
    same source install merges from). Falls back to a minimal set if the
    hooks package cannot be imported (partial install).
    """
    try:
        from hooks.modules.core.plugin_setup import (  # type: ignore
            PERMISSIONS,
            _tool_name,
        )
        allow = set(PERMISSIONS["permissions"]["allow"])
        deny = set(PERMISSIONS["permissions"]["deny"])
        managed_names = {_tool_name(e) for e in allow}
        return managed_names, deny
    except Exception:  # noqa: BLE001
        return {"Bash"}, set()


def _is_gaia_hook_command(command: object) -> bool:
    """True when a hook command string was injected by Gaia.

    Both hook writers -- ``merge_local_hooks`` (cli/_install_helpers.py) and
    ``setup_project_hooks`` (hooks/modules/core/plugin_setup.py) -- bake the
    ``${CLAUDE_PLUGIN_ROOT}/hooks/`` prefix into the workspace's
    ``.claude/hooks/`` directory (the stable symlink into the Gaia package).
    A command Gaia owns therefore resolves through ``.claude/hooks/``. We also
    match the un-converted ``${CLAUDE_PLUGIN_ROOT}/hooks/`` literal in case a
    block was written before symlink resolution. Backslashes are normalized so
    the check holds for Windows-authored paths.
    """
    if not isinstance(command, str):
        return False
    norm = command.replace("\\", "/")
    return ".claude/hooks/" in norm or "${CLAUDE_PLUGIN_ROOT}/hooks/" in norm


def _strip_gaia_hooks(hooks: dict) -> bool:
    """Remove Gaia-owned hook commands from a settings ``hooks`` block, in place.

    Mirrors the writers in reverse: drops individual hook commands that resolve
    into ``.claude/hooks/``, prunes a hook entry whose command list becomes
    empty, and drops an event whose entry list becomes empty. User-authored
    hook entries (and entries with no ``hooks`` list) are preserved untouched.
    Returns True if anything was removed.
    """
    changed = False
    for event in list(hooks.keys()):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept_entries: list = []
        for entry in entries:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            if isinstance(inner, list):
                kept_inner = [
                    h for h in inner
                    if not _is_gaia_hook_command(
                        h.get("command") if isinstance(h, dict) else None
                    )
                ]
                if len(kept_inner) != len(inner):
                    changed = True
                if not kept_inner:
                    # Every command in this entry was Gaia's -> drop the entry.
                    continue
                entry = {**entry, "hooks": kept_inner}
            kept_entries.append(entry)
        if len(kept_entries) != len(entries):
            changed = True
        if kept_entries:
            hooks[event] = kept_entries
        else:
            del hooks[event]
    return changed


def _clean_settings_local_json(root: Path, dry_run: bool) -> dict:
    """Remove only Gaia-injected keys from settings.local.json; preserve user config.

    Mirrors merge_local_permissions and the hook writers:
      - agent: removed only if it equals "gaia-orchestrator".
      - env: removes the two Gaia keys only when their value matches what
        install set (a user override of the value is preserved).
      - permissions.allow: drops entries whose tool name Gaia manages.
      - permissions.deny: drops Gaia's deny rules.
      - hooks: drops hook commands Gaia injected (those resolving into
        ``.claude/hooks/``); the ``hooks`` key is removed when Gaia was its
        sole owner, so no orphan block referencing deleted symlinks survives.
      - Empty containers (env / permissions / allow / deny / hooks) are pruned
        so the file does not retain hollow Gaia scaffolding.

    If nothing Gaia-owned remains and the file becomes ``{}``, it is removed.
    Idempotent: a file with no Gaia keys returns found=False.
    """
    path = root / ".claude" / "settings.local.json"
    if not path.exists():
        return {"found": False}

    data = _read_json_file(path)
    if not isinstance(data, dict):
        return {"found": False, "skipped": "settings.local.json not a JSON object"}

    removed_fields: list[str] = []

    # agent identity
    if data.get("agent") == "gaia-orchestrator":
        removed_fields.append("agent")
        if not dry_run:
            del data["agent"]

    # env vars (only when value still matches what install set)
    env = data.get("env")
    if isinstance(env, dict):
        for key, expected in _GAIA_ENV_KEYS.items():
            if env.get(key) == expected:
                removed_fields.append(f"env.{key}")
                if not dry_run:
                    del env[key]
        if not dry_run and not env:
            del data["env"]

    # permissions
    perms = data.get("permissions")
    if isinstance(perms, dict):
        managed_names, gaia_deny = _gaia_managed_permission_sets()

        allow = perms.get("allow")
        if isinstance(allow, list):
            kept_allow = [e for e in allow if _perm_tool_name(e) not in managed_names]
            if len(kept_allow) != len(allow):
                removed_fields.append("permissions.allow")
                if not dry_run:
                    if kept_allow:
                        perms["allow"] = kept_allow
                    else:
                        del perms["allow"]

        deny = perms.get("deny")
        if isinstance(deny, list):
            kept_deny = [e for e in deny if e not in gaia_deny]
            if len(kept_deny) != len(deny):
                removed_fields.append("permissions.deny")
                if not dry_run:
                    if kept_deny:
                        perms["deny"] = kept_deny
                    else:
                        del perms["deny"]

        # Drop hollow empty lists (ask/allow/deny) and an empty permissions
        # block -- these are Gaia scaffolding, not user data.
        if not dry_run:
            for empty_key in ("ask", "allow", "deny"):
                if perms.get(empty_key) == []:
                    del perms[empty_key]
            if not perms:
                del data["permissions"]

    # hooks (Gaia-injected event commands resolving into .claude/hooks/).
    # This is the mirror of merge_local_hooks / setup_project_hooks: removing
    # them here prevents an orphan `hooks` block (pointing at deleted symlinks)
    # from surviving uninstall and double-registering if the Claude Code plugin
    # is later mounted.
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        # Detect on a copy so dry-run never mutates; apply in place otherwise.
        probe = copy.deepcopy(hooks) if dry_run else hooks
        if _strip_gaia_hooks(probe):
            removed_fields.append("hooks")
        if not dry_run and not hooks:
            del data["hooks"]

    if not removed_fields:
        return {"found": False}

    result = {
        "found": True,
        "removed_fields": removed_fields,
        "file_removed": False,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    try:
        if not data:
            path.unlink()
            result["file_removed"] = True
        else:
            _write_json_file(path, data)
    except OSError as exc:
        result["error"] = str(exc)
    return result


def _perm_tool_name(entry: str) -> str:
    """Base tool name from a permission entry (mirror of plugin_setup._tool_name)."""
    if not isinstance(entry, str):
        return ""
    paren = entry.find("(")
    return entry[:paren] if paren != -1 else entry


def _read_json_file(path: Path):
    """Read JSON, returning None on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_file(path: Path, data) -> None:
    """Write JSON with indent=2 + trailing newline (gaia convention)."""
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _remove_symlinks(root: Path, dry_run: bool) -> dict:
    removed = []
    skipped = []

    targets = list(SYMLINKS_TO_REMOVE)
    targets.append("AGENTS.md")  # project root symlink

    # Also scan for broken symlinks in .claude/
    claude_dir = root / ".claude"
    if claude_dir.exists():
        for entry in claude_dir.iterdir():
            if entry.is_symlink():
                try:
                    entry.resolve(strict=True)
                except OSError:
                    rel = str(entry.relative_to(root))
                    if rel not in targets:
                        targets.append(rel)

    for rel_path in targets:
        full_path = root / rel_path
        try:
            stat = full_path.lstat()
        except OSError:
            skipped.append(rel_path)
            continue

        if stat.st_mode & 0o170000 in (0o120000, 0o100000):  # symlink or file
            removed.append(rel_path)
            if not dry_run:
                try:
                    full_path.unlink()
                except OSError:
                    pass

    return {"removed": removed, "skipped": skipped, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers):
    """Register the 'cleanup' subcommand."""
    p = subparsers.add_parser(
        "cleanup",
        help="Remove CLAUDE.md, settings.json, symlinks and apply data retention policy",
        description=(
            "Cleanup gaia installation files and apply data retention policy.\n"
            "\n"
            "Default mode: removes CLAUDE.md, settings.json, symlinks, then runs retention.\n"
            "--prune / --retain: run data retention only (no file/symlink removal).\n"
            "--dry-run: print what would change without modifying anything.\n"
        ),
    )
    p.add_argument(
        "--prune",
        action="store_true",
        default=False,
        help="Apply data retention policy only (no symlink/settings removal)",
    )
    p.add_argument(
        "--retain",
        action="store_true",
        default=False,
        help="Alias for --prune",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Print what would be pruned/removed without modifying files",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    return p


def cmd_cleanup(args) -> int:
    """Execute the cleanup subcommand."""
    root = _find_project_root()
    prune_only = getattr(args, "prune", False) or getattr(args, "retain", False)
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)

    result = {
        "root": str(root),
        "dry_run": dry_run,
        "prune_only": prune_only,
    }

    # Retention policy display header (mirrors JS)
    retention_policy_info = {
        "audit_logs_days": 30,
        "hook_logs_days": 14,
        "response_contracts_days": 7,
        "episodic_episodes_days": 90,
        "legacy_logs": "all removed",
        "anomaly_flag_hours": 1,
    }

    if prune_only:
        if not as_json:
            print("\ngaia data retention")
            print("\nRetention policy:")
            print("  Audit logs:          30 days")
            print("  Hook logs:           14 days")
            print("  Response contracts:   7 days")
            print("  Episodic episodes:   90 days")
            print("  Legacy logs:         all removed")
            print("  Anomaly flag:         1 hour TTL")
            if dry_run:
                print("  (dry-run mode -- no files will be modified)\n")
            else:
                print()

        retention_actions = _apply_retention_policy(root, dry_run)
        result["retention_actions"] = retention_actions
        result["retention_policy"] = retention_policy_info

        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if retention_actions:
                for action in retention_actions:
                    verb = "Would prune" if dry_run else "Pruned"
                    print(f"  {verb}: {action['path']} ({action['label']})")
                status = "Data retention preview complete" if dry_run else "Data retention completed"
            else:
                status = "All data within retention limits"
            print(f"\n{status}\n")

        return 0

    # Full cleanup mode
    if not as_json:
        print("\ngaia cleanup")
        if dry_run:
            print("  (dry-run mode -- no files will be modified)\n")
        else:
            print()

    claude_md = _remove_claude_md(root, dry_run)
    settings = _remove_settings_json(root, dry_run)
    settings_local = _clean_settings_local_json(root, dry_run)
    plugin_initialized = _remove_plugin_initialized(root, dry_run)
    plugin_registry = _remove_plugin_registry_entry(root, dry_run)
    symlinks = _remove_symlinks(root, dry_run)
    retention_actions = _apply_retention_policy(root, dry_run)

    result["claude_md"] = claude_md
    result["settings_json"] = settings
    result["settings_local_json"] = settings_local
    result["plugin_initialized"] = plugin_initialized
    result["plugin_registry"] = plugin_registry
    result["symlinks"] = symlinks
    result["retention_actions"] = retention_actions
    result["retention_policy"] = retention_policy_info

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        # Report what happened
        anything_done = (
            claude_md.get("found")
            or settings.get("found")
            or settings_local.get("found")
            or plugin_initialized.get("found")
            or plugin_registry.get("found")
            or symlinks.get("removed")
            or retention_actions
        )

        if claude_md.get("found"):
            verb = "Would remove" if dry_run else "Removed"
            print(f"  {verb}: CLAUDE.md")
        if settings.get("found"):
            verb = "Would remove" if dry_run else "Removed"
            print(f"  {verb}: .claude/settings.json")
        if settings_local.get("found"):
            verb = "Would clean" if dry_run else "Cleaned"
            fields = ", ".join(settings_local.get("removed_fields", []))
            print(f"  {verb}: .claude/settings.local.json ({fields})")
        if plugin_initialized.get("found"):
            verb = "Would remove" if dry_run else "Removed"
            print(f"  {verb}: .claude/.plugin-initialized")
        if plugin_registry.get("found"):
            verb = "Would remove" if dry_run else "Removed"
            entries = ", ".join(plugin_registry.get("removed_entries", []))
            print(f"  {verb}: plugin-registry.json entry ({entries})")
        for rel in symlinks.get("removed", []):
            verb = "Would remove symlink" if dry_run else "Removed symlink"
            print(f"  {verb}: {rel}")
        for action in retention_actions:
            verb = "Would prune" if dry_run else "Pruned"
            print(f"  {verb}: {action['path']} ({action['label']})")

        if anything_done:
            status = "Cleanup preview complete" if dry_run else "Cleanup completed"
            print(f"\n{status}")
            print("\nDirectories Gaia does not remove (files within them are pruned by retention policy, not deleted outright):")
            print("  .claude/logs/")
            print("  .claude/tests/")
            print("  .claude/project-context/")
            print("  .claude/session/")
        else:
            print("  Nothing to clean up")
        print()

    return 0
