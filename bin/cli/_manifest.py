"""
Install manifest -- the record of what Gaia wrote into a workspace, so `gaia uninstall` takes back exactly that.

Two channels write it: `gaia install`, and the plugin's session hooks, whose
permission and attribution merge (and, on a legacy launch with no plugin data
directory, its marker and registry) land in the workspace too. The plugin
records only when a session actually wrote (``track`` / ``record_tracked``).

One manifest per workspace, at ``.claude/gaia-manifest.json``. It is never
edited incrementally: every write captures the entries it may touch, derives
the pre-Gaia BASELINE of those entries, runs, captures again, and rewrites the
manifest as the diff baseline -> after. Deriving the baseline is what keeps a
reinstall, or a later session, exact:

  * a manifest already exists -> the baseline is the current state with that
    manifest reverted in memory, so the original pre-Gaia state carries over;
  * no manifest but Gaia is already wired (an install that predates manifests,
    or a plugin workspace whose only trace is Gaia's permissions and
    attribution) -> the workspace is ADOPTED: Gaia's known footprint is
    stripped in memory (its links, markers, registry entry and settings keys
    -- hooks through the single ownership predicate of ``plugin_setup``) and
    everything else counts as the user's;
  * neither -> the baseline is the current state.

An entry records a path (workspace-relative, or absolute for the opt-in writes
outside the workspace) with the state Gaia found and the state it left. Files
keep their prior bytes, so an untouched file is restored byte for byte; a JSON
settings file edited by the user after install is instead reverted key by key
(the ``json_ops``), which leaves the user's later keys in place. The database
is never an entry.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

MANIFEST_NAME = "gaia-manifest.json"
MANIFEST_VERSION = 1

# Workspace-root entries a Gaia channel may write (OpenCode config; CLAUDE.md
# and AGENTS.md so an adopted legacy footprint is visible, never claimed).
_ROOT_CANDIDATES = ("opencode.json", ".opencode", "AGENTS.md", "CLAUDE.md")

# Names Gaia links (or copies, where symlinks are unavailable) into .claude/,
# current and retired; an entry under one of these names found as a link in an
# unmanifested install is Gaia's.
_GAIA_LINK_NAMES = frozenset({
    "agents", "tools", "hooks", "config", "skills", "opencode", "commands",
    "CHANGELOG.md", "README.md", "README.en.md",
})
_GAIA_MARKERS = (".plugin-initialized", ".gaia-symlink-fallback.json")
# What a plugin session writes into .claude/ when its data directory lives
# elsewhere: the permission and attribution merge, and the hooks link
# ``workspace_bootstrap`` creates for hosts that resolve hook paths there.
_PLUGIN_SESSION_ENTRIES = ("settings.local.json", "hooks")
_WINDOWS_ENV_NAME = "GAIA_WORKSPACE_PATH"


def manifest_path(workspace: Path) -> Path:
    """Where *workspace*'s manifest lives."""
    return workspace / ".claude" / MANIFEST_NAME


def load(workspace: Path) -> dict | None:
    """The workspace's manifest, or None when it has none (or it is unreadable)."""
    try:
        data = json.loads(manifest_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("entries"), list) else None


# ---------------------------------------------------------------------------
# Capture: the observable state of every entry an install may touch.
# ---------------------------------------------------------------------------

def _state_of(path: Path) -> dict:
    """Snapshot of one path: absent, symlink (target), dir, or file (bytes)."""
    if path.is_symlink():
        return {"type": "symlink", "target": os.readlink(path)}
    if path.is_dir():
        return {"type": "dir"}
    if path.is_file():
        return {"type": "file", "bytes": path.read_bytes()}
    return {"type": "absent"}


