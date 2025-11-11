"""
Clarification Module

Intelligent ambiguity detection and prompt enrichment for Phase 0 of the
orchestration workflow.

This module detects ambiguous user prompts (e.g., "check the API" when multiple
APIs exist) and generates targeted clarification questions with rich options
from project-context.json.

Public API:
    - execute_workflow(): High-level function for orchestrators
    - request_clarification(): Detect ambiguity and generate questions
    - process_clarification(): Enrich prompt with user responses
    - ClarificationEngine: Core engine class
    - detect_all_ambiguities(): Pattern-based detection

Example usage:
    from clarification import execute_workflow

    result = execute_workflow("check the API")
    enriched_prompt = result["enriched_prompt"]
"""

from .engine import (
    ClarificationEngine,
    request_clarification,
    process_clarification
)

from .patterns import detect_all_ambiguities

from .workflow import (
    execute_workflow,
    should_skip_clarification,
    get_clarification_summary
)

__all__ = [
    # Main workflow function (recommended for orchestrators)
    'execute_workflow',
    'should_skip_clarification',
    'get_clarification_summary',

    # Lower-level functions (for advanced usage)
    'ClarificationEngine',
    'request_clarification',
    'process_clarification',
    'detect_all_ambiguities',
]

__version__ = '1.0.0'
