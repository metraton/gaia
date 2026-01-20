"""
Context Management Module

This module provides tools for managing context in agent conversations:
- Exhaustion detection: Monitors when context is being depleted
- Conditional injection: Smart context injection for resume operations
"""

from .exhaustion_detector import ExhaustionDetector, check_context_health

__all__ = ["ExhaustionDetector", "check_context_health"]
