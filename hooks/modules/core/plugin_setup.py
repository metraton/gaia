"""Plugin setup for the SessionStart hook.

On every session, merges gaia permissions and attribution into
.claude/settings.local.json; what that (or a registry landing in .claude/ when
CLAUDE_PLUGIN_DATA is unset) writes into the workspace is recorded in the
install manifest (recorded_in_manifest), so `gaia uninstall` reverts it. The
init marker lives in the data home, outside the workspace (mark_data_home).
Also owns the single writer of Gaia hook entries in workspace settings
(sync_workspace_hooks), used by the session setup and by install/update.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePath

from .paths import get_data_home, get_plugin_data_dir, legacy_data_dirs

logger = logging.getLogger(__name__)

MARKER_FILE = ".plugin-initialized"

# ---------------------------------------------------------------------------
# Deny list — shared across all modes.  Aligned with blocked_commands.py
# (hook-level enforcement) for dual-barrier security.  These rules are
# merged into settings.local.json so Claude Code's native permission system
# blocks the commands BEFORE they even reach the hook layer.
# ---------------------------------------------------------------------------
_DENY_RULES = [
    # AWS — networking / data infrastructure (irreversible)
    "Bash(aws ec2 delete-vpc:*)",
    "Bash(aws ec2 delete-subnet:*)",
    "Bash(aws ec2 delete-internet-gateway:*)",
    "Bash(aws ec2 delete-route-table:*)",
    "Bash(aws ec2 delete-route:*)",
    "Bash(aws ec2 terminate-instances:*)",
    "Bash(aws rds delete-db-instance:*)",
    "Bash(aws rds delete-db-cluster:*)",
    "Bash(aws dynamodb delete-table:*)",
    "Bash(aws s3 rb:*)",
    "Bash(aws s3api delete-bucket:*)",
    "Bash(aws elasticache delete-cache-cluster:*)",
    "Bash(aws elasticache delete-replication-group:*)",
    "Bash(aws eks delete-cluster:*)",
    # AWS — KMS / Organizations / Route53
    "Bash(aws kms schedule-key-deletion:*)",
    "Bash(aws organizations delete-organization:*)",
    "Bash(aws route53 delete-hosted-zone:*)",
    # AWS — IAM (mutative but denied at settings level too)
    "Bash(aws iam delete-user:*)",
    "Bash(aws iam delete-role:*)",
    "Bash(aws iam delete-access-key:*)",
    "Bash(aws iam delete-group:*)",
    "Bash(aws iam delete-instance-profile:*)",
    "Bash(aws iam delete-policy:*)",
    "Bash(aws iam delete-role-policy:*)",
    "Bash(aws iam delete-user-policy:*)",
    "Bash(aws iam delete-group-policy:*)",
    "Bash(aws iam detach-user-policy:*)",
    "Bash(aws iam detach-role-policy:*)",
    "Bash(aws iam detach-group-policy:*)",
    "Bash(aws iam remove-user-from-group:*)",
    # AWS — other destructive
    "Bash(aws backup delete:*::*)",
    "Bash(aws cloudformation delete-stack:*)",
    "Bash(aws dynamodb delete-item:*)",
    "Bash(aws ec2 delete-key-pair:*)",
    "Bash(aws ec2 delete-snapshot:*)",
    "Bash(aws ec2 delete-volume:*)",
    "Bash(aws ec2 delete-security-group:*)",
    "Bash(aws ec2 delete-network-interface:*)",
    "Bash(aws lambda delete-function:*)",
    "Bash(aws rds delete-db-cluster-parameter-group:*)",
    "Bash(aws rds delete-db-parameter-group:*)",
    "Bash(aws s3api delete-objects:*)",
    "Bash(aws sns delete-topic:*)",
    "Bash(aws sqs delete-queue:*)",
    "Bash(aws eks delete-nodegroup:*)",
    "Bash(aws eks delete-addon:*)",
    # Azure — resource group / networking / data (irreversible)
    "Bash(az group delete:*)",
    "Bash(az network vnet delete:*)",
    "Bash(az network vnet subnet delete:*)",
    "Bash(az network nsg delete:*)",
    "Bash(az network public-ip delete:*)",
    "Bash(az network application-gateway delete:*)",
    "Bash(az network lb delete:*)",
    "Bash(az network dns zone delete:*)",
    "Bash(az network private-dns zone delete:*)",
    "Bash(az vm delete:*)",
    "Bash(az vmss delete:*)",
    "Bash(az disk delete:*)",
    "Bash(az snapshot delete:*)",
    "Bash(az image delete:*)",
    # Azure — databases / storage
    "Bash(az sql server delete:*)",
    "Bash(az sql db delete:*)",
    "Bash(az cosmosdb delete:*)",
    "Bash(az redis delete:*)",
    "Bash(az storage account delete:*)",
    "Bash(az storage container delete:*)",
    "Bash(az storage blob delete-batch:*)",
    # Azure — AKS / container
    "Bash(az aks delete:*)",
    "Bash(az aks nodepool delete:*)",
    "Bash(az acr delete:*)",
    # Azure — IAM / key vault / functions
    "Bash(az role assignment delete:*)",
    "Bash(az role definition delete:*)",
    "Bash(az ad app delete:*)",
    "Bash(az ad sp delete:*)",
    "Bash(az keyvault delete:*)",
    "Bash(az keyvault key delete:*)",
    "Bash(az keyvault secret delete:*)",
    "Bash(az functionapp delete:*)",
    "Bash(az webapp delete:*)",
    # Azure — messaging / monitoring
    "Bash(az servicebus namespace delete:*)",
    "Bash(az servicebus queue delete:*)",
    "Bash(az servicebus topic delete:*)",
    "Bash(az eventhubs namespace delete:*)",
    "Bash(az eventhubs eventhub delete:*)",
    "Bash(az monitor action-group delete:*)",
    # GCP — project / cluster / database (irreversible)
    "Bash(gcloud projects delete:*)",
    "Bash(gcloud container clusters delete:*)",
    "Bash(gcloud container node-pools delete:*)",
    "Bash(gcloud sql instances delete:*)",
    "Bash(gcloud sql databases delete:*)",
    "Bash(gcloud services disable:*)",
    "Bash(gsutil rb:*)",
    "Bash(gsutil rm -r:*)",
    # GCP — compute / IAM / storage
    "Bash(gcloud compute firewall-rules delete:*)",
    "Bash(gcloud compute instances delete:*)",
    "Bash(gcloud compute networks delete:*)",
    "Bash(gcloud compute disks delete:*)",
    "Bash(gcloud compute images delete:*)",
    "Bash(gcloud compute snapshots delete:*)",
    "Bash(gcloud iam roles delete:*)",
    "Bash(gcloud storage rm:*)",
    # Kubernetes — critical cluster operations
    "Bash(kubectl delete namespace:*)",
    "Bash(kubectl delete node:*)",
    "Bash(kubectl delete cluster:*)",
    "Bash(kubectl delete pv:*)",
    "Bash(kubectl delete persistentvolume:*)",
    "Bash(kubectl delete pvc:*)",
    "Bash(kubectl delete persistentvolumeclaim:*)",
    "Bash(kubectl delete crd:*)",
    "Bash(kubectl delete customresourcedefinition:*)",
    "Bash(kubectl delete mutatingwebhookconfiguration:*)",
    "Bash(kubectl delete validatingwebhookconfiguration:*)",
    "Bash(kubectl delete clusterrole:*)",
    "Bash(kubectl delete clusterrolebinding:*)",
    "Bash(kubectl drain:*)",
    # Flux
    "Bash(flux delete:*)",
    # Git — force push (history rewrite)
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(git push origin --force:*)",
    "Bash(git push origin -f:*)",
    # Disk / filesystem destruction
    "Bash(dd:*)",
    "Bash(fdisk:*)",
    "Bash(mkfs:*)",
    "Bash(mkfs.ext4:*)",
    "Bash(mkfs.ext3:*)",
    "Bash(mkfs.fat:*)",
    "Bash(mkfs.ntfs:*)",
    # -------------------------------------------------------------------
    # Generic wildcard rules — catch ALL present and future services.
    # These complement the granular rules above; if a new cloud service
    # is added, these patterns block its delete operations automatically.
    # -------------------------------------------------------------------
    # AWS — any "delete-*" subcommand across all services
    "Bash(aws * delete-*:*)",
    "Bash(aws * terminate-*:*)",
    # Azure — any "delete" subcommand across all services
    "Bash(az * delete:*)",
    # GCP — any "delete" subcommand across all services
    "Bash(gcloud * delete:*)",
    "Bash(gsutil rb:*)",
    "Bash(gsutil rm:*)",
    "Bash(gcloud storage rm:*)",
    # Kubernetes — all delete and drain operations
    "Bash(kubectl delete:*)",
    "Bash(kubectl drain:*)",
    # Terragrunt — multi-module sweep destroy only.
    #
    # Single-module destroy (terraform/terragrunt) is intentionally absent:
    # it is approvable T3, and a harness deny here would reject it before the
    # hook could ever offer consent.  The hyphen-all form needs its OWN entry
    # because these patterns match by PREFIX -- it used to be covered
    # incidentally by the single-module wrapper entry that this change
    # removes, so without the explicit line below it would silently stop
    # being denied.
    "Bash(terragrunt run-all destroy:*)",
    "Bash(terragrunt destroy-all:*)",
    # Helm — uninstall
    "Bash(helm uninstall:*)",
    "Bash(helm delete:*)",
    # Flux — uninstall
    "Bash(flux uninstall:*)",
    # Docker — bulk prune
    "Bash(docker system prune:*)",
    "Bash(docker volume prune:*)",
    # Git — destructive history operations
    "Bash(git reset --hard:*)",
    # Repo deletion
    "Bash(gh repo delete:*)",
    "Bash(glab project delete:*)",
]

# Permissions for the single unified gaia plugin (full orchestrator surface).
PERMISSIONS = {
    "permissions": {
        "allow": [
            "Bash(*)",
            "Read",
            "Glob",
            "Grep",
            "BashOutput",
            "ExitPlanMode",
            "KillShell",
            "Skill",
            "SlashCommand",
            "Task",
            "Agent",
            "SendMessage",
            "AskUserQuestion",
            "TodoWrite",
            "WebFetch",
            "WebSearch",
            "NotebookEdit",
            "Edit",
            "Write",
        ],
        "deny": _DENY_RULES,
        "ask": [],
    }
}


# Claude Code's `attribution` setting with every part hidden: an empty string
# drops the commit trailer and the PR footer, sessionUrl=false the claude.ai
# session link (code.claude.com/docs/en/settings-reference, "Git and
# attribution"). Written by every channel -- the plugin's session setup here
# and `gaia install` -- because nothing Gaia publishes may carry Claude
# attribution. includeGitInstructions is left alone: turning it off also
# removes the git status snapshot, which has nothing to do with attribution.
HIDDEN_ATTRIBUTION = {"commit": "", "pr": "", "sessionUrl": False}


def marker_path() -> Path:
    """The init marker: one per data home, whichever channel launched the session."""
    return get_data_home() / MARKER_FILE


def mark_data_home() -> str:
    """Write the init marker on the first session under the data home.

    Returns a notice naming the per-channel directories an earlier layout left
    logs or session state in, only on the session that writes the marker; ""
    otherwise. Those directories stay where they are: nothing is merged.
    """
    marker = marker_path()
    if marker.exists():
        return ""
    marker.write_text(json.dumps({
        "initialized_at": datetime.now().isoformat(),
        "mode": "gaia",
    }))
    logger.info("Data home marked as initialized: %s", marker)
    legacy = legacy_data_dirs()
    if not legacy:
        return ""
    listing = "\n".join(f"- {path}" for path in legacy)
    return (
        f"Gaia now keeps logs and session state in {marker.parent}, shared by "
        "every channel. Earlier per-channel data was left in place, not merged:\n"
        f"{listing}"
    )


@contextmanager
def recorded_in_manifest():
    """Record in the workspace's install manifest whatever the block writes into the workspace.

    Recording never fails the session: a manifest that cannot be read or
    written leaves the write in place and unrecorded, as before manifests.
    """
    tracked = manifest = version = None
    try:
        from gaia.install_root import installed_root
        from modules.session.plugin_upgrade import _cli_module, package_version

        workspace = installed_root()
        manifest = _cli_module("_manifest")
        version = package_version() or "unknown"
        claude_dir = (workspace / ".claude").resolve()
        data_dir = get_plugin_data_dir().resolve()
        whole = data_dir == claude_dir or claude_dir in data_dir.parents
        tracked = manifest.track(workspace, whole_claude_dir=whole)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Install manifest not tracked: %s", exc)
    try:
        yield
    finally:
        if tracked is not None:
            try:
                manifest.record_tracked(tracked, channel="plugin", version=version)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Install manifest not recorded: %s", exc)


def _tool_name(entry: str) -> str:
    """Extract the base tool name from a permission entry.

    Examples:
        "Edit"          -> "Edit"
        "Edit(/tmp/*)"  -> "Edit"
        "Bash(*)"       -> "Bash"
        "Bash(aws ec2 delete-vpc:*)" -> "Bash"
    """
    paren = entry.find("(")
    return entry[:paren] if paren != -1 else entry


def _authoritative_merge(current: set[str], ours: set[str]) -> list[str]:
    """Merge permissions so Gaia's entries are authoritative.

    For every base tool name that Gaia defines, ALL existing entries for
    that tool are replaced with Gaia's current values.  User-added entries
    for tool names Gaia does NOT manage are preserved.

    This prevents stale scoped entries (e.g. ``Edit(/tmp/*)`` lingering
    after Gaia changes to ``Edit``) while keeping user customizations
    for tools outside Gaia's scope.
    """
    gaia_tool_names = {_tool_name(e) for e in ours}
    # Keep only user entries whose tool name Gaia doesn't manage
    user_entries = {e for e in current if _tool_name(e) not in gaia_tool_names}
    return sorted(user_entries | ours)


def setup_project_permissions() -> bool:
    """Merge gaia permissions into .claude/settings.local.json.

    Uses settings.local.json (highest project-level precedence) so that
    /reload-plugins picks up changes mid-session without restart.
    Preserves enabledPlugins and any existing user configuration.

    Returns True if settings were modified (reload needed); False, writing
    nothing, when the cwd is inside a managed worktree.
    """
    from gaia.install_root import InsideManagedWorktree, installed_root

    try:
        claude_dir = installed_root() / ".claude"
    except InsideManagedWorktree:
        return False
    settings_path = claude_dir / "settings.local.json"

    our_perms = PERMISSIONS
    our_allow = set(our_perms["permissions"]["allow"])
    our_deny = set(our_perms["permissions"].get("deny", []))

    # Load existing settings.local.json (has enabledPlugins from install)
    existing = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Authoritative merge: Gaia's entries replace any existing entries for the
    # same base tool name (removes stale scoped variants like Edit(/tmp/*) when
    # Gaia now says Edit).  User entries for tools Gaia doesn't manage survive.
    perms = existing.get("permissions", {})
    current_allow = set(perms.get("allow", []))
    current_deny = set(perms.get("deny", []))

    merged_allow = _authoritative_merge(current_allow, our_allow)
    merged_deny = _authoritative_merge(current_deny, our_deny)

    attribution = existing.get("attribution")
    attribution = dict(attribution) if isinstance(attribution, dict) else {}
    attribution_current = all(attribution.get(k) == v for k, v in HIDDEN_ATTRIBUTION.items())

    if current_allow == set(merged_allow) and current_deny == set(merged_deny) and attribution_current:
        logger.info("Project permissions already include gaia rules, skipping")
        return False

    # Update only permissions and attribution, preserve everything else (enabledPlugins, etc.)
    existing["attribution"] = {**attribution, **HIDDEN_ATTRIBUTION}
    existing.setdefault("permissions", {})
    existing["permissions"]["allow"] = merged_allow
    existing["permissions"]["deny"] = merged_deny
    existing["permissions"].setdefault("ask", [])

    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info("Merged gaia permissions and env into %s", settings_path)
    return True


def ensure_plugin_registry() -> None:
    """Create plugin-registry.json if missing.

    Detection strategies (in order):
    1. CLAUDE_PLUGIN_ROOT env var (plugin marketplace mode):
       Path looks like .../cache/marketplace/gaia/4.4.0-rc.2
    2. NPM package detection: resolve package name and version from
       node_modules path and package.json
    """
    import os
    data_dir = get_plugin_data_dir()
    registry_path = data_dir / "plugin-registry.json"
    if registry_path.exists():
        return

    plugin_name = None
    plugin_version = None
    source = None

    # Strategy 1: CLAUDE_PLUGIN_ROOT (plugin marketplace or --plugin-dir)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if plugin_root:
        root_path = Path(plugin_root)
        # First, try to read .claude-plugin/plugin.json (most reliable)
        plugin_json = root_path / ".claude-plugin" / "plugin.json"
        if plugin_json.exists():
            try:
                pdata = json.loads(plugin_json.read_text())
                plugin_name = pdata.get("name")
                plugin_version = pdata.get("version")
                source = "plugin-mode"
            except (json.JSONDecodeError, OSError):
                pass
        # Fallback: parse path (marketplace layout: .../name/version)
        if not plugin_name:
            parts = root_path.parts
            if len(parts) >= 2:
                plugin_name = parts[-2]
                plugin_version = parts[-1]
                source = "plugin-mode"

    # Strategy 2: NPM package detection
    if not plugin_name:
        npm_info = _detect_npm_package_info()
        if npm_info:
            plugin_name, plugin_version = npm_info
            source = "npm-mode"

    if not plugin_name:
        return

    registry = {
        "installed": [{"name": plugin_name, "version": plugin_version or "unknown"}],
        "source": source,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")
    logger.info("Created plugin-registry.json: %s@%s (source: %s)", plugin_name, plugin_version, source)


def _detect_npm_package_info() -> tuple[str, str | None] | None:
    """Detect plugin name and version from NPM package path.

    When installed via npm, this module lives at:
      .../node_modules/@jaguilar87/gaia/hooks/modules/core/plugin_setup.py

    Returns (plugin_name, version) or None.
    """
    module_path = Path(__file__).resolve()
    parts = module_path.parts

    # Find node_modules in path and extract package name
    pkg_name = None
    pkg_root = None
    for i, part in enumerate(parts):
        if part == "node_modules" and i + 1 < len(parts):
            next_part = parts[i + 1]
            if next_part.startswith("@") and i + 2 < len(parts):
                # Scoped package: @scope/name
                pkg_name = parts[i + 2]
                pkg_root = Path(*parts[:i + 3])
            else:
                pkg_name = next_part
                pkg_root = Path(*parts[:i + 2])
            break

    # "gaia" is the canonical single-plugin package name.
    if not pkg_name or pkg_name != "gaia":
        return None

    # Try to read version from package.json
    version = None
    if pkg_root:
        pkg_json = Path("/") / pkg_root / "package.json"
        try:
            if pkg_json.exists():
                data = json.loads(pkg_json.read_text())
                version = data.get("version")
        except Exception:
            pass

    return (pkg_name, version)


# ---------------------------------------------------------------------------
# Workspace hook registration -- the single writer.
#
# Every path that registers Gaia's hooks in a workspace (`gaia install`,
# `gaia update`, and the SessionStart/UserPromptSubmit setup below) goes
# through sync_workspace_hooks, which decides ownership with
# is_gaia_hook_command alone. The plugin channel registers hooks through the
# plugin's own hooks.json, so there it leaves zero Gaia entries in the
# workspace; the npm channel is read from settings files, so there it writes
# exactly the (event, matcher, command) triples hooks.json ships.
# ---------------------------------------------------------------------------

_PLUGIN_ROOT_HOOKS_TOKEN = "${CLAUDE_PLUGIN_ROOT}/hooks/"

_GAIA_HOOK_SCRIPT_RE = re.compile(
    r"(?P<root>\.claude|\$\{CLAUDE_PLUGIN_ROOT\})/hooks/(?P<name>[A-Za-z0-9_]+\.py)\b"
)


def _gaia_hook_entrypoints() -> frozenset[str]:
    """File names of the hook entrypoints this package ships (``hooks/*.py``)."""
    hooks_dir = Path(__file__).resolve().parents[2]
    return frozenset(p.name for p in hooks_dir.glob("*.py"))


def _workspace_hook_dirs(workspace: Path) -> set[str]:
    """Posix spellings of *workspace*'s ``.claude/hooks`` a Gaia writer bakes."""
    claude_dir = workspace / ".claude"
    dirs = {(claude_dir / "hooks").as_posix()}
    try:
        dirs.add((claude_dir.resolve() / "hooks").as_posix())
    except OSError:
        pass
    return dirs


def is_gaia_hook_command(command: object, workspace: Path, entrypoints: frozenset[str]) -> bool:
    """True when *command* is a Gaia hook registration -- the one ownership test.

    Qualifies: a ``.claude/hooks/<name>.py`` or ``${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py``
    target whose name is an entrypoint this package ships; any target left under
    the unexpanded placeholder, which only Gaia's hooks.json carries; and a
    target in *workspace*'s own ``.claude/hooks`` whose file is gone -- the
    registration of an entrypoint a release retired. A user script that exists
    there under a name Gaia does not ship is never matched.
    """
    if not isinstance(command, str):
        return False
    normalized = command.replace("\\", "/")
    match = _GAIA_HOOK_SCRIPT_RE.search(normalized)
    if match is None:
        return False
    name = match.group("name")
    if name in entrypoints or match.group("root") != ".claude":
        return True
    in_workspace = any(f"{d}/{name}" in normalized for d in _workspace_hook_dirs(workspace))
    return in_workspace and not (workspace / ".claude" / "hooks" / name).exists()


def render_gaia_hooks(shipped_hooks: dict, hooks_dir: PurePath) -> dict:
    """hooks.json entries with the plugin-root placeholder pointed at *hooks_dir*.

    The directory is written in posix form by plain substitution, so a Windows
    path or one holding backslashes is neither regex-escaped nor rejected.
    """
    prefix = f"{hooks_dir.as_posix()}/"
    rendered: dict = {}
    for event, entries in shipped_hooks.items():
        rendered[event] = []
        for entry in entries:
            new_entry = dict(entry)
            if isinstance(new_entry.get("hooks"), list):
                new_entry["hooks"] = [
                    {**h, "command": h["command"].replace(_PLUGIN_ROOT_HOOKS_TOKEN, prefix)}
                    if isinstance(h, dict) and isinstance(h.get("command"), str) else h
                    for h in new_entry["hooks"]
                ]
            rendered[event].append(new_entry)
    return rendered


def merge_workspace_hooks(
    existing_hooks: dict, gaia_hooks: dict, workspace: Path, entrypoints: frozenset[str]
) -> dict:
    """*existing_hooks* with Gaia's registrations replaced by exactly *gaia_hooks*.

    Every handler :func:`is_gaia_hook_command` claims is removed and the
    entries and events it leaves empty are dropped, so retired matchers and
    events go; each shipped (event, matcher, command) is then placed once,
    ahead of the user's entries for that event. Everything else is kept as
    found. An empty *gaia_hooks* is the plugin channel.
    """
    user_hooks: dict = {}
    for event, entries in existing_hooks.items():
        if not isinstance(entries, list):
            user_hooks[event] = entries
            continue
        kept = []
        for entry in entries:
            handlers = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(handlers, list):
                kept.append(entry)
                continue
            users = [
                h for h in handlers
                if not is_gaia_hook_command(
                    h.get("command") if isinstance(h, dict) else None, workspace, entrypoints
                )
            ]
            if len(users) == len(handlers):
                kept.append(entry)
            elif users:
                kept.append({**entry, "hooks": users})
        if kept:
            user_hooks[event] = kept

    merged = {event: list(entries) for event, entries in gaia_hooks.items()}
    for event, entries in user_hooks.items():
        if event not in merged:
            merged[event] = entries
        elif isinstance(entries, list):
            merged[event].extend(entries)
    return merged


def gaia_plugin_decisions(sources: Iterable[tuple[str, Path]]) -> dict[str, tuple[bool, str]]:
    """key -> (enabled, source label) for every ``gaia@<marketplace>`` key the settings name.

    The first source naming a key decides it, so *sources* go in the order
    Claude Code applies them -- workspace local, workspace, user: a ``false``
    in the workspace outranks a ``true`` in the user's file. The hook writer
    and ``gaia doctor`` both read this, so they cannot disagree on the channel.
    """
    decided: dict[str, tuple[bool, str]] = {}
    for label, path in sources:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
        for key, enabled in (plugins if isinstance(plugins, dict) else {}).items():
            if key.split("@", 1)[0] == "gaia":
                decided.setdefault(key, (enabled is True, label))
    return decided


def enabled_gaia_plugins(workspace: Path) -> list[tuple[str, str]]:
    """``(key, source label)`` for each ``gaia@...`` plugin the settings Claude Code reads for *workspace* leave enabled."""
    claude = workspace / ".claude"
    sources = [
        ("settings.local.json", claude / "settings.local.json"),
        ("settings.json", claude / "settings.json"),
        ("user settings", Path.home() / ".claude" / "settings.json"),
    ]
    return [(key, label) for key, (enabled, label) in gaia_plugin_decisions(sources).items() if enabled]


def _workspace_enables_gaia_plugin(workspace: Path) -> bool:
    """True when the settings Claude Code reads for *workspace* leave a ``gaia@...`` plugin enabled."""
    return bool(enabled_gaia_plugins(workspace))


def workspace_registers_gaia_hooks(workspace: Path) -> bool:
    """True when *workspace*'s settings.local.json holds a Gaia hook registration, the npm channel's mark."""
    try:
        settings = json.loads((workspace / ".claude" / "settings.local.json").read_text())
    except (OSError, ValueError):
        return False
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return False
    entrypoints = _gaia_hook_entrypoints()
    for entries in hooks.values():
        for entry in entries if isinstance(entries, list) else ():
            handlers = entry.get("hooks") if isinstance(entry, dict) else None
            for handler in handlers if isinstance(handlers, list) else ():
                command = handler.get("command") if isinstance(handler, dict) else None
                if is_gaia_hook_command(command, workspace, entrypoints):
                    return True
    return False


def resolve_hook_channel(workspace: Path, *, npm_copy: bool) -> str | None:
    """The channel that owns hook registration in *workspace*.

    ``"plugin"`` for a plugin launch (``CLAUDE_PLUGIN_ROOT`` set) or a
    workspace whose settings enable the Gaia plugin: an npm copy installed
    beside it defers, so the two channels stop undoing each other's writes.
    Otherwise ``"npm"`` when *npm_copy*, else None -- write nothing.
    """
    if os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip() or _workspace_enables_gaia_plugin(workspace):
        return "plugin"
    return "npm" if npm_copy else None


def sync_workspace_hooks(
    workspace: Path,
    channel: str,
    hooks_json_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> tuple[str, str]:
    """Register Gaia's hooks in *workspace*'s settings.local.json for *channel*.

    The only writer of Gaia hook entries in workspace settings. ``"npm"`` bakes
    *hooks_json_path* through the stable ``.claude/hooks`` link -- its parent
    resolved, the link itself not followed, so a repointed install never
    leaves a dead store path behind; ``"plugin"`` registers nothing. A
    settings file that cannot be parsed is left untouched. Returns
    ``(action, details)``, action one of updated, noop, skipped, error.
    """
    claude_dir = workspace / ".claude"
    settings_path = claude_dir / "settings.local.json"

    gaia_hooks: dict = {}
    if channel == "npm":
        if hooks_json_path is None or not hooks_json_path.is_file():
            return "skipped", "hooks.json not found in package"
        try:
            shipped = json.loads(hooks_json_path.read_text())
        except (OSError, ValueError):
            return "error", f"hooks.json invalid: {hooks_json_path}"
        try:
            hooks_dir = claude_dir.resolve() / "hooks"
        except OSError:
            hooks_dir = claude_dir / "hooks"
        gaia_hooks = render_gaia_hooks(shipped.get("hooks", shipped), hooks_dir)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (OSError, ValueError):
            return "error", f"{settings_path} is unreadable; hooks left as they are"
    existing = settings.get("hooks", {}) if isinstance(settings, dict) else None
    if not isinstance(existing, dict):
        return "error", f"{settings_path} has no hooks object; hooks left as they are"

    merged = merge_workspace_hooks(existing, gaia_hooks, workspace, _gaia_hook_entrypoints())
    if merged == existing:
        return "noop", "hooks already up to date"
    if dry_run:
        return "updated", f"would register {channel}-channel hooks"

    if merged:
        settings["hooks"] = merged
    else:
        settings.pop("hooks", None)
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    logger.info("Registered %s-channel hooks in %s", channel, settings_path)
    return "updated", f"registered {channel}-channel hooks"


def _installed_under_node_modules() -> bool:
    """True when this copy's real path is inside an npm or pnpm install."""
    return "node_modules" in Path(__file__).resolve().parts


def _sync_workspace_hooks() -> bool:
    """Bring the installed workspace's hook registration in line with this launch.

    Any launch that is neither plugin nor npm writes nothing. That includes
    the workspace-registered copy an earlier plugin version left behind,
    which runs out of the plugin cache through the ``.claude/hooks`` link
    without ``CLAUDE_PLUGIN_ROOT``: merging from there would undo the
    plugin's cleanup on every event. So does a cwd inside a managed
    worktree, which has no installed workspace. Returns True if settings changed.
    """
    from gaia.install_root import InsideManagedWorktree, installed_root

    try:
        workspace = installed_root()
    except InsideManagedWorktree:
        return False
    channel = resolve_hook_channel(workspace, npm_copy=_installed_under_node_modules())
    if channel is None:
        return False
    hooks_json_path = Path(__file__).resolve().parents[2] / "hooks.json"
    action, details = sync_workspace_hooks(workspace, channel, hooks_json_path)
    if action == "error":
        logger.warning("Hook registration skipped: %s", details)
    return action == "updated"


def run_first_time_setup() -> str | None:
    """Ensure the registry, permissions and hooks exist; a reload message if any were written."""
    with recorded_in_manifest():
        ensure_plugin_registry()
        reload_needed = setup_project_permissions()
        hooks_changed = _sync_workspace_hooks()
    if reload_needed or hooks_changed:
        return "Permissions updated. Run /reload-plugins to activate."
    return None