def _key(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _abs(workspace: Path, key: str) -> Path:
    path = Path(key)
    return path if path.is_absolute() else workspace / key


def capture(workspace: Path, extra: Iterable[Path] = ()) -> dict[str, dict]:
    """State of ``.claude`` and its direct entries, the root candidates, and *extra* paths."""
    claude_dir = workspace / ".claude"
    paths = [claude_dir, *(workspace / name for name in _ROOT_CANDIDATES), *extra]
    if claude_dir.is_dir() and not claude_dir.is_symlink():
        paths.extend(p for p in claude_dir.iterdir() if p.name != MANIFEST_NAME)
    return {_key(workspace, p): _state_of(p) for p in paths}


# ---------------------------------------------------------------------------
# JSON key diff -- the reversible edit of a settings file.
# ---------------------------------------------------------------------------

def json_ops(before: Any, after: Any, path: tuple = ()) -> list[dict]:
    """Edits that turn *before* into *after*, each carrying what reverting it needs."""
    if isinstance(before, dict) and isinstance(after, dict):
        ops: list[dict] = []
        for key, value in after.items():
            child = (*path, key)
            if key in before:
                ops += json_ops(before[key], value, child)
            elif isinstance(value, dict):
                ops += [{"op": "mkdict", "path": list(child)}, *json_ops({}, value, child)]
            elif isinstance(value, list):
                ops += [{"op": "mklist", "path": list(child)}, *json_ops([], value, child)]
            else:
                ops.append({"op": "add", "path": list(child)})
        for key, value in before.items():
            if key not in after:
                ops.append({"op": "remove", "path": [*path, key], "prior": value})
        return ops
    if isinstance(before, list) and isinstance(after, list):
        added = [item for item in after if item not in before]
        removed = [item for item in before if item not in after]
        return [{"op": "list", "path": list(path), "added": added, "removed": removed}] if added or removed else []
    if before != after:
        return [{"op": "set", "path": list(path), "prior": before, "value": after}]
    return []


def _parent(doc: Any, path: list) -> Any:
    for key in path[:-1]:
        if not isinstance(doc, dict) or key not in doc:
            return None
        doc = doc[key]
    return doc if isinstance(doc, dict) else None


def revert_json(doc: Any, ops: list[dict]) -> Any:
    """*doc* with *ops* undone; keys the user changed or added since are kept."""
    doc = copy.deepcopy(doc)
    for op in reversed(ops):
        path = op["path"]
        if not path:
            if op["op"] == "set" and doc == op["value"]:
                doc = op["prior"]
            elif op["op"] == "list" and isinstance(doc, list):
                doc = [i for i in doc if i not in op["added"]] + [i for i in op["removed"] if i not in doc]
            continue
        parent = _parent(doc, path)
        if parent is None:
            continue
        key, kind = path[-1], op["op"]
        if kind == "add":
            parent.pop(key, None)
        elif kind in ("mkdict", "mklist"):
            if key in parent and not parent[key]:
                del parent[key]
        elif kind == "remove":
            parent.setdefault(key, op["prior"])
        elif kind == "set":
            if parent.get(key) == op["value"]:
                parent[key] = op["prior"]
        elif kind == "list" and isinstance(parent.get(key), list):
            current = parent[key]
            parent[key] = [i for i in current if i not in op["added"]] + [
                i for i in op["removed"] if i not in current
            ]
    return doc


def _json_or_none(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _dump_json(doc: Any) -> bytes:
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Diff (install) and revert (uninstall, and baseline of a reinstall).
# ---------------------------------------------------------------------------

def diff(baseline: dict[str, dict], after: dict[str, dict]) -> list[dict]:
    """Manifest entries for every path whose state went from *baseline* to *after*."""
    entries = []
    for key in sorted(set(baseline) | set(after)):
        old = baseline.get(key, {"type": "absent"})
        new = after.get(key, {"type": "absent"})
        if old == new:
            continue
        entry: dict[str, Any] = {"path": key, "prior": _encode(old), "written": _encode(new)}
        if old["type"] in ("file", "absent") and new["type"] == "file":
            before_doc = _json_or_none(old["bytes"]) if old["type"] == "file" else {}
            after_doc = _json_or_none(new["bytes"])
            if isinstance(before_doc, dict) and isinstance(after_doc, dict):
                entry["json_ops"] = json_ops(before_doc, after_doc)
        entries.append(entry)
    return entries


def _encode(state: dict) -> dict:
    out = {k: v for k, v in state.items() if k != "bytes"}
    if "bytes" in state:
        out["b64"] = base64.b64encode(state["bytes"]).decode("ascii")
        out["sha256"] = _sha(state["bytes"])
    return out


def _decode(state: dict) -> dict:
    out = {k: v for k, v in state.items() if k not in ("b64", "sha256")}
    if "b64" in state:
        out["bytes"] = base64.b64decode(state["b64"])
    return out


def revert_states(current: dict[str, dict], entries: list[dict]) -> dict[str, dict]:
    """The state each entry should return to: *current* with the manifest undone.

    A path the user changed after install keeps the user's change: a file Gaia
    created or edited that no longer holds Gaia's bytes is reverted key by key
    when it is JSON and left as it is otherwise.
    """
    target = dict(current)
    for entry in entries:
        key = entry["path"]
        prior, written = _decode(entry["prior"]), _decode(entry["written"])
        now = current.get(key, {"type": "absent"})
        if now == written:
            target[key] = prior
        elif now["type"] == "file" and "json_ops" in entry:
            doc = revert_json(_json_or_none(now["bytes"]), entry["json_ops"])
            if prior["type"] == "absent" and doc == {}:
                target[key] = prior
            elif doc is not None:
                target[key] = {"type": "file", "bytes": _dump_json(doc)}
    return target


def apply_states(workspace: Path, current: dict[str, dict], target: dict[str, dict]) -> list[str]:
    """Make the filesystem hold *target* wherever it differs from *current*; return the paths changed.

    Directories are handled last, deepest first, and removed only when empty
    -- except the copy install made in place of a link -- so a directory Gaia
    created that now holds the user's files survives.
    """

    def order(key: str) -> tuple:
        removes_dir = target[key]["type"] == "absent" and current.get(key, {}).get("type") == "dir"
        return (removes_dir, -len(key) if removes_dir else 0, key)

    changed = []
    for key in sorted(target, key=order):
        want, have = target[key], current.get(key, {"type": "absent"})
        if want == have:
            continue
        path = _abs(workspace, key)
        if have["type"] == "dir" and want["type"] == "absent":
            if path.name == ".claude" or not _is_gaia_copy(workspace, path):
                try:
                    path.rmdir()
                except OSError:
                    continue
            else:
                shutil.rmtree(path)
            changed.append(key)
            continue
        if have["type"] in ("symlink", "file"):
            path.unlink()
        elif have["type"] == "dir":
            continue
        if want["type"] == "symlink":
            path.symlink_to(want["target"])
        elif want["type"] == "file":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(want["bytes"])
        elif want["type"] == "dir":
            path.mkdir(parents=True, exist_ok=True)
        changed.append(key)
    return changed


def _is_gaia_copy(workspace: Path, path: Path) -> bool:
    """A directory under .claude/ named like a Gaia link is the copy install made in its place."""
    return path.parent == workspace / ".claude" and path.name in _GAIA_LINK_NAMES


# ---------------------------------------------------------------------------
# Adoption of an install that predates manifests.
# ---------------------------------------------------------------------------

def is_unmanifested_install(workspace: Path) -> bool:
    """True when Gaia is wired into *workspace* but no manifest records it."""
    if load(workspace) is not None:
        return False
    claude_dir = workspace / ".claude"
    if any((claude_dir / name).is_symlink() for name in _GAIA_LINK_NAMES):
        return True
    if any((claude_dir / name).exists() for name in _GAIA_MARKERS):
        return True
    registry = _json_or_none(_read_bytes(claude_dir / "plugin-registry.json"))
    if isinstance(registry, dict) and _gaia_registry_entries(registry):
        return True
    local = _json_or_none(_read_bytes(claude_dir / "settings.local.json"))
    return isinstance(local, dict) and (local.get("agent") == "gaia-orchestrator" or _holds_gaia_settings(local))


def _holds_gaia_settings(local: dict) -> bool:
    """True when *local* carries what the plugin session writes: Gaia's hidden attribution or its deny rules.

    A plugin-only workspace has no link, marker or agent key in the workspace
    -- those live in the plugin's data directory -- so this footprint is the
    only one left to adopt it by.
    """
    from cli import _install_helpers as helpers  # noqa: PLC0415
    from cli import cleanup  # noqa: PLC0415

    attribution = local.get("attribution")
    if isinstance(attribution, dict) and all(
        attribution.get(k) == v for k, v in helpers._HIDDEN_ATTRIBUTION.items()
    ):
        return True
    permissions = local.get("permissions")
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    gaia_deny = cleanup._gaia_managed_permission_sets()[1]
    return isinstance(deny, list) and bool(gaia_deny) and gaia_deny <= set(deny)


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _gaia_registry_entries(registry: dict) -> list:
    installed = registry.get("installed")
    if not isinstance(installed, list):
        return []
    return [e for e in installed if isinstance(e, dict) and e.get("name") == "gaia"]


def adopted_baseline(workspace: Path, current: dict[str, dict]) -> dict[str, dict]:
    """*current* with Gaia's pre-manifest footprint stripped: what the workspace held before Gaia."""
    from cli import cleanup  # noqa: PLC0415 -- cleanup imports the hooks package lazily too

    baseline = dict(current)
    claude = ".claude"
    stamps = _json_or_none(current.get(f"{claude}/.gaia-symlink-fallback.json", {}).get("bytes", b""))
    copies = set(stamps) if isinstance(stamps, dict) else set()
    for key, state in current.items():
        name = key.rsplit("/", 1)[-1]
        if not key.startswith(f"{claude}/"):
            continue
        if name in _GAIA_MARKERS or (name in _GAIA_LINK_NAMES and (state["type"] == "symlink" or name in copies)):
            baseline[key] = {"type": "absent"}

    registry_key = f"{claude}/plugin-registry.json"
    registry = _json_or_none(current.get(registry_key, {}).get("bytes", b""))
    if isinstance(registry, dict) and _gaia_registry_entries(registry):
        kept = [e for e in registry["installed"] if e not in _gaia_registry_entries(registry)]
        if not kept and set(registry) <= {"installed", "source"}:
            baseline[registry_key] = {"type": "absent"}
        else:
            baseline[registry_key] = {"type": "file", "bytes": _dump_json({**registry, "installed": kept})}

    local_key = f"{claude}/settings.local.json"
    local = _json_or_none(current.get(local_key, {}).get("bytes", b""))
    if isinstance(local, dict):
        stripped = cleanup.strip_gaia_local_settings(copy.deepcopy(local), workspace)
        if stripped != local:
            baseline[local_key] = (
                {"type": "absent"} if not stripped else {"type": "file", "bytes": _dump_json(stripped)}
            )
    return baseline


# ---------------------------------------------------------------------------
# Entry points used by install and uninstall.
# ---------------------------------------------------------------------------

def external_paths(manifest: dict | None) -> list[Path]:
    """The absolute (outside-workspace) paths a manifest records."""
    if not manifest:
        return []
    return [Path(e["path"]) for e in manifest["entries"] if Path(e["path"]).is_absolute()]


def baseline_for(workspace: Path, extra: Iterable[Path] = ()) -> tuple[dict[str, dict], str]:
    """The pre-Gaia state of every entry install may touch, and how it was derived."""
    manifest = load(workspace)
    paths = [*extra, *external_paths(manifest)]
    current = capture(workspace, paths)
    if manifest is not None:
        return revert_states(current, manifest["entries"]), "manifest"
    if is_unmanifested_install(workspace):
        return adopted_baseline(workspace, current), "adopted"
    return current, "fresh"


def track(workspace: Path, *, whole_claude_dir: bool) -> dict:
    """What a session write must be compared against: the entries it may touch, as they are now.

    The plugin channel runs on every session start, so this reads only the
    entries its sessions write (``_PLUGIN_SESSION_ENTRIES``) -- unless
    *whole_claude_dir*, the legacy launch whose plugin data directory is the
    workspace's ``.claude`` itself. How the baseline will be derived is decided
    here too: after the write, Gaia's own settings and hooks link would make a
    fresh workspace look like an unmanifested install.
    """
    if whole_claude_dir:
        states = capture(workspace)
    else:
        paths = (workspace / ".claude" / name for name in _PLUGIN_SESSION_ENTRIES)
        states = {_key(workspace, p): _state_of(p) for p in paths}
    if load(workspace) is not None:
        source = "manifest"
    else:
        source = "adopted" if is_unmanifested_install(workspace) else "fresh"
    return {"workspace": workspace, "states": states, "whole": whole_claude_dir, "source": source}


def record_tracked(tracked: dict, *, channel: str, version: str) -> dict | None:
    """Record what changed since *tracked* was taken; None, and no manifest write, when nothing did."""
    workspace, states = tracked["workspace"], tracked["states"]
    if tracked["whole"]:
        if capture(workspace) == states:
            return None
    elif all(_state_of(_abs(workspace, k)) == state for k, state in states.items()):
        return None
    manifest = load(workspace)
    now = capture(workspace, external_paths(manifest))
    before = {**now, **states}
    if tracked["whole"]:
        before.update({k: {"type": "absent"} for k in now if k not in states})
    if tracked["source"] == "manifest" and manifest is not None:
        baseline = revert_states(before, manifest["entries"])
    elif tracked["source"] == "adopted":
        baseline = adopted_baseline(workspace, before)
    else:
        baseline = before
    return record(workspace, baseline, channel=channel, version=version)


def record(
    workspace: Path,
    baseline: dict[str, dict],
    *,
    channel: str,
    version: str,
    extra: Iterable[Path] = (),
    env: dict | None = None,
) -> dict:
    """Write the manifest for the install that just ran; returns it."""
    previous = load(workspace)
    after = capture(workspace, [*extra, *external_paths(previous)])
    env_entries = dict((previous or {}).get("env", {}))
    if env:
        env_entries.update({k: v for k, v in env.items() if k not in env_entries})
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "channel": channel,
        "gaia_version": version,
        "workspace": str(workspace),
        "entries": diff(baseline, after),
        "env": env_entries,
    }
    path = manifest_path(workspace)
    if path.parent.is_dir():
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def uninstall(workspace: Path, *, dry_run: bool = False) -> dict:
    """Revert *workspace* to its pre-Gaia state as its manifest (or adoption) records it.

    Returns ``{"source", "reverted", "env"}``: whether the manifest existed or
    was derived by adoption, the paths changed (or that would change), and the
    user environment variables restored.
    """
    manifest = load(workspace)
    if manifest is None and not is_unmanifested_install(workspace):
        return {"source": "none", "reverted": [], "env": []}
    current = capture(workspace, external_paths(manifest))
    if manifest is not None:
        target, source, env = revert_states(current, manifest["entries"]), "manifest", manifest.get("env", {})
    else:
        target, source, env = adopted_baseline(workspace, current), "adopted", {}
    if dry_run:
        return {"source": source, "reverted": sorted(k for k in target if target[k] != current.get(k)), "env": sorted(env)}
    try:
        manifest_path(workspace).unlink()
    except OSError:
        pass
    reverted = apply_states(workspace, current, target)
    return {"source": source, "reverted": reverted, "env": [_restore_env(n, p) for n, p in env.items()]}


def _restore_env(name: str, prior: str | None) -> str:
    """Put a user environment variable install persisted back to *prior* (Windows only)."""
    if not sys.platform.startswith("win"):
        return name
    if prior is None:
        subprocess.run(["reg", "delete", r"HKCU\Environment", "/v", name, "/f"], capture_output=True, check=False)
    else:
        subprocess.run(["setx", name, prior], capture_output=True, check=False)
    return name


def windows_env_prior() -> dict:
    """The value install's ``setx`` is about to replace, keyed by variable name."""
    return {_WINDOWS_ENV_NAME: os.environ.get(_WINDOWS_ENV_NAME)}
