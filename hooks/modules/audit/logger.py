"""
Audit logger for tool executions.

Logs all tool executions to daily audit log files.
Note: session-<id>.jsonl was removed — it duplicated the daily audit log
with no additional value (session_id was always "default").
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from ..core.paths import get_logs_dir
from ..core.state import get_session_id

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
        self.session_id = get_session_id()

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
        # UTC, not local time: gaia metrics (bin/cli/metrics.py) filters
        # "today" / anomaly windows by comparing against
        # datetime.now(timezone.utc) -- a naive local timestamp here would
        # silently drift the Activity Today section by the host's UTC offset.
        timestamp = datetime.now(timezone.utc).isoformat()

        # Extract command for bash tools
        command = ""
        if tool_name.lower() == "bash":
            command = parameters.get("command", "")

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
        }

        # Write to daily audit log (session log removed — was always "session-default.jsonl")
        # Filename rotates on UTC midnight, matching `timestamp` above.
        daily_log_file = self.log_dir / f"audit-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        self._write_record(daily_log_file, audit_record)

        logger.debug(f"Logged execution: {tool_name} - {command[:50]} - {duration:.2f}s")

    def log_error(
        self,
        component: str,
        error_type: str,
        detail: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a component error to the always-on daily audit log.

        Unlike the ``logging`` module (which hook code routes through a
        ``NullHandler`` by default -- see modules.core.logging_setup), this writes
        to the same ``audit-YYYY-MM-DD.jsonl`` the tool-execution audit uses, so
        a rare-but-critical failure (e.g. a persistence failure that falls back
        to a hollow "ask") is diagnosable AFTER the fact regardless of whether
        GAIA_DEBUG was set. ``gaia metrics`` already loads these files via the
        ``audit-*.jsonl`` glob, so error records surface in the same window.

        Args:
            component: Subsystem that raised the error (e.g. "gaia.approvals").
            error_type: Short machine tag (e.g. "approval_persist_failed").
            detail: The underlying exception text or human-readable detail.
            context: Optional extra fields (sanitized) for triage.
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "event": "error",
            "component": component,
            "error_type": error_type,
            "detail": detail[:2000],
        }
        if context:
            record["context"] = self._sanitize_params(context)
        daily_log_file = self.log_dir / f"audit-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        self._write_record(daily_log_file, record)

    def log_event(
        self,
        event: str,
        component: str,
        *,
        tier: Optional[str] = None,
        reason: Optional[str] = None,
        fingerprint: Optional[str] = None,
        origin: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a synthetic (non-execution) event to the always-on audit log.

        Companion to ``log_error``: both write to the same
        ``audit-YYYY-MM-DD.jsonl`` the tool-execution audit uses (NOT gated by
        GAIA_DEBUG), so a policy-level signal is diagnosable after the fact and
        loadable by ``gaia metrics`` via the ``audit-*.jsonl`` glob. Where
        ``log_error`` emits ``event="error"`` records, this emits an arbitrary
        ``event`` tag (e.g. ``t3_degraded_allow``) alongside a component and the
        optional tier / reason / fingerprint / origin fields the metrics layer
        discriminates on.

        A ``fingerprint`` is expected to be a HASH (e.g. the approval store's
        SHA-256 sealed-payload fingerprint) -- never a raw command -- so no
        secret ever lands in the log. Any ``context`` dict is run through the
        same ``_sanitize_params`` redaction the execution audit uses.

        Args:
            event: Machine event tag (e.g. "t3_degraded_allow").
            component: Subsystem that raised the event (e.g. "gaia.bash_validator").
            tier: Optional security tier string (e.g. "T3").
            reason: Optional underlying-cause tag (e.g. "approval_persist_failed").
            fingerprint: Optional redacted command fingerprint (a hash, not the
                raw command).
            origin: Optional origin marker ("subagent" / "orchestrator") when
                determinable.
            context: Optional extra fields (sanitized) for triage.
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "event": event,
            "component": component,
        }
        if tier is not None:
            record["tier"] = tier
        if reason is not None:
            record["reason"] = reason
        if fingerprint is not None:
            record["fingerprint"] = fingerprint
        if origin is not None:
            record["origin"] = origin
        if context:
            record["context"] = self._sanitize_params(context)
        daily_log_file = self.log_dir / f"audit-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        self._write_record(daily_log_file, record)

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


def log_error(
    component: str,
    error_type: str,
    detail: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a component error to the always-on audit log (convenience function)."""
    get_audit_logger().log_error(component, error_type, detail, context)


def log_event(
    event: str,
    component: str,
    *,
    tier: Optional[str] = None,
    reason: Optional[str] = None,
    fingerprint: Optional[str] = None,
    origin: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a synthetic audit event to the always-on log (convenience function)."""
    get_audit_logger().log_event(
        event,
        component,
        tier=tier,
        reason=reason,
        fingerprint=fingerprint,
        origin=origin,
        context=context,
    )
