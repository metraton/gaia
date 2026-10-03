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

Channels share the manifest: ``channel`` is the Claude Code channel that owns
hook registration (plugin or npm, else opencode), ``package_channels`` the ones
`gaia install` wired. Which channel wrote an entry follows from its path
(``entry_channel``), so `gaia uninstall --channel` takes back one channel's
entries and leaves the others' recorded.

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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

MANIFEST_NAME = "gaia-manifest.json"
MANIFEST_VERSION = 1

OPENCODE_CHANNEL = "opencode"
# OpenCode 1.18.32 runs subagents in the background only under this variable
# or the OPENCODE_EXPERIMENTAL umbrella (effect/runtime-flags.ts); it has no
# config key, and Gaia writes nothing outside the workspace, so the user's
# shell sets it.
OPENCODE_BACKGROUND_SUBAGENTS_EXPORT = "export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true"
CLAUDE_CODE_CHANNELS = ("npm", "plugin")
_PACKAGE_COPY_CHANNELS = ("npm", OPENCODE_CHANNEL)

# Workspace-root entries a Gaia channel may write (OpenCode config; CLAUDE.md
# and AGENTS.md so an adopted legacy footprint is visible, never claimed).
_OPENCODE_ROOTS = ("opencode.json", ".opencode")
_ROOT_CANDIDATES = (*_OPENCODE_ROOTS, "AGENTS.md", "CLAUDE.md")
_OPENCODE_SKILLS = Path(".opencode") / "skills"


class ChannelNotRecorded(ValueError):
    """`gaia uninstall --channel` named a channel the workspace's manifest does not record."""


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
    """Snapshot of one path: absent, symlink (target), dir, or file (bytes).

    A Windows junction -- the link install falls back to without symlink
    privilege -- is a symlink here: as a dir it would be rmtree'd, which
    Python 3.12 refuses on a junction.
    """
    if path.is_symlink() or _is_junction(path):
        return {"type": "symlink", "target": os.readlink(path)}
    if path.is_dir():
        return {"type": "dir"}
    if path.is_file():
        return {"type": "file", "bytes": path.read_bytes()}
    return {"type": "absent"}


