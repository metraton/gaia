"""
Gaia-Ops Tools Package

Core orchestration tools for the gaia-ops system.

This package is organized into atomic modules:
- 1-routing: Agent semantic routing and intent classification
- 2-context: Context provisioning and enrichment
- 3-clarification: Phase 0 ambiguity detection (clarify())
- 4-validation: Approval gates and commit validation
- 5-task-management: Large plan chunking and execution
- 6-semantic: Embedding-based semantic matching
- 7-utilities: Helper tools and audit logging
- 8-shared: Common schemas and utilities
- 9-agent-framework: Agnostic agent execution protocol (5-layer workflow)
- fast-queries: Agent diagnostic scripts

Backward Compatibility
======================

All old imports continue to work:

    from agent_router import AgentRouter
    from context_provider import context_provider
    from approval_gate import ApprovalGate

Preferred New Imports
=====================

    from tools["1-routing"] import AgentRouter
    from tools["2-context"] import context_provider
    from tools["4-validation"] import ApprovalGate

Agent Framework (New)
====================

    from tools["9-agent-framework"] import AgentOrchestrator
    from tools["9-agent-framework"] import PayloadValidator, LocalDiscoverer
"""

import importlib

# Import submodules using importlib to handle hyphenated names
_routing = importlib.import_module(".1-routing", package=__name__)
_context = importlib.import_module(".2-context", package=__name__)
_clarification = importlib.import_module(".3-clarification", package=__name__)
_validation = importlib.import_module(".4-validation", package=__name__)
_task_management = importlib.import_module(".5-task-management", package=__name__)
_semantic = importlib.import_module(".6-semantic", package=__name__)
_utilities = importlib.import_module(".7-utilities", package=__name__)
_shared = importlib.import_module(".8-shared", package=__name__)
_agent_framework = importlib.import_module(".9-agent-framework", package=__name__)

# Extract main exports for backward compatibility
AgentRouter = _routing.AgentRouter
load_project_context = _context.load_project_context
get_contract_context = _context.get_contract_context
ContextSectionReader = _context.ContextSectionReader
ApprovalGate = _validation.ApprovalGate
CommitMessageValidator = _validation.CommitMessageValidator
validate_commit_message = _validation.validate_commit_message
ClarificationEngine = _clarification.ClarificationEngine
execute_workflow = _clarification.execute_workflow
TaskManager = _task_management.TaskManager
SemanticMatcher = _semantic.SemanticMatcher
generate_embeddings = _semantic.generate_embeddings
AgentInvokerHelper = _utilities.AgentInvokerHelper
TaskAuditLogger = _utilities.TaskAuditLogger

# Agent framework exports (new)
AgentOrchestrator = _agent_framework.AgentOrchestrator
PayloadValidator = _agent_framework.PayloadValidator
LocalDiscoverer = _agent_framework.LocalDiscoverer
FindingClassifier = _agent_framework.FindingClassifier
ExecutionManager = _agent_framework.ExecutionManager
JSONLogger = _agent_framework.JSONLogger

# Re-export submodules for access
routing = _routing
context = _context
clarification = _clarification
validation = _validation
task_management = _task_management
semantic = _semantic
utilities = _utilities
shared = _shared
agent_framework = _agent_framework

__all__ = [
    # Main classes and functions (backward compatible)
    "AgentRouter",
    "load_project_context",
    "get_contract_context",
    "ContextSectionReader",
    "ApprovalGate",
    "CommitMessageValidator",
    "validate_commit_message",
    "ClarificationEngine",
    "execute_workflow",
    "TaskManager",
    "SemanticMatcher",
    "generate_embeddings",
    "AgentInvokerHelper",
    "TaskAuditLogger",
    # Agent framework (new)
    "AgentOrchestrator",
    "PayloadValidator",
    "LocalDiscoverer",
    "FindingClassifier",
    "ExecutionManager",
    "JSONLogger",
    # Submodules (new style)
    "routing",
    "context",
    "clarification",
    "validation",
    "task_management",
    "semantic",
    "utilities",
    "shared",
    "agent_framework",
]

__version__ = "2.0.0"  # Major version bump for reorganization
