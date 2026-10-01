"""The plugin channel of `gaia dev`: serve a packed build to Claude Code as gaia@gaia-dev.

The tarball is extracted into one stable directory per workspace that is also a
local-directory marketplace named ``gaia-dev`` whose only entry has source ``.``.
Claude Code loads a relative-path plugin of a local-directory marketplace in
place, so replacing the directory's contents reaches a running session through
``/reload-plugins`` with no version bump, and no dependency install runs there
(Gaia's package has no runtime dependencies).

Registration is local scope in the target workspace only, and
``gaia@gaia-marketplace`` is disabled there: both plugins' manifests are named
``gaia``, every component is namespaced by that name, and Claude Code defines no
tie-break between two installed marketplaces, so leaving both enabled would load
two ``gaia:*`` sets and run every hook twice. The workspace's local settings
outrank user settings key by key, which is what makes the disable local.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any

MARKETPLACE_NAME = "gaia-dev"
PLUGIN_ID = "gaia@gaia-dev"
PUBLISHED_PLUGIN_ID = "gaia@gaia-marketplace"


def plugin_dir(workspace: Path) -> Path:
    """The stable per-workspace directory the gaia-dev marketplace is registered at."""
    from gaia.paths import cache_dir, workspace_id

    return cache_dir() / "dev-plugin" / workspace_id(cwd=workspace)


def _marketplace(package: Path) -> dict:
    """The gaia-dev catalog: one gaia entry at the marketplace root, versioned by plugin.json."""
    try:
        owner = json.loads((package / ".claude-plugin" / "marketplace.json").read_text())["owner"]
    except (OSError, ValueError, KeyError, TypeError):
        owner = {"name": MARKETPLACE_NAME}
    return {
        "name": MARKETPLACE_NAME,
        "owner": owner,
        "description": "Local development build of Gaia served by `gaia dev --channel plugin`.",
        "plugins": [{
            "name": "gaia",
            "source": ".",
            "description": "Gaia built from an unpublished source checkout.",
        }],
    }


def extract_plugin(tarball: Path, destination: Path) -> dict[str, Any]:
    """Replace *destination* with the tarball's package tree plus the gaia-dev marketplace.

    The new tree is assembled beside *destination* and swapped in by rename, so
    the registered path never points at a half-extracted build.
    """
    from gaia.agent_identity import missing_orchestrator_identity

    if destination.is_symlink():
        return {"action": "error", "path": str(destination),
                "details": "refusing a redirected plugin directory"}
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
    except OSError as exc:
        return {"action": "error", "path": str(destination), "details": str(exc)}
    try:
        with tarfile.open(tarball) as archive:
            archive.extractall(staging, filter="data")
        package = staging / "package"
        manifest = json.loads((package / ".claude-plugin" / "plugin.json").read_text())
        if not isinstance(manifest, dict) or manifest.get("name") != "gaia":
            raise ValueError("packed plugin.json is not the gaia plugin")
        missing = missing_orchestrator_identity(package, plugin=True)
        if missing:
            raise ValueError(f"packed plugin cannot start the host as the orchestrator: {missing}")
        (package / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(_marketplace(package), indent=2) + "\n")
        retired = parent / f".retired-{uuid.uuid4().hex}"
        if destination.exists():
            os.rename(destination, retired)
        os.rename(package, destination)
        shutil.rmtree(retired, ignore_errors=True)
    except (OSError, ValueError, tarfile.TarError) as exc:
        return {"action": "error", "path": str(destination), "details": f"cannot extract plugin: {exc}"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"action": "created", "path": str(destination),
            "details": f"gaia {manifest.get('version', '?')} with marketplace {MARKETPLACE_NAME}"}


def _claude(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the Claude Code CLI from *workspace*, so local scope means that workspace."""
    return subprocess.run(["claude", *args], cwd=str(workspace), capture_output=True,
                          text=True, check=False, timeout=120)


def _listed(workspace: Path, *args: str) -> list:
    """A `claude ... --json` listing; an unreadable one is an error, never an empty list."""
    result = _claude(workspace, *args, "--json")
    if result.returncode != 0:
        raise RuntimeError(f"claude {' '.join(args)} exited {result.returncode}: "
                           f"{(result.stderr or result.stdout).strip()[-300:]}")
    listing = json.loads(result.stdout)
    if not isinstance(listing, list):
        raise ValueError(f"claude {' '.join(args)} --json did not return a list")
    return listing


def _same_path(value: object, path: Path) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).resolve() == path.resolve()


def _registered(workspace: Path) -> tuple[list, list]:
    """The gaia-dev marketplaces Claude Code lists, and its gaia@gaia-dev installs local to *workspace*."""
    known = [m for m in _listed(workspace, "plugin", "marketplace", "list")
             if isinstance(m, dict) and m.get("name") == MARKETPLACE_NAME]
    installed = [p for p in _listed(workspace, "plugin", "list")
                 if isinstance(p, dict) and p.get("id") == PLUGIN_ID
                 and p.get("scope") == "local" and _same_path(p.get("projectPath"), workspace)]
    return known, installed


def _run_steps(workspace: Path, steps: list[tuple[str, ...]]) -> list[str]:
    done = []
    for step in steps:
        result = _claude(workspace, *step)
        if result.returncode != 0:
            raise RuntimeError(f"claude {' '.join(step)} exited {result.returncode}: "
                               f"{(result.stderr or result.stdout).strip()[-300:]}")
        done.append(" ".join(step[:3]))
    return done


