#!/usr/bin/env python3
"""
Episodic Memory Auto-Capture Hook

Automatically captures completed workflows as episodic memories.
Triggered by post_tool_use hook when a workflow completes.

Usage:
    from modules.workflow.episodic_capture import capture_episode
    
    result = capture_episode(workflow_context)
    # Returns: {"episode_id": "ep_...", "stored": True}
"""

import sys
import logging
from pathlib import Path
from typing import Dict, Optional, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

# Add tools/memory to path
def _get_memory_module_path() -> Path:
    """Get path to memory module."""
    # Try multiple possible locations
    candidates = [
        Path(__file__).parent.parent.parent.parent / "tools" / "memory",
        Path.cwd() / "node_modules" / "@jaguilar87" / "gaia-ops" / "tools" / "memory",
        Path(__file__).parent.parent.parent.parent / "node_modules" / "@jaguilar87" / "gaia-ops" / "tools" / "memory"
    ]
    
    for candidate in candidates:
        if (candidate / "episodic.py").exists():
            return candidate
    
    raise FileNotFoundError("Could not locate memory module")

try:
    memory_path = _get_memory_module_path()
    sys.path.insert(0, str(memory_path))
    from episodic import EpisodicMemory
    MEMORY_AVAILABLE = True
except (ImportError, FileNotFoundError) as e:
    logger.warning(f"Episodic memory not available: {e}")
    MEMORY_AVAILABLE = False


def capture_episode(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Capture a completed workflow as an episodic memory.
    
    Args:
        context: Workflow context containing:
            - original_prompt: User's initial request
            - enriched_prompt: Final prompt after clarifications
            - clarifications: Q&A exchanges
            - workflow: Workflow state (approved/rejected/completed)
            - outcome: success/failed/partial/abandoned
            - duration_seconds: Workflow duration
            - commands_executed: List of commands run
            - agent_name: Agent that handled the workflow
            - tags: Optional tags for categorization
    
    Returns:
        {
            "episode_id": str,
            "stored": bool,
            "error": str (if stored=False)
        }
    """
    if not MEMORY_AVAILABLE:
        return {"stored": False, "error": "Episodic memory module not available"}
    
    try:
        memory = EpisodicMemory()
        
        # Extract relevant fields
        prompt = context.get("original_prompt", context.get("prompt", ""))
        clarifications = context.get("clarifications", {})
        enriched_prompt = context.get("enriched_prompt", prompt)
        
        # Determine outcome
        workflow = context.get("workflow", {})
        outcome = context.get("outcome")
        if not outcome:
            if workflow.get("approval_decision") == "approved":
                outcome = "success"
            elif workflow.get("approval_decision") == "rejected":
                outcome = "abandoned"
            else:
                outcome = "pending"
        
        # Extract tags
        tags = context.get("tags", [])
        if not tags:
            tags = _extract_tags_from_context(context)
        
        # Store episode
        episode_id = memory.store_episode(
            prompt=prompt,
            clarifications=clarifications,
            enriched_prompt=enriched_prompt,
            context=context,
            tags=tags,
            outcome=outcome
        )
        
        # Update outcome with additional details if available
        if outcome != "pending":
            memory.update_outcome(
                episode_id=episode_id,
                outcome=outcome,
                success=(outcome == "success"),
                duration_seconds=context.get("duration_seconds"),
                commands_executed=context.get("commands_executed", [])
            )
        
        logger.info(f"Captured episode: {episode_id} (outcome: {outcome})")
        
        return {
            "episode_id": episode_id,
            "stored": True,
            "outcome": outcome
        }
        
    except Exception as e:
        logger.error(f"Failed to capture episode: {e}", exc_info=True)
        return {
            "stored": False,
            "error": str(e)
        }


def _extract_tags_from_context(context: Dict[str, Any]) -> List[str]:
    """
    Extract meaningful tags from workflow context.
    
    Args:
        context: Workflow context
    
    Returns:
        List of tags
    """
    tags = []
    
    # Add agent name
    if agent := context.get("agent_name"):
        tags.append(agent)
    
    # Add tool name if available
    if tool := context.get("tool_name"):
        tags.append(tool)
    
    # Add workflow type
    workflow = context.get("workflow", {})
    if workflow.get("is_t3"):
        tags.append("t3-operation")
    
    # Extract from prompt keywords
    prompt = context.get("original_prompt", "").lower()
    keyword_map = {
        "deploy": "deployment",
        "terraform": "terraform",
        "kubectl": "kubernetes",
        "fix": "troubleshooting",
        "error": "troubleshooting",
        "create": "creation",
        "delete": "deletion",
        "update": "update",
        "migrate": "migration"
    }
    
    for keyword, tag in keyword_map.items():
        if keyword in prompt:
            tags.append(tag)
    
    return list(set(tags))  # Remove duplicates


def search_relevant_episodes(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Search episodic memory for relevant past workflows.
    
    Args:
        query: Search query (user prompt or keywords)
        max_results: Maximum number of results to return
    
    Returns:
        List of relevant episodes with match scores
    """
    if not MEMORY_AVAILABLE:
        return []
    
    try:
        memory = EpisodicMemory()
        episodes = memory.search_episodes(
            query=query,
            max_results=max_results,
            min_score=0.3  # Only return reasonably relevant matches
        )
        return episodes
    except Exception as e:
        logger.error(f"Failed to search episodes: {e}")
        return []


def get_memory_statistics() -> Dict[str, Any]:
    """
    Get episodic memory statistics.
    
    Returns:
        Statistics dict or empty dict if unavailable
    """
    if not MEMORY_AVAILABLE:
        return {}
    
    try:
        memory = EpisodicMemory()
        return memory.get_statistics()
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}")
        return {}


# CLI for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test episodic capture")
    parser.add_argument("--test", action="store_true", help="Run test capture")
    parser.add_argument("--search", type=str, help="Search query")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    
    args = parser.parse_args()
    
    if args.test:
        test_context = {
            "original_prompt": "Deploy graphql-server to production",
            "agent_name": "gitops-operator",
            "outcome": "success",
            "duration_seconds": 45.0,
            "commands_executed": ["kubectl get pods"],
            "tags": ["deployment", "kubernetes"]
        }
        result = capture_episode(test_context)
        print(f"Test capture result: {result}")
    
    elif args.search:
        episodes = search_relevant_episodes(args.search)
        print(f"Found {len(episodes)} relevant episodes:")
        for ep in episodes:
            print(f"  [{ep['match_score']:.2f}] {ep['title']} ({ep['type']})")
    
    elif args.stats:
        stats = get_memory_statistics()
        if stats:
            print(f"Episodic Memory Statistics:")
            print(f"  Total episodes: {stats['total_episodes']}")
            print(f"  Storage size: {stats['storage_size_mb']:.2f} MB")
            print(f"  Outcomes: {stats['outcomes']}")
        else:
            print("No statistics available")
    
    else:
        parser.print_help()
