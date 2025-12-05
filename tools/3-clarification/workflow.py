"""
Clarification Workflow Functions

High-level helper functions for orchestrators to integrate Phase 0 clarification
with minimal code changes.
"""

import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

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

    # Step 1.5: SEARCH EPISODIC MEMORY (NEW)
    relevant_episodes = _search_episodic_memory(user_prompt)

    if relevant_episodes:
        # Log found episodes for transparency
        print(f"📚 Found {len(relevant_episodes)} relevant episodes from project memory", file=sys.stderr)

        # Enrich the prompt internally with historical context
        historical_context = "\n\n[Historical Context from Project Memory]:\n"
        for episode in relevant_episodes[:2]:  # Max 2 episodes to avoid overload
            historical_context += f"- {episode.get('title', 'Previous task')}: {episode.get('outcome', 'completed')}"
            if episode.get('lessons_learned'):
                historical_context += f" (Lesson: {episode['lessons_learned'][0] if isinstance(episode['lessons_learned'], list) else episode['lessons_learned']})"
            historical_context += "\n"

        # Add context to clarification data
        clarification["historical_context"] = relevant_episodes
        clarification["historical_summary"] = historical_context

    # Step 2: Decision point - no clarification needed
    if not clarification["needs_clarification"]:
        return {
            "enriched_prompt": user_prompt,
            "clarification_occurred": False,
            "clarification_data": None,
            "questions": [],
            "historical_context": relevant_episodes if relevant_episodes else []  # Include episodes even without clarification
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


def _search_episodic_memory(user_prompt: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Search episodic memory for relevant historical context.

    Uses simple keyword matching. Can be enhanced with embeddings later.

    Args:
        user_prompt: User's request
        max_results: Maximum episodes to return

    Returns:
        List of relevant episodes with relevance scores
    """
    import json
    from datetime import datetime, timedelta

    try:
        # Find episodic memory path (search in current project first)
        memory_paths = [
            Path(".claude/project-context/episodic-memory"),  # Current project
            Path("../.claude/project-context/episodic-memory"),  # Parent directory
            Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")  # Absolute fallback
        ]

        index_file = None
        for memory_dir in memory_paths:
            candidate = memory_dir / "index.json"
            if candidate.exists():
                index_file = candidate
                break

        if not index_file:
            # No episodic memory found (normal for new projects)
            return []

        with open(index_file) as f:
            index = json.load(f)

        # Extract keywords from prompt
        prompt_lower = user_prompt.lower()
        prompt_words = set(prompt_lower.split())

        # Score each episode
        scored_episodes = []
        for episode in index.get("episodes", []):
            score = 0.0

            # Tag matching (highest weight)
            for tag in episode.get("tags", []):
                if tag.lower() in prompt_lower:
                    score += 0.4

            # Title matching
            title_words = set(episode.get("title", "").lower().split())
            common_words = prompt_words & title_words
            if common_words:
                score += 0.3 * (len(common_words) / max(len(title_words), 1))

            # Type matching
            if episode.get("type", "") in prompt_lower:
                score += 0.2

            # Apply time decay (if timestamp is available)
            try:
                episode_date = datetime.fromisoformat(episode["timestamp"].replace("Z", "+00:00"))
                age_days = (datetime.now(episode_date.tzinfo) - episode_date).days

                if age_days < 30:
                    time_factor = 1.0
                elif age_days < 90:
                    time_factor = 0.8
                elif age_days < 180:
                    time_factor = 0.6
                else:
                    time_factor = 0.4
            except:
                time_factor = 0.5  # Default if timestamp parsing fails

            final_score = score * time_factor * episode.get("relevance_score", 0.5)

            if final_score > 0.1:  # Threshold
                # Load full episode details from JSONL if needed
                episode_full = _load_full_episode(episode["id"], index_file.parent)
                if episode_full:
                    episode_full["match_score"] = final_score
                    scored_episodes.append(episode_full)
                else:
                    episode["match_score"] = final_score
                    scored_episodes.append(episode)

        # Sort by score and return top N
        scored_episodes.sort(key=lambda x: x["match_score"], reverse=True)
        top_episodes = scored_episodes[:max_results]

        if top_episodes:
            print(f"📚 Memory search found {len(top_episodes)} relevant episodes (searched {len(index.get('episodes', []))} total)", file=sys.stderr)

        return top_episodes

    except Exception as e:
        # Silent fail - episodic memory is optional enhancement
        import sys
        print(f"Warning: Could not search episodic memory: {e}", file=sys.stderr)
        return []


def _load_full_episode(episode_id: str, memory_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Load full episode details from JSONL file.

    Args:
        episode_id: Episode ID to load
        memory_dir: Directory containing episodes.jsonl

    Returns:
        Full episode dict or None if not found
    """
    try:
        episodes_file = memory_dir / "episodes.jsonl"
        if not episodes_file.exists():
            return None

        with open(episodes_file) as f:
            for line in f:
                try:
                    episode = json.loads(line)
                    if episode.get("id") == episode_id:
                        return episode
                except:
                    continue

    except Exception:
        pass

    return None
