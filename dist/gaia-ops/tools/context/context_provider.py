#!/usr/bin/env python3
"""
Context Provider for Claude Agent System

Generates structured context payloads for agents based on:
1. Agent contracts (context-contracts.json + cloud overlays)
2. Universal rules (universal-rules.json)
3. Historical episodes (episodic memory)

Usage:
    python3 context_provider.py <agent_name> [user_task] [--context-file PATH]
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    from ._paths import resolve_config_dir
    from .surface_router import (
        build_investigation_brief,
        classify_surfaces,
        load_surface_routing_config,
    )
except ImportError:
    from _paths import resolve_config_dir
    from surface_router import (
        build_investigation_brief,
        classify_surfaces,
        load_surface_routing_config,
    )

# Default paths
DEFAULT_CONTEXT_PATH = Path(".claude/project-context/project-context.json")


# ============================================================================
# CONTRACTS DIRECTORY RESOLUTION
# ============================================================================

def get_contracts_dir():
    """Determines the correct contracts directory based on execution context."""
    return resolve_config_dir()


DEFAULT_CONTRACTS_DIR = get_contracts_dir()


# ============================================================================
# UNIVERSAL RULES SYSTEM
# ============================================================================

DEFAULT_RULES_FILE = "universal-rules.json"


def load_universal_rules(agent_name: str, rules_file: Optional[Path] = None) -> Dict[str, Any]:
    """Load universal rules and agent-specific rules from JSON file."""
    if rules_file is None:
        rules_file = get_contracts_dir() / DEFAULT_RULES_FILE

    if not rules_file.is_file():
        print(f"Warning: Rules file not found: {rules_file}", file=sys.stderr)
        return {"universal": [], "agent_specific": []}

    try:
        with open(rules_file, 'r', encoding='utf-8') as f:
            rules_data = json.load(f)

        universal = [r["rule"] for r in rules_data.get("rules", {}).get("universal", [])]
        agent_specific = [
            r["rule"]
            for r in rules_data.get("rules", {}).get("agent_specific", {}).get(agent_name, [])
        ]

        total_rules = len(universal) + len(agent_specific)
        if total_rules > 0:
            print(f"Loaded {len(universal)} universal rules, {len(agent_specific)} agent-specific", file=sys.stderr)

        return {
            "universal": universal,
            "agent_specific": agent_specific
        }
    except Exception as e:
        print(f"Warning: Could not load rules: {e}", file=sys.stderr)
        return {"universal": [], "agent_specific": []}


# ============================================================================
# CLOUD PROVIDER DETECTION
# ============================================================================

def detect_cloud_provider(project_context: Dict[str, Any]) -> str:
    """Detects the cloud provider from project-context.json.

    Detection priority:
      1. metadata.cloud_provider (explicit user/scanner setting)
      2. infrastructure.cloud_providers[0].name (v2 scanner section)
      3. metadata.project_id presence -> gcp
      4. Fallback -> gcp
    """
    metadata = project_context.get("metadata", {})
    if "cloud_provider" in metadata:
        provider = metadata["cloud_provider"].lower()
        if provider == "multi-cloud":
            print("Multi-cloud detected, using GCP contracts as primary", file=sys.stderr)
            return "gcp"
        return provider

    sections = project_context.get("sections", {})

    # v2: read from infrastructure.cloud_providers
    infra = sections.get("infrastructure", {})
    if isinstance(infra, dict):
        cloud_providers = infra.get("cloud_providers", [])
        if isinstance(cloud_providers, list) and cloud_providers:
            primary = cloud_providers[0]
            if isinstance(primary, dict):
                name = primary.get("name", "")
                if name:
                    provider = name.lower()
                    if provider == "multi-cloud":
                        return "gcp"
                    return provider

    if "project_id" in metadata:
        return "gcp"

    print("Could not detect cloud provider, defaulting to GCP", file=sys.stderr)
    return "gcp"


def load_provider_contracts(cloud_provider: str, contracts_dir: Path = DEFAULT_CONTRACTS_DIR) -> Dict[str, Any]:
    """
    Loads context contracts using the base+cloud merge strategy.

    Strategy:
    1. Load base contracts from context-contracts.json (cloud-agnostic)
    2. Load cloud overrides from cloud/{provider}.json and merge (extend) read/write lists
    3. If base contracts missing → error (contracts are the single source of truth)
    """
    base_file = contracts_dir / "context-contracts.json"
    cloud_file = contracts_dir / "cloud" / f"{cloud_provider}.json"

    # --- Step 1: Load base contracts ---
    if not base_file.is_file():
        print(f"Error: Contract file not found at {base_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(base_file, 'r', encoding='utf-8') as f:
            base_contracts = json.load(f)
        print(f"Loaded base contracts from {base_file}", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {base_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Step 2: Merge cloud-specific overrides ---
    if cloud_file.is_file():
        try:
            with open(cloud_file, 'r', encoding='utf-8') as f:
                cloud_overrides = json.load(f)
            print(f"Loaded {cloud_provider.upper()} cloud overrides from {cloud_file}", file=sys.stderr)

            for agent_name, agent_overrides in cloud_overrides.get("agents", {}).items():
                if agent_name in base_contracts.get("agents", {}):
                    existing_read = base_contracts["agents"][agent_name].get("read", [])
                    existing_write = base_contracts["agents"][agent_name].get("write", [])
                    extra_read = [s for s in agent_overrides.get("read", []) if s not in existing_read]
                    extra_write = [s for s in agent_overrides.get("write", []) if s not in existing_write]
                    base_contracts["agents"][agent_name]["read"] = existing_read + extra_read
                    base_contracts["agents"][agent_name]["write"] = existing_write + extra_write
                else:
                    base_contracts["agents"][agent_name] = agent_overrides

        except json.JSONDecodeError as e:
            print(f"Warning: Invalid JSON in {cloud_file}: {e} — skipping cloud overrides", file=sys.stderr)
    else:
        print(f"No cloud overrides found at {cloud_file}, using base contracts only", file=sys.stderr)

    return {
        "version": base_contracts.get("version", "unknown"),
        "provider": cloud_provider,
        "agents": base_contracts.get("agents", {})
    }


def load_project_context(context_path: Path) -> Dict[str, Any]:
    """Loads the project context from the specified JSON file."""
    if not context_path.is_file():
        print(f"Error: Context file not found at {context_path}", file=sys.stderr)
        sys.exit(1)
    with open(context_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================================
# CONTEXT EXTRACTION
# ============================================================================

def get_relevant_sections(
    sections: Dict[str, Any],
    contract_keys: List[str],
    surface_routing: Optional[Dict[str, Any]] = None,
    routing_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Filter sections by surface relevance, with fallback to all readable sections.

    Args:
        sections: All available sections from project-context.json.
        contract_keys: The agent's permitted read keys (from context-contracts).
        surface_routing: The routing result from classify_surfaces().
        routing_config: The full surface-routing.json config (has contract_sections per surface).

    Returns:
        Filtered dict of sections. Falls back to all readable sections when:
        - No surface_routing or routing_config provided
        - No active surfaces detected
        - Surface has no contract_sections defined
        - Intersection of surface sections and agent permissions is empty
    """
    all_readable = {k: sections[k] for k in contract_keys if k in sections}

    if not surface_routing or not routing_config:
        return all_readable

    active_surfaces = surface_routing.get("active_surfaces", [])
    if not active_surfaces:
        return all_readable

    surfaces_cfg = routing_config.get("surfaces", {})

    # Collect relevant sections from all active surfaces
    relevant: set = set()
    for surface in active_surfaces:
        surface_config = surfaces_cfg.get(surface, {})
        surface_sections = surface_config.get("contract_sections", [])
        relevant.update(surface_sections)

    if not relevant:
        # Surfaces have no contract_sections defined -- inject all (fallback)
        return all_readable

    # Filter: agent permissions AND surface relevance
    filtered = {k: sections[k] for k in contract_keys if k in sections and k in relevant}

    if not filtered:
        # Nothing matched -- inject all (fallback)
        return all_readable

    omitted = set(all_readable.keys()) - set(filtered.keys())
    if omitted:
        print(
            f"Surface gating: {len(filtered)} sections injected, "
            f"{len(omitted)} omitted ({', '.join(sorted(omitted))})",
            file=sys.stderr,
        )
    else:
        print(
            f"Surface gating: all {len(filtered)} readable sections match active surfaces",
            file=sys.stderr,
        )

    return filtered


