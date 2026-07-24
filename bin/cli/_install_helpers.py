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
import shutil
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Reuse the canonical permission/hook merge logic from plugin_setup.py.
# That module is the SINGLE SOURCE OF TRUTH for PERMISSIONS, deny rules,
# the authoritative-merge algorithm, and the hooks.json conversion. We import
# the constants but reimplement the orchestration here so we can return the
# {action, path, details} contract instead of plain booleans.
# ---------------------------------------------------------------------------

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent  # bin/cli -> bin -> pkg/
_HOOKS_ROOT = _PACKAGE_ROOT / "hooks"

# The hooks/ dir must be on sys.path to pull PERMISSIONS from plugin_setup.
# plugin_setup lives at hooks/modules/core/plugin_setup.py, and importing it
# runs hooks/modules/core/__init__.py, which transitively does
# `from adapters.host_session import ...` -- a TOP-LEVEL `adapters` import that
# only resolves when hooks/ itself is on sys.path (that is how the hook
# entrypoints set it up at runtime, and why the runtime import form is
# `modules.core.plugin_setup`, NOT the dotted `hooks.modules.core...`).
# During `gaia install` only the package root was on the path, so BOTH the
# transitive `adapters` import AND the dotted-package form failed, the `except`
# fallback below fired, and PERMISSIONS silently became the EMPTY-deny fallback
# -- a fresh install then wrote settings.local.json with NO deny rules, which
# `gaia doctor` correctly flags as an error (release-check gate 2). Putting
# hooks/ on the path and importing via the runtime form makes the canonical
# import succeed so the full _DENY_RULES set is merged in.
if str(_HOOKS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HOOKS_ROOT))

try:
    from modules.core.plugin_setup import (  # type: ignore  # noqa: E402
        PERMISSIONS,
        _authoritative_merge,
        _tool_name,
    )
