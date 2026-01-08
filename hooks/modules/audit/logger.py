"""
Audit logger for tool executions.

Logs all tool executions to session and daily log files.
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from ..core.paths import get_logs_dir

logger = logging.getLogger(__name__)


class AuditLogger:
    """Audit logger for tracking all tool executions."""

    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize audit logger.

        Args:
            log_dir: Override log directory (for testing)
        """
        if log_dir is not None:
            self.log_dir = Path(log_dir) if isinstance(log_dir, str) else log_dir
        else:
            self.log_dir = get_logs_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = os.environ.get("CLAUDE_SESSION_ID", "default")

    def hash_output(self, output: str, max_length: int = 1000) -> str:
        """
        Create hash of output for audit trail.

        Args:
            output: Output string to hash
            max_length: Max length to consider for hashing

        Returns:
            Truncated SHA256 hash
        """
        truncated = output[:max_length] if len(output) > max_length else output
        return hashlib.sha256(truncated.encode()).hexdigest()[:16]

    def log_execution(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        duration: float,
        exit_code: int = 0,
        tier: str = "unknown"
    ) -> None:
        """
        Log tool execution details.

        Args:
            tool_name: Name of the tool
            parameters: Tool parameters
            result: Execution result
            duration: Duration in seconds
            exit_code: Exit code (0 = success)
            tier: Security tier
        """
        timestamp = datetime.now().isoformat()

        # Extract command for bash tools
        command = ""
        if tool_name.lower() == "bash":
            command = parameters.get("command", "")

        # Process result
        output_preview = ""
        output_hash = ""
        if result:
            result_str = str(result)
            output_preview = result_str[:200] + "..." if len(result_str) > 200 else result_str
            output_hash = self.hash_output(result_str)

        # Create audit record
        audit_record = {
            "timestamp": timestamp,
            "session_id": self.session_id,
            "tool_name": tool_name,
            "command": command,
            "parameters": self._sanitize_params(parameters),
            "duration_ms": round(duration * 1000, 2),
            "exit_code": exit_code,
            "tier": tier,
            "output_hash": output_hash,
            "output_preview": output_preview,
        }

        # Write to session log
        session_log_file = self.log_dir / f"session-{self.session_id}.jsonl"
        self._write_record(session_log_file, audit_record)

        # Write to daily audit log
        daily_log_file = self.log_dir / f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        self._write_record(daily_log_file, audit_record)

        logger.debug(f"Logged execution: {tool_name} - {command[:50]} - {duration:.2f}s")

    def _sanitize_params(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from parameters."""
        sanitized = {}
        sensitive_keys = ["password", "secret", "token", "key", "credential"]

        for key, value in parameters.items():
            if any(s in key.lower() for s in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, str) and len(value) > 500:
                sanitized[key] = value[:500] + "...[truncated]"
            else:
                sanitized[key] = value

        return sanitized

    def _write_record(self, file_path: Path, record: Dict) -> None:
        """Write record to JSONL file."""
        try:
            with open(file_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Error writing audit record to {file_path}: {e}")


# Singleton logger
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get singleton audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def log_execution(
    tool_name: str,
    parameters: Dict[str, Any],
    result: Any,
    duration: float,
    exit_code: int = 0,
    tier: str = "unknown"
) -> None:
    """Log tool execution (convenience function)."""
    get_audit_logger().log_execution(
        tool_name, parameters, result, duration, exit_code, tier
    )
