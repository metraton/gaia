"""
Context Management Module

This module provides tools for managing context in agent conversations:
- context_writer: Progressive enrichment of project-context.json via CONTEXT_UPDATE blocks
- contracts_loader: Load context contracts, detect cloud provider, merge agent permissions
- context_injector: Core context injection subsystem for project agents
- context_freshness: Check staleness of project-context.json for SessionStart
"""

__all__ = []
