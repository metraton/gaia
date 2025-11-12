import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

# This script is expected to be run from the root of the repository.
# Project context is always stored at `.claude/project-context/project-context.json`.
DEFAULT_CONTEXT_PATH = Path(".claude/project-context/project-context.json")

# Path to provider-specific contract files
# When running from installed project: .claude/config (symlinked)
# When running from package: config/ (relative to script location)
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

    # Final fallback
    return Path(".claude/config")

DEFAULT_CONTRACTS_DIR = get_contracts_dir()

# Fallback contracts (legacy support - used if provider-specific contracts don't exist)
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
    "gcp-troubleshooter": [
        "project_details",
        "infrastructure_topology",
        "terraform_infrastructure",
        "gitops_configuration",
        "application_services",
        "monitoring_observability",
    ],
    "aws-troubleshooter": [
        "project_details",
        "infrastructure_topology",
        "terraform_infrastructure",
        "gitops_configuration",
        "application_services",
        "monitoring_observability",
    ],
    # devops-developer has a more generic contract, often needing the full view.
    "devops-developer": [
        "project_details",
        "application_architecture",
        "application_services",
        "development_standards",
        "operational_guidelines"
    ]
}

def detect_cloud_provider(project_context: Dict[str, Any]) -> str:
    """
    Detects the cloud provider from project-context.json.

    Order of detection:
    1. metadata.cloud_provider
    2. sections.project_details.cloud_provider
    3. Infer from field presence (project_id vs account_id)
    4. Default to 'gcp'
    """
    # Check metadata
    metadata = project_context.get("metadata", {})
    if "cloud_provider" in metadata:
        provider = metadata["cloud_provider"].lower()
        # Handle multi-cloud: default to gcp for now
        if provider == "multi-cloud":
            print("⚠️  Multi-cloud detected, using GCP contracts as primary", file=sys.stderr)
            return "gcp"
        return provider

    # Check project_details
    sections = project_context.get("sections", {})
    project_details = sections.get("project_details", {})
    if "cloud_provider" in project_details:
        provider = project_details["cloud_provider"].lower()
        if provider == "multi-cloud":
            print("⚠️  Multi-cloud detected, using GCP contracts as primary", file=sys.stderr)
            return "gcp"
        return provider

    # Infer from fields
    if "account_id" in project_details or "aws_account" in project_details:
        print("⚠️  Inferred AWS from account_id field", file=sys.stderr)
        return "aws"

    if "project_id" in project_details or "project_id" in metadata:
        print("⚠️  Inferred GCP from project_id field", file=sys.stderr)
        return "gcp"

    # Default
    print("⚠️  Could not detect cloud provider, defaulting to GCP", file=sys.stderr)
    return "gcp"

def load_provider_contracts(cloud_provider: str, contracts_dir: Path = DEFAULT_CONTRACTS_DIR) -> Dict[str, Any]:
    """
    Loads provider-specific context contracts from JSON file.

    Args:
        cloud_provider: Provider name (gcp, aws, azure)
        contracts_dir: Directory containing contract files

    Returns:
        Dict with contract definitions for all agents

    Raises:
        FileNotFoundError: If contract file doesn't exist and no legacy fallback
    """
    contract_file = contracts_dir / f"context-contracts.{cloud_provider}.json"

    if not contract_file.is_file():
        print(f"⚠️  Contract file not found: {contract_file}", file=sys.stderr)
        print(f"⚠️  Falling back to legacy hardcoded contracts", file=sys.stderr)
        return {"agents": {name: {"required": fields} for name, fields in LEGACY_AGENT_CONTRACTS.items()}}

    try:
        with open(contract_file, 'r', encoding='utf-8') as f:
            contracts = json.load(f)
            print(f"✓ Loaded {cloud_provider.upper()} contracts from {contract_file}", file=sys.stderr)
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


def get_project_root() -> Path:
    """
    Finds the project root by looking for CLAUDE.md.
    This is the reference point for resolving relative paths.
    Similar to JAVA_HOME or GAIA_HOME concept.
    """
    current = Path.cwd()

    # Walk up the directory tree looking for CLAUDE.md
    while current != current.parent:
        claude_md = current / "CLAUDE.md"
        if claude_md.is_file():
            return current
        current = current.parent

    # If not found, assume current directory is project root
    print("Warning: CLAUDE.md not found, using current directory as project root", file=sys.stderr)
    return Path.cwd()


def resolve_path(relative_path: str, project_root: Optional[Path] = None) -> Path:
    """
    Resolves a path to absolute, handling both relative and absolute paths.

    Examples:
        ./gitops         → $PROJECT_ROOT/gitops
        ../shared-infra  → $PROJECT_ROOT/../shared-infra
        /abs/path        → /abs/path (unchanged)

    Args:
        relative_path: Path from project-context.json
        project_root: Project root (where CLAUDE.md is). If None, auto-detected.

    Returns:
        Absolute Path object
    """
    if project_root is None:
        project_root = get_project_root()

    path = Path(relative_path)

    # If already absolute, return as-is
    if path.is_absolute():
        return path

    # Resolve relative to project root
    return (project_root / path).resolve()


