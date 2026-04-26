#!/usr/bin/env python3
"""
record-iteration.py

Atomically update state.json and append to worklog.md after each iteration.
The LLM never writes state.json directly — this script is the only writer.

Usage:
    python3 record-iteration.py \
        --state-file state.json \
        --worklog worklog.md \
        --iteration 5 \
        --metric-value 94.5 \
        --status keep \
        --description "Handle hyphenated verbs" \
        --insight "delete-objects splits correctly" \
        --next "Check camelCase+hyphen combined"

    Optional flags:
        --changed TEXT      What was modified (default: same as description)
        --metric-name TEXT  Name of the metric recorded (default: "metric")

Atomic write guarantee: state.json is written to a .tmp sibling, fsynced,
then renamed over the original.  Either the full write lands or the original
is untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone


def load_state(path: str) -> dict:
    """Load existing state.json or return an empty skeleton."""
    if not os.path.exists(path):
        return {
            "iteration": 0,
            "current_metric": None,
            "best_metric": None,
            "consecutive_discards": 0,
            "pivot_count": 0,
            "timestamp": None,
            "status": None,
        }
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read state file '{path}': {exc}", file=sys.stderr)
        sys.exit(1)


def atomic_write_json(path: str, data: dict) -> None:
    """Write *data* to *path* atomically using write-fsync-rename."""
    dir_name = os.path.dirname(os.path.abspath(path))
    # Use a temp file in the same directory so rename is on the same filesystem.
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except Exception:
            # Clean up orphaned temp file on failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        print(f"error: atomic write to '{path}' failed: {exc}", file=sys.stderr)
        sys.exit(1)


def append_worklog(
    path: str,
    iteration: int,
    description: str,
    metric_name: str,
    metric_value: float,
    status: str,
    changed: str,
    insight: str,
    next_step: str,
    best_metric: float | None,
) -> None:
    """Append a structured run entry to worklog.md."""
    status_upper = status.upper()

    # Build result sentence
    if best_metric is None:
        result_text = f"{metric_name}={metric_value} (first run, no prior best)"
    else:
        comparison = (
            f"improved from {best_metric}"
            if metric_value > best_metric
            else (
                f"unchanged from {best_metric}"
                if metric_value == best_metric
                else f"regressed from {best_metric}"
            )
        )
        result_text = f"{metric_name}={metric_value} ({comparison})"

    entry = (
        f"\n### Run {iteration}: {description} — {metric_name}={metric_value} ({status_upper})\n"
        f"- **Changed:** {changed}\n"
        f"- **Result:** {result_text}\n"
        f"- **Insight:** {insight}\n"
        f"- **Next:** {next_step}\n"
    )

    try:
        with open(path, "a") as fh:
            fh.write(entry)
    except OSError as exc:
        print(f"error: cannot append to worklog '{path}': {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Atomically record an agentic-loop iteration into state.json and worklog.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Status values:
  keep     — metric improved; best is updated, consecutive_discards reset to 0
  discard  — metric did not improve; consecutive_discards incremented
  pivot    — forced strategy change (also increments pivot_count)
  stop     — terminal state; loop should halt

Exit codes:
  0  success
  1  error (message on stderr)
        """,
    )
    parser.add_argument("--state-file", required=True, metavar="PATH", help="Path to state.json")
    parser.add_argument("--worklog", required=True, metavar="PATH", help="Path to worklog.md (append-only)")
    parser.add_argument("--iteration", required=True, type=int, help="Current iteration number (1-based)")
    parser.add_argument("--metric-value", required=True, type=float, metavar="NUM", help="Numeric metric value this run")
    parser.add_argument(
        "--status",
        required=True,
        choices=["keep", "discard", "pivot", "stop"],
        help="Outcome classification for this iteration",
    )
    parser.add_argument("--description", required=True, help="Short description of what changed this run")
    parser.add_argument("--insight", required=True, help="What was learned from this run")
    parser.add_argument("--next", required=True, dest="next_step", help="What to try in the next iteration")
    parser.add_argument(
        "--changed",
        default=None,
        metavar="TEXT",
        help="What was specifically modified (defaults to --description)",
    )
    parser.add_argument(
        "--metric-name",
        default="metric",
        metavar="NAME",
        help="Name label for the metric (default: metric)",
    )
    args = parser.parse_args()

    changed = args.changed if args.changed is not None else args.description

    # --- Load current state ---
    state = load_state(args.state_file)

    prev_best: float | None = state.get("best_metric")

    # --- Compute new state values ---
    state["iteration"] = args.iteration
    state["current_metric"] = args.metric_value
    state["status"] = args.status
    state["timestamp"] = datetime.now(tz=timezone.utc).isoformat()

    if args.status == "keep":
        # Keep: this run is better; promote to best.
        state["best_metric"] = args.metric_value
        state["consecutive_discards"] = 0
    elif args.status == "discard":
        # Do not update best; increment discard counter.
        state["consecutive_discards"] = int(state.get("consecutive_discards") or 0) + 1
    elif args.status == "pivot":
        # Pivot: counts as a discard for streak purposes, but also advances pivot_count.
        state["consecutive_discards"] = int(state.get("consecutive_discards") or 0) + 1
        state["pivot_count"] = int(state.get("pivot_count") or 0) + 1
    elif args.status == "stop":
        # Terminal — no counter changes needed beyond recording.
        pass

    # --- Atomic write ---
    atomic_write_json(args.state_file, state)

    # --- Append worklog ---
    append_worklog(
        path=args.worklog,
        iteration=args.iteration,
        description=args.description,
        metric_name=args.metric_name,
        metric_value=args.metric_value,
        status=args.status,
        changed=changed,
        insight=args.insight,
        next_step=args.next_step,
        best_metric=prev_best,
    )

    # Emit updated state summary for easy inspection.
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
