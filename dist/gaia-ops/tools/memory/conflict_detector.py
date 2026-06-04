#!/usr/bin/env python3
"""
Conflict Detection Module for GAIA-OPS Memory Files

Scans memory .md files for contradictions by:
1. Loading all .md files from the memory directory
2. Computing Jaccard similarity on word sets (after stopword removal)
3. For similar file pairs (Jaccard > threshold), checking for polarity contradictions
4. Returning structured conflict reports

Inspired by hippo-memory's conflict detection heuristics.
"""

import os
import re
from pathlib import Path
from typing import Optional


# 50-word stopword list
STOPWORDS = frozenset([
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "that", "this", "these",
    "those", "it", "its", "as", "if", "than", "then", "so", "not", "no",
    "all", "any", "each", "both", "more",
])


def _tokenize(text: str) -> set:
    """Tokenize text into lowercase words, removing stopwords."""
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _jaccard(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two word sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _extract_lines(text: str) -> list:
    """Return non-empty, non-heading stripped lines from text."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _check_polarity_contradictions(lines_a: list, lines_b: list) -> list:
    """
    Check for polarity contradictions between two sets of lines.

    Patterns checked:
    - "use X" vs "do not use X" / "don't use X"
    - "enabled" vs "disabled"
    - "always" vs "never"
    - "Windows" vs "WSL" / "Linux" in same context
    - Version mismatches (e.g., "v4" vs "v5")
    """
    conflicts = []

    # Pattern 1: "use X" vs "do not use X" / "don't use X"
    use_pattern = re.compile(r"\buse\s+(\w+)", re.IGNORECASE)
    no_use_pattern = re.compile(r"\b(?:do\s+not|don\'t|never\s+use)\s+(\w+)", re.IGNORECASE)

    uses_a = {m.group(1).lower(): line for line in lines_a for m in [use_pattern.search(line)] if m}
    uses_b = {m.group(1).lower(): line for line in lines_b for m in [use_pattern.search(line)] if m}
    no_uses_a = {m.group(1).lower(): line for line in lines_a for m in [no_use_pattern.search(line)] if m}
    no_uses_b = {m.group(1).lower(): line for line in lines_b for m in [no_use_pattern.search(line)] if m}

    for term, line_a in uses_a.items():
        if term in no_uses_b:
            conflicts.append({
                "line_a": line_a,
                "line_b": no_uses_b[term],
                "reason": f"'use {term}' contradicts 'do not use {term}'",
            })

    for term, line_b in uses_b.items():
        if term in no_uses_a:
            conflicts.append({
                "line_a": no_uses_a[term],
                "line_b": line_b,
                "reason": f"'use {term}' contradicts 'do not use {term}'",
            })

    # Pattern 2: "enabled" vs "disabled"
    enabled_pattern = re.compile(r"\benabled\b", re.IGNORECASE)
    disabled_pattern = re.compile(r"\bdisabled\b", re.IGNORECASE)

    enabled_lines_a = [l for l in lines_a if enabled_pattern.search(l)]
    disabled_lines_a = [l for l in lines_a if disabled_pattern.search(l)]
    enabled_lines_b = [l for l in lines_b if enabled_pattern.search(l)]
    disabled_lines_b = [l for l in lines_b if disabled_pattern.search(l)]

    for line_a in enabled_lines_a:
        for line_b in disabled_lines_b:
            # Only flag if they share a common significant keyword
            shared = _tokenize(line_a) & _tokenize(line_b)
            if shared:
                conflicts.append({
                    "line_a": line_a,
                    "line_b": line_b,
                    "reason": "enabled vs disabled on shared topic",
                })

    for line_a in disabled_lines_a:
        for line_b in enabled_lines_b:
            shared = _tokenize(line_a) & _tokenize(line_b)
            if shared:
                conflicts.append({
                    "line_a": line_a,
                    "line_b": line_b,
                    "reason": "disabled vs enabled on shared topic",
                })

    # Pattern 3: "always" vs "never"
    always_pattern = re.compile(r"\balways\b", re.IGNORECASE)
    never_pattern = re.compile(r"\bnever\b", re.IGNORECASE)

    always_lines_a = [l for l in lines_a if always_pattern.search(l)]
    never_lines_a = [l for l in lines_a if never_pattern.search(l)]
    always_lines_b = [l for l in lines_b if always_pattern.search(l)]
    never_lines_b = [l for l in lines_b if never_pattern.search(l)]

    for line_a in always_lines_a:
        for line_b in never_lines_b:
            shared = _tokenize(line_a) & _tokenize(line_b)
            if shared:
                conflicts.append({
                    "line_a": line_a,
                    "line_b": line_b,
                    "reason": "always vs never on shared topic",
                })

    for line_a in never_lines_a:
        for line_b in always_lines_b:
            shared = _tokenize(line_a) & _tokenize(line_b)
            if shared:
                conflicts.append({
                    "line_a": line_a,
                    "line_b": line_b,
                    "reason": "never vs always on shared topic",
                })

    # Pattern 4: "Windows" vs "WSL/Linux" in same context
    windows_pattern = re.compile(r"\bWindows\b")
    wsl_pattern = re.compile(r"\b(?:WSL|Linux)\b")

    windows_lines_a = [l for l in lines_a if windows_pattern.search(l)]
    wsl_lines_a = [l for l in lines_a if wsl_pattern.search(l)]
    windows_lines_b = [l for l in lines_b if windows_pattern.search(l)]
    wsl_lines_b = [l for l in lines_b if wsl_pattern.search(l)]

    # Cross-file: file_a says Windows, file_b says WSL for same context
    for line_a in windows_lines_a:
        for line_b in wsl_lines_b:
            shared = _tokenize(line_a) & _tokenize(line_b)
            if shared:
                conflicts.append({
                    "line_a": line_a,
                    "line_b": line_b,
                    "reason": "Windows vs WSL/Linux on shared topic",
                })

    for line_a in wsl_lines_a:
        for line_b in windows_lines_b:
            shared = _tokenize(line_a) & _tokenize(line_b)
            if shared:
                conflicts.append({
                    "line_a": line_a,
                    "line_b": line_b,
                    "reason": "WSL/Linux vs Windows on shared topic",
                })

    # Pattern 5: Version mismatches (e.g., "v4" vs "v5")
    version_pattern = re.compile(r"\bv(\d+)\b", re.IGNORECASE)

    def extract_versions(lines: list) -> dict:
        """Map version number -> line for lines containing a version."""
        result = {}
        for line in lines:
            for m in version_pattern.finditer(line):
                v = int(m.group(1))
                result[v] = line
        return result

    versions_a = extract_versions(lines_a)
    versions_b = extract_versions(lines_b)

    for va, line_a in versions_a.items():
        for vb, line_b in versions_b.items():
            if va != vb:
                # Only flag if the context words (excluding the version itself) overlap
                tokens_a = _tokenize(re.sub(r"\bv\d+\b", "", line_a, flags=re.IGNORECASE))
                tokens_b = _tokenize(re.sub(r"\bv\d+\b", "", line_b, flags=re.IGNORECASE))
                shared = tokens_a & tokens_b
                if shared:
                    conflicts.append({
                        "line_a": line_a,
                        "line_b": line_b,
                        "reason": f"version mismatch: v{va} vs v{vb} on shared topic",
                    })

    # Deduplicate: remove identical (line_a, line_b, reason) triples
    seen = set()
    deduped = []
    for c in conflicts:
        key = (c["line_a"], c["line_b"], c["reason"])
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    return deduped


def _load_memory_files(memory_dir: Path) -> dict:
    """
    Load all .md files from memory_dir.
    Returns dict mapping filename -> file content string.
    """
    files = {}
    try:
        for entry in memory_dir.iterdir():
            if entry.is_file() and entry.suffix == ".md":
                try:
                    content = entry.read_text(encoding="utf-8", errors="replace")
                    files[str(entry)] = content
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError, FileNotFoundError):
        pass
    return files


def detect_conflicts(
    memory_dir: Optional[Path] = None,
    threshold: float = 0.3,
) -> list:
    """
    Scan memory .md files for contradictions.

    Args:
        memory_dir: Path to directory containing memory .md files.
                    Defaults to ~/.claude/projects/-home-jorge-ws-me/memory/
        threshold:  Jaccard similarity threshold above which pairs are checked
                    for polarity contradictions. Default 0.3.

    Returns:
        List of dicts with keys:
            file_a    - absolute path of first file
            file_b    - absolute path of second file
            similarity - Jaccard similarity score (float)
            conflicts  - list of {"line_a", "line_b", "reason"} dicts
    """
    if memory_dir is None:
        default = Path.home() / ".claude" / "projects" / "-home-jorge-ws-me" / "memory"
        memory_dir = default

    memory_dir = Path(memory_dir)

    file_contents = _load_memory_files(memory_dir)
    if not file_contents:
        return []

    file_paths = sorted(file_contents.keys())
    word_sets = {fp: _tokenize(file_contents[fp]) for fp in file_paths}
    file_lines = {fp: _extract_lines(file_contents[fp]) for fp in file_paths}

    results = []

    for i in range(len(file_paths)):
        for j in range(i + 1, len(file_paths)):
            fp_a = file_paths[i]
            fp_b = file_paths[j]

            sim = _jaccard(word_sets[fp_a], word_sets[fp_b])
            if sim <= threshold:
                continue

            conflicts = _check_polarity_contradictions(
                file_lines[fp_a],
                file_lines[fp_b],
            )

            results.append({
                "file_a": fp_a,
                "file_b": fp_b,
                "similarity": round(sim, 4),
                "conflicts": conflicts,
            })

    return results