def _is_junction(path: Path) -> bool:
    """True for a Windows directory junction (os.path.isjunction exists from 3.12)."""
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _key(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _abs(workspace: Path, key: str) -> Path:
    path = Path(key)
    return path if path.is_absolute() else workspace / key


def capture(workspace: Path, extra: Iterable[Path] = ()) -> dict[str, dict]:
    """State of ``.claude`` and its direct entries, the root candidates, OpenCode's skill links, and *extra* paths."""
    claude_dir = workspace / ".claude"
    opencode_skills = workspace / _OPENCODE_SKILLS
    paths = [claude_dir, *(workspace / name for name in _ROOT_CANDIDATES), *extra]
    if claude_dir.is_dir() and not claude_dir.is_symlink():
        paths.extend(p for p in claude_dir.iterdir() if p.name != MANIFEST_NAME)
    if opencode_skills.is_dir() and not opencode_skills.is_symlink():
        paths.append(opencode_skills)
        paths.extend(opencode_skills.iterdir())
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
    when it is JSON and left as it is otherwise. A link Gaia made and Gaia
    itself later re-pointed (``_repointed_by_gaia``) is still Gaia's.
    """
    target = dict(current)
    for entry in entries:
        key = entry["path"]
        prior, written = _decode(entry["prior"]), _decode(entry["written"])
        now = current.get(key, {"type": "absent"})
        if now == written or _repointed_by_gaia(key, now, written):
            target[key] = prior
        elif now["type"] == "file" and "json_ops" in entry:
            doc = revert_json(_json_or_none(now["bytes"]), entry["json_ops"])
            if prior["type"] == "absent" and doc == {}:
                target[key] = prior
            elif doc is not None:
                target[key] = {"type": "file", "bytes": _dump_json(doc)}
    return target


def _repointed_by_gaia(key: str, now: dict, written: dict) -> bool:
    """True for a link Gaia wrote that now points elsewhere and is still Gaia's to take back.

    Inside the workspace that is a link under one of Gaia's ``.claude`` names,
    which Gaia re-points itself (a hooks link follows the install it belongs
    to). Outside, the path is shared by every install, so a link that still
    resolves belongs to whichever install re-pointed it; only a dangling one is
    taken back.
    """
    if now["type"] != "symlink" or written["type"] != "symlink":
        return False
    if Path(key).is_absolute():
        return not os.path.exists(key)
    parts = Path(key).parts
    return len(parts) == 2 and parts[0] == ".claude" and parts[1] in _GAIA_LINK_NAMES


def unreverted(workspace: Path, current: dict[str, dict], target: dict[str, dict], entries: list[dict]) -> list[dict]:
    """Entries whose path stays as it is, each with the reason -- a link or file that is not Gaia's now."""
    kept = []
    for entry in entries:
        key = entry["path"]
        now, prior = current.get(key, {"type": "absent"}), _decode(entry["prior"])
        if target.get(key) != now or now == prior or now["type"] not in ("symlink", "file"):
            continue
        if now["type"] == "symlink":
            reason = f"a link that now points at {now['target']}, which another install owns"
        else:
            reason = "changed since install, and not a settings file Gaia can revert key by key"
        kept.append({"path": str(_abs(workspace, key)), "reason": reason})
    return kept


def apply_states(
    workspace: Path,
    current: dict[str, dict],
    target: dict[str, dict],
    *,
    dry_run: bool = False,
    gone: Iterable[Path] = (),
) -> list[str]:
    """Make the filesystem hold *target* wherever it differs from *current*; return the paths changed.

    Directories are handled last, deepest first, and removed only when empty
    -- except the copy install made in place of a link -- so a directory Gaia
    created that now holds the user's files survives. With *dry_run* nothing
    is written and the same list comes back: a directory counts as empty when
    everything left in it is being removed, here or in *gone* (paths the
    caller removes itself).
    """
    removed = set(gone)

    def order(key: str) -> tuple:
        removes_dir = target[key]["type"] == "absent" and current.get(key, {}).get("type") == "dir"
        return (removes_dir, -len(key) if removes_dir else 0, key)

    changed = []
    for key in sorted(target, key=order):
        want, have = target[key], current.get(key, {"type": "absent"})
        if want == have:
            continue
        path = _abs(workspace, key)
        if have["type"] == "dir":
            gaia_copy = _is_gaia_copy(workspace, path)
            if want["type"] != "absent" or not (gaia_copy or _holds_only(path, removed)):
                continue
            if not dry_run:
                try:
                    shutil.rmtree(path) if gaia_copy else path.rmdir()
                except OSError:
                    continue
        elif not dry_run:
            if _is_junction(path):
                path.rmdir()
            elif have["type"] in ("symlink", "file"):
                path.unlink()
            if want["type"] == "symlink":
                path.symlink_to(want["target"])
            elif want["type"] == "file":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(want["bytes"])
            elif want["type"] == "dir":
                path.mkdir(parents=True, exist_ok=True)
        if want["type"] == "absent":
            removed.add(path)
        changed.append(key)
    return changed


def _holds_only(directory: Path, removed: set[Path]) -> bool:
    """True when nothing in *directory* survives *removed* -- an absent directory holds nothing."""
    try:
        return all(child in removed for child in directory.iterdir())
    except OSError:
        return True


def _is_gaia_copy(workspace: Path, path: Path) -> bool:
    """A directory Gaia's install copied in place of a link: under a Gaia name in .claude/, or one of OpenCode's skills."""
    if path.parent == workspace / _OPENCODE_SKILLS:
        return True
    return path.parent == workspace / ".claude" and path.name in _GAIA_LINK_NAMES


# ---------------------------------------------------------------------------
# Adoption of an install that predates manifests.
# ---------------------------------------------------------------------------

def is_unmanifested_install(workspace: Path) -> bool:
    """True when Gaia is wired into *workspace* but no manifest records it."""
    if load(workspace) is not None:
        return False
    return _claude_footprint(workspace) or bool(_strip_opencode(workspace, capture(workspace)))


def _gaia_opencode_root(item: object) -> str | None:
    """The Gaia package root an opencode.json ``plugin`` item loads, or None for a foreign plugin."""
    if not isinstance(item, str):
        return None
    path = Path(item.replace("\\", "/"))
    if path.parts[-2:] != ("opencode", "plugin.ts"):
        return None
    package = _json_or_none(_read_bytes(path.parents[1] / "package.json"))
    named = isinstance(package, dict) and package.get("name") == "@jaguilar87/gaia"
    return str(path.parents[1]) if named or path.as_posix().endswith("/@jaguilar87/gaia/opencode/plugin.ts") else None


def _strip_opencode(workspace: Path, current: dict[str, dict]) -> dict[str, dict]:
    """The OpenCode entries of *current* that carry Gaia's footprint, each as it was before Gaia.

    An OpenCode-only folder has no ``.claude/`` to hold a manifest, so its
    install is adopted by what ``configure_opencode_plugin`` writes: Gaia's
    plugin item, the agents whose prompt is a file of that package,
    ``default_agent``, and the skill links into the package; the user's own
    plugins, agents and keys stay.
    """
    from cli import _install_helpers as helpers  # noqa: PLC0415

    config = _json_or_none(current.get("opencode.json", {}).get("bytes", b""))
    plugins = config.get("plugin") if isinstance(config, dict) else None
    roots = {root for root in map(_gaia_opencode_root, plugins or []) if root}
    if not roots:
        return {}
    doc = {**config, "plugin": [item for item in plugins if not _gaia_opencode_root(item)]}
    agents = doc.get("agent")
    if isinstance(agents, dict):
        doc["agent"] = {
            name: agent for name, agent in agents.items()
            if not (isinstance(agent, dict) and any(
                str(agent.get("prompt", "")).startswith(f"{{file:{root}/") for root in roots))
        }
    if doc.get("default_agent") == "gaia-orchestrator":
        del doc["default_agent"]
    doc = {key: value for key, value in doc.items() if value not in ([], {})}
    stripped = {"opencode.json": {"type": "file", "bytes": _dump_json(doc)} if doc else {"type": "absent"}}
    skills = _OPENCODE_SKILLS.as_posix()
    for key in current:
        if key.startswith(f"{skills}/") and any(
            helpers._is_gaia_opencode_skill(_abs(workspace, key), Path(root) / "skills") for root in roots
        ):
            stripped[key] = {"type": "absent"}
    if len(stripped) > 1:
        stripped[skills] = stripped[".opencode"] = {"type": "absent"}
    return stripped


def _claude_footprint(workspace: Path) -> bool:
    """True when ``.claude/`` holds Gaia's links, markers, registry entry or settings."""
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
    baseline.update(_strip_opencode(workspace, current))
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
    package_channels: Iterable[str] = (),
) -> dict:
    """Write the manifest for the install that just ran; returns it.

    ``package_channels`` accumulates the channels `gaia install` wired here
    (npm, opencode), which `gaia update` re-wires; a session write keeps them.
    """
    previous = load(workspace)
    after = capture(workspace, [*extra, *external_paths(previous)])
    env_entries = dict((previous or {}).get("env", {}))
    if env:
        env_entries.update({k: v for k, v in env.items() if k not in env_entries})
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "channel": channel,
        "package_channels": sorted({*(previous or {}).get("package_channels", ()), *package_channels}),
        "gaia_version": version,
        "workspace": str(workspace),
        "entries": diff(baseline, after),
        "env": env_entries,
    }
    _write(workspace, manifest)
    return manifest


