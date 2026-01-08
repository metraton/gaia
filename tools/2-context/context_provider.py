#!/usr/bin/env python3
"""
Context Provider for Claude Agent System

Generates structured context payloads for agents based on:
1. Agent contracts (required sections)
2. Semantic enrichment (task-related sections)
3. Progressive disclosure (context level based on query complexity)
4. Historical episodes (relevant past operations)
5. Standards (security tiers, output format, etc.)

Usage:
    python3 context_provider.py <agent_name> [user_task] [--context-file PATH]
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

# Default paths
DEFAULT_CONTEXT_PATH = Path(".claude/project-context/project-context.json")


# ============================================================================
# PROGRESSIVE DISCLOSURE INTEGRATION
# ============================================================================

def get_progressive_disclosure_manager():
    """
    Lazy import of ProgressiveDisclosureManager to avoid circular imports.
    Returns None if module not available.
    """
    try:
        # Add conversation module to path
        conversation_path = Path(__file__).parent.parent / "conversation"
        if conversation_path.is_dir():
            sys.path.insert(0, str(conversation_path.parent))
        
        from conversation.progressive_disclosure import ProgressiveDisclosureManager
        return ProgressiveDisclosureManager()
    except ImportError as e:
        print(f"Warning: ProgressiveDisclosureManager not available: {e}", file=sys.stderr)
        return None


def analyze_query_for_context_level(user_task: str) -> Dict[str, Any]:
    """
    Analyze user query to determine appropriate context level.
    
    Uses ProgressiveDisclosureManager if available, otherwise returns default.
    
    Context Levels:
    - Level 1: Minimal (basic info: cluster, project_id)
    - Level 2: Standard (key facts, recent errors summary)
    - Level 3: Detailed (full errors, recent actions, analysis)
    - Level 4: Full (complete history, infrastructure details)
    
    Returns:
        Dict with level, intent flags, and entities
    """
    manager = get_progressive_disclosure_manager()
    
    if manager is None:
        # Default to Level 2 (Standard) when module not available
        return {
            "recommended_level": 2,
            "complexity_score": 3,
            "confidence": 0.5,
            "needs_debugging": False,
            "needs_detail": False,
            "is_simple": True,
            "action": "custom",
            "entities": {"namespaces": [], "resources": [], "services": []},
            "fallback": True
        }
    
    return manager.analyze_query_intent(user_task)


def filter_context_by_level(
    full_context: Dict[str, Any],
    level: int,
    agent_name: str
) -> Dict[str, Any]:
    """
    Filter context based on progressive disclosure level.
    
    Args:
        full_context: Complete contract context
        level: Context level (1-4)
        agent_name: Agent receiving context
    
    Returns:
        Filtered context appropriate for the level
    """
    if level >= 4:
        # Full context - return everything
        return full_context
    
    filtered = {}
    
    # Level 1: Always include project basics
    basic_sections = ["project_details"]
    for section in basic_sections:
        if section in full_context:
            if level == 1:
                # Only include essential fields
                filtered[section] = _extract_essential_fields(full_context[section])
            else:
                filtered[section] = full_context[section]
    
    # Level 2+: Include operational guidelines
    if level >= 2:
        if "operational_guidelines" in full_context:
            filtered["operational_guidelines"] = full_context["operational_guidelines"]
        
        # Include agent-specific sections
        agent_sections = _get_agent_primary_sections(agent_name)
        for section in agent_sections:
            if section in full_context:
                filtered[section] = full_context[section]
    
    # Level 3+: Include supporting sections
    if level >= 3:
        supporting_sections = [
            "infrastructure_topology",
            "application_services", 
            "monitoring_observability"
        ]
        for section in supporting_sections:
            if section in full_context and section not in filtered:
                filtered[section] = full_context[section]
    
    return filtered


def _extract_essential_fields(section: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only essential fields from a section for Level 1 context."""
    essential_keys = [
        "project_id", "project_name", "cluster", "region", 
        "environment", "cloud_provider", "namespace"
    ]
    
    result = {}
    for key in essential_keys:
        if key in section:
            result[key] = section[key]
    
    # Handle nested cluster/region
    if "cluster" in section and isinstance(section["cluster"], dict):
        result["cluster"] = {
            "name": section["cluster"].get("name"),
            "region": section["cluster"].get("region")
        }
    
    return result


def _get_agent_primary_sections(agent_name: str) -> List[str]:
    """Get primary context sections for each agent."""
    agent_sections = {
        "terraform-architect": ["terraform_infrastructure"],
        "gitops-operator": ["gitops_configuration", "cluster_details"],
        "cloud-troubleshooter": ["terraform_infrastructure", "gitops_configuration"],
        "cloud-troubleshooter": ["terraform_infrastructure", "gitops_configuration"],
        "devops-developer": ["application_architecture", "development_standards"],
        "speckit-planner": ["application_architecture"]
    }
    return agent_sections.get(agent_name, [])


