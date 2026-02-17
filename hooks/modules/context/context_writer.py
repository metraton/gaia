"""
ContextWriter module for gaia-ops progressive context enrichment.

Parses CONTEXT_UPDATE blocks from agent output, validates write permissions
against contracts, and applies deep-merge updates to project-context.json.

Public API:
    - parse_context_update(agent_output) -> Optional[dict]
    - validate_permissions(update, agent_type, contracts) -> (dict, list)
    - apply_update(context_path, update, agent_type) -> dict
    - load_contracts(provider, config_dir) -> dict
    - process_agent_output(agent_output, task_info) -> dict
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dynamic import: deep_merge from tools/context/deep_merge.py
# ---------------------------------------------------------------------------
def _import_deep_merge():
    """Import deep_merge function from tools/context/deep_merge.py."""
    import importlib.util
    import sys

    # Resolve paths relative to this file:
    # this file:   hooks/modules/context/context_writer.py
    # deep_merge:  tools/context/deep_merge.py
    hooks_dir = Path(__file__).resolve().parents[2]  # hooks/
    repo_root = hooks_dir.parent                      # repo root
    deep_merge_path = repo_root / "tools" / "context" / "deep_merge.py"

    if not deep_merge_path.exists():
        raise ImportError(f"deep_merge.py not found at {deep_merge_path}")

    spec = importlib.util.spec_from_file_location("deep_merge", str(deep_merge_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["deep_merge"] = module
    spec.loader.exec_module(module)
    return module.deep_merge


_deep_merge = _import_deep_merge()


# ---------------------------------------------------------------------------
# LEGACY_AGENT_CONTRACTS (fallback when no contracts file exists)
# In legacy mode, write permissions = read permissions.
# ---------------------------------------------------------------------------
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
        "cluster_details",
    ],
    "devops-developer": [
        "project_details",
        "application_architecture",
        "application_services",
        "development_standards",
        "operational_guidelines",
    ],
}


# ---------------------------------------------------------------------------
# Module-level cache for load_contracts
# ---------------------------------------------------------------------------
_contracts_cache: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Known markers that terminate CONTEXT_UPDATE extraction
# ---------------------------------------------------------------------------
_KNOWN_MARKERS = re.compile(
    r"^(?:AGENT_STATUS|CONTEXT_UPDATE|<!-- AGENT_STATUS):",
    re.MULTILINE,
)


# ============================================================================
# 1. parse_context_update
# ============================================================================

def parse_context_update(agent_output: str) -> Optional[dict]:
    """Extract and parse a CONTEXT_UPDATE block from agent output.

    Searches for the ``CONTEXT_UPDATE:`` marker (case-sensitive, on its own
    line), extracts the JSON that follows until end-of-output or the next
    known marker, and returns the parsed dict.

    Returns None when:
    - No marker is found
    - The JSON is malformed
    - The parsed value is not a dict
    """
    # Find the CONTEXT_UPDATE: marker on its own line
    marker = "CONTEXT_UPDATE:"
    lines = agent_output.split("\n")

    marker_idx = None
    for i, line in enumerate(lines):
        if line.strip() == marker:
            marker_idx = i
            break

    if marker_idx is None:
        return None

    # Collect all text after the marker
    remaining = "\n".join(lines[marker_idx + 1:]).strip()

    if not remaining:
        return None

    # Strip markdown code fences — LLMs reading SKILL.md documentation
    # often wrap the JSON in ```json ... ``` or ``` ... ``` blocks.
    if remaining.startswith("```"):
        fence_lines = remaining.split("\n")
        # Remove opening fence (```json, ```JSON, ```, etc.)
        fence_lines.pop(0)
        # Remove closing fence if present
        for i in range(len(fence_lines) - 1, -1, -1):
            if fence_lines[i].strip() == "```":
                fence_lines.pop(i)
                break
        remaining = "\n".join(fence_lines).strip()

    if not remaining:
        return None

    # Use raw_decode to extract the first complete JSON value, ignoring
    # any trailing text (summaries, AGENT_STATUS blocks, etc.)
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(remaining)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Malformed JSON in CONTEXT_UPDATE block: %s", exc)
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


# ============================================================================
# 2. validate_permissions
# ============================================================================

def validate_permissions(
    update: dict,
    agent_type: str,
    contracts: dict,
) -> tuple:
    """Validate which sections the agent is allowed to write.

    Returns ``(allowed_updates, rejected_sections)`` where:
    - ``allowed_updates``: dict with only permitted sections
    - ``rejected_sections``: list of section names that were rejected
    """
    allowed: dict = {}
    rejected: List[str] = []

    # Determine writable sections for this agent
    agents_map = contracts.get("agents", {})

    if agent_type in agents_map:
        # Use explicit write list from contracts file
        writable = set(agents_map[agent_type].get("write", []))
    elif agent_type in LEGACY_AGENT_CONTRACTS:
        # Fallback: in legacy mode, write = read
        writable = set(LEGACY_AGENT_CONTRACTS[agent_type])
    else:
        # Unknown agent: no permissions
        writable = set()

    for section, data in update.items():
        if section in writable:
            allowed[section] = data
        else:
            rejected.append(section)

    return allowed, rejected


# ============================================================================
# 3. apply_update
# ============================================================================

def apply_update(
    context_path: Path,
    update: dict,
    agent_type: str,
) -> dict:
    """Apply a validated update to project-context.json using deep merge.

    Performs an atomic write (write to .tmp, then rename) and appends an
    audit entry to ``context-audit.jsonl`` in the same directory.

    Returns an audit entry dict. Never raises on I/O errors.
    """
    context_path = Path(context_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    sections_updated = list(update.keys())

    audit_entry = {
        "timestamp": timestamp,
        "agent": agent_type,
        "sections_updated": sections_updated,
        "changes": {},
        "success": False,
        "error": None,
    }

    try:
        # Read current context
        current = json.loads(context_path.read_text())

        # Deep merge each section
        all_changes = {}
        for section, section_data in update.items():
            current_section = current.get("sections", {}).get(section, {})
            merged_section, diff = _deep_merge(current_section, section_data)

            # Ensure sections dict exists
            if "sections" not in current:
                current["sections"] = {}
            current["sections"][section] = merged_section

            if diff:
                all_changes[section] = diff

        # Update metadata timestamp
        if "metadata" not in current:
            current["metadata"] = {}
        current["metadata"]["last_updated"] = timestamp

        # Atomic write: write to .tmp, then rename
        tmp_path = context_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(current, indent=2))
        tmp_path.rename(context_path)

        audit_entry["changes"] = all_changes
        audit_entry["success"] = True

    except Exception as exc:
        logger.error("Failed to apply context update: %s", exc)
        audit_entry["error"] = str(exc)
        audit_entry["success"] = False
        return audit_entry

    # Append audit entry to context-audit.jsonl
    try:
        audit_file = context_path.parent / "context-audit.jsonl"
        with open(audit_file, "a") as f:
            f.write(json.dumps(audit_entry) + "\n")
    except Exception as exc:
        logger.warning("Failed to write audit entry: %s", exc)
        # Audit write failure doesn't affect the main result

    return audit_entry


# ============================================================================
# 4. load_contracts
# ============================================================================

def load_contracts(provider: str, config_dir: Path) -> dict:
    """Load agent contracts from config file, with caching and legacy fallback.

    Attempts to load ``config/context-contracts.{provider}.json`` from
    *config_dir*. Falls back to generating a contract structure from
    ``LEGACY_AGENT_CONTRACTS`` if the file is missing or unreadable.

    Results are cached by provider string.
    """
    config_dir = Path(config_dir)

    # Check cache first
    if provider in _contracts_cache:
        return _contracts_cache[provider]

    contracts_path = config_dir / f"context-contracts.{provider}.json"

    if contracts_path.exists():
        try:
            contracts = json.loads(contracts_path.read_text())
            _contracts_cache[provider] = contracts
            return contracts
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to load contracts from %s: %s. Using legacy fallback.",
                contracts_path, exc,
            )

    # Generate fallback from LEGACY_AGENT_CONTRACTS (write = read)
    contracts = {
        "version": "legacy",
        "provider": provider,
        "agents": {
            agent: {"read": sections, "write": list(sections)}
            for agent, sections in LEGACY_AGENT_CONTRACTS.items()
        },
    }

    _contracts_cache[provider] = contracts
    return contracts


# ============================================================================
# 5. process_agent_output
# ============================================================================

def process_agent_output(agent_output: str, task_info: dict) -> dict:
    """Orchestrate the full context-update flow.

    Steps: parse -> detect provider -> load contracts -> validate -> apply.

    Parameters
    ----------
    agent_output : str
        Full agent output string.
    task_info : dict
        Must contain: ``agent_type``, ``context_path``, ``config_dir``.

    Returns
    -------
    dict
        ``{updated, sections/sections_updated, rejected, error}``
    """
    result = {
        "updated": False,
        "sections_updated": [],
        "rejected": [],
        "error": None,
    }

    # 1. Parse CONTEXT_UPDATE
    update = parse_context_update(agent_output)
    if update is None:
        return result

    agent_type = task_info.get("agent_type", "unknown")
    context_path = Path(task_info.get("context_path", ""))
    config_dir = Path(task_info.get("config_dir", ""))

    # 2. Detect cloud provider from existing context metadata
    provider = "gcp"  # default
    try:
        if context_path.exists():
            ctx = json.loads(context_path.read_text())
            provider = ctx.get("metadata", {}).get("cloud_provider", "gcp").lower()
    except Exception:
        pass

    # 3. Load contracts
    # Clear cache to avoid cross-test pollution (keyed by provider+config_dir)
    cache_key = f"{provider}:{config_dir}"
    contracts = _load_contracts_with_dir(provider, config_dir)

    # 4. Validate permissions
    allowed, rejected = validate_permissions(update, agent_type, contracts)
    result["rejected"] = rejected

    # 5. If nothing allowed, return early
    if not allowed:
        return result

    # 6. Apply update
    audit = apply_update(context_path, allowed, agent_type)

    if audit.get("success"):
        result["updated"] = True
        result["sections_updated"] = list(allowed.keys())
    else:
        result["error"] = audit.get("error")

    return result


def _load_contracts_with_dir(provider: str, config_dir: Path) -> dict:
    """Load contracts, bypassing the module-level cache for process_agent_output.

    This ensures each call with a different config_dir gets fresh results
    while still allowing load_contracts to cache for repeated calls with
    the same provider.
    """
    config_dir = Path(config_dir)
    contracts_path = config_dir / f"context-contracts.{provider}.json"

    if contracts_path.exists():
        try:
            return json.loads(contracts_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to legacy
    return {
        "version": "legacy",
        "provider": provider,
        "agents": {
            agent: {"read": sections, "write": list(sections)}
            for agent, sections in LEGACY_AGENT_CONTRACTS.items()
        },
    }
