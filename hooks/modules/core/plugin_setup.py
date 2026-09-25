"""First-time plugin setup for SessionStart hook.

Detects first run via marker file in CLAUDE_PLUGIN_DATA.
On first run, merges gaia permissions into .claude/settings.local.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .paths import get_plugin_data_dir

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


def is_first_run() -> bool:
    """Check if this is the first time the plugin runs."""
    marker = get_plugin_data_dir() / MARKER_FILE
    return not marker.exists()


def mark_initialized() -> None:
    """Mark the plugin as initialized."""
    marker = get_plugin_data_dir() / MARKER_FILE
    marker.write_text(json.dumps({
        "initialized_at": datetime.now().isoformat(),
        "mode": "gaia",
    }))
    logger.info("Plugin marked as initialized: %s", marker)


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

    Returns True if settings were modified (reload needed).
    """
    claude_dir = Path.cwd() / ".claude"
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

    if current_allow == set(merged_allow) and current_deny == set(merged_deny):
        logger.info("Project permissions already include gaia rules, skipping")
        return False

    # Update only permissions, preserve everything else (enabledPlugins, etc.)
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


def setup_project_hooks() -> bool:
    """Merge hooks from hooks.json into .claude/settings.local.json.

    In npm mode, Claude Code reads hooks from settings files, not hooks.json.
    This resolves hook script paths to absolute paths (via .claude/hooks symlink)
    so hooks work regardless of CWD at execution time.

    Returns True if settings were modified.
    """
    import re

    claude_dir = Path.cwd() / ".claude"
    settings_path = claude_dir / "settings.local.json"

    # Find hooks.json — try package root (npm) or plugin root
    hooks_json_path = None
    # Strategy 1: relative to this module (npm layout)
    module_dir = Path(__file__).resolve().parent.parent.parent
    candidate = module_dir / "hooks.json"
    if candidate.is_file():
        hooks_json_path = candidate
    else:
        # Strategy 2: .claude/hooks/hooks.json (symlinked)
        candidate2 = claude_dir / "hooks" / "hooks.json"
        if candidate2.is_file():
            hooks_json_path = candidate2

    if not hooks_json_path:
        logger.info("hooks.json not found, skipping hooks merge")
        return False

    try:
        hooks_data = json.loads(hooks_json_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("hooks.json is invalid, skipping hooks merge")
        return False

    # Unwrap outer "hooks" key if present
    source_hooks = hooks_data.get("hooks", hooks_data)

    # Absolute path to the hooks directory so hooks work regardless of CWD at
    # execution time (Stop/PostCompact hooks may run from unknown CWD).
    #
    # CRITICAL: resolve the .claude PARENT to an absolute path, but do NOT
    # follow the `hooks` symlink itself -- `.claude/hooks` is the STABLE
    # indirection that install repoints on every run. Following it (the old
    # `hooks_dir.resolve()`) baked the symlink's current target -- under
    # `gaia dev` the pnpm content-addressed store path whose hash segment
    # changes on every content change and whose old dir is pruned -- into
    # settings.local.json, so a resumed session pinned a path that no longer
    # existed. This mirrors the same fix in cli/_install_helpers.merge_local_hooks.
    hooks_dir = claude_dir / "hooks"
    if hooks_dir.exists():
        hooks_abs = str(claude_dir.resolve() / "hooks")
    else:
        # Fallback: use relative .claude/hooks/ if symlink not yet created
        hooks_abs = str(claude_dir / "hooks")

    def convert_command(cmd: str) -> str:
        return re.sub(r'\$\{CLAUDE_PLUGIN_ROOT\}/hooks/', f'{hooks_abs}/', cmd)

    converted_hooks: dict = {}
    for event, entries in source_hooks.items():
        converted_hooks[event] = []
        for entry in entries:
            new_entry = dict(entry)
            if "hooks" in new_entry:
                new_entry["hooks"] = [
                    {**h, "command": convert_command(h["command"])} if "command" in h else h
                    for h in new_entry["hooks"]
                ]
            converted_hooks[event].append(new_entry)

    # Load existing settings.local.json
    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Replace hooks entirely — gaia is the sole author of hook entries.
    # Previous merge-append logic caused duplicates when setup ran twice
    # per session (session_start + user_prompt_submit).
    existing_hooks = existing.get("hooks", {})
    if existing_hooks == converted_hooks:
        logger.info("settings.local.json hooks already up to date")
        return False

    existing["hooks"] = converted_hooks
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info("Merged hooks into %s", settings_path)
    return True


_GAIA_HOOK_SCRIPT_RE = re.compile(
    r"(?:\.claude|\$\{CLAUDE_PLUGIN_ROOT\})/hooks/([A-Za-z0-9_]+\.py)\b"
)


def _gaia_hook_entrypoints() -> frozenset[str]:
    """File names of the hook entrypoints this package ships (``hooks/*.py``)."""
    hooks_dir = Path(__file__).resolve().parents[2]
    return frozenset(p.name for p in hooks_dir.glob("*.py"))


def _is_gaia_hook_command(command: object, entrypoints: frozenset[str]) -> bool:
    """True when *command* runs one of Gaia's own hook entrypoints.

    Both shapes a Gaia writer ever left in workspace settings qualify: the
    rewritten ``<workspace>/.claude/hooks/<entrypoint>.py`` and the raw
    ``${CLAUDE_PLUGIN_ROOT}/hooks/<entrypoint>.py``. The entrypoint name must
    be one this package ships, so a user script that merely lives in a
    ``.claude/hooks`` directory is never matched.
    """
    if not isinstance(command, str):
        return False
    match = _GAIA_HOOK_SCRIPT_RE.search(command.replace("\\", "/"))
    return match is not None and match.group(1) in entrypoints


def remove_merged_gaia_hooks() -> bool:
    """Strip Gaia hook entries from the workspace ``settings.local.json``.

    A plugin install registers its hooks through the plugin's own hooks.json;
    any Gaia entry also present in workspace settings makes every hook fire
    twice. Removes only commands :func:`_is_gaia_hook_command` recognises,
    prunes the matcher entries and events that end up empty, and drops the
    ``hooks`` key only when nothing is left in it. User entries are kept as
    they were. Returns True if the file was rewritten.
    """
    settings_path = Path.cwd() / ".claude" / "settings.local.json"
    if not settings_path.is_file():
        return False
    try:
        settings = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("settings.local.json is unreadable, skipping hook cleanup")
        return False
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return False

    entrypoints = _gaia_hook_entrypoints()
    kept_hooks: dict = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            kept_hooks[event] = entries
            continue
        kept_entries = []
        for entry in entries:
            handlers = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(handlers, list):
                kept_entries.append(entry)
                continue
            kept_handlers = [
                h for h in handlers
                if not _is_gaia_hook_command(
                    h.get("command") if isinstance(h, dict) else None, entrypoints
                )
            ]
            if kept_handlers:
                kept_entries.append({**entry, "hooks": kept_handlers})
        if kept_entries:
            kept_hooks[event] = kept_entries

    if kept_hooks == hooks:
        return False
    if kept_hooks:
        settings["hooks"] = kept_hooks
    else:
        del settings["hooks"]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    logger.info("Removed Gaia hook entries merged into %s", settings_path)
    return True


def _installed_under_node_modules() -> bool:
    """True when this copy's real path is inside an npm or pnpm install."""
    return "node_modules" in Path(__file__).resolve().parts


def _sync_workspace_hooks() -> bool:
    """Bring workspace hook registration in line with how this copy was launched.

    A plugin launch (``CLAUDE_PLUGIN_ROOT`` set) owns its hooks through the
    plugin's hooks.json, so workspace entries are stripped. An npm/pnpm copy
    is read by Claude Code from settings files, so hooks.json is merged there.
    Any other launch writes nothing. That includes the workspace-registered
    copy an earlier plugin version left behind, which runs out of the plugin
    cache through the ``.claude/hooks`` link without ``CLAUDE_PLUGIN_ROOT``:
    merging from there would undo the plugin's cleanup on every event.
    """
    if os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip():
        return remove_merged_gaia_hooks()
    if _installed_under_node_modules():
        return setup_project_hooks()
    return False


def run_first_time_setup(mark_done: bool = True) -> str | None:
    """Run setup. Returns a reload message if permissions were written.

    Args:
        mark_done: If True, mark the plugin as initialized after setup.
                   Set to False when the caller wants to defer marking
                   (e.g., UserPromptSubmit marks after showing the welcome).
    """
    # Always ensure registry, permissions, and hooks exist (even on subsequent runs)
    ensure_plugin_registry()
    reload_needed = setup_project_permissions()
    hooks_changed = _sync_workspace_hooks()
    reload_needed = reload_needed or hooks_changed

    if not is_first_run():
        if reload_needed:
            return "Permissions updated. Run /reload-plugins to activate."
        return None

    if mark_done:
        mark_initialized()

    if reload_needed:
        return "GAIA setup complete. Run /reload-plugins to activate permissions."

    return None
