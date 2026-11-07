"""
Context Management Module

This module provides tools for managing context in agent conversations:
- context_writer: Progressive enrichment of project context via the update_contracts array in the agent_contract_handoff envelope (DB-backed)
- contracts_loader: Read an agent's writable contracts and the workspace's stored sections from gaia.db, and build the context-update reminder
- kernel_builder: Render the dispatch kernel blocks from a claimed born row
- context_freshness: Check staleness of the workspace's project-context sections
"""

__all__ = []
