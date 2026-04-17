"""
Evidence Runner module.

Executes evidence.shape blocks declared in brief frontmatter
and persists results under .claude/project-context/briefs/{feature}/evidence/.

Sub-modules:
- assertions: Declarative DSL for evaluating assert specs against data.
- loader: Parses brief frontmatter into Evidence dataclass list.
- runner: Dispatches command and artifact evidence types.
- index_writer: Generates INDEX.md summary from run results.
"""

from .assertions import evaluate
from .index_writer import write_index
from .loader import (
    Evidence,
    InvalidBriefFrontmatter,
    UnknownEvidenceType,
    load_acs,
)
from .runner import EvidenceResult, run_artifact, run_command

__all__ = [
    "evaluate",
    "Evidence",
    "InvalidBriefFrontmatter",
    "UnknownEvidenceType",
    "load_acs",
    "EvidenceResult",
    "run_artifact",
    "run_command",
    "write_index",
]
