#!/usr/bin/env python3
"""
decide-status.py

Mechanically decide what to do based on numbers alone.  No LLM judgment.

Usage:
    python3 decide-status.py \
        --current 94.5 \
        --best 92.0 \
        --threshold 98 \
        --direction higher \
        --consecutive-discards 2 \
        --pivot-count 1

Output JSON:
    {
      "decision": "keep",
      "reason": "Metric improved from 92.0 to 94.5",
      "improved": true,
      "gap_remaining": 3.5
    }

Decision precedence (evaluated top-to-bottom, first match wins):
  1. pivot_count >= 3                          → stop
  2. consecutive_discards >= 5                 → pivot   (also a discard)
  3. consecutive_discards >= 3                 → refine  (also a discard)
  4. current meets or passes threshold         → threshold_reached
  5. current improved vs best (per direction)  → keep
  6. current same or worse                     → discard

Exit codes:
  0  success (decision emitted as JSON)
  1  invalid input
"""

import argparse
import json
import sys


Decision = str  # type alias for readability


def _is_improved(current: float, best: float, direction: str) -> bool:
    """Return True if *current* is strictly better than *best* per direction."""
    if direction == "higher":
        return current > best
    return current < best  # lower is better


def _threshold_reached(current: float, threshold: float, direction: str) -> bool:
    """Return True if *current* has met or surpassed *threshold*."""
    if direction == "higher":
        return current >= threshold
    return current <= threshold


def _gap_remaining(current: float, threshold: float, direction: str) -> float:
    """Absolute gap between current value and threshold."""
    if direction == "higher":
        return max(0.0, threshold - current)
    return max(0.0, current - threshold)


def decide(
    current: float,
    best: float,
    threshold: float,
    direction: str,
    consecutive_discards: int,
    pivot_count: int,
) -> dict:
    """Pure function: return decision dict from numeric inputs."""

    gap = _gap_remaining(current, threshold, direction)
    improved = _is_improved(current, best, direction)

    # --- Precedence 1: hard stop on too many pivots ---
    if pivot_count >= 3:
        return {
            "decision": "stop",
            "reason": f"pivot_count={pivot_count} has reached the maximum of 3; halting loop",
            "improved": improved,
            "gap_remaining": gap,
        }

    # --- Precedence 2 & 3: discard streak escalations ---
    # Evaluated before threshold/keep so an ongoing failing streak is flagged
    # even if the current run happens to reach the threshold.
    if consecutive_discards >= 5:
        return {
            "decision": "pivot",
            "reason": (
                f"consecutive_discards={consecutive_discards} >= 5; "
                "strategy is not working, force a pivot"
            ),
            "improved": improved,
            "gap_remaining": gap,
        }

    if consecutive_discards >= 3:
        return {
            "decision": "refine",
            "reason": (
                f"consecutive_discards={consecutive_discards} >= 3; "
                "current approach needs refinement before continuing"
            ),
            "improved": improved,
            "gap_remaining": gap,
        }

    # --- Precedence 4: threshold reached ---
    if _threshold_reached(current, threshold, direction):
        return {
            "decision": "threshold_reached",
            "reason": (
                f"current={current} {'≥' if direction == 'higher' else '≤'} "
                f"threshold={threshold}; goal achieved"
            ),
            "improved": improved,
            "gap_remaining": 0.0,
        }

    # --- Precedence 5 & 6: standard keep/discard ---
    if improved:
        return {
            "decision": "keep",
            "reason": f"Metric improved from {best} to {current}",
            "improved": True,
            "gap_remaining": gap,
        }

    return {
        "decision": "discard",
        "reason": f"Metric did not improve (current={current}, best={best})",
        "improved": False,
        "gap_remaining": gap,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute the next agentic-loop decision from metric numbers only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Decisions:
  keep               current improved vs best
  discard            current same or worse
  refine             3+ consecutive discards (improvement needed in approach)
  pivot              5+ consecutive discards (strategy change required)
  stop               3+ pivots already attempted
  threshold_reached  current meets or surpasses the goal threshold

Direction values:
  higher   larger numbers are better  (e.g. accuracy, passing tests)
  lower    smaller numbers are better (e.g. error rate, latency ms)
        """,
    )
    parser.add_argument("--current", required=True, type=float, help="Metric value for the current run")
    parser.add_argument("--best", required=True, type=float, help="Best metric seen so far (from state.json)")
    parser.add_argument("--threshold", required=True, type=float, help="Target threshold to reach")
    parser.add_argument(
        "--direction",
        required=True,
        choices=["higher", "lower"],
        help="Whether higher or lower values are better",
    )
    parser.add_argument(
        "--consecutive-discards",
        required=True,
        type=int,
        metavar="N",
        help="Number of consecutive discard outcomes so far (from state.json)",
    )
    parser.add_argument(
        "--pivot-count",
        required=True,
        type=int,
        metavar="N",
        help="Number of pivots executed so far (from state.json)",
    )
    args = parser.parse_args()

    # --- Input validation ---
    errors = []
    if args.consecutive_discards < 0:
        errors.append("--consecutive-discards must be >= 0")
    if args.pivot_count < 0:
        errors.append("--pivot-count must be >= 0")

    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        sys.exit(1)

    result = decide(
        current=args.current,
        best=args.best,
        threshold=args.threshold,
        direction=args.direction,
        consecutive_discards=args.consecutive_discards,
        pivot_count=args.pivot_count,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