def _write(workspace: Path, manifest: dict) -> None:
    path = manifest_path(workspace)
    if path.parent.is_dir():
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def recorded_channels(manifest: dict | None) -> list[str]:
    """Every channel *manifest* records: the package channels and the Claude Code channel."""
    if not manifest:
        return []
    claude = [manifest.get("channel")] if manifest.get("channel") in CLAUDE_CODE_CHANNELS else []
    return sorted({*manifest.get("package_channels", ()), *claude})


def uninstall_command(workspace: Path, channel: str) -> str:
    """The `gaia uninstall` that takes back *channel*, scoped with --channel while another channel is recorded."""
    recorded = recorded_channels(load(workspace))
    scope = f" --channel {channel}" if channel in recorded and len(recorded) > 1 else ""
    return f"gaia uninstall{scope} --workspace {shlex.quote(str(workspace))}"


def entry_channel(manifest: dict, key: str) -> str | None:
    """The channel that wrote entry *key*; None for what every channel shares.

    Shared are the manifest's own directory and the launcher outside the
    workspace, which stay until the last channel goes.
    """
    if key == ".claude" or Path(key).is_absolute():
        return None
    return OPENCODE_CHANNEL if Path(key).parts[0] in _OPENCODE_ROOTS else manifest.get("channel")


def _uninstall_channel(workspace: Path, manifest: dict, channel: str, remaining: list[str], *,
                       dry_run: bool, package_manager_owns_package: bool) -> dict:
    """Take back *channel*'s entries and rewrite the manifest with what the *remaining* channels wrote.

    npm and the plugin both write ``.claude/`` and their entries cannot be told
    apart, so either one takes back every Claude Code entry; a plugin still
    enabled re-records its own on the next session.
    Hook scratch state and `gaia dev` caches serve whichever channel remains,
    so they stay; the package copy goes only when no remaining channel runs
    from it.
    """
    from cli import _leftovers  # noqa: PLC0415 -- _leftovers imports this module

    bucket = set(CLAUDE_CODE_CHANNELS) if channel in CLAUDE_CODE_CHANNELS else {channel}
    taken = [e for e in manifest["entries"] if entry_channel(manifest, e["path"]) in bucket]
    current = capture(workspace, external_paths(manifest))
    target = revert_states(current, taken)
    kept = unreverted(workspace, current, target, taken)
    from gaia.install_provenance import PLUGIN_CHANNEL  # noqa: PLC0415

    artifacts, dev_kept = _leftovers.dev_plugin_channel(workspace) if channel == PLUGIN_CHANNEL else ([], [])
    kept += dev_kept
    if not set(remaining) & set(_PACKAGE_COPY_CHANNELS):
        found, leftover_kept = _leftovers.plan(
            workspace, package_manager_owns_package=package_manager_owns_package, runtime=False
        )
        artifacts += found
        kept += leftover_kept
    if not dry_run:
        artifacts, failed = _leftovers.apply(artifacts)
        kept += failed
    gone = [Path(a["path"]) for a in artifacts if a["action"] == "remove"]
    reverted = sorted(apply_states(workspace, current, target, dry_run=dry_run, gone=gone))
    if not dry_run:
        _write(workspace, {
            **manifest,
            "channel": OPENCODE_CHANNEL if channel == manifest.get("channel") else manifest.get("channel"),
            "package_channels": [c for c in manifest.get("package_channels", []) if c != channel],
            "entries": [e for e in manifest["entries"] if e not in taken],
        })
    return {"source": "manifest", "channel": channel, "remaining": remaining, "reverted": reverted,
            "env": [], "artifacts": artifacts, "kept": kept}


