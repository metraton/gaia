"""
_install_helpers.py -- shared helpers for `gaia install` and `gaia update`.

This module centralises the workspace-level configuration logic that both
`install.py` (first-time bootstrap) and `update.py` (post-upgrade sync) need
to invoke. Every helper is idempotent: re-running over a populated workspace
must not corrupt or duplicate data.

Public helpers exposed to install/update:

  - configure_settings_json   Create or repair `.claude/settings.json`.
  - merge_local_permissions   Union gaia permissions into `settings.local.json`.
  - merge_local_hooks         Merge hook event entries into `settings.local.json`.
  - manage_symlinks           Create or repair `.claude/{agents,hooks,...}` symlinks.
  - register_plugin           Write `.claude/plugin-registry.json` with the version.

Each helper returns a result dict with the shape:

    {"action": "created" | "updated" | "noop" | "skipped" | "error",
     "path":   "<absolute path of the artifact touched>",
     "details": "<human-readable one-liner>"}

Callers report these dicts to the user. ``dry_run=True`` is honoured by
every helper -- no filesystem mutation occurs and the returned ``action``
reflects what *would* have happened.

Naming convention: this module is private (leading underscore) because the
helper API is stable for the install/update CLI commands but not part of
the public Gaia plugin contract.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Reuse the canonical permission/hook merge logic from plugin_setup.py.
# That module is the SINGLE SOURCE OF TRUTH for OPS_PERMISSIONS, deny rules,
# the authoritative-merge algorithm, and the hooks.json conversion. We import
# the constants but reimplement the orchestration here so we can return the
# {action, path, details} contract instead of plain booleans.
# ---------------------------------------------------------------------------

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent  # bin/cli -> bin -> pkg/

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

try:
    from hooks.modules.core.plugin_setup import (  # type: ignore  # noqa: E402
        OPS_PERMISSIONS,
        SECURITY_PERMISSIONS,
        _authoritative_merge,
        _tool_name,
    )
    from hooks.modules.core.plugin_mode import get_plugin_mode  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    # Fallback constants if the hooks package cannot be imported (e.g. partial
    # install). These mirror the canonical values in plugin_setup.py at the
    # time of writing -- if those drift, this fallback becomes stale, but the
    # primary path is the import above. Tests pin the import path.
    OPS_PERMISSIONS = {"permissions": {"allow": ["Bash(*)"], "deny": [], "ask": []}}
    SECURITY_PERMISSIONS = {"permissions": {"allow": ["Bash(*)"], "deny": [], "ask": []}}

    def _tool_name(entry: str) -> str:  # type: ignore[no-redef]
        paren = entry.find("(")
        return entry[:paren] if paren != -1 else entry

    def _authoritative_merge(current, ours):  # type: ignore[no-redef]
        gaia_tools = {_tool_name(e) for e in ours}
        kept = {e for e in current if _tool_name(e) not in gaia_tools}
        return sorted(kept | ours)

    def get_plugin_mode() -> str:  # type: ignore[no-redef]
        return os.environ.get("GAIA_PLUGIN_MODE", "ops")


# ---------------------------------------------------------------------------
# Result helper
# ---------------------------------------------------------------------------

def _result(action: str, path: Path | str, details: str) -> dict[str, Any]:
    """Build the canonical helper return dict."""
    return {"action": action, "path": str(path), "details": details}


def _read_json(path: Path) -> dict | None:
    """Read JSON, returning None on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: dict) -> None:
    """Write JSON with indent=2 + trailing newline (matches gaia conventions)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. settings.json
# ---------------------------------------------------------------------------

def configure_settings_json(workspace: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Create .claude/settings.json if missing. Idempotent.

    Non-invasive -- if the file already exists, we never overwrite it. Hooks
    live in settings.local.json (see merge_local_hooks); settings.json stays
    minimal.
    """
    claude_dir = workspace / ".claude"
    settings_path = claude_dir / "settings.json"

    if not claude_dir.exists():
        return _result("skipped", settings_path, ".claude/ not found")

    if settings_path.exists():
        return _result("noop", settings_path, "settings.json already exists")

    if dry_run:
        return _result("created", settings_path, "would create empty settings.json")

    settings_path.write_text("{}\n", encoding="utf-8")
    return _result("created", settings_path, "created empty settings.json")


# ---------------------------------------------------------------------------
# 2. settings.local.json -- permissions + env + agent
# ---------------------------------------------------------------------------

