"""Review engine for managing pending context update suggestions.

This module provides the review logic for pending update suggestions,
including listing, approving, rejecting, and viewing statistics of
updates discovered by agents.
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Import PendingUpdateStore from the context module
sys.path.insert(0, str(Path(__file__).parent.parent))
from context.pending_updates import PendingUpdateStore


def review_pending(
    action: str,
    update_id: Optional[str] = None,
    context_path: Optional[Path] = None,
    store: Optional["PendingUpdateStore"] = None
) -> dict:
    """Execute a review action on pending updates.

    Args:
        action: One of "list", "approve", "reject", "stats"
        update_id: Required for approve/reject actions
        context_path: Optional path to project-context.json (for apply)
        store: Optional PendingUpdateStore instance (for testing)

    Returns:
        dict: Action-specific results

    Raises:
        ValueError: If action is invalid or required parameters are missing
    """
    valid_actions = {"list", "approve", "reject", "stats"}

    if action not in valid_actions:
        raise ValueError(
            f"Invalid action: {action}. Must be one of: {', '.join(sorted(valid_actions))}"
        )

    # Validate update_id requirement for approve/reject
    if action in {"approve", "reject"} and not update_id:
        raise ValueError(f"update_id is required for {action} action")

    # Use provided store or create default
    if store is None:
        store = PendingUpdateStore()
    
    # Execute action
    if action == "list":
        updates = store.list_pending()
        # Convert dataclass instances to dicts for JSON serialization
        updates_dict = [
            {
                "update_id": u.update_id,
                "category": u.category,
                "target_section": u.target_section,
                "summary": u.summary,
                "confidence": u.confidence,
                "source_agent": u.source_agent,
                "status": u.status,
                "created_at": u.created_at,
                "seen_count": u.seen_count,
            }
            for u in updates
        ]
        return {
            "action": "list",
            "updates": updates_dict,
            "count": len(updates_dict)
        }
    
    elif action == "approve":
        # Approve the update
        store.approve(update_id)
        
        # Apply the update to project-context.json
        result = store.apply(update_id, context_path)
        
        return {
            "action": "approve",
            "update_id": update_id,
            "applied": result.get("success", False),
            "result": result
        }
    
    elif action == "reject":
        store.reject(update_id)
        return {
            "action": "reject",
            "update_id": update_id,
            "success": True
        }
    
    elif action == "stats":
        statistics = store.get_statistics()
        return {
            "action": "stats",
            "statistics": statistics
        }


def main():
    """CLI entry point for review operations."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Review pending context updates"
    )
    parser.add_argument(
        "action",
        choices=["list", "approve", "reject", "stats"],
        help="Action to perform"
    )
    parser.add_argument(
        "--update-id",
        help="Update ID (required for approve/reject)"
    )
    parser.add_argument(
        "--context-path",
        help="Path to project-context.json"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (default behavior)"
    )
    
    args = parser.parse_args()
    
    try:
        # Execute review action
        result = review_pending(
            args.action,
            args.update_id,
            Path(args.context_path) if args.context_path else None
        )
        
        # Output result as JSON
        print(json.dumps(result, indent=2))
        
    except Exception as e:
        # Output error as JSON
        error_result = {
            "error": str(e),
            "action": args.action
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
