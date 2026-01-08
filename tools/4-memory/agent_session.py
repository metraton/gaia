#!/usr/bin/env python3
"""
Agent Session Management for GAIA-OPS

This module provides functionality to manage agent execution sessions with resume capabilities.
Designed to maintain context across approval pauses and multi-turn investigations.

Architecture:
- Session state stored in .claude/session/{agent_id}/state.json
- Resume logic based on phase, timing, and error status
- Integration with workflow hooks for lifecycle management
- No direct coupling to episodic memory (query by timestamp if needed)

Key Capabilities:
- Create and track agent sessions with unique IDs
- Persist session state for resume after interruptions
- Intelligent resume decision based on context
- Automatic cleanup of stale sessions
- Minimal footprint - only active session data

Typical Flow:
1. create_session() -> agent_id
2. update_state() -> throughout execution
3. should_resume() -> on next delegation
4. finalize_session() -> on completion
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AgentSession:
    """
    Manages agent execution sessions with resume capabilities.
    
    A session represents a unit of agent work that may span multiple interactions,
    approval pauses, or investigative turns. Sessions maintain context to enable
    seamless resume after interruptions.
    """
    
    # Valid phases for agent execution
    VALID_PHASES = frozenset([
        "initializing",   # Session created, agent starting
        "investigating",  # Agent gathering information
        "planning",       # Agent creating execution plan
        "approval",       # Waiting for user approval
        "executing",      # Agent performing operations
        "validating",     # Agent verifying results
        "completed",      # Session finished successfully
        "failed",         # Session failed with errors
        "abandoned"       # Session abandoned by user
    ])
    
    # Phases that can be resumed
    RESUMABLE_PHASES = frozenset([
        "approval",       # Most common: resume after user approves
        "investigating",  # Multi-turn investigations
        "planning"        # Resume planning if interrupted
    ])
    
    # Resume timeout (after this, create new session)
    RESUME_TIMEOUT_MINUTES = 30
    
    def __init__(self, base_path: Optional[Union[str, Path]] = None):
        """
        Initialize AgentSession manager.
        
        Args:
            base_path: Base directory for session storage.
                      Defaults to .claude/session/
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Try to find .claude directory
            candidates = [
                Path(".claude/session"),
                Path("/home/jaguilar/aaxis/vtr/repositories/.claude/session")
            ]
            
            for path in candidates:
                if path.parent.exists():
                    self.base_path = path
                    break
            else:
                self.base_path = candidates[0]
        
        # Ensure base directory exists
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"AgentSession initialized at {self.base_path}")
    
    def create_session(
        self,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        purpose: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create a new agent session.
        
        Args:
            agent_id: Optional custom agent ID (auto-generated if not provided)
            agent_name: Name of agent (e.g., "terraform-architect", "gitops-operator")
            purpose: Purpose of session (e.g., "approval_workflow", "investigation")
            metadata: Additional metadata (task_id, tags, etc.)
        
        Returns:
            agent_id: Unique session identifier
        """
        # Generate agent_id if not provided
        if not agent_id:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            agent_id = f"agent-{timestamp}-{uuid.uuid4().hex[:8]}"
        
        # Create session state
        now = datetime.now(timezone.utc)
        session_state = {
            "agent_id": agent_id,
            "agent_name": agent_name or "unknown",
            "purpose": purpose or "general",
            "created_at": now.isoformat(),
            "last_updated": now.isoformat(),
            "phase": "initializing",
            "metadata": metadata or {},
            "resume_ready": False,  # Not ready until phase changes
            "history": [],  # Track phase transitions
            "error_count": 0,
            "last_error": None
        }
        
        # Save to disk
        session_dir = self.base_path / agent_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        state_file = session_dir / "state.json"
        with open(state_file, 'w') as f:
            json.dump(session_state, f, indent=2)
        
        logger.info(f"Created session: {agent_id} (agent: {agent_name}, purpose: {purpose})")
        
        return agent_id
    
    def update_state(
        self,
        agent_id: str,
        phase: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Update session state during execution.
        
        Args:
            agent_id: Session identifier
            phase: New execution phase
            metadata: Additional metadata to merge
            error: Error message if phase failed
        
        Returns:
            True if updated successfully, False if session not found
        """
        session = self._load_session(agent_id)
        if not session:
            logger.error(f"Session {agent_id} not found")
            return False
        
        now = datetime.now(timezone.utc)
        
        # Update phase if provided
        if phase:
            if phase not in self.VALID_PHASES:
                logger.warning(f"Invalid phase '{phase}'. Valid phases: {self.VALID_PHASES}")
                return False
            
            # Record phase transition
            old_phase = session["phase"]
            session["history"].append({
                "from_phase": old_phase,
                "to_phase": phase,
                "timestamp": now.isoformat()
            })
            
            session["phase"] = phase
            
            # Update resume_ready based on phase
            session["resume_ready"] = phase in self.RESUMABLE_PHASES
        
        # Update metadata if provided
        if metadata:
            session["metadata"].update(metadata)
        
        # Track errors
        if error:
            session["error_count"] += 1
            session["last_error"] = {
                "message": error,
                "timestamp": now.isoformat()
            }
        
        session["last_updated"] = now.isoformat()
        
        # Save updated state
        self._save_session(agent_id, session)
        
        logger.debug(f"Updated session {agent_id}: phase={phase}, resume_ready={session['resume_ready']}")
        
        return True
    
    def should_resume(self, agent_id: str) -> bool:
        """
        Decide if an existing session should be resumed.
        
        Decision criteria:
        - Session exists and state is valid
        - Last activity < RESUME_TIMEOUT_MINUTES
        - Phase is resumable (approval, investigating, planning)
        - No fatal errors
        
        Args:
            agent_id: Session identifier to check
        
        Returns:
            True if session should be resumed, False otherwise
        """
        session = self._load_session(agent_id)
        if not session:
            logger.debug(f"Session {agent_id} not found - cannot resume")
            return False
        
        # Check if resume_ready flag is set
        if not session.get("resume_ready", False):
            logger.debug(f"Session {agent_id} not resume_ready (phase: {session.get('phase')})")
            return False
        
        # Check phase
        phase = session.get("phase")
        if phase not in self.RESUMABLE_PHASES:
            logger.debug(f"Session {agent_id} phase '{phase}' not resumable")
            return False
        
        # Check timeout
        try:
            last_updated = datetime.fromisoformat(session["last_updated"])
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            
            elapsed = datetime.now(timezone.utc) - last_updated
            if elapsed > timedelta(minutes=self.RESUME_TIMEOUT_MINUTES):
                logger.debug(f"Session {agent_id} timed out ({elapsed.total_seconds() / 60:.1f}m > {self.RESUME_TIMEOUT_MINUTES}m)")
                return False
        except (ValueError, KeyError) as e:
            logger.warning(f"Could not parse last_updated for session {agent_id}: {e}")
            return False
        
        # Check for fatal errors
        error_count = session.get("error_count", 0)
        if error_count >= 3:
            logger.debug(f"Session {agent_id} has too many errors ({error_count})")
            return False
        
        logger.info(f"Session {agent_id} can be resumed (phase: {phase}, age: {elapsed.total_seconds() / 60:.1f}m)")
        return True
    
    def get_session(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve session state.
        
        Args:
            agent_id: Session identifier
        
        Returns:
            Session state dict or None if not found
        """
        return self._load_session(agent_id)
    
    def finalize_session(
        self,
        agent_id: str,
        outcome: str,
        summary: Optional[str] = None
    ) -> bool:
        """
        Finalize a session when agent completes.
        
        Args:
            agent_id: Session identifier
            outcome: Final outcome ("completed", "failed", "abandoned")
            summary: Optional summary of session results
        
        Returns:
            True if finalized successfully, False if session not found
        """
        session = self._load_session(agent_id)
        if not session:
            logger.error(f"Session {agent_id} not found")
            return False
        
        now = datetime.now(timezone.utc)
        
        # Update to final phase
        valid_outcomes = {"completed", "failed", "abandoned"}
        if outcome not in valid_outcomes:
            logger.warning(f"Invalid outcome '{outcome}'. Valid: {valid_outcomes}")
            outcome = "failed"
        
        session["phase"] = outcome
        session["resume_ready"] = False  # Cannot resume finalized sessions
        session["finalized_at"] = now.isoformat()
        session["last_updated"] = now.isoformat()
        
        if summary:
            session["summary"] = summary
        
        # Calculate total duration
        try:
            created = datetime.fromisoformat(session["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            duration = (now - created).total_seconds()
            session["duration_seconds"] = duration
        except:
            pass
        
        # Save final state
        self._save_session(agent_id, session)
        
        logger.info(f"Finalized session {agent_id}: outcome={outcome}, duration={session.get('duration_seconds', 'unknown')}s")
        
        return True
    
    def cleanup_old_sessions(self, hours: int = 24) -> int:
        """
        Remove sessions older than specified hours.
        
        Args:
            hours: Age threshold in hours
        
        Returns:
            Number of sessions deleted
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        deleted_count = 0
        
        for session_dir in self.base_path.iterdir():
            if not session_dir.is_dir():
                continue
            
            state_file = session_dir / "state.json"
            if not state_file.exists():
                continue
            
            try:
                session = self._load_session(session_dir.name)
                if not session:
                    continue
                
                # Check last_updated timestamp
                last_updated = datetime.fromisoformat(session["last_updated"])
                if last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=timezone.utc)
                
                if last_updated < cutoff:
                    # Delete session directory
                    import shutil
                    shutil.rmtree(session_dir)
                    deleted_count += 1
                    logger.debug(f"Deleted old session: {session_dir.name}")
            except Exception as e:
                logger.warning(f"Error checking session {session_dir.name}: {e}")
                continue
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} sessions older than {hours}h")
        
        return deleted_count
    
    def list_sessions(
        self,
        active_only: bool = False,
        agent_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all sessions with optional filters.
        
        Args:
            active_only: Only return sessions with resume_ready=True
            agent_name: Filter by agent name
        
        Returns:
            List of session summaries
        """
        sessions = []
        
        for session_dir in self.base_path.iterdir():
            if not session_dir.is_dir():
                continue
            
            session = self._load_session(session_dir.name)
            if not session:
                continue
            
            # Apply filters
            if active_only and not session.get("resume_ready", False):
                continue
            
            if agent_name and session.get("agent_name") != agent_name:
                continue
            
            # Create summary
            summary = {
                "agent_id": session["agent_id"],
                "agent_name": session.get("agent_name", "unknown"),
                "phase": session.get("phase", "unknown"),
                "created_at": session.get("created_at"),
                "last_updated": session.get("last_updated"),
                "resume_ready": session.get("resume_ready", False),
                "error_count": session.get("error_count", 0)
            }
            sessions.append(summary)
        
        # Sort by last_updated (most recent first)
        sessions.sort(key=lambda s: s.get("last_updated", ""), reverse=True)
        
        return sessions
    
    def _load_session(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Load session state from disk."""
        session_dir = self.base_path / agent_id
        state_file = session_dir / "state.json"
        
        if not state_file.exists():
            return None
        
        try:
            with open(state_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading session {agent_id}: {e}")
            return None
    
    def _save_session(self, agent_id: str, session_state: Dict[str, Any]):
        """Save session state to disk."""
        session_dir = self.base_path / agent_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        state_file = session_dir / "state.json"
        with open(state_file, 'w') as f:
            json.dump(session_state, f, indent=2)


# Convenience functions for direct use

def create_session(
    agent_name: Optional[str] = None,
    purpose: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Create a new agent session (convenience function).
    
    Args:
        agent_name: Name of agent
        purpose: Purpose of session
        metadata: Additional metadata
    
    Returns:
        agent_id: Session identifier
    """
    manager = AgentSession()
    return manager.create_session(
        agent_name=agent_name,
        purpose=purpose,
        metadata=metadata
    )


def should_resume(agent_id: str) -> bool:
    """
    Check if session should be resumed (convenience function).
    
    Args:
        agent_id: Session identifier
    
    Returns:
        True if should resume, False otherwise
    """
    manager = AgentSession()
    return manager.should_resume(agent_id)


def get_session(agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Get session state (convenience function).
    
    Args:
        agent_id: Session identifier
    
    Returns:
        Session state dict or None
    """
    manager = AgentSession()
    return manager.get_session(agent_id)


def update_state(
    agent_id: str,
    phase: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None
) -> bool:
    """
    Update session state (convenience function).
    
    Args:
        agent_id: Session identifier
        phase: New phase
        metadata: Additional metadata
        error: Error message
    
    Returns:
        True if updated successfully
    """
    manager = AgentSession()
    return manager.update_state(agent_id, phase, metadata, error)


def finalize_session(
    agent_id: str,
    outcome: str,
    summary: Optional[str] = None
) -> bool:
    """
    Finalize a session (convenience function).
    
    Args:
        agent_id: Session identifier
        outcome: Final outcome
        summary: Optional summary
    
    Returns:
        True if finalized successfully
    """
    manager = AgentSession()
    return manager.finalize_session(agent_id, outcome, summary)


# CLI interface for testing and management
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Agent Session Management")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new session")
    create_parser.add_argument("--agent", help="Agent name")
    create_parser.add_argument("--purpose", help="Session purpose")
    
    # Update command
    update_parser = subparsers.add_parser("update", help="Update session state")
    update_parser.add_argument("agent_id", help="Agent ID")
    update_parser.add_argument("--phase", help="New phase")
    update_parser.add_argument("--error", help="Error message")
    
    # Should-resume command
    resume_parser = subparsers.add_parser("should-resume", help="Check if should resume")
    resume_parser.add_argument("agent_id", help="Agent ID")
    
    # Get command
    get_parser = subparsers.add_parser("get", help="Get session state")
    get_parser.add_argument("agent_id", help="Agent ID")
    
    # Finalize command
    finalize_parser = subparsers.add_parser("finalize", help="Finalize session")
    finalize_parser.add_argument("agent_id", help="Agent ID")
    finalize_parser.add_argument("outcome", choices=["completed", "failed", "abandoned"])
    finalize_parser.add_argument("--summary", help="Summary of results")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--active-only", action="store_true", help="Only active sessions")
    list_parser.add_argument("--agent", help="Filter by agent name")
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Cleanup old sessions")
    cleanup_parser.add_argument("--hours", type=int, default=24, help="Age threshold")
    
    args = parser.parse_args()
    
    manager = AgentSession()
    
    if args.command == "create":
        agent_id = manager.create_session(
            agent_name=args.agent,
            purpose=args.purpose
        )
        print(f"Created session: {agent_id}")
    
    elif args.command == "update":
        success = manager.update_state(
            agent_id=args.agent_id,
            phase=args.phase,
            error=args.error
        )
        if success:
            print(f"Updated session: {args.agent_id}")
        else:
            print(f"Failed to update session: {args.agent_id}")
    
    elif args.command == "should-resume":
        should = manager.should_resume(args.agent_id)
        print(f"Should resume {args.agent_id}: {should}")
        sys.exit(0 if should else 1)
    
    elif args.command == "get":
        session = manager.get_session(args.agent_id)
        if session:
            print(json.dumps(session, indent=2))
        else:
            print(f"Session {args.agent_id} not found")
            sys.exit(1)
    
    elif args.command == "finalize":
        success = manager.finalize_session(
            agent_id=args.agent_id,
            outcome=args.outcome,
            summary=args.summary
        )
        if success:
            print(f"Finalized session: {args.agent_id}")
        else:
            print(f"Failed to finalize session: {args.agent_id}")
    
    elif args.command == "list":
        sessions = manager.list_sessions(
            active_only=args.active_only,
            agent_name=args.agent
        )
        if sessions:
            print(f"\nFound {len(sessions)} sessions:\n")
            for s in sessions:
                status = "ACTIVE" if s["resume_ready"] else "INACTIVE"
                print(f"{status} {s['agent_id']}")
                print(f"  Agent: {s['agent_name']}, Phase: {s['phase']}")
                print(f"  Last updated: {s['last_updated']}")
                if s['error_count'] > 0:
                    print(f"  Errors: {s['error_count']}")
                print()
        else:
            print("No sessions found")
    
    elif args.command == "cleanup":
        count = manager.cleanup_old_sessions(hours=args.hours)
        print(f"Cleaned up {count} sessions older than {args.hours}h")
    
    else:
        parser.print_help()