def merge_local_permissions(
    workspace: Path,
    *,
    mode: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge gaia permissions, env vars, and agent identity into settings.local.json.

    Authoritative merge -- Gaia owns its tool entries (Bash, Edit, Write,
    etc.) and replaces stale scoped variants. User-added entries for tools
    Gaia does NOT manage are preserved.

    Args:
        workspace: directory containing .claude/.
        mode: "ops" or "security". Default: detect via plugin_mode.
        dry_run: if True, compute the diff but do not write.
    """
    claude_dir = workspace / ".claude"
    local_path = claude_dir / "settings.local.json"

    if not claude_dir.exists():
        return _result("skipped", local_path, ".claude/ not found")

    resolved_mode = mode or get_plugin_mode() or "ops"
    our_perms = OPS_PERMISSIONS if resolved_mode == "ops" else SECURITY_PERMISSIONS
    our_allow = set(our_perms["permissions"].get("allow", []))
    our_deny = set(our_perms["permissions"].get("deny", []))

    existing = _read_json(local_path) if local_path.exists() else {}
    if existing is None:
        existing = {}

    changed_fields: list[str] = []

    # Agent identity (always set if not gaia-orchestrator)
    if existing.get("agent") != "gaia-orchestrator":
        existing["agent"] = "gaia-orchestrator"
        changed_fields.append("agent")

    # env vars (smart merge -- preserve user values)
    env = existing.setdefault("env", {})
    if "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in env:
        env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        changed_fields.append("env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
    if "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in env:
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        changed_fields.append("env.CLAUDE_CODE_DISABLE_AUTO_MEMORY")

    # Permissions: authoritative merge
    perms = existing.get("permissions", {})
    current_allow = set(perms.get("allow", []))
    current_deny = set(perms.get("deny", []))

    merged_allow = _authoritative_merge(current_allow, our_allow)
    merged_deny = _authoritative_merge(current_deny, our_deny)

    if current_allow != set(merged_allow):
        changed_fields.append("permissions.allow")
    if current_deny != set(merged_deny):
        changed_fields.append("permissions.deny")

    existing.setdefault("permissions", {})
    existing["permissions"]["allow"] = merged_allow
    existing["permissions"]["deny"] = merged_deny
    existing["permissions"].setdefault("ask", [])

    if not changed_fields:
        return _result("noop", local_path, f"settings.local.json already up to date ({resolved_mode} mode)")

    if dry_run:
        return _result(
            "updated",
            local_path,
            f"would update {len(changed_fields)} field(s): {', '.join(changed_fields)}",
        )

    _write_json(local_path, existing)
    return _result(
        "updated",
        local_path,
        f"merged {len(changed_fields)} field(s): {', '.join(changed_fields)}",
    )


# ---------------------------------------------------------------------------
# 3. settings.local.json -- hooks merge (npm mode)
# ---------------------------------------------------------------------------

def merge_local_hooks(
    workspace: Path,
    plugin_root: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge hooks from hooks.json into settings.local.json.

    In npm mode Claude Code reads hooks from settings.local.json, not from
    hooks.json directly, so this is required for hooks to fire. Resolves
    command paths to absolute via the .claude/hooks symlink so hooks work
    regardless of cwd at execution time.

    Args:
        workspace: directory containing .claude/.
        plugin_root: gaia package root (where hooks/hooks.json lives).
            Defaults to the resolved package root of this module.
        dry_run: if True, compute the diff but do not write.
    """
    claude_dir = workspace / ".claude"
    local_path = claude_dir / "settings.local.json"
    pkg_root = plugin_root or _PACKAGE_ROOT

    if not claude_dir.exists():
        return _result("skipped", local_path, ".claude/ not found")

    # Locate hooks.json -- prefer package root, fall back to symlink.
    hooks_json_path: Path | None = None
    candidate = pkg_root / "hooks" / "hooks.json"
    if candidate.is_file():
        hooks_json_path = candidate
    else:
        candidate2 = claude_dir / "hooks" / "hooks.json"
        if candidate2.is_file():
            hooks_json_path = candidate2

    if hooks_json_path is None:
        return _result("skipped", local_path, "hooks.json not found in package")

    hooks_data = _read_json(hooks_json_path)
    if hooks_data is None:
        return _result("error", local_path, f"hooks.json invalid: {hooks_json_path}")

    source_hooks = hooks_data.get("hooks", hooks_data)

    # Resolve absolute path for hook commands
    hooks_dir = claude_dir / "hooks"
    if hooks_dir.exists():
        try:
            hooks_abs = str(hooks_dir.resolve())
        except OSError:
            hooks_abs = str(hooks_dir)
    else:
        hooks_abs = str(hooks_dir)

    def _convert(cmd: str) -> str:
        # Replace ${CLAUDE_PLUGIN_ROOT}/hooks/ -> absolute hooks dir
        return re.sub(r"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/", f"{hooks_abs}/", cmd)

    converted: dict[str, list] = {}
    for event, entries in source_hooks.items():
        converted[event] = []
        for entry in entries:
            new_entry = dict(entry)
            if "hooks" in new_entry:
                new_entry["hooks"] = [
                    {**h, "command": _convert(h["command"])} if "command" in h else h
                    for h in new_entry["hooks"]
                ]
            converted[event].append(new_entry)

    existing = _read_json(local_path) if local_path.exists() else {}
    if existing is None:
        existing = {}

    existing_hooks = existing.get("hooks", {})

    # Migrate any legacy ".claude/hooks/..." paths to absolute
    migrated = False
    for entries in existing_hooks.values():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if cmd.startswith(".claude/hooks/"):
                    h["command"] = cmd.replace(".claude/hooks/", f"{hooks_abs}/", 1)
                    migrated = True

    # Smart merge -- gaia owns its event commands (dedupe by command string)
    changed = migrated
    for event, new_entries in converted.items():
        if event not in existing_hooks:
            existing_hooks[event] = new_entries
            changed = True
            continue

        existing_cmds: set[str] = set()
        for entry in existing_hooks[event]:
            for h in entry.get("hooks", []):
                if h.get("command"):
                    existing_cmds.add(h["command"])

        for new_entry in new_entries:
            new_cmds = [h.get("command") for h in new_entry.get("hooks", []) if h.get("command")]
            all_present = bool(new_cmds) and all(c in existing_cmds for c in new_cmds)
            if not all_present:
                existing_hooks[event].append(new_entry)
                changed = True

    if not changed:
        return _result("noop", local_path, "hooks already up to date")

    existing["hooks"] = existing_hooks

    if dry_run:
        return _result("updated", local_path, "would merge hooks from hooks.json")

    _write_json(local_path, existing)
    return _result("updated", local_path, f"merged hooks from {hooks_json_path}")


# ---------------------------------------------------------------------------
# 4. Symlinks under .claude/
# ---------------------------------------------------------------------------

# Directories the package exposes via .claude/<name> symlinks
_SYMLINK_NAMES = ["agents", "tools", "hooks", "commands", "templates", "config", "skills"]
# Files (not dirs) we link or copy into .claude/
_SYMLINK_FILES = ["CHANGELOG.md"]


def _symlink_is_stale(link: Path, plugin_root: Path) -> tuple[bool, str | None]:
    """Return (stale, reason). Stale if target missing or pointing at legacy gaia-ops."""
    try:
        raw = os.readlink(link)
    except OSError:
        return False, None

    if os.path.isabs(raw):
        target = Path(raw)
    else:
        target = (link.parent / raw).resolve(strict=False)

    if not target.exists():
        return True, f"target missing: {raw}"

    # If we're installed as @jaguilar87/gaia but the link still references
    # the legacy @jaguilar87/gaia-ops path, treat it as stale.
    pkg_name = plugin_root.name
    if pkg_name == "gaia" and "@jaguilar87/gaia-ops" in raw:
        return True, f"legacy target: {raw}"

    return False, None


def manage_symlinks(
    workspace: Path,
    plugin_root: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create/repair .claude/<name> symlinks pointing at the plugin root.

    Idempotent: existing valid symlinks are preserved; broken or
    legacy-target symlinks are repaired.
    """
    claude_dir = workspace / ".claude"
    pkg_root = plugin_root or _PACKAGE_ROOT

    if not claude_dir.exists():
        return _result("skipped", claude_dir, ".claude/ not found")

    fixed: list[str] = []
    valid: list[str] = []
    failed: list[dict] = []

    for name in _SYMLINK_NAMES + _SYMLINK_FILES:
        link = claude_dir / name
        target = pkg_root / name

        if not target.exists():
            # Source not in package; skip silently (e.g. a release without skills/)
            continue

        # If link does not exist as anything (no entry, not even broken symlink)
        if not link.exists() and not link.is_symlink():
            if dry_run:
                fixed.append(name)
                continue
            try:
                link.symlink_to(target)
                fixed.append(name)
            except OSError as exc:
                failed.append({"name": name, "error": str(exc)})
            continue

        # Entry exists -- check if it's a stale symlink
        if link.is_symlink():
            stale, reason = _symlink_is_stale(link, pkg_root)
            if stale:
                if dry_run:
                    fixed.append(f"{name} ({reason})")
                    continue
                try:
                    link.unlink()
                    link.symlink_to(target)
                    fixed.append(f"{name} ({reason})")
                except OSError as exc:
                    failed.append({"name": name, "error": str(exc)})
            else:
                valid.append(name)
        else:
            # Regular file/dir already exists; assume user-managed
            valid.append(name)

    total = len(fixed) + len(valid)
    if failed:
        details = f"{len(fixed)} fixed, {len(valid)} valid, {len(failed)} failed"
        action = "error"
    elif fixed:
        details = f"{len(fixed)} fixed, {len(valid)} valid"
        action = "updated"
    else:
        details = f"{total} valid"
        action = "noop"

    out = _result(action, claude_dir, details)
    out["fixed"] = fixed
    out["valid"] = valid
    out["failed"] = failed
    return out


# ---------------------------------------------------------------------------
# 5. plugin-registry.json
# ---------------------------------------------------------------------------

def _read_plugin_version(plugin_root: Path) -> str | None:
    """Read version from plugin_root/package.json. None on failure."""
    pkg_json = plugin_root / "package.json"
    data = _read_json(pkg_json)
    if not data:
        return None
    return data.get("version")


def _read_plugin_name(plugin_root: Path) -> str:
    """Read package name from plugin_root/package.json or fall back to dir name."""
    pkg_json = plugin_root / "package.json"
    data = _read_json(pkg_json)
    if data and data.get("name"):
        # @jaguilar87/gaia -> "gaia-ops" historically; modern source publishes as
        # @jaguilar87/gaia. Strip scope for the registry (Claude Code does the same).
        name = data["name"]
        if "/" in name:
            name = name.split("/", 1)[1]
        # Doctor expects "gaia-ops" or "gaia-security" in the registry. If
        # package.json says just "gaia", record "gaia-ops" (the canonical name).
        if name == "gaia":
            return "gaia-ops"
        return name
    return "gaia-ops"


def register_plugin(
    workspace: Path,
    plugin_root: Path | None = None,
    *,
    source: str = "cli-install",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write .claude/plugin-registry.json with the installed package metadata.

    Idempotent: if the registry already records the current version,
    nothing changes.

    Args:
        workspace: directory containing .claude/.
        plugin_root: gaia package root (for reading version).
        source: identifier recorded in registry.source. Common values:
            "cli-install" (manual gaia install), "npm-postinstall",
            "cli-update", "plugin-mode".
    """
    pkg_root = plugin_root or _PACKAGE_ROOT
    claude_dir = workspace / ".claude"
    registry_path = claude_dir / "plugin-registry.json"

    plugin_name = _read_plugin_name(pkg_root)
    version = _read_plugin_version(pkg_root) or "unknown"

    desired = {
        "installed": [{"name": plugin_name, "version": version}],
        "source": source,
    }

    if not claude_dir.exists():
        if dry_run:
            return _result("created", registry_path, f"would create registry for {plugin_name}@{version}")
        claude_dir.mkdir(parents=True, exist_ok=True)

    existing = _read_json(registry_path) if registry_path.exists() else None
    if existing == desired:
        return _result("noop", registry_path, f"{plugin_name}@{version} already registered")

    # Preserve "source" when it was set by a higher-priority installer
    # (e.g. plugin-mode set by SessionStart) and only the version differs.
    if existing and existing.get("source") in ("plugin-mode",) and source == "cli-update":
        # Don't overwrite plugin-mode source -- only update the version inside.
        installed = existing.get("installed") or []
        if installed and installed[0].get("name") == plugin_name and installed[0].get("version") == version:
            return _result("noop", registry_path, f"{plugin_name}@{version} already registered (plugin-mode)")

    if dry_run:
        return _result(
            "updated" if existing else "created",
            registry_path,
            f"would register {plugin_name}@{version} (source={source})",
        )

    _write_json(registry_path, desired)
    action = "updated" if existing else "created"
    return _result(action, registry_path, f"registered {plugin_name}@{version} (source={source})")


# ---------------------------------------------------------------------------
# Re-exports for tests / callers
# ---------------------------------------------------------------------------

__all__ = [
    "configure_settings_json",
    "merge_local_permissions",
    "merge_local_hooks",
    "manage_symlinks",
    "register_plugin",
]
