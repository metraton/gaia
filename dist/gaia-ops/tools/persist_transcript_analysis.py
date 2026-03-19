#!/usr/bin/env python3
"""Retroactive transcript analysis: analyze .output files and persist metrics.

Reads all .output transcript files from a session's tasks directory,
runs analyze() and compute_compliance_score() from transcript_analyzer,
and appends non-empty results as JSON lines to the episodic-memory metrics file.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the hooks package is importable
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))

from hooks.modules.agents.transcript_analyzer import analyze, compute_compliance_score

SESSION_ID = "5da6ec0a-5471-4af9-b301-794748b21f62"
TASKS_DIR = Path(
    f"/tmp/claude-1000/-home-jaguilar-aaxis-qxo/{SESSION_ID}/tasks"
)
METRICS_FILE = Path(
    "/home/jaguilar/aaxis/qxo/.claude/project-context"
    "/workflow-episodic-memory/metrics.jsonl"
)


def main() -> None:
    if not TASKS_DIR.exists():
        print(f"Tasks directory not found: {TASKS_DIR}")
        sys.exit(1)

    output_files = sorted(TASKS_DIR.glob("*.output"))
    if not output_files:
        print("No .output files found.")
        sys.exit(0)

    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)

    persisted = 0

    with open(METRICS_FILE, "a") as f:
        for output_path in output_files:
            task_id = output_path.stem  # filename without .output

            analysis = analyze(str(output_path))

            if analysis.api_call_count == 0:
                continue

            score = compute_compliance_score(analysis, True, False, 0.5)

            timestamp = analysis.first_timestamp or datetime.now(
                timezone.utc
            ).isoformat()

            entry = {
                "timestamp": timestamp,
                "session_id": SESSION_ID,
                "task_id": task_id,
                "agent": "retroactive-analysis",
                "source": "transcript_analyzer_retroactive",
                "input_tokens": analysis.input_tokens,
                "cache_creation_tokens": analysis.cache_creation_tokens,
                "cache_read_tokens": analysis.cache_read_tokens,
                "output_tokens": analysis.output_tokens,
                "duration_ms": analysis.duration_ms,
                "tool_call_count": analysis.tool_call_count,
                "skills_injected": analysis.skills_injected,
                "model_used": analysis.model,
                "compliance_score": score.total,
                "compliance_grade": score.grade,
                "api_call_count": analysis.api_call_count,
            }

            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            persisted += 1

    print(f"Persisted {persisted} entries to {METRICS_FILE}")


if __name__ == "__main__":
    main()
