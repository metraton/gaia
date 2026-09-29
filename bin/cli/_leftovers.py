"""
What a Gaia install leaves that the install manifest cannot hold, for `gaia uninstall` to remove or name.

The manifest records the state of paths Gaia wrote, captured around its own
run. The package manager runs before Gaia does, and `gaia dev` and the hooks
write outside that window, so these leftovers have no recorded prior state.
They are found by what they are instead -- the Gaia package entry, its `.bin`
shims, its dependency line, the packed tarballs of `gaia dev`, hook scratch
state -- and every item is either removed or reported with the reason it stays.

``plan`` only reads. ``apply`` performs a plan, so a dry run lists exactly
what the real run does.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from cli._manifest import _is_junction  # type: ignore

GAIA_PACKAGE = "@jaguilar87/gaia"

_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json")
_OTHER_LOCKFILES = {"pnpm-lock.yaml": "pnpm install", "yarn.lock": "yarn install", "bun.lock": "bun install"}
_SHIM_NAMES = ("gaia", "gaia.cmd", "gaia.ps1")
_HOOK_SCRATCH = (".hooks_state", ".hooks_state.json")


def _remove(path: Path, reason: str, **extra) -> dict:
    return {"path": str(path), "action": "remove", "reason": reason, **extra}


def _edit(path: Path, reason: str) -> dict:
    return {"path": str(path), "action": "edit", "reason": reason}


def _kept(path: Path, reason: str) -> dict:
    return {"path": str(path), "reason": reason}


def plan(workspace: Path, *, package_manager_owns_package: bool) -> tuple[list[dict], list[dict]]:
    """``(artifacts, kept)``: what uninstall removes or edits, and what it leaves with the reason.

    *package_manager_owns_package* is the `npm uninstall` run: npm is about to
    remove its own package entry, dependency and lockfile lines, so they are
    not touched here.
    """
    artifacts: list[dict] = []
    kept: list[dict] = []
    if not package_manager_owns_package:
        for found in (_package_entry(workspace), _dependency_line(workspace), _lockfiles(workspace)):
            artifacts += found[0]
            kept += found[1]
    artifacts += _dev_caches(workspace)
    hook_scratch, logs = _hook_runtime(workspace)
    return artifacts + hook_scratch, kept + logs


def apply(artifacts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Perform *artifacts*; returns ``(done, failed)``, each failure as a kept item with its error."""
    done: list[dict] = []
    failed: list[dict] = []
    for item in artifacts:
        path = Path(item["path"])
        try:
            if item["action"] == "edit":
                _EDITORS[path.name](path)
            elif item.get("empty_dir") or _is_junction(path):
                path.rmdir()
            elif path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
        except (OSError, ValueError) as exc:
            failed.append(_kept(path, f"could not be removed: {exc}"))
        else:
            done.append(item)
    return done, failed


def _is_gaia_package(entry: Path) -> bool:
    if entry.is_symlink() and not entry.exists():
        return True
    try:
        return json.loads((entry / "package.json").read_text(encoding="utf-8")).get("name") == GAIA_PACKAGE
    except (OSError, ValueError, AttributeError):
        return False


def _is_gaia_shim(shim: Path) -> bool:
    if shim.is_symlink():
        return GAIA_PACKAGE in os.readlink(shim).replace("\\", "/")
    try:
        return GAIA_PACKAGE in shim.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _package_entry(workspace: Path) -> tuple[list[dict], list[dict]]:
    modules = workspace / "node_modules"
    entry = modules / GAIA_PACKAGE
    removals: list[dict] = []
    kept: list[dict] = []
    if os.path.lexists(entry):
        if _is_gaia_package(entry):
            removals.append(_remove(entry, "the Gaia package the package manager installed"))
        else:
            kept.append(_kept(entry, f"not the {GAIA_PACKAGE} package"))
    for name in _SHIM_NAMES:
        shim = modules / ".bin" / name
        if os.path.lexists(shim) and _is_gaia_shim(shim):
            removals.append(_remove(shim, "the `gaia` command shim of that package"))
    store = modules / ".pnpm"
    if store.is_dir() and not store.is_symlink():
        removals += [_remove(p, "pnpm's store copy of the Gaia package") for p in sorted(store.glob("@jaguilar87+gaia@*"))]

    gone = {Path(item["path"]) for item in removals}
    for directory in (entry.parent, modules / ".bin", store, modules):
        children = list(directory.iterdir()) if directory.is_dir() and not directory.is_symlink() else []
        if children and all(child in gone for child in children):
            gone.add(directory)
            removals.append(_remove(directory, "left empty", empty_dir=True))
    return removals, kept


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _drop_gaia_dependency(container) -> bool:
    """Remove Gaia from every dependency section of *container*; a section it empties goes too."""
    changed = False
    if not isinstance(container, dict):
        return changed
    for section in _DEPENDENCY_SECTIONS:
        deps = container.get(section)
        if isinstance(deps, dict) and GAIA_PACKAGE in deps:
            del deps[GAIA_PACKAGE]
            changed = True
            if not deps:
                del container[section]
    return changed


