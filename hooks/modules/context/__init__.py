"""
Context Management Module

This module provides tools for managing context in agent conversations:
- context_writer: Progressive enrichment of project context via the update_contracts array in the agent_contract_handoff envelope (DB-backed)
- contracts_loader: Load agent contracts from ~/.gaia/gaia.db, detect cloud provider, merge permissions
- kernel_builder: Render the dispatch kernel blocks from a claimed born row
- context_freshness: Check staleness of the project context for SessionStart
"""

__all__ = []
