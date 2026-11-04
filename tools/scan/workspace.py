"""Directory names a workspace scan should skip (read by no scanner)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".tox", ".venv",
    "venv", "dist", "build", ".next", ".nuxt", "target",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor",
    ".terraform", ".terragrunt-cache",
})