def _strip_lock(doc) -> bool:
    if not isinstance(doc, dict):
        return False
    changed = _drop_gaia_dependency(doc)
    packages = doc.get("packages")
    if isinstance(packages, dict):
        for key in [k for k in packages if k == f"node_modules/{GAIA_PACKAGE}" or k.endswith(f"/node_modules/{GAIA_PACKAGE}")]:
            del packages[key]
            changed = True
        changed = _drop_gaia_dependency(packages.get("")) or changed
    return changed


def _dependency_line(workspace: Path) -> tuple[list[dict], list[dict]]:
    path = workspace / "package.json"
    probe = _load_json(path)
    if isinstance(probe, dict) and _drop_gaia_dependency(probe):
        return [_edit(path, "Gaia's dependency line removed; your other dependencies stay")], []
    return [], []


def _lockfiles(workspace: Path) -> tuple[list[dict], list[dict]]:
    edits: list[dict] = []
    kept: list[dict] = []
    for name in _NPM_LOCKFILES:
        path = workspace / name
        if _strip_lock(_load_json(path)):
            edits.append(_edit(path, "Gaia's entries removed from the lockfile; the rest stays"))
    for name, refresh in _OTHER_LOCKFILES.items():
        path = workspace / name
        try:
            mentions_gaia = GAIA_PACKAGE in path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if mentions_gaia:
            kept.append(_kept(path, f"still lists {GAIA_PACKAGE}; a lockfile in this format is regenerated by `{refresh}`"))
    return edits, kept


def _rewrite_json(path: Path, strip) -> None:
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    if not strip(doc):
        return
    indent = re.search(r'^([ \t]+)"', raw, re.MULTILINE)
    text = json.dumps(doc, indent=indent.group(1) if indent else 2, ensure_ascii=False)
    path.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")


_EDITORS = {
    "package.json": lambda p: _rewrite_json(p, _drop_gaia_dependency),
    "package-lock.json": lambda p: _rewrite_json(p, _strip_lock),
    "npm-shrinkwrap.json": lambda p: _rewrite_json(p, _strip_lock),
}


def _dev_caches(workspace: Path) -> list[dict]:
    """The tarballs and install record `gaia dev` keeps for this workspace outside it."""
    from cli.dev import default_pack_dest  # noqa: PLC0415
    from gaia.install_provenance import PLUGIN_CHANNEL, provenance_path  # noqa: PLC0415

    found = [(default_pack_dest(workspace), "the tarballs `gaia dev` packed for this workspace")]
    found += [(provenance_path(workspace, channel), "the record `gaia dev` keeps of this install") for channel in ("npm", PLUGIN_CHANNEL)]
    return [_remove(path, reason) for path, reason in found if os.path.lexists(path)]


def _hook_runtime(workspace: Path) -> tuple[list[dict], list[dict]]:
    """Hook scratch state goes; the logs a session wrote under `.claude/logs` are history, so they are named and kept."""
    claude = workspace / ".claude"
    scratch = [_remove(claude / name, "hook scratch state") for name in _HOOK_SCRATCH if os.path.lexists(claude / name)]
    logs = claude / "logs"
    history = logs.is_dir() and any(logs.iterdir())
    return scratch, [_kept(logs, "Gaia's audit and violation history; delete it by hand if you do not want it")] if history else []
