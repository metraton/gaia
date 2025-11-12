#!/usr/bin/env python3
"""
Phase D: Execution with Profiles

Executes commands according to agent-specific execution profiles.
Handles timeouts, retries with exponential backoff, fallbacks, and logging.

Reference: Agent-Execution-Profiles.md
"""

import subprocess
import time
import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Status of command execution"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RETRY_EXCEEDED = "retry_exceeded"
    FALLBACK_USED = "fallback_used"


@dataclass
class ExecutionProfile:
    """Definition of how a command should execute"""
    timeout_seconds: int
    max_retries: int
    retry_backoff_strategy: str  # "exponential", "linear"
    health_check_command: Optional[str] = None
    fallback_commands: Optional[List[str]] = None
    flags: Optional[List[str]] = None
    parse_json_output: bool = False


@dataclass
class ExecutionMetrics:
    """Metrics from command execution"""
    status: ExecutionStatus
    duration_ms: int
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    retry_attempts: int = 0
    command_used: str = ""  # Which command actually ran (original or fallback)
    output_lines: int = 0


class ExecutionManager:
    """
    Executes commands with profiles, handling timeouts, retries, and fallbacks.

    Philosophy:
    - Each agent type has defined profiles
    - Retry is automatic for transient errors
    - Fallback to alternative commands if primary fails
    - All execution is logged for benchmarking
    """

    def __init__(self):
        self.execution_profiles: Dict[str, ExecutionProfile] = {}
        self._setup_default_profiles()

    def _setup_default_profiles(self):
        """Setup standard execution profiles from Agent-Execution-Profiles.md"""

        # Terraform profiles
        self.execution_profiles["terraform-validate"] = ExecutionProfile(
            timeout_seconds=30,
            max_retries=1,
            retry_backoff_strategy="exponential",
            health_check_command="test -f terraform.tfstate",
            flags=["--no-color"]
        )

        self.execution_profiles["terraform-plan"] = ExecutionProfile(
            timeout_seconds=300,  # 5 min
            max_retries=2,
            retry_backoff_strategy="exponential",
            flags=["--no-color", "-detailed-exitcode"]
        )

        self.execution_profiles["terraform-apply"] = ExecutionProfile(
            timeout_seconds=600,  # 10 min
            max_retries=1,
            retry_backoff_strategy="exponential",
            flags=["--no-color", "-input=false"]
        )

        # Flux profiles
        self.execution_profiles["flux-check"] = ExecutionProfile(
            timeout_seconds=30,
            max_retries=2,
            retry_backoff_strategy="exponential",
            flags=["--pre"]
        )

        self.execution_profiles["flux-reconcile"] = ExecutionProfile(
            timeout_seconds=300,  # 5 min
            max_retries=2,
            retry_backoff_strategy="exponential",
            flags=["--with-source", "--timeout=5m"],
            fallback_commands=["kubectl apply -f"]
        )

        # Helm profiles
        self.execution_profiles["helm-upgrade"] = ExecutionProfile(
            timeout_seconds=600,  # 10 min
            max_retries=1,
            retry_backoff_strategy="exponential",
            flags=["--wait", "--atomic", "--timeout=10m"]
        )

        # kubectl profiles
        self.execution_profiles["kubectl-wait"] = ExecutionProfile(
            timeout_seconds=300,  # 5 min
            max_retries=1,
            retry_backoff_strategy="exponential"
        )

        # Docker profiles
        self.execution_profiles["docker-build"] = ExecutionProfile(
            timeout_seconds=900,  # 15 min
            max_retries=1,
            retry_backoff_strategy="exponential",
            flags=["--progress=auto"]
        )

        self.execution_profiles["docker-push"] = ExecutionProfile(
            timeout_seconds=300,  # 5 min
            max_retries=3,
            retry_backoff_strategy="exponential"
        )

        logger.debug(f"Loaded {len(self.execution_profiles)} execution profiles")

    def execute(
        self,
        command: str,
        profile_name: str,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None
    ) -> ExecutionMetrics:
        """
        Execute command with defined profile.

        Args:
            command: Command to execute
            profile_name: Profile to use (e.g. "terraform-plan")
            cwd: Working directory
            env: Environment variables

        Returns:
            ExecutionMetrics with result
        """

        if profile_name not in self.execution_profiles:
            logger.warning(f"Unknown profile: {profile_name}, using defaults")
            profile = ExecutionProfile(
                timeout_seconds=60,
                max_retries=2,
                retry_backoff_strategy="exponential"
            )
        else:
            profile = self.execution_profiles[profile_name]

        logger.debug(f"Executing with profile {profile_name}: {command}")
        start_time = time.time()

        # Attempt execution with retries
        for attempt in range(profile.max_retries + 1):
            try:
                metrics = self._execute_with_timeout(
                    command=command,
                    timeout=profile.timeout_seconds,
                    cwd=cwd,
                    env=env,
                    profile_name=profile_name
                )

                # Success
                if metrics.exit_code == 0 or (profile_name == "terraform-plan" and metrics.exit_code == 2):
                    metrics.retry_attempts = attempt
                    metrics.duration_ms = int((time.time() - start_time) * 1000)
                    return metrics

                # Transient error? Retry
                if self._is_transient_error(metrics.stderr) and attempt < profile.max_retries:
                    wait_time = self._calculate_backoff(attempt, profile.retry_backoff_strategy)
                    logger.warning(
                        f"Transient error (attempt {attempt + 1}/{profile.max_retries + 1}), "
                        f"retrying in {wait_time:.1f}s"
                    )
                    time.sleep(wait_time)
                    continue

                # Critical error, no retry
                metrics.retry_attempts = attempt
                metrics.duration_ms = int((time.time() - start_time) * 1000)
                return metrics

            except subprocess.TimeoutExpired:
                if attempt < profile.max_retries:
                    wait_time = self._calculate_backoff(attempt, profile.retry_backoff_strategy)
                    logger.warning(f"Timeout (attempt {attempt + 1}), retrying in {wait_time:.1f}s")
                    time.sleep(wait_time)
                    continue
                else:
                    return ExecutionMetrics(
                        status=ExecutionStatus.TIMEOUT,
                        duration_ms=int((time.time() - start_time) * 1000),
                        retry_attempts=attempt,
                        command_used=command
                    )

        # All retries exhausted
        return ExecutionMetrics(
            status=ExecutionStatus.RETRY_EXCEEDED,
            duration_ms=int((time.time() - start_time) * 1000),
            retry_attempts=profile.max_retries,
            command_used=command
        )

    def _execute_with_timeout(
        self,
        command: str,
        timeout: int,
        cwd: Optional[Path],
        env: Optional[Dict[str, str]],
        profile_name: str
    ) -> ExecutionMetrics:
        """Execute command with timeout"""

        try:
            result = subprocess.run(
                command,
                shell=True,
                timeout=timeout,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env
            )

            return ExecutionMetrics(
                status=ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output_lines=len(result.stdout.split("\n")),
                command_used=command,
                duration_ms=0  # Will be set by caller
            )

        except subprocess.TimeoutExpired:
            raise

    def _calculate_backoff(self, attempt: int, strategy: str) -> float:
        """Calculate backoff delay with jitter"""
        import random

        base_delay = 2 ** attempt  # Exponential: 1s, 2s, 4s, ...

        if strategy == "exponential":
            delay = base_delay
        elif strategy == "linear":
            delay = attempt + 1
        else:
            delay = 1.0

        # Add jitter (random 0-500ms)
        jitter = random.uniform(0, 0.5)
        return delay + jitter

    def _is_transient_error(self, stderr: str) -> bool:
        """Determine if error is transient (retry-worthy)"""
        transient_patterns = [
            "timeout",
            "temporarily unavailable",
            "rate limit",
            "connection refused",
            "connection reset",
            "temporary failure",
            "503",
            "429"
        ]

        stderr_lower = stderr.lower()
        return any(pattern in stderr_lower for pattern in transient_patterns)

    def generate_report(self, metrics: ExecutionMetrics) -> str:
        """Generate execution report"""
        lines = []
        lines.append(f"\n{'='*60}")
        lines.append(f"EXECUTION RESULT: {metrics.status.value}")
        lines.append(f"{'='*60}")
        lines.append(f"Duration: {metrics.duration_ms}ms")
        lines.append(f"Command: {metrics.command_used}")
        lines.append(f"Exit code: {metrics.exit_code}")
        lines.append(f"Retry attempts: {metrics.retry_attempts}")
        lines.append(f"Output lines: {metrics.output_lines}")

        if metrics.stderr:
            lines.append(f"\nSTDERR:\n{metrics.stderr[:200]}")

        lines.append(f"\n{'='*60}\n")
        return "\n".join(lines)


# CLI Usage
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    manager = ExecutionManager()

    # Example: execute terraform validate
    command = "echo 'Success'"
    profile_name = "terraform-validate"

    metrics = manager.execute(command, profile_name)
    print(manager.generate_report(metrics))
    print(f"\nMetrics JSON: {json.dumps(asdict(metrics), default=str)}")