def get_contract_context(
    project_context: Dict[str, Any],
    agent_name: str,
    provider_contracts: Dict[str, Any],
    surface_routing: Optional[Dict[str, Any]] = None,
    routing_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extracts the contract-defined context sections for a given agent.

    When surface_routing and routing_config are provided, sections are filtered
    to only those relevant to the active surface(s).  Falls back to returning
    all readable sections when routing is unavailable or yields an empty set.
    """
    agent_contract = provider_contracts.get("agents", {}).get(agent_name)
    if not agent_contract:
        print(f"ERROR: Invalid agent '{agent_name}'. Available: {list(provider_contracts.get('agents', {}).keys())}", file=sys.stderr)
        sys.exit(1)

    contract_keys = agent_contract.get("read", [])

    sections = project_context.get("sections", {})
    if not sections:
        raise KeyError("project-context.json must contain a 'sections' object.")

    return get_relevant_sections(
        sections, contract_keys,
        surface_routing=surface_routing,
        routing_config=routing_config,
    )


def get_context_update_contract(
    agent_name: str,
    provider_contracts: Dict[str, Any]
) -> Dict[str, Any]:
    """Return the SSOT contract agents should use for CONTEXT_UPDATE decisions."""
    agent_contract = provider_contracts.get("agents", {}).get(agent_name)
    if not agent_contract:
        print(f"ERROR: Invalid agent '{agent_name}'. Available: {list(provider_contracts.get('agents', {}).keys())}", file=sys.stderr)
        sys.exit(1)

    return {
        "readable_sections": agent_contract.get("read", []),
        "writable_sections": agent_contract.get("write", []),
        "source": "config/context-contracts.json + config/cloud/{provider}.json",
    }


# ============================================================================
# EPISODIC MEMORY
# ============================================================================

def load_relevant_episodes(user_task: str, max_episodes: int = 2) -> Dict[str, Any]:
    """Load relevant historical episodes for the user's task."""
    try:
        index_file = Path(".claude/project-context/episodic-memory/index.json")
        if not index_file.exists():
            return {}

        with open(index_file) as f:
            index = json.load(f)

        task_lower = user_task.lower()
        task_words = set(task_lower.split())

        relevant_episodes = []
        for episode in index.get("episodes", []):
            score = 0.0
            for tag in episode.get("tags", []):
                if tag.lower() in task_lower:
                    score += 0.4
            title_words = set(episode.get("title", "").lower().split())
            common_words = task_words & title_words
            if common_words:
                score += 0.3 * (len(common_words) / max(len(title_words), 1))

            final_score = score * episode.get("relevance_score", 0.5)

            if final_score > 0.1:
                full_episode = load_full_episode(episode["id"], index_file.parent)
                if full_episode:
                    relevant_episodes.append({
                        "id": full_episode["id"],
                        "title": full_episode["title"],
                        "type": full_episode["type"],
                        "relevance": final_score,
                        "lessons_learned": full_episode.get("lessons_learned", [])[:2],
                        "resolution": full_episode.get("resolution", "")[:200]
                    })

        relevant_episodes.sort(key=lambda x: x["relevance"], reverse=True)
        relevant_episodes = relevant_episodes[:max_episodes]

        if relevant_episodes:
            print(f"Added {len(relevant_episodes)} historical episodes to context", file=sys.stderr)
            return {
                "episodes": relevant_episodes,
                "summary": f"Found {len(relevant_episodes)} relevant historical episodes"
            }

        return {}

    except Exception as e:
        print(f"Warning: Could not load episodic memory: {e}", file=sys.stderr)
        return {}


def load_full_episode(episode_id: str, memory_dir: Path) -> Optional[Dict[str, Any]]:
    """Load full episode details from JSONL file."""
    try:
        episodes_file = memory_dir / "episodes.jsonl"
        if episodes_file.exists():
            with open(episodes_file) as f:
                for line in f:
                    try:
                        episode = json.loads(line)
                        if episode.get("id") == episode_id:
                            return episode
                    except Exception:
                        continue
    except Exception:
        pass
    return None


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    """Main function to generate and print the context payload."""
    parser = argparse.ArgumentParser(
        description="Generates a structured context payload for a Claude agent."
    )
    parser.add_argument("agent_name", help="The name of the agent being invoked.")
    parser.add_argument("user_task", nargs="?", default="General inquiry",
                        help="The user's task or query for the agent.")
    parser.add_argument(
        "--context-file",
        type=Path,
        default=DEFAULT_CONTEXT_PATH,
        help=f"Path to the project-context.json file. Defaults to '{DEFAULT_CONTEXT_PATH}'"
    )

    args = parser.parse_args()

    # Load project context
    project_context = load_project_context(args.context_file)

    # Detect cloud provider and load contracts
    cloud_provider = detect_cloud_provider(project_context)
    provider_contracts = load_provider_contracts(cloud_provider)

    # Compute surface routing BEFORE extracting sections so we can gate by surface
    surface_routing_config = load_surface_routing_config()
    surface_routing = classify_surfaces(
        args.user_task,
        current_agent=args.agent_name,
        routing_config=surface_routing_config,
    )

    # Extract contracted sections (surface-gated when routing is available)
    contract_context = get_contract_context(
        project_context, args.agent_name, provider_contracts,
        surface_routing=surface_routing,
        routing_config=surface_routing_config,
    )
    context_update_contract = get_context_update_contract(args.agent_name, provider_contracts)

    # Load historical episodes
    historical_context = load_relevant_episodes(args.user_task)

    # Load universal rules
    rules_context = load_universal_rules(args.agent_name)
    investigation_brief = build_investigation_brief(
        args.user_task,
        args.agent_name,
        contract_context,
        routing_config=surface_routing_config,
        routing=surface_routing,
    )

    # Build final payload
    final_payload = {
        "project_knowledge": contract_context,
        "write_permissions": context_update_contract,
        "rules": rules_context,
        "surface_routing": surface_routing,
        "investigation_brief": investigation_brief,
        "metadata": {
            "cloud_provider": cloud_provider,
            "contract_version": provider_contracts.get("version", "unknown"),
            "historical_episodes_count": len(historical_context.get("episodes", [])),
            "rules_count": len(rules_context.get("universal", [])) + len(rules_context.get("agent_specific", [])),
            "surface_routing_version": surface_routing_config.get("version", "unknown"),
            "active_surfaces_count": len(surface_routing.get("active_surfaces", [])),
            "surface_routing_confidence": surface_routing.get("confidence", 0.0),
        }
    }

    # Add historical context if episodes found
    if historical_context:
        final_payload["historical_context"] = historical_context

    print(json.dumps(final_payload, indent=2))


if __name__ == "__main__":
    main()
