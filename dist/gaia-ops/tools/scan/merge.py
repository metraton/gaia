"""
Section-Aware Context Combining Logic

Merges scanner results with existing project-context.json following the
merge rules from contracts/merge-behavior.md:

  Rule 1: Scanner-owned sections -> full replace
  Rule 2: Agent-enriched sections -> never touch
  Rule 3: Mixed sections -> selective update at sub-key level
  Rule 4: Unknown/user-custom sections -> preserve
  Rule 5: Metadata -> always update

Note: Backward-compatible sections (project_details, application_architecture,
development_standards) were removed in v3. Consumers read v2 scanner sections
directly (project_identity, stack, git, environment, infrastructure).

Special handling for sub-section level ownership: the `environment` section
is jointly owned by the `tools` scanner (tools, tool_preferences) and the
`environment` scanner (os, runtimes, env_files). Each scanner replaces only
its owned sub-keys without clobbering the other scanner's sub-keys.

Contract: specs/002-gaia-scan/contracts/merge-behavior.md
"""

import copy
import logging
from typing import Any, Dict, Optional, Set

from tools.context.deep_merge import deep_merge

logger = logging.getLogger(__name__)

# Sections fully owned by scanners -- replaced entirely on each scan (Rule 1)
# Top-level sections only; sub-key ownership handled separately
SCANNER_OWNED_TOP_LEVEL: Dict[str, str] = {
    "project_identity": "stack",
    "stack": "stack",
    "git": "git",
    "infrastructure": "infrastructure",
    "orchestration": "orchestration",
    # "environment" is NOT listed here because it has sub-key ownership
}

# Sub-key ownership within the `environment` section (Rule 4 / sub-section)
# Maps environment sub-key -> owning scanner name
ENVIRONMENT_SUBKEY_OWNERS: Dict[str, str] = {
    "tools": "tools",
    "tool_preferences": "tools",
    "os": "environment",
    "runtimes": "environment",
    "env_files": "environment",
}

# Agent-enriched sections -- never modified by scanners (Rule 2)
AGENT_ENRICHED_SECTIONS: frozenset = frozenset([
    "operational_guidelines",
    "cluster_details",
    "infrastructure_topology",
    "monitoring_observability",
    "architecture_overview",
    "gcp_services",
    "workload_identity",
])

# Mixed sections with partial scanner ownership (Rule 3)
# Maps section_name -> set of scanner-owned field names
MIXED_SECTION_SCANNER_FIELDS: Dict[str, Set[str]] = {
    "terraform_infrastructure": {"layout"},
    "gitops_configuration": {"repository"},
    "application_services": {"base_path", "services"},
}


def merge_context(
    existing: Dict[str, Any],
    scan_sections: Dict[str, Any],
    section_owners: Dict[str, str],
) -> Dict[str, Any]:
    """Merge scanner results with existing project-context sections.

    Applies the merge rules from contracts/merge-behavior.md to produce
    the final merged sections dict.

    Args:
        existing: Current sections from project-context.json (may be empty).
        scan_sections: Combined sections from all scanners.
        section_owners: Mapping of section/sub-section name to scanner name,
                        from ScannerRegistry.get_section_owners().

    Returns:
        Merged sections dict ready to be written to project-context.json.
        The merge is deterministic: same inputs always produce the same output.
    """
    result = copy.deepcopy(existing)

    # --- Rule 1: Scanner-owned top-level sections -> full replace ---
    for section_name in SCANNER_OWNED_TOP_LEVEL:
        if section_name in scan_sections:
            result[section_name] = copy.deepcopy(scan_sections[section_name])

    # --- Sub-section level ownership for `environment` ---
    # Both the `tools` scanner and `environment` scanner contribute sub-keys
    # to the `environment` section. Each scanner's sub-keys replace their owned
    # portion without clobbering the other scanner's sub-keys.
    _merge_environment_section(result, scan_sections)

    # --- Rule 2: Agent-enriched sections -> never touch ---
    # These are already in `result` from the deepcopy of `existing`.
    # We explicitly do NOT overwrite them, even if a scanner accidentally
    # produced data for one of these section names.
    # (No action needed -- they are preserved by the deepcopy.)

    # --- Rule 3: Mixed sections -> selective update ---
    for section_name, scanner_fields in MIXED_SECTION_SCANNER_FIELDS.items():
        if section_name in scan_sections:
            scan_data = scan_sections[section_name]
            if section_name not in result:
                result[section_name] = {}
            # Only update scanner-owned fields; preserve agent fields
            for field_name in scanner_fields:
                if field_name in scan_data:
                    result[section_name][field_name] = copy.deepcopy(
                        scan_data[field_name]
                    )

    # --- Rule 5: Unknown/user-custom sections -> preserve ---
    # Any section in `existing` that is not scanner-owned, not agent-enriched,
    # not backward-compat, and not mixed is a user-custom section.
    # These are already preserved by the initial deepcopy of `existing`.
    # We do NOT add new unknown sections from scan_sections.

    return result


def _merge_environment_section(
    result: Dict[str, Any],
    scan_sections: Dict[str, Any],
) -> None:
    """Merge the `environment` section with sub-key level ownership.

    Two scanners contribute to the `environment` section:
    - `tools` scanner owns: tools, tool_preferences
    - `environment` scanner owns: os, runtimes, env_files

    Each scanner's sub-keys replace their owned portion; the other scanner's
    sub-keys are preserved. The `_source` field gets a combined tag.

    Args:
        result: The result dict being built (mutated in place).
        scan_sections: Combined sections from all scanners.
    """
    if "environment" not in scan_sections:
        return

    scan_env = scan_sections["environment"]

    if "environment" not in result:
        result["environment"] = {}

    env = result["environment"]

    # Replace each sub-key based on ownership
    for subkey in ENVIRONMENT_SUBKEY_OWNERS:
        if subkey in scan_env:
            env[subkey] = copy.deepcopy(scan_env[subkey])

    # Set combined _source tag
    env["_source"] = "scanner:environment+tools"


def collect_scanner_sections(
    scanner_results: Dict[str, Any],
) -> Dict[str, Any]:
    """Collect and combine sections from all scanner results.

    Handles the environment section specially: both `tools` and `environment`
    scanners produce sub-keys under `environment`, so their outputs are
    combined into a single `environment` section.

    Args:
        scanner_results: Mapping of scanner_name -> ScanResult (must have
                         a `sections` attribute that is a dict).

    Returns:
        Combined sections dict from all scanners.
    """
    combined: Dict[str, Any] = {}
    environment_parts: Dict[str, Any] = {}

    for _scanner_name, scan_result in scanner_results.items():
        sections = scan_result.sections if hasattr(scan_result, "sections") else {}

        for section_name, section_data in sections.items():
            if section_name == "environment":
                # Merge environment sub-keys from both scanners
                if isinstance(section_data, dict):
                    for key, value in section_data.items():
                        if key != "_source":
                            environment_parts[key] = value
            else:
                # Non-environment sections: direct assignment (last scanner wins,
                # but each section should have exactly one owner)
                combined[section_name] = section_data

    # Reassemble environment section if we got any parts
    if environment_parts:
        combined["environment"] = {
            "_source": "scanner:environment+tools",
            **environment_parts,
        }

    return combined
