"""
Scan Module: Modular project scanner for gaia-ops

This module provides a pluggable scanner system that detects project stack,
infrastructure, git configuration, tools, orchestration, and environment.
Each scanner is a pure function (filesystem path -> JSON sections) that runs
independently and in parallel. The system produces an agnostic project-context.json
schema that works for any project type.

Main components:
- BaseScanner: Abstract base class for scanner modules
- ScannerRegistry: Auto-discovery registry for scanner modules
- ScanOrchestrator: Parallel scanner execution and result aggregation
- ScanConfig: Scanner configuration and tool definitions
"""

__version__ = "0.1.0"

__all__ = [
    "__version__",
]