except Exception:  # noqa: BLE001
    # Fallback constants if the hooks package cannot be imported (e.g. partial
    # install). These mirror the canonical values in plugin_setup.py at the
    # time of writing -- if those drift, this fallback becomes stale, but the
    # primary path is the import above. Tests pin the import path.
    PERMISSIONS = {"permissions": {"allow": ["Bash(*)"], "deny": [], "ask": []}}

    def _tool_name(entry: str) -> str:  # type: ignore[no-redef]
        paren = entry.find("(")
        return entry[:paren] if paren != -1 else entry

    def _authoritative_merge(current, ours):  # type: ignore[no-redef]
        gaia_tools = {_tool_name(e) for e in ours}
        kept = {e for e in current if _tool_name(e) not in gaia_tools}
        return sorted(kept | ours)


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
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge gaia permissions, env vars, and agent identity into settings.local.json.

    Authoritative merge -- Gaia owns its tool entries (Bash, Edit, Write,
    etc.) and replaces stale scoped variants. User-added entries for tools
    Gaia does NOT manage are preserved.

    Args:
        workspace: directory containing .claude/.
        dry_run: if True, compute the diff but do not write.
    """
    claude_dir = workspace / ".claude"
    local_path = claude_dir / "settings.local.json"

    if not claude_dir.exists():
        return _result("skipped", local_path, ".claude/ not found")

    our_perms = PERMISSIONS
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
        return _result("noop", local_path, "settings.local.json already up to date")

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
    hooks.json directly, so this is required for hooks to fire. Command
    paths are made absolute through the STABLE `.claude/hooks` symlink (the
    symlink itself is NOT followed) so hooks work regardless of cwd at
    execution time AND survive repeated `gaia dev` runs -- see the
    hooks_abs computation below for why the symlink must not be resolved
    through.

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

    # Absolute path for hook commands. Always normalized to forward-slash via
    # .as_posix() -- this is what neutralizes both the re.sub "bad escape" on a
    # Windows "C:\Users\..." backslash (the string below is used as a
    # *replacement* pattern, where backslash is special) and the Windows shell
    # eating backslash escapes when the command is written into
    # settings.local.json. Python accepts forward-slash paths natively on
    # Windows, so this is safe on every platform.
    #
    # CRITICAL: resolve the .claude PARENT to an absolute, normalized path, but
    # do NOT follow the `hooks` symlink itself. `.claude/hooks` is the STABLE
    # indirection point `manage_symlinks` repoints on every install; following
    # it (the old `hooks_dir.resolve()`) baked the symlink's *current* target
    # into settings.local.json. Under `gaia dev` that target is the pnpm
    # content-addressed virtual-store path
    # (node_modules/.pnpm/@jaguilar87+gaia@file+...+<sha8>.tgz/...), whose <sha8>
    # segment changes on EVERY content change (see content_address_tarball in
    # cli/dev.py) and whose old store dir is pruned/replaced on reinstall. The
    # Claude Code harness pins hook commands at session start (no hot-reload),
    # so a resumed/next-run session kept the stale resolved path and its hooks
    # pointed at a store dir that no longer existed. Baking the stable
    # `.claude/hooks/...` path instead keeps settings.local.json valid across
    # repeated dev iterations -- install just repoints the one symlink.
    hooks_dir = claude_dir / "hooks"
    try:
        # Resolve only the parent (the real workspace `.claude` dir), then
        # re-attach the unresolved `hooks` symlink component.
        hooks_abs = (claude_dir.resolve() / "hooks").as_posix()
    except OSError:
        hooks_abs = hooks_dir.as_posix()

    def _convert(cmd: str) -> str:
        # Replace ${CLAUDE_PLUGIN_ROOT}/hooks/ -> absolute hooks dir.
        # hooks_abs is forward-slash only (see above), so it never contains
        # a backslash escape sequence -- but the replacement is still passed
        # through a lambda (not a raw string) as defense in depth, since
        # re.sub interprets backslashes in a string replacement specially.
        return re.sub(
            r"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/",
            lambda _m: f"{hooks_abs}/",
            cmd,
        )

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

    # Note: Gaia has no shipped users yet; we assume a clean install or a
    # workspace that already went through this helper. Auto-migration of
    # legacy ".claude/hooks/..." relative paths used to live here -- it was
    # removed in Pass 4 of the install refactor because no production
    # workspaces ever wrote that flavor (no released version emitted it).
    # If a future schema migration becomes necessary, add it explicitly with
    # a versioned migration step rather than re-introducing silent rewrites.

    # Smart merge -- gaia owns its event commands (dedupe by command string)
    changed = False
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
_SYMLINK_NAMES = ["agents", "tools", "hooks", "config", "skills"]
# Files (not dirs) we link or copy into .claude/
_SYMLINK_FILES = ["CHANGELOG.md"]

# When symlink_to fails (Windows without the "Create symbolic links"
# privilege -> OSError / WinError 1314), manage_symlinks falls back to a REAL
# copy. A copy passes `resolve(strict=True)` (so doctor's Symlinks check stays
# green) but is otherwise indistinguishable from a user-managed file, and would
# fall into the "user-managed, never refreshed" branch -> silent staleness on a
# reinstall/update (defect F5/R2). This registry, written next to the copies in
# `.claude/`, records the package VERSION each fallback copy was materialized
# from, so the copy is (a) RECOGNIZABLE as Gaia-managed and (b) REFRESHABLE:
# manage_symlinks re-materializes it when the stamped version drifts from the
# package version, exactly as it repairs a stale symlink. `doctor.py`
# (check_symlinks_freshness) reads the same file to evaluate a copy's freshness
# by its stamp rather than by `resolved.parent/package.json` (which is not the
# package root for a copy). Keep this literal in sync with
# `doctor._FALLBACK_STAMP_FILE` (a test asserts parity).
_FALLBACK_STAMP_FILE = ".gaia-symlink-fallback.json"


def _read_stamps(claude_dir: Path) -> dict:
    """Read the fallback-copy stamp registry ({} when absent/invalid)."""
    data = _read_json(claude_dir / _FALLBACK_STAMP_FILE)
    return data if isinstance(data, dict) else {}


def _set_stamp(claude_dir: Path, name: str, version: str) -> None:
    """Record that `.claude/<name>` is a Gaia-managed copy of `version`."""
    stamps = _read_stamps(claude_dir)
    stamps[name] = {"version": version, "kind": "copy"}
    _write_json(claude_dir / _FALLBACK_STAMP_FILE, stamps)


def _clear_stamp(claude_dir: Path, name: str) -> None:
    """Drop any fallback stamp for `name` (called when a symlink succeeds, so a
    copy that later becomes a real symlink is no longer treated as a copy).

    Removes the stamp file entirely once empty to keep `.claude/` clean. A
    no-op when there is no stamp to clear -- never creates the file.
    """
    stamp_path = claude_dir / _FALLBACK_STAMP_FILE
    stamps = _read_stamps(claude_dir)
    if name not in stamps:
        return
    del stamps[name]
    if stamps:
        _write_json(stamp_path, stamps)
    else:
        try:
            stamp_path.unlink()
        except OSError:
            pass


def _materialize_copy(target: Path, link: Path) -> None:
    """Copy `target` to `link` -- copytree for dirs, copy2 for files."""
    if target.is_dir():
        shutil.copytree(target, link)
    else:
        shutil.copy2(target, link)


def _remove_link_entry(link: Path) -> None:
    """Remove `link` whether it is a symlink, a file, or a copied directory."""
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    elif link.exists():
        link.unlink()


def _create_link_or_copy(
    target: Path, link: Path, claude_dir: Path, version: str
) -> tuple[str, str | None]:
    """Create `link` -> `target` as a symlink; fall back to a real copy.

    On platforms where symlink creation is unavailable (Windows without the
    privilege -> OSError / WinError 1314), materialize a copy instead and stamp
    it with `version` so freshness logic can recognize and refresh it later.

    Returns ``(kind, error)`` where ``kind`` is ``"symlink"`` or ``"copy"`` on
    success (``error`` None), or ``("error", <msg>)`` when even the copy failed.
    """
    try:
        link.symlink_to(target)
    except OSError:
        # Symlink unavailable -> copy is the universal floor.
        try:
            _materialize_copy(target, link)
        except OSError as exc:
            return "error", str(exc)
        _set_stamp(claude_dir, link.name, version)
        return "copy", None
    # Symlink succeeded -> this name is no longer a fallback copy.
    _clear_stamp(claude_dir, link.name)
    return "symlink", None


def _symlink_is_stale(link: Path, plugin_root: Path) -> tuple[bool, str | None]:
    """Return (stale, reason).

    A symlink is stale when EITHER:

      * its target no longer exists (dangling), OR
      * its resolved target no longer matches the DESIRED package location
        (``plugin_root / link.name``) -- even when the old target still
        exists.

    The second case is the fix for the stale-symlink defect: a workspace
    previously wired to an OLDER installed copy (e.g. a prior pnpm virtual-
    store entry that is still on disk) must be re-pointed at the freshly
    installed package. Without this, ``manage_symlinks`` classified a
    valid-but-old link as "valid" and never repaired it, so a new install
    never reached the runtime -- ``.claude/hooks`` (and agents/skills/...)
    kept resolving to the old code. ``plugin_root`` was previously accepted
    but unused; it is now the anchor for the desired target.
    """
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

    # Desired target for this link: the same-named entry under the package
    # root we are installing from. Compare CANONICAL paths so a link that
    # already resolves to the desired package (possibly via an intermediate
    # package-manager symlink) is left untouched, while one resolving to a
    # different (older) location is repaired.
    desired = plugin_root / link.name
    try:
        target_real = target.resolve(strict=False)
        desired_real = desired.resolve(strict=False)
    except OSError:
        return False, None

    if desired.exists() and target_real != desired_real:
        return True, f"points to {target_real}, expected {desired_real}"

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

    Windows fallback: when symlink creation is unavailable (no privilege ->
    OSError / WinError 1314), the entry is materialized as a REAL copy stamped
    with the package version (see _FALLBACK_STAMP_FILE). Such a copy is NOT
    treated as an immutable user-managed file -- it is refreshed on a reinstall/
    update whenever the stamped version drifts from the package version, exactly
    as a stale symlink is repaired.
    """
    claude_dir = workspace / ".claude"
    pkg_root = plugin_root or _PACKAGE_ROOT

    if not claude_dir.exists():
        return _result("skipped", claude_dir, ".claude/ not found")

    version = _read_plugin_version(pkg_root) or "unknown"

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
            kind, err = _create_link_or_copy(target, link, claude_dir, version)
            if err:
                failed.append({"name": name, "error": err})
            else:
                fixed.append(name if kind == "symlink" else f"{name} (copy)")
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
                except OSError as exc:
                    failed.append({"name": name, "error": str(exc)})
                    continue
                kind, err = _create_link_or_copy(target, link, claude_dir, version)
                if err:
                    failed.append({"name": name, "error": err})
                else:
                    fixed.append(f"{name} ({reason})")
            else:
                valid.append(name)
            continue

        # Regular file/dir already exists. Distinguish a Gaia-materialized
        # fallback copy (has a version stamp) from a genuinely user-managed
        # entry (no stamp -- left untouched).
        stamp = _read_stamps(claude_dir).get(name)
        if stamp is None:
            valid.append(name)
            continue

        # Gaia-managed copy: refresh when the stamped version drifts from the
        # package version, so a copy never goes silently stale on reinstall.
        if stamp.get("version") == version:
            valid.append(name)
            continue

        if dry_run:
            fixed.append(f"{name} (stale copy)")
            continue
        try:
            _remove_link_entry(link)
        except OSError as exc:
            failed.append({"name": name, "error": str(exc)})
            continue
        kind, err = _create_link_or_copy(target, link, claude_dir, version)
        if err:
            failed.append({"name": name, "error": err})
        else:
            fixed.append(f"{name} (refreshed copy)")

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
        # @jaguilar87/gaia -> "gaia" is the canonical registry identity: Gaia
        # ships as a single unified plugin. Strip scope for the registry
        # (Claude Code does the same).
        name = data["name"]
        if "/" in name:
            name = name.split("/", 1)[1]
        return name
    # No package.json / no name -- "gaia" is the canonical fallback identity.
    return "gaia"


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
