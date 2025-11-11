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

from .generic_engine import clarify_generic
from .user_interaction import ask, ask_confirmation, ask_choice, ask_multiple

def clarify(user_prompt: str, max_iterations: int = 1) -> str:
    """
    🎯 Ultra-simple clarification in 1 line.

    Auto-detects ambiguity from project-context.json and asks questions if needed.

    Args:
        user_prompt: User's original request
        max_iterations: Max clarification rounds (default: 1)

    Returns:
        Enriched prompt if clarification occurred, otherwise original prompt

    Example:
        enriched = clarify("Check the API")
        # If ambiguous → User gets question
        # If specific → Returns immediately

    Performance: ~200ms (before user interaction)
    """
    try:
        from pathlib import Path
        import json

        # Load project-context
        context_path = Path(".claude/project-context/project-context.json")
        if not context_path.exists():
            return user_prompt

        with open(context_path, 'r', encoding='utf-8') as f:
            project_context = json.load(f)

        # Run clarification
        current_prompt = user_prompt

        for iteration in range(max_iterations):
            enriched, occurred = clarify_generic(
                current_prompt,
                project_context,
                ask_func=None  # Will import AskUserQuestion if available
            )

            if not occurred:
                # No clarification needed
                return enriched

            current_prompt = enriched

        return current_prompt

    except Exception:
        # If anything fails, return original prompt
        return user_prompt


__all__ = [
    # New generic clarification (recommended)
    'clarify',
    'clarify_generic',
    'ask',
    'ask_confirmation',
    'ask_choice',
    'ask_multiple',

    # Legacy functions (still available)
    'execute_workflow',
    'should_skip_clarification',
    'get_clarification_summary',

    # Lower-level functions (for advanced usage)
    'ClarificationEngine',
    'request_clarification',
    'process_clarification',
    'detect_all_ambiguities',
]

__version__ = '1.1.0'