def register_plugin(workspace: Path, directory: Path) -> dict[str, Any]:
    """Register gaia-dev and install gaia@gaia-dev at local scope, skipping what is already there."""
    manual = (f"claude plugin marketplace add {directory} --scope local, then "
              f"claude plugin install {PLUGIN_ID} --scope local (from {workspace})")
    if shutil.which("claude") is None:
        return {"action": "error", "path": str(directory),
                "details": f"claude CLI not found on PATH; register by hand: {manual}"}
    try:
        known, installed = _registered(workspace)
        if known and not _same_path(known[0].get("installLocation"), directory):
            raise ValueError(f"marketplace {MARKETPLACE_NAME} already points at "
                             f"{known[0].get('installLocation')}; remove it first")
        steps = [] if known else [("plugin", "marketplace", "add", str(directory), "--scope", "local")]
        if not installed:
            steps.append(("plugin", "install", PLUGIN_ID, "--scope", "local"))
        done = _run_steps(workspace, steps)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"action": "error", "path": str(directory), "details": f"{exc}; by hand: {manual}"}
    return {"action": "updated" if done else "noop", "path": str(directory),
            "details": "; ".join(done) if done else f"{PLUGIN_ID} already registered at local scope"}


def select_dev_plugin(workspace: Path) -> dict[str, Any]:
    """Enable gaia@gaia-dev and disable gaia@gaia-marketplace in the workspace's local settings."""
    path = workspace / ".claude" / "settings.local.json"
    try:
        if path.is_symlink():
            raise ValueError("refusing redirected local settings")
        settings = json.loads(path.read_text()) if path.exists() else {}
        plugins = settings.setdefault("enabledPlugins", {}) if isinstance(settings, dict) else None
        if not isinstance(plugins, dict):
            raise ValueError("local settings have no enabledPlugins object")
        wanted = {PLUGIN_ID: True, PUBLISHED_PLUGIN_ID: False}
        if all(plugins.get(key) is value for key, value in wanted.items()):
            return {"action": "noop", "path": str(path), "details": f"{PLUGIN_ID} already selected"}
        record = selection_record(workspace)
        if not record.exists():
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps({"present": PUBLISHED_PLUGIN_ID in plugins,
                                          "value": plugins.get(PUBLISHED_PLUGIN_ID)}) + "\n")
        plugins.update(wanted)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n")
    except (OSError, ValueError) as exc:
        return {"action": "error", "path": str(path), "details": f"{exc}; plugins left as they are"}
    return {"action": "updated", "path": str(path),
            "details": f"{PLUGIN_ID} enabled, {PUBLISHED_PLUGIN_ID} disabled in this workspace"}


def selection_record(workspace: Path) -> Path:
    """Where the first selection keeps gaia@gaia-marketplace's local entry as it was before gaia dev."""
    directory = plugin_dir(workspace)
    return directory.with_name(f"{directory.name}.selection.json")


def unregister_plugin(workspace: Path) -> None:
    """Uninstall gaia@gaia-dev from the workspace's local scope and remove the gaia-dev marketplace."""
    if shutil.which("claude") is None:
        raise RuntimeError(f"claude CLI not found on PATH; by hand from {workspace}: "
                           f"claude plugin uninstall {PLUGIN_ID} --scope local, then "
                           f"claude plugin marketplace remove {MARKETPLACE_NAME} --scope local")
    known, installed = _registered(workspace)
    steps = [("plugin", "uninstall", PLUGIN_ID, "--scope", "local")] if installed else []
    if known:
        steps.append(("plugin", "marketplace", "remove", MARKETPLACE_NAME, "--scope", "local"))
    _run_steps(workspace, steps)


def deselect_dev_plugin(workspace: Path) -> None:
    """Undo :func:`select_dev_plugin` in the workspace's local settings.

    gaia@gaia-dev is dropped. gaia@gaia-marketplace gets back the entry the
    selection record holds, and only while it still has the ``false`` gaia dev
    wrote; otherwise it is left as :func:`published_left_reason` says.
    """
    path = workspace / ".claude" / "settings.local.json"
    if path.is_symlink():
        raise ValueError("refusing redirected local settings")
    prior = _prior_published_entry(workspace)
    settings = json.loads(path.read_text())
    plugins = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    if not isinstance(plugins, dict):
        return
    plugins.pop(PLUGIN_ID, None)
    if published_left_reason(workspace, plugins) is None:
        if prior.get("present"):
            plugins[PUBLISHED_PLUGIN_ID] = prior.get("value")
        else:
            del plugins[PUBLISHED_PLUGIN_ID]
    if not plugins:
        del settings["enabledPlugins"]
    path.write_text(json.dumps(settings, indent=2) + "\n")


def _prior_published_entry(workspace: Path) -> dict | None:
    record = selection_record(workspace)
    prior = json.loads(record.read_text()) if record.exists() else None
    return prior if isinstance(prior, dict) else None


def published_left_reason(workspace: Path, plugins: dict) -> str | None:
    """Why :func:`deselect_dev_plugin` leaves gaia@gaia-marketplace in *plugins* as it is; None when it restores it."""
    enable = f"to enable it: claude plugin enable {PUBLISHED_PLUGIN_ID} --scope local"
    if plugins.get(PUBLISHED_PLUGIN_ID) is not False:
        return f"{PUBLISHED_PLUGIN_ID} left as it is: changed after `gaia dev` disabled it; {enable}"
    if _prior_published_entry(workspace) is None:
        return (f"{PUBLISHED_PLUGIN_ID} stays disabled: no record of its entry before `gaia dev` "
                f"disabled it; {enable}")
    return None


def reload_notice(workspace: Path, directory: Path) -> str:
    """What the user runs to load the build: the plugin channel applies without a restart."""
    return (f"  Run /reload-plugins in the Claude Code session open in {workspace}\n"
            f"  to load {PLUGIN_ID} from {directory}.")
