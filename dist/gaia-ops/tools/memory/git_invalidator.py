#!/usr/bin/env python3
"""
Git Invalidation Module for GAIA-OPS Episodic Memory

Scans recent git commits for migration/deprecation patterns and weakens
matching episodic memories by reducing their relevance_score.

Inspired by hippo-memory's invalidation.ts patterns.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional


# Patterns that signal a tool/library/concept has changed or been removed.
# Each entry is a (compiled_regex, group_extractor_fn) tuple.
# The extractor receives a re.Match and returns a list of affected term strings.
_PATTERNS: List[tuple] = []

def _build_patterns() -> List[tuple]:
    """Build compiled pattern list once."""
    raw = [
        # "migrate from X" / "migrated from X to Y"
        (
            re.compile(r'\bmigrate(?:d)?\s+from\s+([\w\-./]+)', re.IGNORECASE),
            lambda m: [m.group(1)],
        ),
        # "deprecate X" / "deprecated X"
        (
            re.compile(r'\bdeprecate[sd]?\s+([\w\-./]+)', re.IGNORECASE),
            lambda m: [m.group(1)],
        ),
        # "remove X" (at least 3-char word to skip noise)
        (
            re.compile(r'\bremove[sd]?\s+([\w\-./]{3,})', re.IGNORECASE),
            lambda m: [m.group(1)],
        ),
        # "replace X with Y"
        (
            re.compile(r'\breplace\s+([\w\-./]+)\s+with\s+([\w\-./]+)', re.IGNORECASE),
            lambda m: [m.group(1), m.group(2)],
        ),
        # "upgrade from X" / "upgraded from X to Y"
        (
            re.compile(r'\bupgrade[sd]?\s+from\s+([\w\-./]+)', re.IGNORECASE),
            lambda m: [m.group(1)],
        ),
        # "drop support for X" / "drop X"
        (
            re.compile(r'\bdrop(?:\s+support(?:\s+for)?)?\s+([\w\-./]{3,})', re.IGNORECASE),
            lambda m: [m.group(1)],
        ),
        # version bump: "v3 to v4" or "3.x to 4.x"
        (
            re.compile(r'\bv?(\d+)(?:\.\w+)?\s+to\s+v?(\d+)(?:\.\w+)?\b', re.IGNORECASE),
            lambda m: [f"v{m.group(1)}", f"v{m.group(2)}"],
        ),
    ]
    return raw


_COMPILED_PATTERNS = _build_patterns()


def _get_git_log(commit_count: int, cwd: Optional[Path] = None) -> Optional[str]:
    """
    Run git log and return stdout, or None if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{commit_count}", "--no-merges"],
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except FileNotFoundError:
        # git not available
        return None


def _scan_commits(log_output: str) -> tuple:
    """
    Scan git log output for migration/deprecation patterns.

    Returns:
        (patterns_detected, affected_terms) where patterns_detected is a list
        of matched pattern strings and affected_terms is a set of lowercased
        affected tool/library names.
    """
    patterns_detected: List[str] = []
    affected_terms: set = set()

    for line in log_output.splitlines():
        # Strip the short SHA prefix (7-char hex + space)
        message = re.sub(r'^[0-9a-f]{7,}\s+', '', line).strip()
        if not message:
            continue

        for regex, extractor in _COMPILED_PATTERNS:
            match = regex.search(message)
            if match:
                terms = extractor(match)
                patterns_detected.append(message)
                for term in terms:
                    affected_terms.add(term.lower())
                break  # one pattern per commit line is enough

    return patterns_detected, affected_terms


def _find_index_json() -> Optional[Path]:
    """
    Locate episodic memory index.json relative to the cwd.
    Checks the canonical path: .claude/project-context/episodic-memory/index.json
    """
    candidate = Path(".claude/project-context/episodic-memory/index.json")
    if candidate.exists():
        return candidate
    return None


def _episode_mentions_terms(episode: Dict[str, Any], terms: set) -> bool:
    """
    Return True if the episode's searchable text overlaps with any affected term.
    Checks: keywords, tags, title.
    """
    if not terms:
        return False

    searchable: List[str] = []

    keywords = episode.get("keywords", [])
    if isinstance(keywords, list):
        searchable.extend(str(k).lower() for k in keywords)

    tags = episode.get("tags", [])
    if isinstance(tags, list):
        searchable.extend(str(t).lower() for t in tags)

    title = episode.get("title", "")
    if title:
        searchable.extend(re.split(r'\W+', title.lower()))

    for word in searchable:
        for term in terms:
            # term could be "v3" etc — use substring match for flexibility
            if term and (term in word or word in term):
                return True
    return False


def check_recent_commits(
    dry_run: bool = True,
    commit_count: int = 20,
) -> Dict[str, Any]:
    """
    Scan recent git commits for migration/deprecation patterns and weaken
    matching episodic memories.

    Args:
        dry_run: If True, identify affected episodes but do NOT modify index.json.
        commit_count: Number of recent commits to scan (default 20).

    Returns:
        dict with keys:
            affected_episodes: list of episode IDs that match
            patterns_detected: list of commit message strings that matched a pattern
            would_modify: count of episodes that would be (or were) modified
            commits_scanned: number of commits examined
    """
    empty_result: Dict[str, Any] = {
        "affected_episodes": [],
        "patterns_detected": [],
        "would_modify": 0,
        "commits_scanned": 0,
    }

    # Step 1: get git log
    log_output = _get_git_log(commit_count)
    if log_output is None:
        return empty_result

    commits_scanned = len([l for l in log_output.splitlines() if l.strip()])

    # Step 2: detect patterns
    patterns_detected, affected_terms = _scan_commits(log_output)

    # If nothing matched, short-circuit
    if not affected_terms:
        return {
            "affected_episodes": [],
            "patterns_detected": patterns_detected,
            "would_modify": 0,
            "commits_scanned": commits_scanned,
        }

    # Step 3: load index.json
    index_path = _find_index_json()
    if index_path is None:
        return {
            "affected_episodes": [],
            "patterns_detected": patterns_detected,
            "would_modify": 0,
            "commits_scanned": commits_scanned,
        }

    try:
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "affected_episodes": [],
            "patterns_detected": patterns_detected,
            "would_modify": 0,
            "commits_scanned": commits_scanned,
        }

    episodes: List[Dict[str, Any]] = index_data.get("episodes", [])

    # Step 4: find matching episodes
    affected_ids: List[str] = []
    for ep in episodes:
        if _episode_mentions_terms(ep, affected_terms):
            ep_id = ep.get("id") or ep.get("episode_id", "")
            if ep_id:
                affected_ids.append(ep_id)

    would_modify = len(affected_ids)

    # Step 5: apply changes if not dry_run
    if not dry_run and affected_ids:
        affected_set = set(affected_ids)
        for ep in episodes:
            ep_id = ep.get("id") or ep.get("episode_id", "")
            if ep_id in affected_set:
                current_score = ep.get("relevance_score", 1.0)
                ep["relevance_score"] = current_score * 0.5

        index_data["episodes"] = episodes
        index_path.write_text(
            json.dumps(index_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return {
        "affected_episodes": affected_ids,
        "patterns_detected": patterns_detected,
        "would_modify": would_modify,
        "commits_scanned": commits_scanned,
    }


if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    result = check_recent_commits(dry_run=dry)
    print(json.dumps(result, indent=2))