def uninstall(workspace: Path, *, dry_run: bool = False, package_manager_owns_package: bool = False,
              channel: str | None = None) -> dict:
    """Revert *workspace* to its pre-Gaia state as its manifest (or adoption) records it.

    Returns ``{"source", "reverted", "env", "artifacts", "kept"}``: whether the
    manifest existed or was derived by adoption, the manifest paths changed (or
    that would change), the user environment variables restored, the leftovers
    outside the manifest removed or edited (``_leftovers``), and what stays
    with the reason. A dry run returns what the real run returns.
    *package_manager_owns_package* leaves the package entry, dependency and
    lockfiles to the `npm uninstall` that is running. A *channel* takes back
    only that channel's entries while another channel stays recorded (the
    result then also names ``channel`` and ``remaining``), and raises
    ``ChannelNotRecorded`` when the manifest does not record it.
    """
    from cli import _leftovers  # noqa: PLC0415 -- _leftovers imports this module

    manifest = load(workspace)
    if channel is not None:
        recorded = recorded_channels(manifest)
        if (manifest is None and channel == OPENCODE_CHANNEL and not _claude_footprint(workspace)
                and _strip_opencode(workspace, capture(workspace))):
            recorded = [OPENCODE_CHANNEL]
        if channel not in recorded:
            raise ChannelNotRecorded(
                f"the {channel} channel is not recorded in {manifest_path(workspace)}; "
                f"recorded: {', '.join(recorded) or 'none'}"
            )
        remaining = [c for c in recorded if c != channel]
        if remaining:
            return _uninstall_channel(workspace, manifest, channel, remaining, dry_run=dry_run,
                                      package_manager_owns_package=package_manager_owns_package)
    if manifest is None and not is_unmanifested_install(workspace):
        artifacts, kept = _take_back_dev_plugin(workspace, dry_run=dry_run)
        return {"source": "none", "reverted": [], "env": [], "artifacts": artifacts, "kept": kept}
    current = capture(workspace, external_paths(manifest))
    if manifest is not None:
        target, source, env = revert_states(current, manifest["entries"]), "manifest", manifest.get("env", {})
        kept = unreverted(workspace, current, target, manifest["entries"])
    else:
        target, source, env, kept = adopted_baseline(workspace, current), "adopted", {}, []
    artifacts, leftover_kept = _leftovers.plan(workspace, package_manager_owns_package=package_manager_owns_package)
    kept += leftover_kept
    if not dry_run:
        artifacts, failed = _leftovers.apply(artifacts)
        kept += failed
        try:
            manifest_path(workspace).unlink()
        except OSError:
            pass
    gone = [Path(a["path"]) for a in artifacts if a["action"] == "remove"] + [manifest_path(workspace)]
    reverted = sorted(apply_states(workspace, current, target, dry_run=dry_run, gone=gone))
    # After apply_states: it rewrites .claude/settings.local.json from the state captured above.
    dev_plugin, failed = _take_back_dev_plugin(workspace, dry_run=dry_run)
    kept += failed
    restored = sorted(env) if dry_run else [_restore_env(n, p) for n, p in sorted(env.items())]
    return {"source": source, "reverted": reverted, "env": restored,
            "artifacts": artifacts + dev_plugin, "kept": kept}


def _take_back_dev_plugin(workspace: Path, *, dry_run: bool) -> tuple[list[dict], list[dict]]:
    """What `gaia dev --channel plugin` set up here, taken back (or only listed on a dry run), and what stays.

    It writes no manifest entry, so a workspace it alone touched has no
    recorded install and still has this to take back.
    """
    from cli import _leftovers  # noqa: PLC0415 -- _leftovers imports this module

    found, kept = _leftovers.dev_plugin_channel(workspace)
    if dry_run:
        return found, kept
    done, failed = _leftovers.apply(found)
    return done, kept + failed


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
