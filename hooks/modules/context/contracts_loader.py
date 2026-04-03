"""Load context contracts and merge agent permissions.

Subsystem 2 of the pre_tool_use Task/Agent path.

Loads context-contracts.json + cloud overlays, merges agent permissions,
finds empty writable sections, and builds a reminder string.

Cloud provider detection (formerly infrastructure_reader) is internal
to the contract loading process.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _detect_cloud_from_infrastructure(sections: dict) -> str:
    """Extract cloud provider name from v2 infrastructure.cloud_providers section.

    Args:
        sections: The sections dict from project-context.json.

    Returns:
        Cloud provider name string (e.g. aws, gcp) or empty string.
    """
    infra = sections.get("infrastructure", {})
    if isinstance(infra, dict):
        providers = infra.get("cloud_providers", [])
        if isinstance(providers, list) and providers:
            primary = providers[0]
            if isinstance(primary, dict):
                return primary.get("name", "")
    return ""


def build_context_update_reminder(
    subagent_type: str,
    project_agents: list,
    hooks_dir: Path = None,
) -> str:
    """Check which writable sections are empty and build a reminder.

    Reads the context contracts to find writable sections for this agent,
    then checks project-context.json to see which are empty.

    Args:
        subagent_type: The agent type string (e.g. developer).
        project_agents: List of valid project agent names.
        hooks_dir: Path to the hooks directory (for fallback config lookup).
            Defaults to Path(__file__).parent.parent.parent if None.

    Returns:
        Reminder string or empty string if no empty sections.
    """
    if subagent_type not in project_agents:
        return ""

    if hooks_dir is None:
        hooks_dir = Path(__file__).parent.parent.parent

    # Load contracts to find writable sections.
    # Strategy: load context-contracts.json (base) then merge cloud/{provider}.json.
    # Fallback to legacy per-provider files for backward compatibility.
    # We detect the cloud provider from project-context.json first.
    cloud_provider = "gcp"  # default
    pc_paths_for_provider = [
        Path(".claude/project-context/project-context.json"),
        Path("project-context.json"),
    ]
    for pp in pc_paths_for_provider:
        if pp.exists():
            try:
                pc_data = json.loads(pp.read_text())
                detected = (
                    pc_data.get("metadata", {}).get("cloud_provider", "")
                    or _detect_cloud_from_infrastructure(pc_data.get("sections", {}))
                )
                if detected:
                    cloud_provider = detected.lower()
                break
            except Exception:
                continue

    # Candidate config directories (installed project first, package fallback)
    config_dirs = [
        Path(".claude/config"),
        hooks_dir.parent / "config",
    ]

    writable = []
    for config_dir in config_dirs:
        if not config_dir.is_dir():
            continue
        # Load base contracts
        base_file = config_dir / "context-contracts.json"
        cloud_file = config_dir / "cloud" / f"{cloud_provider}.json"

        merged_agents = {}
        if base_file.exists():
            try:
                data = json.loads(base_file.read_text())
                merged_agents = data.get("agents", {})
            except Exception:
                pass

        # Merge cloud overrides
        if merged_agents and cloud_file.exists():
            try:
                cloud_data = json.loads(cloud_file.read_text())
                agent_cloud = cloud_data.get("agents", {}).get(subagent_type, {})
                base_write = merged_agents.get(subagent_type, {}).get("write", [])
                extra_write = [s for s in agent_cloud.get("write", []) if s not in base_write]
                if subagent_type in merged_agents:
                    merged_agents[subagent_type]["write"] = base_write + extra_write
            except Exception:
                pass

        if merged_agents:
            agent_perms = merged_agents.get(subagent_type, {})
            writable = agent_perms.get("write", [])
            if writable:
                break

    if not writable:
        return ""

    # Load project-context.json to find empty sections
    pc_paths = [
        Path(".claude/project-context/project-context.json"),
        Path("project-context.json"),
    ]

    sections = {}
    for pp in pc_paths:
        if pp.exists():
            try:
                pc = json.loads(pp.read_text())
                sections = pc.get("sections", {})
                break
            except Exception:
                continue

    # Find empty writable sections
    empty = []
    for section_name in writable:
        section_data = sections.get(section_name, {})
        if not section_data or section_data == {}:
            empty.append(section_name)

    if not empty:
        return ""

    empty_list = ", ".join(f"`{s}`" for s in empty)
    return (
        f"\n**CONTEXT_UPDATE REQUIRED:** Your writable sections {empty_list} "
        f"are currently EMPTY. After completing your task, you MUST emit a "
        f"CONTEXT_UPDATE block with any data you discovered. "
        f"See \"Context Updater Protocol\" above for the format.\n\n"
    )
