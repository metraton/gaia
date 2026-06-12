"""
Filtered os.walk utility for scan performance.

Replaces pathlib.rglob with a single filtered os.walk pass that prunes
expensive directories (node_modules, .git, .terraform, etc.) before descending.
"""

import os
from pathlib import Path
from typing import FrozenSet, Iterator, Sequence

# Directories to skip during scanning -- shared across all scanners
SKIP_DIRS: FrozenSet[str] = frozenset({
    "node_modules",
    ".git",
    "__pycache__",
    ".terraform",
    ".terragrunt-cache",
    "vendor",
    "dist",
    "build",
    ".venv",
    "venv",
    ".cache",
    ".npm",
    ".next",
    ".nuxt",
    "target",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
})


def walk_project(root: Path, extensions: Sequence[str]) -> Iterator[Path]:
    """Walk project tree yielding files matching given extensions.

    Prunes SKIP_DIRS and hidden directories (starting with '.') to avoid
    descending into expensive subtrees like node_modules or .terraform.

    Much faster than pathlib.rglob on large monorepos because rglob
    traverses every directory before filtering.

    Args:
        root: Absolute path to start walking from.
        extensions: Sequence of file extensions to match (e.g., [".tf", ".hcl"]).

    Yields:
        Path objects for matching files.
    """
    root_str = str(root)
    ext_set = set(extensions)

    for dirpath, dirnames, filenames in os.walk(root_str):
        # Prune in-place to prevent os.walk from descending
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for f in filenames:
            # Check extension by finding the last dot
            dot_idx = f.rfind(".")
            if dot_idx >= 0 and f[dot_idx:] in ext_set:
                yield Path(dirpath) / f


def walk_project_prefix(root: Path, prefixes: Sequence[str]) -> Iterator[Path]:
    """Walk project tree yielding files whose names start with given prefixes.

    Useful for patterns like 'Dockerfile.*' where the prefix is 'Dockerfile.'.

    Args:
        root: Absolute path to start walking from.
        prefixes: Sequence of filename prefixes to match.

    Yields:
        Path objects for matching files.
    """
    root_str = str(root)

    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for f in filenames:
            if any(f.startswith(p) for p in prefixes):
                yield Path(dirpath) / f


def walk_project_named(root: Path, filenames: Sequence[str]) -> Iterator[Path]:
    """Walk project tree yielding files matching exact filenames.

    Same pruning as walk_project but matches on exact filename rather than
    extension. Useful for files like 'Dockerfile', 'Chart.yaml', etc.

    Args:
        root: Absolute path to start walking from.
        filenames: Sequence of exact filenames to match.

    Yields:
        Path objects for matching files.
    """
    root_str = str(root)
    name_set = set(filenames)

    for dirpath, dirnames, fnames in os.walk(root_str):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for f in fnames:
            if f in name_set:
                yield Path(dirpath) / f
