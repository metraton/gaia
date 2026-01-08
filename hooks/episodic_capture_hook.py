#!/usr/bin/env python3
"""
Episodic Capture Hook - Automatic episodic memory capture throughout workflow phases

This hook provides functions to capture and update episodic memory entries
as user requests progress through the gaia-ops workflow phases.

Phases captured:
- Phase 0: Initial prompt enrichment (capture_phase_0)
- Phase 3: Agent realization package complete (update_phase_3)
- Phase 4: User approval decision (update_phase_4)
- Phase 5: Final outcome (update_phase_5)

Architecture:
- Episodes are created in Phase 0 (enriched prompt generated)
- Episodes are progressively updated with more context as workflow progresses
- Final outcome is recorded in Phase 5 with success/failure status
- All updates are non-blocking and fail silently to avoid disrupting workflow
"""

import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

# Add tools path to import episodic memory
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "4-memory"))

try:
    from episodic import EpisodicMemory
    from agent_session import AgentSession as _AgentSession
except ImportError:
    print("Warning: Could not import EpisodicMemory/AgentSession. Episodic capture disabled.", file=sys.stderr)
    EpisodicMemory = None
    _AgentSession = None


def capture_phase_0(
    original_prompt: str,
    enriched_prompt: str,
    clarification_data: Optional[Dict[str, Any]] = None,
    command_context: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Capture initial episode when enriched prompt is generated (Phase 0).
    
    This creates a new episode with:
    - Original and enriched prompts
    - Clarification data (if any)
    - Command context
    - Initial tags and type classification
    
    Args:
        original_prompt: User's original request
        enriched_prompt: Clarified/enriched version of prompt
        clarification_data: Clarification details from Phase 0
        command_context: Optional command context (e.g., {"command": "speckit.specify"})
    
    Returns:
        episode_id if successful, None if failed (fails silently)
    
    Example:
        episode_id = capture_phase_0(
            original_prompt="check the API",
            enriched_prompt="Check graphql-server API in common namespace on digital-eks-prod",
            clarification_data={"ambiguity_score": 45, "questions": [...]},
            command_context={"command": "general"}
        )
    """
    if not EpisodicMemory:
        return None
    
    try:
        memory = EpisodicMemory()
        
        # Extract tags from context and prompt
        tags = _extract_tags(original_prompt, enriched_prompt, command_context)
        
        # Sanitize context for storage
        sanitized_context = _sanitize_context(command_context or {})
        
        # Add clarification metadata if present
        if clarification_data:
            sanitized_context["clarification"] = {
                "occurred": clarification_data.get("clarification_occurred", False),
                "ambiguity_score": clarification_data.get("ambiguity_score", 0),
                "patterns_detected": clarification_data.get("patterns_detected", [])
            }
        
        # Add workflow phase tracking
        sanitized_context["workflow"] = {
            "phase_0_timestamp": datetime.now(timezone.utc).isoformat(),
            "phases_completed": ["phase_0"]
        }
        
        # Store episode (starts with unknown outcome)
        episode_id = memory.store_episode(
            prompt=original_prompt,
            clarifications=clarification_data or {},
            enriched_prompt=enriched_prompt,
            context=sanitized_context,
            tags=tags,
            outcome=None,  # Unknown at this point
            success=None    # Unknown at this point
        )
        
        print(f"📚 Episodic capture: Created episode {episode_id}", file=sys.stderr)
        return episode_id
        
    except Exception as e:
        print(f"Warning: Episodic capture failed in Phase 0: {e}", file=sys.stderr)
        return None


def update_phase_3(
    episode_id: str,
    realization_package: Dict[str, Any],
    agent_name: Optional[str] = None
) -> bool:
    """
    Update episode when agent completes realization package (Phase 3).
    
    Adds:
    - Agent name and execution details
    - Security tier of operations
    - Commands or actions planned
    - Validation results
    
    Args:
        episode_id: Episode ID from Phase 0
        realization_package: Agent's realization package with plan and actions
        agent_name: Name of agent that generated the package
    
    Returns:
        True if updated successfully, False otherwise
    
    Example:
        success = update_phase_3(
            episode_id="ep_20251119_141451_7e5733f8",
            realization_package={
                "tier": "T2",
                "operations": ["kubectl get pods", "kubectl describe service"],
                "validation_results": {"checks_passed": 5}
            },
            agent_name="devops-agent"
        )
    """
    if not EpisodicMemory or not episode_id:
        return False
    
    try:
        memory = EpisodicMemory()
        
        # Load existing episode
        episode = memory.get_episode(episode_id)
        if not episode:
            print(f"Warning: Episode {episode_id} not found for Phase 3 update", file=sys.stderr)
            return False
        
        # Extract relevant information from realization package
        context = episode.get("context", {})
        workflow = context.get("workflow", {})
        
        # Update workflow tracking
        workflow["phase_3_timestamp"] = datetime.now(timezone.utc).isoformat()
        workflow["phases_completed"] = workflow.get("phases_completed", []) + ["phase_3"]
        workflow["agent_name"] = agent_name
        
        # Extract tier and operations
        tier = realization_package.get("tier", "unknown")
        operations = realization_package.get("operations", [])
        
        workflow["tier"] = tier
        workflow["operation_count"] = len(operations)
        
        # Store limited operation info (avoid storing full commands for security)
        if operations:
            workflow["operation_types"] = _classify_operations(operations)
        
        context["workflow"] = workflow
        
        # Update the episode file
        episode["context"] = context
        
        # Save updated episode
        episode_file = memory.episodes_dir / f"episode-{episode_id}.json"
        with open(episode_file, 'w') as f:
            json.dump(episode, f, indent=2)
        
        print(f"📚 Episodic capture: Updated episode {episode_id} (Phase 3, agent={agent_name}, tier={tier})", file=sys.stderr)
        return True
        
    except Exception as e:
        print(f"Warning: Episodic capture failed in Phase 3: {e}", file=sys.stderr)
        return False


def update_phase_4(
    episode_id: str,
    approval_decision: str,
    tier: Optional[str] = None,
    user_feedback: Optional[str] = None
) -> bool:
    """
    Update episode with user approval decision (Phase 4).
    
    Adds:
    - Approval status (approved, rejected, modified)
    - User feedback or modification notes
    - Decision timestamp
    
    Args:
        episode_id: Episode ID from Phase 0
        approval_decision: "approved", "rejected", or "modified"
        tier: Security tier (T0-T3)
        user_feedback: Optional user comments on approval
    
    Returns:
        True if updated successfully, False otherwise
    
    Example:
        success = update_phase_4(
            episode_id="ep_20251119_141451_7e5733f8",
            approval_decision="approved",
            tier="T3",
            user_feedback="Looks good, proceed with deployment"
        )
    """
    if not EpisodicMemory or not episode_id:
        return False
    
    try:
        memory = EpisodicMemory()
        
        # Load existing episode
        episode = memory.get_episode(episode_id)
        if not episode:
            print(f"Warning: Episode {episode_id} not found for Phase 4 update", file=sys.stderr)
            return False
        
        context = episode.get("context", {})
        workflow = context.get("workflow", {})
        
        # Update workflow tracking
        workflow["phase_4_timestamp"] = datetime.now(timezone.utc).isoformat()
        workflow["phases_completed"] = workflow.get("phases_completed", []) + ["phase_4"]
        workflow["approval_decision"] = approval_decision
        
        if tier:
            workflow["tier"] = tier
        
        if user_feedback:
            workflow["user_feedback"] = user_feedback
        
        # If rejected, pre-mark as abandoned
        if approval_decision == "rejected":
            episode["outcome"] = "abandoned"
            episode["success"] = False
        
        context["workflow"] = workflow
        episode["context"] = context
        
        # Save updated episode
        episode_file = memory.episodes_dir / f"episode-{episode_id}.json"
        with open(episode_file, 'w') as f:
            json.dump(episode, f, indent=2)
        
        print(f"📚 Episodic capture: Updated episode {episode_id} (Phase 4, decision={approval_decision})", file=sys.stderr)
        return True
        
    except Exception as e:
        print(f"Warning: Episodic capture failed in Phase 4: {e}", file=sys.stderr)
        return False


def update_phase_5(
    episode_id: str,
    outcome: str,
    success: bool,
    duration_seconds: Optional[float] = None,
    commands_executed: Optional[List[str]] = None,
    error_message: Optional[str] = None,
    artifacts: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Update episode with final outcome (Phase 5).
    
    This is the final update that records:
    - Final outcome (success, partial, failed, abandoned)
    - Success boolean
    - Total duration
    - Commands that were executed
    - Error messages if failed
    - Artifacts produced (files, deployments, etc.)
    
    Args:
        episode_id: Episode ID from Phase 0
        outcome: "success", "partial", "failed", or "abandoned"
        success: Boolean indicating overall success
        duration_seconds: Total time taken for workflow
        commands_executed: List of commands that were executed
        error_message: Error message if failed
        artifacts: Dictionary of artifacts produced (e.g., {"files": ["x.yaml"], "deployments": ["svc-v1"]})
    
    Returns:
        True if updated successfully, False otherwise
    
    Example:
        success = update_phase_5(
            episode_id="ep_20251119_141451_7e5733f8",
            outcome="success",
            success=True,
            duration_seconds=45.3,
            commands_executed=["kubectl apply -f service.yaml", "kubectl rollout status"],
            artifacts={"deployments": ["graphql-server-v1.0.177"]}
        )
    """
    if not EpisodicMemory or not episode_id:
        return False
    
    try:
        memory = EpisodicMemory()
        
        # Load existing episode
        episode = memory.get_episode(episode_id)
        if not episode:
            print(f"Warning: Episode {episode_id} not found for Phase 5 update", file=sys.stderr)
            return False
        
        # Ensure context and workflow exist
        if "context" not in episode:
            episode["context"] = {}
        context = episode["context"]
        
        if "workflow" not in context:
            context["workflow"] = {}
        workflow = context["workflow"]
        
        # Update workflow tracking
        workflow["phase_5_timestamp"] = datetime.now(timezone.utc).isoformat()
        workflow["phases_completed"] = workflow.get("phases_completed", []) + ["phase_5"]
        
        # Calculate duration if start time is available
        if duration_seconds is None and "phase_0_timestamp" in workflow:
            try:
                start_time = datetime.fromisoformat(workflow["phase_0_timestamp"])
                end_time = datetime.now(timezone.utc)
                duration_seconds = (end_time - start_time).total_seconds()
            except:
                pass
        
        if error_message:
            workflow["error_message"] = error_message
        
        if artifacts:
            workflow["artifacts"] = artifacts
        
        context["workflow"] = workflow
        episode["context"] = context
        
        # P1 Phase 1: Get agent information for this episode BEFORE saving
        agents_info = []
        if "phase_0_timestamp" in workflow:
            agents_info = get_agents_for_episode(workflow["phase_0_timestamp"])
        
        # Add agents to episode if any found
        if agents_info:
            episode["agents"] = agents_info
            workflow["agents_count"] = len(agents_info)
            context["workflow"] = workflow
            episode["context"] = context
        
        # Save updated context with agents
        episode_file = memory.episodes_dir / f"episode-{episode_id}.json"
        with open(episode_file, 'w') as f:
            import json
            json.dump(episode, f, indent=2)
        
        # Then use the official update_outcome method which handles index updates
        success_update = memory.update_outcome(
            episode_id=episode_id,
            outcome=outcome,
            success=success,
            duration_seconds=duration_seconds,
            commands_executed=commands_executed
        )
        
        if success_update:
            print(f"📚 Episodic capture: Completed episode {episode_id} (outcome={outcome}, success={success}, duration={duration_seconds}s)", file=sys.stderr)
        
        return success_update
        
    except Exception as e:
        print(f"Warning: Episodic capture failed in Phase 5: {e}", file=sys.stderr)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═════════════════════════════════════════════════════════════════════════════

def get_memory() -> Optional['EpisodicMemory']:
    """
    Get EpisodicMemory instance for custom operations.
    
    Returns:
        EpisodicMemory instance or None if unavailable
    
    Example:
        memory = get_memory()
        if memory:
            episodes = memory.search_episodes("deployment failed")
    """
    if not EpisodicMemory:
        return None
    
    try:
        return EpisodicMemory()
    except Exception as e:
        print(f"Warning: Could not initialize EpisodicMemory: {e}", file=sys.stderr)
        return None


def _extract_tags(
    original_prompt: str,
    enriched_prompt: str,
    command_context: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Extract relevant tags from prompts and context.
    
    Tags help with episode search and categorization.
    
    Args:
        original_prompt: Original user prompt
        enriched_prompt: Enriched prompt
        command_context: Optional command context
    
    Returns:
        List of tags
    """
    tags = []
    
    # Combine prompts for analysis
    combined = f"{original_prompt} {enriched_prompt}".lower()
    
    # Infrastructure tags
    if any(word in combined for word in ["kubernetes", "k8s", "kubectl", "pod", "deployment"]):
        tags.append("kubernetes")
    
    if any(word in combined for word in ["terraform", "tfstate", "plan", "apply"]):
        tags.append("terraform")
    
    if any(word in combined for word in ["aws", "eks", "s3", "ec2", "lambda"]):
        tags.append("aws")
    
    if any(word in combined for word in ["gcp", "gke", "cloud run"]):
        tags.append("gcp")
    
    # Operation type tags
    if any(word in combined for word in ["deploy", "deployment", "release", "rollout"]):
        tags.append("deployment")
    
    if any(word in combined for word in ["fix", "error", "issue", "problem", "debug", "troubleshoot"]):
        tags.append("troubleshooting")
    
    if any(word in combined for word in ["monitor", "check", "status", "health", "logs"]):
        tags.append("monitoring")
    
    if any(word in combined for word in ["config", "configure", "setup", "init"]):
        tags.append("configuration")
    
    # Environment tags
    if any(word in combined for word in ["prod", "production"]):
        tags.append("production")
    elif any(word in combined for word in ["dev", "development", "staging"]):
        tags.append("development")
    
    # Add command context as tag if present
    if command_context and "command" in command_context:
        cmd = command_context["command"]
        if cmd and cmd != "general_prompt":
            tags.append(f"cmd:{cmd}")
    
    return list(set(tags))  # Remove duplicates


def _sanitize_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize context to remove sensitive data before storage.
    
    Removes:
    - Credentials, tokens, passwords
    - Large data blobs
    - Sensitive file paths
    
    Args:
        context: Raw context dictionary
    
    Returns:
        Sanitized context dictionary
    """
    sanitized = {}
    
    # Keys to exclude (sensitive or too large)
    exclude_keys = {
        "password", "token", "secret", "credential", "api_key",
        "private_key", "ssh_key", "access_key", "auth"
    }
    
    for key, value in context.items():
        # Skip sensitive keys
        if any(sensitive in key.lower() for sensitive in exclude_keys):
            sanitized[key] = "[REDACTED]"
            continue
        
        # Limit string size
        if isinstance(value, str) and len(value) > 1000:
            sanitized[key] = value[:1000] + "... [truncated]"
            continue
        
        # Limit list size
        if isinstance(value, list) and len(value) > 50:
            sanitized[key] = value[:50] + ["... [truncated]"]
            continue
        
        # Recursively sanitize dicts
        if isinstance(value, dict):
            sanitized[key] = _sanitize_context(value)
            continue
        
        # Keep other values as-is
        sanitized[key] = value
    
    return sanitized


def _classify_operations(operations: List[str]) -> List[str]:
    """
    Classify operation types from command list.
    
    Returns high-level operation categories without exposing full commands.
    
    Args:
        operations: List of command strings
    
    Returns:
        List of operation type categories
    """
    categories = set()
    
    for op in operations:
        op_lower = op.lower() if isinstance(op, str) else ""
        
        if "kubectl get" in op_lower or "kubectl describe" in op_lower:
            categories.add("read")
        elif "kubectl apply" in op_lower or "kubectl create" in op_lower:
            categories.add("create")
        elif "kubectl delete" in op_lower:
            categories.add("delete")
        elif "kubectl edit" in op_lower or "kubectl patch" in op_lower:
            categories.add("update")
        elif "terraform plan" in op_lower:
            categories.add("plan")
        elif "terraform apply" in op_lower:
            categories.add("apply")
        elif "git" in op_lower:
            categories.add("git")
        elif any(cmd in op_lower for cmd in ["aws", "gcloud", "az"]):
            categories.add("cloud_cli")
        else:
            categories.add("other")
    
    return list(categories)


# CLI for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test episodic capture hook")
    parser.add_argument("phase", choices=["0", "3", "4", "5"], help="Phase to test")
    parser.add_argument("--episode-id", help="Episode ID (for phases 3-5)")
    args = parser.parse_args()
    
    if args.phase == "0":
        episode_id = capture_phase_0(
            original_prompt="Deploy graphql-server to production",
            enriched_prompt="Deploy graphql-server v1.0.177 to digital-eks-prod cluster in common namespace",
            clarification_data={"clarification_occurred": True, "ambiguity_score": 60},
            command_context={"command": "deployment"}
        )
        print(f"Created episode: {episode_id}")
    
    elif args.phase == "3":
        if not args.episode_id:
            print("Error: --episode-id required for phase 3")
            sys.exit(1)
        
        success = update_phase_3(
            episode_id=args.episode_id,
            realization_package={
                "tier": "T3",
                "operations": ["kubectl apply -f deployment.yaml", "kubectl rollout status deployment/graphql-server"]
            },
            agent_name="devops-agent"
        )
        print(f"Phase 3 update: {'success' if success else 'failed'}")
    
    elif args.phase == "4":
        if not args.episode_id:
            print("Error: --episode-id required for phase 4")
            sys.exit(1)
        
        success = update_phase_4(
            episode_id=args.episode_id,
            approval_decision="approved",
            tier="T3",
            user_feedback="Approved for deployment"
        )
        print(f"Phase 4 update: {'success' if success else 'failed'}")
    
    elif args.phase == "5":
        if not args.episode_id:
            print("Error: --episode-id required for phase 5")
            sys.exit(1)
        
        success = update_phase_5(
            episode_id=args.episode_id,
            outcome="success",
            success=True,
            duration_seconds=45.0,
            commands_executed=["kubectl apply -f deployment.yaml"],
            artifacts={"deployments": ["graphql-server-v1.0.177"]}
        )
        print(f"Phase 5 update: {'success' if success else 'failed'}")


def get_agents_for_episode(episode_timestamp: str) -> List[Dict[str, Any]]:
    """
    Extract agent information from AgentSession data for a given episode.
    
    This function queries AgentSession storage for sessions created after
    the episode timestamp and extracts relevant information:
    - agent_id: Unique session identifier
    - agent_name: Name of the agent (e.g., "gitops-operator", "terraform-architect")
    - phases: List of phases the agent went through
    - duration_seconds: Total time agent spent (from created_at to finalized_at)
    - success: Boolean indicating if agent completed successfully
    
    Args:
        episode_timestamp: ISO timestamp of episode creation (from Phase 0)
    
    Returns:
        List of agent info dictionaries
    
    Example:
        agents = get_agents_for_episode("2026-01-08T14:30:00+00:00")
        # Returns:
        # [
        #   {
        #     "agent_id": "agent-20260108-143015-abc123",
        #     "agent_name": "gitops-operator",
        #     "phases": ["initializing", "planning", "approval", "executing", "completed"],
        #     "duration_seconds": 45.2,
        #     "success": True
        #   }
        # ]
    """
    if not _AgentSession:
        return []
    
    try:
        # Parse episode timestamp
        episode_time = datetime.fromisoformat(episode_timestamp)
        if episode_time.tzinfo is None:
            episode_time = episode_time.replace(tzinfo=timezone.utc)
        
        # Initialize AgentSession manager
        session_manager = _AgentSession()
        
        # List all sessions
        all_sessions = session_manager.list_sessions()
        
        agents_info = []
        for session_summary in all_sessions:
            try:
                # Parse session creation time
                session_created = datetime.fromisoformat(session_summary.get("created_at", ""))
                if session_created.tzinfo is None:
                    session_created = session_created.replace(tzinfo=timezone.utc)
                
                # Only include sessions created after episode start (within 5 minute window)
                time_diff = (session_created - episode_time).total_seconds()
                if time_diff < 0 or time_diff > 300:  # Within 5 minutes
                    continue
                
                # Get full session details
                agent_id = session_summary["agent_id"]
                full_session = session_manager.get_session(agent_id)
                
                if not full_session:
                    continue
                
                # Extract phase history
                phases = ["initializing"]  # Start phase
                for transition in full_session.get("history", []):
                    phases.append(transition.get("to_phase", ""))
                
                # Calculate duration if finalized
                duration_seconds = None
                if "finalized_at" in full_session:
                    try:
                        created = datetime.fromisoformat(full_session["created_at"])
                        finalized = datetime.fromisoformat(full_session["finalized_at"])
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        if finalized.tzinfo is None:
                            finalized = finalized.replace(tzinfo=timezone.utc)
                        duration_seconds = (finalized - created).total_seconds()
                    except:
                        duration_seconds = full_session.get("duration_seconds")
                
                # Determine success
                current_phase = full_session.get("phase", "")
                success = current_phase == "completed"
                
                # Build agent info
                agent_info = {
                    "agent_id": agent_id,
                    "agent_name": full_session.get("agent_name", "unknown"),
                    "phases": phases,
                    "duration_seconds": duration_seconds,
                    "success": success
                }
                
                agents_info.append(agent_info)
                
            except Exception as e:
                # Skip sessions with parsing errors
                print(f"Warning: Could not process session {session_summary.get('agent_id')}: {e}", file=sys.stderr)
                continue
        
        return agents_info
        
    except Exception as e:
        print(f"Warning: Could not retrieve agent sessions: {e}", file=sys.stderr)
        return []