def validate_project_paths(project_context: Dict[str, Any], auto_create: bool = True) -> List[str]:
    """
    Validates that all critical paths in project-context.json exist.
    Optionally creates missing directories with a warning.

    Args:
        project_context: The loaded project context
        auto_create: If True, creates missing directories (default: True for agents)

    Returns:
        List of warning messages (empty if all OK)
    """
    warnings = []
    project_root = get_project_root()

    # Get paths section
    paths = project_context.get("paths", {})
    if not paths:
        # Fallback to old format (backward compatibility)
        paths = {
            "gitops": project_context.get("gitops_configuration", {}).get("repository", {}).get("path"),
            "terraform": project_context.get("terraform_infrastructure", {}).get("layout", {}).get("base_path"),
            "app_services": project_context.get("application_services", {}).get("base_path")
        }
        # Filter out None values
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
                    print(f"⚠️  {msg}", file=sys.stderr)
                    warnings.append(msg)
                except Exception as e:
                    msg = f"Failed to create {path_name} at {abs_path}: {e}"
                    print(f"❌ {msg}", file=sys.stderr)
                    warnings.append(msg)
            else:
                msg = f"Path does not exist: {path_name} = {path_value} (resolved to {abs_path})"
                print(f"⚠️  {msg}", file=sys.stderr)
                warnings.append(msg)

    return warnings


def get_contract_context(
    project_context: Dict[str, Any],
    agent_name: str,
    provider_contracts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extracts the contract-defined context for a given agent.

    Args:
        project_context: The loaded project context
        agent_name: Name of the agent
        provider_contracts: Provider-specific contracts (if None, uses legacy fallback)

    Returns:
        Dict with required contract sections
    """
    # Use provider-specific contracts if available
    if provider_contracts and "agents" in provider_contracts:
        agent_contract = provider_contracts["agents"].get(agent_name)
        if not agent_contract:
            print(
                f"ERROR: Invalid agent '{agent_name}'. Agent not found in provider contracts.",
                file=sys.stderr,
            )
            sys.exit(1)

        contract_keys = agent_contract.get("required", [])
    else:
        # Fallback to legacy hardcoded contracts
        contract_keys = LEGACY_AGENT_CONTRACTS.get(agent_name)
        if not contract_keys:
            print(
                f"ERROR: Invalid agent '{agent_name}'. Agent not recognized.",
                file=sys.stderr,
            )
            sys.exit(1)

    sections = project_context.get("sections", {})
    if not sections:
        raise KeyError("project-context.json must contain a 'sections' object.")

    # Extract only top-level section keys (e.g., "project_details" from "project_details.project_id")
    section_keys = set()
    for key in contract_keys:
        section_name = key.split('.')[0]
        section_keys.add(section_name)

    return {key: sections[key] for key in section_keys if key in sections}


def get_semantic_enrichment(
    project_context: Dict[str, Any], contract_keys: List[str], user_task: str
) -> Dict[str, Any]:
    """
    Performs semantic analysis to find additional relevant context.

    NOTE: This is a placeholder implementation. A real implementation would use
    vector embeddings (e.g., SentenceTransformers, OpenAI embeddings) to find
    sections of the project_context semantically similar to the user_task.

    For now, it performs a simple keyword match, excluding keys already in the contract.
    """
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

    # Light metadata hints when the user asks about freshness/versioning.
    metadata = project_context.get("metadata")
    if metadata and any(word in {"metadata", "version", "updated"} for word in task_words):
        enrichment.setdefault("metadata", metadata)

    # Heuristic: include matching services based on task wording.
    def normalize_services(raw: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if isinstance(raw, list):
            for svc in raw:
                if isinstance(svc, dict) and svc.get("name"):
                    normalized.append(svc)
        elif isinstance(raw, dict):
            for name, payload in raw.items():
                entry = {"name": name}
                if isinstance(payload, dict):
                    entry.update(payload)
                normalized.append(entry)
        return normalized

    candidate_services: List[Dict[str, Any]] = []
    candidate_services.extend(normalize_services(sections.get("application_services")))

    service_catalog = sections.get("service_catalog")
    if isinstance(service_catalog, dict):
        candidate_services.extend(
            normalize_services(service_catalog.get("applications"))
        )

    app_arch = sections.get("application_architecture")
    if isinstance(app_arch, dict):
        candidate_services.extend(normalize_services(app_arch.get("services")))

    if candidate_services and "application_services" not in contract_key_set:
        matched = [
            svc for svc in candidate_services
            if svc.get("name", "").lower() in user_task.lower()
        ]
        if matched:
            enrichment["application_services"] = matched

    return enrichment

def main():
    """Main function to generate and print the context payload."""
    parser = argparse.ArgumentParser(
        description="""
        Generates a structured context payload for a Claude agent based on its contract
        and a semantic analysis of the user's task.
        """
    )
    parser.add_argument("agent_name", help="The name of the agent being invoked.")
    parser.add_argument("user_task", nargs="?", default="General inquiry",
                        help="The user's task or query for the agent (default: 'General inquiry' if omitted).")
    parser.add_argument(
        "--context-file",
        type=Path,
        default=DEFAULT_CONTEXT_PATH,
        help=f"Path to the project-context.json file. Defaults to '{DEFAULT_CONTEXT_PATH}'"
    )

    args = parser.parse_args()

    # Load project context
    project_context = load_project_context(args.context_file)

    # Detect cloud provider and load provider-specific contracts
    cloud_provider = detect_cloud_provider(project_context)
    provider_contracts = load_provider_contracts(cloud_provider)

    # Validate project paths and auto-create missing directories
    validate_project_paths(project_context, auto_create=True)

    # Get contract context using provider-specific contracts
    contract_context = get_contract_context(project_context, args.agent_name, provider_contracts)

    # Get semantic enrichment
    enrichment_context = get_semantic_enrichment(
        project_context,
        list(contract_context.keys()),
        args.user_task
    )

    # Build final payload
    final_payload = {
        "contract": contract_context,
        "enrichment": enrichment_context,
        "metadata": {
            "cloud_provider": cloud_provider,
            "contract_version": provider_contracts.get("version", "unknown")
        }
    }

    print(json.dumps(final_payload, indent=2))

if __name__ == "__main__":
    main()
