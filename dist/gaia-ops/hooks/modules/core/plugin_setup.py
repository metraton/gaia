"""First-time plugin setup for SessionStart hook.

Detects first run via marker file in CLAUDE_PLUGIN_DATA.
On first run, creates .claude/settings.json in the project with base permissions.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from .paths import get_plugin_data_dir
from .plugin_mode import get_plugin_mode

logger = logging.getLogger(__name__)

MARKER_FILE = ".plugin-initialized"

# Base permissions for security-only mode
SECURITY_PERMISSIONS = {
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
            "TodoWrite",
            "WebFetch",
            "WebSearch",
            "NotebookEdit",
        ],
        "deny": [],
        "ask": [],
    }
}

# Extended permissions for ops mode (adds agent dispatch tools)
OPS_PERMISSIONS = {
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
            "Edit(/tmp/*)",
            "Write(/tmp/*)",
        ],
        "deny": [],
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
        "mode": get_plugin_mode(),
    }))
    logger.info("Plugin marked as initialized: %s", marker)


def setup_project_permissions() -> bool:
    """Ensure .claude/settings.json has gaia permissions merged in.

    Merges our allow/deny rules into existing settings without
    overwriting user hooks or custom configuration.

    Returns True if settings were modified (restart needed).
    """
    claude_dir = Path.cwd() / ".claude"
    settings_path = claude_dir / "settings.json"

    mode = get_plugin_mode()
    our_perms = OPS_PERMISSIONS if mode == "ops" else SECURITY_PERMISSIONS
    our_allow = set(our_perms["permissions"]["allow"])
    our_deny = set(our_perms["permissions"].get("deny", []))

    # Load existing settings or start fresh
    existing = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Merge permissions — add ours without removing user's
    perms = existing.get("permissions", {})
    current_allow = set(perms.get("allow", []))
    current_deny = set(perms.get("deny", []))

    merged_allow = sorted(current_allow | our_allow)
    merged_deny = sorted(current_deny | our_deny)

    if current_allow == set(merged_allow) and current_deny == set(merged_deny):
        logger.info("Project permissions already include gaia rules, skipping")
        return False

    # Update only permissions, preserve everything else (hooks, env, etc.)
    existing.setdefault("permissions", {})
    existing["permissions"]["allow"] = merged_allow
    existing["permissions"]["deny"] = merged_deny
    existing["permissions"].setdefault("ask", [])

    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info("Merged gaia %s permissions into %s", mode, settings_path)
    return True


def ensure_plugin_registry() -> None:
    """Create plugin-registry.json from CLAUDE_PLUGIN_ROOT if missing.

    In plugin mode, CLAUDE_PLUGIN_ROOT looks like:
      .../cache/marketplace/gaia-ops/4.4.0-rc.2
    We extract the plugin name and version from the path.
    """
    import os
    data_dir = get_plugin_data_dir()
    registry_path = data_dir / "plugin-registry.json"
    if registry_path.exists():
        return

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root:
        return

    parts = Path(plugin_root).parts
    if len(parts) < 2:
        return

    plugin_name = parts[-2]  # e.g. "gaia-ops" or "gaia-security"
    plugin_version = parts[-1]  # e.g. "4.4.0-rc.2"

    registry = {
        "installed": [{"name": plugin_name, "version": plugin_version}],
        "source": "plugin-mode",
    }
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")
    logger.info("Created plugin-registry.json: %s@%s", plugin_name, plugin_version)


def run_first_time_setup() -> str | None:
    """Run setup. Returns a log message if restart needed."""
    # Always ensure registry and permissions exist (even on subsequent runs)
    ensure_plugin_registry()
    restart_needed = setup_project_permissions()

    if not is_first_run():
        return None

    mark_initialized()

    if restart_needed:
        mode = get_plugin_mode()
        return f"gaia-{mode} setup complete. Settings created. Restart needed."

    return None
