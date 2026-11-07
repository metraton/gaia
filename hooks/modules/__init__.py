"""
Gaia Hooks Modular Architecture

This package provides a modular, maintainable hook system for Claude Code.

Modules:
- core: Shared utilities (paths, state, stdin, file locking, hook entry)
- security: Security tiers, safe commands, blocked patterns
- tools: Tool-specific validators (Bash, Task)
- audit: Logging, metrics, event detection, workflow audit
- agents: Subagent dispatch, contract, and transcript handling
- context: Dispatch kernel and project-context assembly
- session: Session lifecycle, registry, and manifest
- events: Harness event writing and Gaia CLI grammar
- memory: Workflow episode storage
- orchestrator: Delegate-mode enforcement
- scanning: Scan trigger
- validation: Commit validation
"""

__version__ = "2.0.0"
