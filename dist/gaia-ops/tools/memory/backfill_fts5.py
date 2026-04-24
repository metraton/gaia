#!/usr/bin/env python3
"""
Backfill FTS5 search index from episodes.jsonl.

Reads all episodes from the episodic memory JSONL file and indexes them
into the FTS5 search store. Idempotent -- safe to run multiple times.

Usage:
    python3 tools/memory/backfill_fts5.py
    python3 -m tools.memory.backfill_fts5
"""

import json
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """Walk up from cwd to find the directory containing .claude/."""
    current = Path.cwd()
    for candidate in [current, *current.parents]:
        if (candidate / ".claude").is_dir():
            return candidate
    return current


def main() -> int:
    project_root = _find_project_root()
    episodes_path = project_root / ".claude" / "project-context" / "episodic-memory" / "episodes.jsonl"

    if not episodes_path.exists():
        print(f"ERROR: episodes.jsonl not found at {episodes_path}", file=sys.stderr)
        return 1

    # Import here so the script works when run from the project root
    # (tools/memory is on the path via the module run, or cwd is project root)
    try:
        from tools.memory import search_store
    except ImportError:
        # Fallback: add project root to sys.path and retry
        sys.path.insert(0, str(project_root))
        from tools.memory import search_store

    indexed = 0
    skipped_event = 0
    skipped_malformed = 0

    with open(episodes_path, encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"WARNING: line {lineno} is malformed JSON, skipping ({exc})", file=sys.stderr)
                skipped_malformed += 1
                continue

            # Skip outcome_update / relationship_added events
            if "event_type" in record:
                skipped_event += 1
                continue

            episode_id = record.get("episode_id") or record.get("id", "")
            if not episode_id:
                print(f"WARNING: line {lineno} has no episode_id/id, skipping", file=sys.stderr)
                skipped_malformed += 1
                continue

            prompt = record.get("prompt", "")
            enriched_prompt = record.get("enriched_prompt", "")
            title = record.get("title", "")

            raw_tags = record.get("tags", [])
            if isinstance(raw_tags, list):
                tags = " ".join(str(t) for t in raw_tags)
            else:
                tags = str(raw_tags)

            search_store.index_episode(
                episode_id=episode_id,
                prompt=prompt,
                enriched_prompt=enriched_prompt,
                tags=tags,
                title=title,
            )
            indexed += 1

            if indexed % 1000 == 0:
                print(f"Progress: {indexed} episodes indexed...")

    if indexed == 0 and skipped_event == 0 and skipped_malformed == 0:
        print("0 episodes indexed (file is empty)")
    else:
        print(
            f"Done: {indexed} episodes indexed"
            + (f", {skipped_event} event records skipped" if skipped_event else "")
            + (f", {skipped_malformed} malformed lines skipped" if skipped_malformed else "")
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