# ============================================================================
# CONTRACTS DIRECTORY RESOLUTION
# ============================================================================

def get_contracts_dir():
    """Determines the correct contracts directory based on execution context."""
    # First try .claude/config (installed project)
    installed_path = Path(".claude/config")
    if installed_path.is_dir():
        return installed_path

    # Fallback to package location (for development/testing)
    script_dir = Path(__file__).parent.parent  # tools/ -> gaia-ops/
    package_path = script_dir / "config"
    if package_path.is_dir():
        return package_path

    return Path(".claude/config")


DEFAULT_CONTRACTS_DIR = get_contracts_dir()


# ============================================================================
# LEGACY AGENT CONTRACTS (fallback)
# ============================================================================

LEGACY_AGENT_CONTRACTS: Dict[str, List[str]] = {
    "terraform-architect": [
        "project_details",
        "terraform_infrastructure",
        "operational_guidelines",
    ],
    "gitops-operator": [
        "project_details",
        "gitops_configuration",
        "infrastructure_topology",
        "cluster_details",
        "operational_guidelines",
    ],
    "cloud-troubleshooter": [
        "project_details",
        "infrastructure_topology",
        "terraform_infrastructure",
        "gitops_configuration",
        "application_services",
        "monitoring_observability",
    ],
    "cloud-troubleshooter": [
        "project_details",
        "infrastructure_topology",
        "terraform_infrastructure",
        "gitops_configuration",
        "application_services",
        "monitoring_observability",
    ],
    "devops-developer": [
        "project_details",
        "application_architecture",
        "application_services",
        "development_standards",
        "operational_guidelines"
    ]
}


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
# STANDARDS PRE-LOADING SYSTEM
# ============================================================================

def get_standards_dir() -> Path:
    """Determines the correct standards directory based on execution context."""
    installed_path = Path(".claude/docs/standards")
    if installed_path.is_dir():
        return installed_path
    
    script_dir = Path(__file__).parent.parent
    package_path = script_dir.parent / "docs" / "standards"
    if package_path.is_dir():
        return package_path
    
    return Path(__file__).parent.parent.parent / "docs" / "standards"


ALWAYS_PRELOAD_STANDARDS = {
    "security_tiers": "security-tiers.md",
    "output_format": "output-format.md",
}

ON_DEMAND_STANDARDS = {
    "command_execution": {
        "file": "command-execution.md",
        "triggers": ["kubectl", "terraform", "terragrunt", "gcloud", "aws", "helm", "flux", 
                     "apply", "plan", "deploy", "create", "execute", "run", "bash", "command"]
    },
    "anti_patterns": {
        "file": "anti-patterns.md",
        "triggers": ["create", "apply", "deploy", "delete", "destroy", "update", 
                     "modify", "change", "push", "build", "troubleshoot", "fix", "debug"]
    }
}


def read_standard_file(filename: str, standards_dir: Optional[Path] = None) -> Optional[str]:
    """Reads a standard file from the standards directory."""
    if standards_dir is None:
        standards_dir = get_standards_dir()
    
    file_path = standards_dir / filename
    
    if not file_path.is_file():
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not read standard file {filename}: {e}", file=sys.stderr)
        return None


def should_preload_standard(standard_config: Dict[str, Any], task: str) -> bool:
    """Determines if an on-demand standard should be pre-loaded based on task keywords."""
    task_lower = task.lower()
    triggers = standard_config.get("triggers", [])
    return any(trigger in task_lower for trigger in triggers)


def build_standards_context(task: str) -> Dict[str, Any]:
    """Builds the standards context for a task using hybrid pre-loading strategy."""
    standards_dir = get_standards_dir()
    standards_content = {}
    preloaded_list = []
    
    # Always load critical standards
    for name, filename in ALWAYS_PRELOAD_STANDARDS.items():
        content = read_standard_file(filename, standards_dir)
        if content:
            standards_content[name] = content
            preloaded_list.append(name)
    
    # Load on-demand standards based on task
    for name, config in ON_DEMAND_STANDARDS.items():
        if should_preload_standard(config, task):
            content = read_standard_file(config["file"], standards_dir)
            if content:
                standards_content[name] = content
                preloaded_list.append(name)
    
    if preloaded_list:
        print(f"Pre-loaded standards: {', '.join(preloaded_list)}", file=sys.stderr)
    
    return {
        "content": standards_content,
        "preloaded": preloaded_list,
        "total_standards": len(standards_content)
    }


