#!/usr/bin/env python3
"""
Task Wrapper - Interceptor for Task tool invocations
Logs all Task tool executions (agente invocations) to audit trail
Integrates with post_tool_use.py for consistent audit logging

Usage:
  - Called automatically when Task tool is invoked
  - Captures agent type, prompt, and results
  - Logs to .claude/logs/session-{SESSION_ID}.jsonl
  - Compatible with security tiers (T0-T3)
"""

import sys
import json
import hashlib
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def find_claude_dir() -> Path:
    """Find the .claude directory by searching upward from current location"""
    current = Path.cwd()

    # If we're already in a .claude directory, return it
    if current.name == ".claude":
        return current

    # Look for .claude in current directory
    claude_dir = current / ".claude"
    if claude_dir.exists():
        return claude_dir

    # Search upward through parent directories
    for parent in current.parents:
        claude_dir = parent / ".claude"
        if claude_dir.exists():
            return claude_dir

    # Fallback
    logger.warning(f"No .claude directory found, using {current}/.claude")
    return current / ".claude"


class TaskAuditLogger:
    """Specialized logger for Task tool invocations"""

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            claude_dir = find_claude_dir()
            self.log_dir = claude_dir / "logs"

        self.log_dir.mkdir(exist_ok=True, parents=True)
        self.session_id = os.environ.get("CLAUDE_SESSION_ID", "default")

    def hash_output(self, output: str, max_length: int = 500) -> str:
        """Create hash of output for audit trail"""
        truncated = output[:max_length] if len(output) > max_length else output
        return hashlib.sha256(truncated.encode()).hexdigest()[:16]

    def _classify_agent_tier(self, agent_type: str) -> str:
        """Classify agent by security tier"""
        # T0: Read-only agents
        if agent_type in ["gcp-troubleshooter", "aws-troubleshooter", "Explore"]:
            return "T0"

        # T1: Validation agents
        if agent_type in ["Plan", "terraform-architect"]:
            return "T1"

        # T2-T3: Write agents (require approval)
        if agent_type in ["gitops-operator", "terraform-architect"]:
            return "T3"  # terraform apply, kubectl apply

        return "T2"

    def log_task_invocation(self,
                           agent_type: str,
                           description: str,
                           prompt: str,
                           result: Any = None,
                           duration: float = 0,
                           status: str = "PENDING") -> Dict:
        """Log a Task tool invocation"""

        timestamp = datetime.now().isoformat()

        # Process result
        result_preview = ""
        result_hash = ""
        if result:
            result_str = str(result)
            result_preview = result_str[:200] + "..." if len(result_str) > 200 else result_str
            result_hash = self.hash_output(result_str)

        # Create audit record (compatible with post_tool_use.py format)
        audit_record = {
            "timestamp": timestamp,
            "session_id": self.session_id,
            "tool_name": "Task",
            "agent_type": agent_type,
            "description": description,
            "tier": self._classify_agent_tier(agent_type),
            "command": f"Task({agent_type}): {description}",
            "prompt_hash": self.hash_output(prompt),
            "prompt_preview": prompt[:150] + "..." if len(prompt) > 150 else prompt,
            "duration_ms": round(duration * 1000, 2),
            "result_hash": result_hash,
            "result_preview": result_preview,
            "status": status
        }

        # Write to session log
        session_log_file = self.log_dir / f"session-{self.session_id}.jsonl"
        with open(session_log_file, "a") as f:
            f.write(json.dumps(audit_record) + "\n")

        # Write to daily audit log
        daily_log_file = self.log_dir / f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(daily_log_file, "a") as f:
            f.write(json.dumps(audit_record) + "\n")

        # Write to agent-specific log
        agent_log_file = self.log_dir / f"agent-{agent_type}-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(agent_log_file, "a") as f:
            f.write(json.dumps(audit_record) + "\n")

        logger.info(f"Logged Task invocation: {agent_type} - {description[:40]} - {duration:.2f}s - {status}")

        return audit_record

    def log_task_completion(self,
                           agent_type: str,
                           description: str,
                           duration: float,
                           exit_code: int = 0,
                           result_message: str = "") -> Dict:
        """Log Task completion with final status"""

        timestamp = datetime.now().isoformat()
        status = "SUCCESS" if exit_code == 0 else "FAILED"

        audit_record = {
            "timestamp": timestamp,
            "session_id": self.session_id,
            "tool_name": "Task",
            "agent_type": agent_type,
            "description": description,
            "tier": self._classify_agent_tier(agent_type),
            "command": f"Task({agent_type}): {description}",
            "duration_ms": round(duration * 1000, 2),
            "exit_code": exit_code,
            "result_message": result_message[:200],
            "status": status
        }

        # Write to logs
        session_log_file = self.log_dir / f"session-{self.session_id}.jsonl"
        with open(session_log_file, "a") as f:
            f.write(json.dumps(audit_record) + "\n")

        daily_log_file = self.log_dir / f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(daily_log_file, "a") as f:
            f.write(json.dumps(audit_record) + "\n")

        agent_log_file = self.log_dir / f"agent-{agent_type}-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(agent_log_file, "a") as f:
            f.write(json.dumps(audit_record) + "\n")

        logger.info(f"Task completion: {agent_type} - {description[:40]} - {duration:.2f}s - {status}")

        return audit_record


def main():
    """Main entry point for testing"""
    import argparse

    parser = argparse.ArgumentParser(description="Task Wrapper - Log Task tool invocations")
    parser.add_argument("--agent", required=True, help="Agent type (e.g., terraform-architect)")
    parser.add_argument("--description", required=True, help="Task description")
    parser.add_argument("--prompt", default="", help="Task prompt")
    parser.add_argument("--result", default="", help="Task result")
    parser.add_argument("--duration", type=float, default=0.0, help="Execution duration in seconds")
    parser.add_argument("--status", default="PENDING", help="Task status")
    parser.add_argument("--completion", action="store_true", help="Log as completion (not invocation)")
    parser.add_argument("--exit-code", type=int, default=0, help="Exit code (for completion)")
    parser.add_argument("--result-message", default="", help="Result message (for completion)")

    args = parser.parse_args()

    logger = TaskAuditLogger()

    if args.completion:
        record = logger.log_task_completion(
            agent_type=args.agent,
            description=args.description,
            duration=args.duration,
            exit_code=args.exit_code,
            result_message=args.result_message
        )
    else:
        record = logger.log_task_invocation(
            agent_type=args.agent,
            description=args.description,
            prompt=args.prompt,
            result=args.result,
            duration=args.duration,
            status=args.status
        )

    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
