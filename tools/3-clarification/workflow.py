"""
Clarification Workflow Functions

High-level helper functions for orchestrators to integrate Phase 0 clarification
with minimal code changes.
"""

import sys
from typing import Dict, Any, Optional

from .engine import request_clarification, process_clarification


def execute_workflow(
    user_prompt: str,
    command_context: Optional[Dict[str, Any]] = None,
    ask_user_question_func: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Execute complete Phase 0 clarification workflow.

    This is the main entry point for orchestrators. It handles:
    1. Ambiguity detection
    2. Question generation
    3. User interaction (if ask function provided)
    4. Prompt enrichment

    Args:
        user_prompt: Original user request
        command_context: Optional context (e.g., {"command": "speckit.specify"})
        ask_user_question_func: Function to ask questions (e.g., AskUserQuestion tool)
                                If None, returns questions for manual handling

    Returns:
        {
            "enriched_prompt": str,              # Enriched or original prompt
            "clarification_occurred": bool,       # True if clarification happened
            "clarification_data": Dict or None,   # Full clarification data (for logging)
            "questions": List[Dict],              # Questions asked (if any)

            # Only if ask_user_question_func is None:
            "needs_manual_questioning": bool,     # True if manual question handling needed
            "summary": str                        # Clarification summary to show user
        }

    Example (automatic mode - with AskUserQuestion):
        from clarification import execute_workflow

        result = execute_workflow(
            user_prompt="check the API",
            ask_user_question_func=AskUserQuestion  # Claude Code tool
        )
        enriched_prompt = result["enriched_prompt"]

    Example (manual mode - for custom UX):
        result = execute_workflow("check the API")

        if result.get("needs_manual_questioning"):
            print(result["summary"])
            for q in result["questions"]:
                # Show question to user with custom UI
                # Get user response
                # Then call process_clarification manually
    """

    # Step 1: Detect ambiguity
    clarification = request_clarification(
        user_prompt=user_prompt,
        command_context=command_context or {"command": "general_prompt"}
    )

    # Step 2: Decision point - no clarification needed
    if not clarification["needs_clarification"]:
        return {
            "enriched_prompt": user_prompt,
            "clarification_occurred": False,
            "clarification_data": None,
            "questions": []
        }

    # Step 3: If no ask function provided, return questions for manual handling
    if ask_user_question_func is None:
        return {
            "enriched_prompt": user_prompt,  # Not enriched yet
            "clarification_occurred": False,
            "clarification_data": clarification,
            "questions": clarification["question_config"]["questions"],
            "summary": clarification["summary"],
            "needs_manual_questioning": True
        }

    # Step 4: Ask user questions (using provided function)
    try:
        user_responses = ask_user_question_func(**clarification["question_config"])
    except Exception as e:
        # If questioning fails, return original prompt
        print(f"Warning: Failed to ask clarification questions: {e}", file=sys.stderr)
        return {
            "enriched_prompt": user_prompt,
            "clarification_occurred": False,
            "clarification_data": None,
            "questions": [],
            "error": str(e)
        }

    # Step 5: Enrich prompt with responses
    result = process_clarification(
        engine_instance=clarification["engine_instance"],
        original_prompt=user_prompt,
        user_responses=user_responses.get("answers", {}),
        clarification_context=clarification["clarification_context"]
    )

    return {
        "enriched_prompt": result["enriched_prompt"],
        "clarification_occurred": True,
        "clarification_data": {
            "original_prompt": user_prompt,
            "ambiguity_score": clarification.get("ambiguity_score", 0),
            "patterns_detected": [a["pattern"] for a in clarification.get("ambiguity_points", [])],
            "user_responses": user_responses.get("answers", {})
        },
        "questions": clarification["question_config"]["questions"]
    }


def should_skip_clarification(
    user_prompt: str,
    command_context: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Quick check if Phase 0 should be skipped for this prompt.

    Skip clarification for:
    - System commands (starts with "/")
    - Read-only queries with low ambiguity ("show", "get", "list")
    - Already-specific prompts (includes service name, namespace, etc.)

    Args:
        user_prompt: User's request
        command_context: Optional command context

    Returns:
        True if Phase 0 should be skipped, False otherwise

    Example:
        if should_skip_clarification("/help"):
            # Skip Phase 0
        else:
            # Execute Phase 0
    """

    # Skip system commands
    if user_prompt.strip().startswith("/"):
        return True

    # For read-only queries, use higher ambiguity threshold
    read_only_keywords = ["show", "get", "list", "ver", "mostrar", "listar", "view"]
    if any(keyword in user_prompt.lower() for keyword in read_only_keywords):
        clarification = request_clarification(user_prompt, command_context)
        # Higher threshold (50 instead of 30) for read-only operations
        return clarification.get("ambiguity_score", 0) < 50

    return False


def get_clarification_summary(clarification_data: Dict[str, Any]) -> str:
    """
    Generate human-readable summary of clarification that occurred.

    Useful for logging or displaying to user.

    Args:
        clarification_data: Clarification data from execute_workflow()

    Returns:
        String summary (multi-line)

    Example:
        result = execute_workflow("check the API")
        if result["clarification_occurred"]:
            print(get_clarification_summary(result["clarification_data"]))
    """

    if not clarification_data:
        return "No clarification needed"

    original = clarification_data.get("original_prompt", "N/A")
    score = clarification_data.get("ambiguity_score", 0)
    patterns = clarification_data.get("patterns_detected", [])
    responses = clarification_data.get("user_responses", {})

    lines = []
    lines.append("=" * 60)
    lines.append("PHASE 0 CLARIFICATION SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Original prompt: {original}")
    lines.append(f"Ambiguity score: {score}/100")
    lines.append(f"Patterns detected: {', '.join(patterns)}")
    lines.append(f"\nUser responses:")
    for question_id, answer in responses.items():
        lines.append(f"  {question_id}: {answer}")
    lines.append("=" * 60)

    return "\n".join(lines)
