"""
Routing Module: Agent semantic routing and intent classification

This module provides the core agent routing logic, using semantic matching
to determine which agent is best suited for a given task.
"""

from .agent_router import AgentRouter, IntentClassifier, CapabilityValidator

__all__ = ["AgentRouter", "IntentClassifier", "CapabilityValidator"]
