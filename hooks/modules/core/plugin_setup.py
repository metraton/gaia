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
    """Create .claude/settings.json in the project if it doesn't exist.

    Uses cwd (the project root) instead of find_claude_dir() which may
    resolve to a parent directory's .claude/.

    Returns True if settings were created (restart needed).
    """
    claude_dir = Path.cwd() / ".claude"
    settings_path = claude_dir / "settings.json"

    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
            if existing.get("permissions", {}).get("allow"):
                logger.info("Project settings already have permissions, skipping")
                return False
        except (json.JSONDecodeError, OSError):
            pass

    mode = get_plugin_mode()
    permissions = OPS_PERMISSIONS if mode == "ops" else SECURITY_PERMISSIONS

    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(permissions, indent=2) + "\n")
    logger.info("Created project settings.json with %s permissions at %s", mode, settings_path)
    return True


def run_first_time_setup() -> str | None:
    """Run first-time setup. Returns a log message if restart needed."""
    if not is_first_run():
        return None

    restart_needed = setup_project_permissions()
    mark_initialized()

    if restart_needed:
        mode = get_plugin_mode()
        return f"gaia-{mode} setup complete. Settings created. Restart needed."

    return None