# ============================================================================
# CLOUD PROVIDER DETECTION
# ============================================================================

def detect_cloud_provider(project_context: Dict[str, Any]) -> str:
    """Detects the cloud provider from project-context.json."""
    metadata = project_context.get("metadata", {})
    if "cloud_provider" in metadata:
        provider = metadata["cloud_provider"].lower()
        if provider == "multi-cloud":
            print("Multi-cloud detected, using GCP contracts as primary", file=sys.stderr)
            return "gcp"
        return provider

    sections = project_context.get("sections", {})
    project_details = sections.get("project_details", {})
    if "cloud_provider" in project_details:
        provider = project_details["cloud_provider"].lower()
        if provider == "multi-cloud":
            return "gcp"
        return provider

    if "account_id" in project_details or "aws_account" in project_details:
        return "aws"

    if "project_id" in project_details or "project_id" in metadata:
        return "gcp"

    print("Could not detect cloud provider, defaulting to GCP", file=sys.stderr)
    return "gcp"


def load_provider_contracts(cloud_provider: str, contracts_dir: Path = DEFAULT_CONTRACTS_DIR) -> Dict[str, Any]:
    """Loads provider-specific context contracts from JSON file."""
    contract_file = contracts_dir / f"context-contracts.{cloud_provider}.json"

    if not contract_file.is_file():
        print(f"Contract file not found: {contract_file}, using legacy contracts", file=sys.stderr)
        return {"agents": {name: {"required": fields} for name, fields in LEGACY_AGENT_CONTRACTS.items()}}

    try:
        with open(contract_file, 'r', encoding='utf-8') as f:
            contracts = json.load(f)
            print(f"Loaded {cloud_provider.upper()} contracts from {contract_file}", file=sys.stderr)
            return contracts
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {contract_file}: {e}", file=sys.stderr)
        sys.exit(1)


