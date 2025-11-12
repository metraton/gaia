"""
Agent Framework - Agnostic Agent Execution Protocol

5-Layer Workflow:
  1. Payload Validation (Phase A)
  2. Local Discovery (Phase B)
  3. Finding Classification (Phase C)
  4. Remote Validation (Phase D - optional)
  5. Execution with Profiles (Phase E)

Reference: docs/Agent-Complete-Workflow.md
"""

__version__ = "0.1.0"

from .payload_validator import PayloadValidator, ValidationResult
from .local_discoverer import LocalDiscoverer, DiscoveryResult
from .finding_classifier import FindingClassifier, Finding, FindingTier, DataOrigin
from .execution_manager import ExecutionManager, ExecutionProfile, ExecutionMetrics
from .logging_manager import JSONLogger, LogEvent
from .agent_orchestrator import AgentOrchestrator, AgentExecutionResult

__all__ = [
    "PayloadValidator",
    "ValidationResult",
    "LocalDiscoverer",
    "DiscoveryResult",
    "FindingClassifier",
    "Finding",
    "FindingTier",
    "DataOrigin",
    "ExecutionManager",
    "ExecutionProfile",
    "ExecutionMetrics",
    "JSONLogger",
    "LogEvent",
    "AgentOrchestrator",
    "AgentExecutionResult",
]
