"""
Context Module: Context provisioning and enrichment

This module provides context loading, filtering, and enrichment for agents.
It manages the SSOT (Single Source of Truth) context contracts and ensures
agents receive the necessary context for execution.

Main functions:
- load_project_context(): Load project context from JSON
- get_contract_context(): Get context for an agent based on its contract
- load_provider_contracts(): Load cloud provider-specific contracts
"""

from . import context_provider
from .context_section_reader import ContextSectionReader

# Re-export key functions for convenience
from .context_provider import (
    load_project_context,
    get_contract_context,
    load_provider_contracts,
)

__all__ = [
    "context_provider",  # module
    "ContextSectionReader",
    # Main functions
    "load_project_context",
    "get_contract_context",
    "load_provider_contracts",
]
