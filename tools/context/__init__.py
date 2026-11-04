"""
Context Module: Context provisioning and enrichment

This module provides context loading, filtering, and enrichment for agents.
It manages the SSOT (Single Source of Truth) context contracts and ensures
agents receive the necessary context for execution.

Main functions:
- load_project_context(): Load project context from JSON
- get_contract_context(): Get context for an agent based on its contract
- get_context_update_contract(): Get an agent's readable and writable sections
- load_provider_contracts(): Load cloud provider-specific contracts
- classify_surfaces(), build_investigation_brief(), load_surface_routing_config():
  route a prompt to surfaces (surface_router)
"""

from . import context_provider
from .context_provider import (
    load_project_context,
    get_contract_context,
    get_context_update_contract,
    load_provider_contracts,
)
from .surface_router import (
    build_investigation_brief,
    classify_surfaces,
    load_surface_routing_config,
)

__all__ = [
    "context_provider",
    "load_project_context",
    "get_contract_context",
    "get_context_update_contract",
    "load_provider_contracts",
    "build_investigation_brief",
    "classify_surfaces",
    "load_surface_routing_config",
]
