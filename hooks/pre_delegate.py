#!/usr/bin/env python3
"""
Pre-Delegation Hook for GAIA-OPS

Executes BEFORE delegating tasks to agents. Checks if an existing agent session
should be resumed instead of starting fresh.

Purpose:
- Maintain context across approval pauses
- Resume interrupted investigations
- Avoid redundant agent starts

Flow:
1. Hook receives delegation context (agent_name, task_id, etc.)
2. Checks if agent_id exists in context
3. If yes, calls should_resume() from agent_session
4. If should resume, adds resume metadata to context
5. Returns enriched context to orchestrator

Integration:
- Called automatically before Task tool execution
- Works with agent_session.py for state management
- Transparent to agents - they receive enriched context

Output:
- JSON with resume decision and context metadata
- Logs to .claude/logs/ for audit trail
"""

import json
import os
import sys
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

# Configure logging
log_dir = Path(".claude/logs")
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "pre_delegate.log"),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)


def find_agent_session_module() -> Optional[Any]:
    """
    Dynamically find and import agent_session module.
    
    Handles multiple possible locations:
    - tools/4-memory/agent_session.py (standard)
    - .claude/tools/agent_session.py (project-local)
    """
    import importlib.util
    
    candidates = [
        Path(__file__).parent.parent / "tools" / "4-memory" / "agent_session.py",
        Path(".claude/tools/agent_session.py"),
        Path("/home/jaguilar/aaxis/vtr/repositories/gaia-ops/tools/4-memory/agent_session.py")
    ]
    
    for path in candidates:
        if path.exists():
            try:
                spec = importlib.util.spec_from_file_location("agent_session", path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    logger.debug(f"Loaded agent_session from {path}")
                    return module
            except Exception as e:
                logger.warning(f"Failed to load agent_session from {path}: {e}")
                continue
    
    logger.error("Could not find agent_session module")
    return None


def pre_delegate_hook(delegation_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main pre-delegation hook - checks for session resume.
    
    Args:
        delegation_context: Context for agent delegation
            - agent_name: Name of agent to delegate to
            - task_id: Task identifier
            - agent_id: Optional existing agent ID
            - task_description: Task details
            - metadata: Additional context
    
    Returns:
        Enriched context with resume decision
            - should_resume: Boolean
            - resume_metadata: Session state if resuming
            - agent_id: Session ID to use
    """
    try:
        agent_name = delegation_context.get("agent_name", "unknown")
        agent_id = delegation_context.get("agent_id")
        task_id = delegation_context.get("task_id", "unknown")
        
        logger.info(f"Pre-delegate check: agent={agent_name}, task={task_id}, agent_id={agent_id}")
        
        # If no agent_id, cannot resume
        if not agent_id:
            logger.debug("No agent_id provided - will create new session")
            return {
                "should_resume": False,
                "reason": "no_existing_session",
                "original_context": delegation_context
            }
        
        # Load agent_session module
        agent_session = find_agent_session_module()
        if not agent_session:
            logger.warning("Could not load agent_session module - skipping resume check")
            return {
                "should_resume": False,
                "reason": "module_not_found",
                "original_context": delegation_context
            }
        
        # Check if session should resume
        should_resume = agent_session.should_resume(agent_id)
        
        if should_resume:
            # Load session state
            session_state = agent_session.get_session(agent_id)
            
            logger.info(f"RESUME APPROVED: {agent_id} (phase: {session_state.get('phase')})")
            
            return {
                "should_resume": True,
                "reason": "session_resumable",
                "agent_id": agent_id,
                "resume_metadata": {
                    "previous_phase": session_state.get("phase"),
                    "created_at": session_state.get("created_at"),
                    "last_updated": session_state.get("last_updated"),
                    "history": session_state.get("history", []),
                    "session_metadata": session_state.get("metadata", {})
                },
                "original_context": delegation_context
            }
        else:
            logger.info(f"RESUME DECLINED: {agent_id} (will create new session)")
            
            return {
                "should_resume": False,
                "reason": "session_not_resumable",
                "agent_id": agent_id,
                "original_context": delegation_context
            }
    
    except Exception as e:
        logger.error(f"Error in pre_delegate_hook: {e}", exc_info=True)
        return {
            "should_resume": False,
            "reason": "error",
            "error": str(e),
            "original_context": delegation_context
        }


def main():
    """CLI interface for testing and direct invocation"""
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 pre_delegate.py --test")
        print("  echo '{json}' | python3 pre_delegate.py")
        sys.exit(1)
    
    if sys.argv[1] == "--test":
        # Test mode
        print("\n=== Pre-Delegate Hook Test ===\n")
        
        # Test 1: No agent_id (new session)
        print("Test 1: No agent_id (should create new session)")
        test_context_1 = {
            "agent_name": "terraform-architect",
            "task_id": "T001",
            "task_description": "Plan infrastructure changes"
        }
        result_1 = pre_delegate_hook(test_context_1)
        print(f"  should_resume: {result_1['should_resume']}")
        print(f"  reason: {result_1['reason']}")
        print()
        
        # Test 2: With agent_id (check resume)
        print("Test 2: With agent_id (check if resumable)")
        
        # First create a session
        agent_session = find_agent_session_module()
        if agent_session:
            agent_id = agent_session.create_session(
                agent_name="gitops-operator",
                purpose="test_resume"
            )
            print(f"  Created test session: {agent_id}")
            
            # Set to approval phase (resumable)
            agent_session.update_state(agent_id, phase="approval")
            print(f"  Updated to approval phase")
            
            # Test resume
            test_context_2 = {
                "agent_name": "gitops-operator",
                "task_id": "T002",
                "agent_id": agent_id
            }
            result_2 = pre_delegate_hook(test_context_2)
            print(f"  should_resume: {result_2['should_resume']}")
            print(f"  reason: {result_2['reason']}")
            if result_2['should_resume']:
                print(f"  previous_phase: {result_2['resume_metadata']['previous_phase']}")
        else:
            print("  ERROR: Could not load agent_session module")
        
        print("\n=== Test Complete ===\n")
    
    else:
        # JSON input mode (standard hook interface)
        try:
            input_data = json.loads(sys.stdin.read())
            result = pre_delegate_hook(input_data)
            print(json.dumps(result, indent=2))
        except json.JSONDecodeError as e:
            print(json.dumps({
                "should_resume": False,
                "reason": "invalid_json",
                "error": str(e)
            }), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
