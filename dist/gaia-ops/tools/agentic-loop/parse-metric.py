#!/usr/bin/env python3
"""
parse-metric.py

Read stdout from eval_command and extract METRIC lines.

Usage:
    echo "output" | python3 parse-metric.py --metric accuracy
    python3 parse-metric.py --metric accuracy --file /tmp/eval-output.txt

Input lines must match: METRIC {name}={number}
Output: JSON to stdout with metric name, numeric value, and raw line.
"""

import argparse
import json
import re
import sys
from typing import Optional


METRIC_PATTERN = re.compile(r"^METRIC\s+(\w+)=([\d.]+)\s*$")


def parse_lines(lines: list[str]) -> list[dict]:
    """Extract all METRIC entries from a sequence of lines."""
    results = []
    for line in lines:
        stripped = line.rstrip("\n")
        match = METRIC_PATTERN.match(stripped)
        if match:
            name = match.group(1)
            raw_value = match.group(2)
            # Preserve int vs float from the source text.
            value: int | float
            if "." in raw_value:
                value = float(raw_value)
            else:
                value = int(raw_value)
            results.append(
                {
                    "metric": name,
                    "value": value,
                    "raw_line": stripped,
                }
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract METRIC lines from eval_command output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  echo "METRIC accuracy=94.5" | python3 parse-metric.py --metric accuracy
  python3 parse-metric.py --metric passing_tests --file /tmp/out.txt
  python3 parse-metric.py --file /tmp/out.txt          # returns all metrics
        """,
    )
    parser.add_argument(
        "--metric",
        metavar="NAME",
        help="Return only this named metric (case-sensitive). Exits 1 if not found.",
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        help="Read from file instead of stdin.",
    )
    args = parser.parse_args()

    # --- Read input ---
    try:
        if args.file:
            with open(args.file, "r") as fh:
                lines = fh.readlines()
        else:
            lines = sys.stdin.readlines()
    except OSError as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Parse ---
    all_metrics = parse_lines(lines)

    if args.metric:
        # Filter to the requested metric name.
        matches = [m for m in all_metrics if m["metric"] == args.metric]
        if not matches:
            print(
                f"error: metric '{args.metric}' not found in input",
                file=sys.stderr,
            )
            sys.exit(1)
        # Return the last occurrence if there are duplicates.
        result = matches[-1]
    else:
        # Return all metrics as a list when no --metric filter is given.
        result = all_metrics  # type: ignore[assignment]

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
