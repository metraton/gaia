"""
Gaia-Ops Hooks Modular Architecture

This package provides a modular, maintainable hook system for Claude Code.

Modules:
- core: Shared utilities (paths, state, config loading)
- security: Security tiers, safe commands, blocked patterns
- tools: Tool-specific validators (Bash, Task)
- workflow: Phase validation and state tracking
- audit: Logging, metrics, event detection
- agents: Subagent metrics and anomaly detection
"""

__version__ = "2.0.0"