def load_project_context(context_path: Path) -> Dict[str, Any]:
    """Loads the project context from the specified JSON file."""
    if not context_path.is_file():
        print(f"Error: Context file not found at {context_path}", file=sys.stderr)
        sys.exit(1)
    with open(context_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================================
# PATH RESOLUTION
# ============================================================================

def get_project_root() -> Path:
    """Finds the project root by looking for CLAUDE.md."""
    current = Path.cwd()
    while current != current.parent:
        claude_md = current / "CLAUDE.md"
        if claude_md.is_file():
            return current
        current = current.parent
    return Path.cwd()


def resolve_path(relative_path: str, project_root: Optional[Path] = None) -> Path:
    """Resolves a path to absolute."""
    if project_root is None:
        project_root = get_project_root()
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def validate_project_paths(project_context: Dict[str, Any], auto_create: bool = True) -> List[str]:
    """Validates that all critical paths in project-context.json exist."""
    warnings = []
    project_root = get_project_root()
    paths = project_context.get("paths", {})
    
    if not paths:
        paths = {
            "gitops": project_context.get("gitops_configuration", {}).get("repository", {}).get("path"),
            "terraform": project_context.get("terraform_infrastructure", {}).get("layout", {}).get("base_path"),
            "app_services": project_context.get("application_services", {}).get("base_path")
        }
        paths = {k: v for k, v in paths.items() if v}

    for path_name, path_value in paths.items():
        if not path_value:
            continue
        abs_path = resolve_path(path_value, project_root)
        if not abs_path.exists():
            if auto_create:
                try:
                    abs_path.mkdir(parents=True, exist_ok=True)
                    msg = f"Created missing directory: {path_name} at {abs_path}"
                    warnings.append(msg)
                except Exception as e:
                    msg = f"Failed to create {path_name} at {abs_path}: {e}"
                    warnings.append(msg)

    return warnings


# ============================================================================
# CONTEXT EXTRACTION
# ============================================================================

def get_contract_context(
    project_context: Dict[str, Any],
    agent_name: str,
    provider_contracts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Extracts the contract-defined context for a given agent."""
    if provider_contracts and "agents" in provider_contracts:
        agent_contract = provider_contracts["agents"].get(agent_name)
        if not agent_contract:
            print(f"ERROR: Invalid agent '{agent_name}'.", file=sys.stderr)
            sys.exit(1)
        contract_keys = agent_contract.get("required", [])
    else:
        contract_keys = LEGACY_AGENT_CONTRACTS.get(agent_name)
        if not contract_keys:
            print(f"ERROR: Invalid agent '{agent_name}'.", file=sys.stderr)
            sys.exit(1)

    sections = project_context.get("sections", {})
    if not sections:
        raise KeyError("project-context.json must contain a 'sections' object.")

    section_keys = set()
    for key in contract_keys:
        section_name = key.split('.')[0]
        section_keys.add(section_name)

    return {key: sections[key] for key in section_keys if key in sections}


def get_semantic_enrichment(
    project_context: Dict[str, Any], 
    contract_keys: List[str], 
    user_task: str
) -> Dict[str, Any]:
    """Performs semantic analysis to find additional relevant context."""
    sections = project_context.get("sections", {})
    if not sections:
        raise KeyError("project-context.json must contain a 'sections' object.")
    
    enrichment: Dict[str, Any] = {}
    contract_key_set = set(contract_keys)
    potential_keys = set(sections.keys()) - contract_key_set
    task_words = {word.strip(".,:;!?") for word in user_task.lower().split()}

    for key in potential_keys:
        normalized_key = key.lower()
        if normalized_key in task_words:
            enrichment[key] = sections[key]

    metadata = project_context.get("metadata")
    if metadata and any(word in {"metadata", "version", "updated"} for word in task_words):
        enrichment.setdefault("metadata", metadata)

    return enrichment


# ============================================================================
# EPISODIC MEMORY
# ============================================================================

def load_relevant_episodes(user_task: str, max_episodes: int = 2) -> Dict[str, Any]:
    """Load relevant historical episodes for the user's task."""
    try:
        memory_paths = [
            Path(".claude/project-context/episodic-memory"),
            Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")
        ]

        index_file = None
        for memory_dir in memory_paths:
            candidate = memory_dir / "index.json"
            if candidate.exists():
                index_file = candidate
                break

        if not index_file:
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
                    except:
                        continue
    except:
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
    parser.add_argument(
        "--no-standards",
        action="store_true",
        help="Disable standards pre-loading"
    )
    parser.add_argument(
        "--full-context",
        action="store_true",
        help="Force full context (Level 4) regardless of query complexity"
    )

    args = parser.parse_args()

    # Load project context
    project_context = load_project_context(args.context_file)

    # Detect cloud provider and load contracts
    cloud_provider = detect_cloud_provider(project_context)
    provider_contracts = load_provider_contracts(cloud_provider)

    # Validate paths
    validate_project_paths(project_context, auto_create=True)

    # Analyze query for context level (Progressive Disclosure)
    query_analysis = analyze_query_for_context_level(args.user_task)
    context_level = 4 if args.full_context else query_analysis.get("recommended_level", 2)
    
    print(f"Context Level: {context_level} (complexity: {query_analysis.get('complexity_score', 'N/A')})", file=sys.stderr)

    # Get full contract context
    full_contract_context = get_contract_context(project_context, args.agent_name, provider_contracts)
    
    # Filter context by level
    contract_context = filter_context_by_level(full_contract_context, context_level, args.agent_name)

    # Get semantic enrichment
    enrichment_context = get_semantic_enrichment(
        project_context,
        list(contract_context.keys()),
        args.user_task
    )

    # Load historical episodes
    historical_context = load_relevant_episodes(args.user_task)

    # Build standards context
    if not args.no_standards:
        standards_context = build_standards_context(args.user_task)
    else:
        standards_context = {"content": {}, "preloaded": [], "total_standards": 0}

    # Load universal rules
    rules_context = load_universal_rules(args.agent_name)

    # Build final payload
    final_payload = {
        "contract": contract_context,
        "enrichment": enrichment_context,
        "rules": rules_context,
        "metadata": {
            "cloud_provider": cloud_provider,
            "contract_version": provider_contracts.get("version", "unknown"),
            "context_level": context_level,
            "query_complexity": query_analysis.get("complexity_score", 0),
            "historical_episodes_count": len(historical_context.get("episodes", [])),
            "standards_preloaded": standards_context["preloaded"],
            "standards_count": standards_context["total_standards"],
            "rules_count": len(rules_context.get("universal", [])) + len(rules_context.get("agent_specific", []))
        }
    }

    # Add progressive disclosure metadata
    final_payload["progressive_disclosure"] = {
        "level": context_level,
        "needs_debugging": query_analysis.get("needs_debugging", False),
        "needs_detail": query_analysis.get("needs_detail", False),
        "action": query_analysis.get("action", "custom"),
        "entities": query_analysis.get("entities", {})
    }

    # Add historical context if episodes found
    if historical_context:
        final_payload["historical_context"] = historical_context

    # Add standards context if any were loaded
    if standards_context["content"]:
        final_payload["standards"] = standards_context["content"]

    print(json.dumps(final_payload, indent=2))


if __name__ == "__main__":
    main()
