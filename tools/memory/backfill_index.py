#!/usr/bin/env python3
"""
P3 Phase 2: Backfill episodic index with workflow metric fields.

Reads index.json, enriches each entry missing the `agent` field by extracting
context.metrics from the corresponding episode file, then writes back the
updated index.

Idempotent: entries that already have `agent` are skipped. Safe to run
multiple times.

Usage:
    python tools/memory/backfill_index.py
    python tools/memory/backfill_index.py --dry-run
    python tools/memory/backfill_index.py --episodic-dir /path/to/episodic-memory
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Fields to extract from context.metrics and add to the index entry
METRIC_FIELDS = [
    "agent",
    "session_id",
    "task_id",
    "exit_code",
    "plan_status",
    "output_length",
    "output_tokens_approx",
    "prompt",
]

# Default values when a field is missing from the episode metrics
FIELD_DEFAULTS = {
    "agent": "",
    "session_id": "",
    "task_id": "",
    "exit_code": None,
    "plan_status": "",
    "output_length": 0,
    "output_tokens_approx": 0,
    "prompt": "",
}


def find_episodic_dir():
    """Locate the episodic memory directory relative to the project root."""
    # Try the standard location
    candidates = [
        Path(os.environ.get("EPISODIC_MEMORY_DIR", "")),
        Path.home() / ".claude" / "project-context" / "episodic-memory",
        Path(__file__).resolve().parent.parent.parent.parent
        / ".claude"
        / "project-context"
        / "episodic-memory",
    ]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "index.json").exists():
            return candidate
    return None


def load_episode(episodes_dir: Path, episode_id: str) -> dict | None:
    """Load a single episode file and return its contents, or None."""
    episode_path = episodes_dir / f"episode-{episode_id}.json"
    if not episode_path.exists():
        return None
    try:
        with open(episode_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Could not read {episode_path.name}: {e}", file=sys.stderr)
        return None


def extract_metrics(episode_data: dict) -> dict | None:
    """Extract context.metrics from an episode, returning a dict of the 8 fields."""
    metrics = (episode_data.get("context") or {}).get("metrics")
    if not metrics:
        return None

    result = {}
    for field_name in METRIC_FIELDS:
        result[field_name] = metrics.get(field_name, FIELD_DEFAULTS[field_name])
    return result


def backfill(episodic_dir: Path, dry_run: bool = False) -> dict:
    """
    Backfill index.json with workflow metric fields from episode files.

    Returns stats dict with counts.
    """
    index_path = episodic_dir / "index.json"
    episodes_dir = episodic_dir / "episodes"

    if not index_path.exists():
        print(f"ERROR: index.json not found at {index_path}", file=sys.stderr)
        sys.exit(1)

    if not episodes_dir.is_dir():
        print(f"ERROR: episodes directory not found at {episodes_dir}", file=sys.stderr)
        sys.exit(1)

    # Load index
    with open(index_path, "r") as f:
        index_data = json.load(f)

    episodes = index_data.get("episodes", [])
    total = len(episodes)

    stats = {
        "total": total,
        "already_enriched": 0,
        "updated": 0,
        "no_episode_file": 0,
        "no_metrics": 0,
    }

    for entry in episodes:
        # Skip entries that already have the agent field
        if "agent" in entry:
            stats["already_enriched"] += 1
            continue

        episode_id = entry.get("id", "")
        if not episode_id:
            stats["no_episode_file"] += 1
            continue

        # Load the episode file
        episode_data = load_episode(episodes_dir, episode_id)
        if episode_data is None:
            stats["no_episode_file"] += 1
            continue

        # Extract metrics
        metrics = extract_metrics(episode_data)
        if metrics is None:
            stats["no_metrics"] += 1
            continue

        # Enrich the index entry
        for field_name in METRIC_FIELDS:
            entry[field_name] = metrics[field_name]

        stats["updated"] += 1

    # Write back
    if not dry_run:
        with open(index_path, "w") as f:
            json.dump(index_data, f, indent=2)
            f.write("\n")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill episodic index with workflow metric fields from episode files."
    )
    parser.add_argument(
        "--episodic-dir",
        type=Path,
        default=None,
        help="Path to episodic-memory directory (auto-detected if omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing changes",
    )
    args = parser.parse_args()

    episodic_dir = args.episodic_dir or find_episodic_dir()
    if episodic_dir is None:
        print("ERROR: Could not locate episodic-memory directory.", file=sys.stderr)
        print(
            "Provide --episodic-dir or set EPISODIC_MEMORY_DIR environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Episodic memory directory: {episodic_dir}")
    if args.dry_run:
        print("DRY RUN: no changes will be written\n")
    else:
        print()

    stats = backfill(episodic_dir, dry_run=args.dry_run)

    print("=== Backfill Results ===")
    print(f"  Total index entries:    {stats['total']}")
    print(f"  Already enriched:       {stats['already_enriched']}")
    print(f"  Updated (backfilled):   {stats['updated']}")
    print(f"  No episode file found:  {stats['no_episode_file']}")
    print(f"  No metrics in episode:  {stats['no_metrics']}")
    print()

    if args.dry_run:
        print("No changes written (dry run).")
    else:
        print("index.json updated successfully.")


if __name__ == "__main__":
    main()
