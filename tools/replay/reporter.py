"""
Results reporter for gaia-ops replay testing.

Formats and presents ReplayResult data for human consumption and
programmatic analysis. Completely decoupled from execution.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from replay.extractor import ReplayEvent
from replay.runner import ReplayResult


class ReplayReporter:
    """Formats replay results for human consumption and export."""

    def events_payload(self, events: list[ReplayEvent]) -> list[dict[str, Any]]:
        """Convert extracted events to JSON-serializable dicts."""
        return [asdict(event) for event in events]

    def results_payload(self, results: list[ReplayResult]) -> list[dict[str, Any]]:
        """Convert replay results to JSON-serializable dicts."""
        output: list[dict[str, Any]] = []
        for r in results:
            entry = {
                "timestamp": r.event.timestamp,
                "hook_name": r.event.hook_name,
                "tool_name": r.event.tool_name,
                "source_file": r.event.source_file,
                "limitations": list(r.event.limitations),
                "expected": {
                    "decision": r.event.expected_decision,
                    "exit_code": r.event.expected_exit_code,
                    "tier": r.event.expected_tier,
                    "metadata": r.event.expected_metadata,
                },
                "actual": {
                    "decision": r.actual_decision,
                    "exit_code": r.actual_exit_code,
                    "tier": r.actual_tier,
                    "metadata": r.actual_metadata,
                },
                "matched": r.matched,
                "regression_type": r.regression_type,
            }

            tool_input = r.event.stdin_payload.get("tool_input", {})
            if r.event.tool_name == "Bash":
                entry["command"] = tool_input.get("command", "")
            elif r.event.tool_name == "Agent":
                entry["agent"] = tool_input.get("subagent_type", "")

            output.append(entry)
        return output

    def summary(self, results: list[ReplayResult]) -> str:
        """Quick summary: X events, Y matched, Z regressions.

        Args:
            results: List of ReplayResult instances.

        Returns:
            Multi-line summary string.
        """
        if not results:
            return "No events replayed."

        total = len(results)
        matched = sum(1 for r in results if r.matched)
        regressions = total - matched

        lines = [
            "=" * 60,
            "REPLAY SUMMARY",
            "=" * 60,
            f"Total events:  {total}",
            f"Matched:       {matched}",
            f"Regressions:   {regressions}",
        ]

        if regressions > 0:
            lines.append("")
            lines.append("Regression breakdown:")
            reg_types = Counter(
                r.regression_type for r in results if not r.matched
            )
            for rtype, count in reg_types.most_common():
                lines.append(f"  {rtype}: {count}")

        # Quick stats by decision
        lines.append("")
        decision_counts = Counter(r.event.expected_decision for r in results)
        lines.append("Events by expected decision:")
        for dec, count in decision_counts.most_common():
            lines.append(f"  {dec}: {count}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def regressions_only(self, results: list[ReplayResult]) -> str:
        """Show only regressions with details.

        Args:
            results: List of ReplayResult instances.

        Returns:
            Formatted string showing regression details, or a success message.
        """
        regressions = [r for r in results if not r.matched]

        if not regressions:
            return "No regressions detected. All events matched expected behavior."

        lines = [
            "=" * 60,
            f"REGRESSIONS FOUND: {len(regressions)}",
            "=" * 60,
        ]

        for i, r in enumerate(regressions, 1):
            lines.append("")
            lines.append(f"--- Regression #{i} [{r.regression_type}] ---")
            lines.append(f"  Timestamp:  {r.event.timestamp}")
            lines.append(f"  Hook:       {r.event.hook_name}")
            lines.append(f"  Tool:       {r.event.tool_name}")
            lines.append(f"  Source:     {r.event.source_file}")

            # Show the command or agent name
            tool_input = r.event.stdin_payload.get("tool_input", {})
            if r.event.tool_name == "Bash":
                cmd = tool_input.get("command", "")
                if len(cmd) > 120:
                    cmd = cmd[:120] + "..."
                lines.append(f"  Command:    {cmd}")
            elif r.event.tool_name == "Agent":
                agent = tool_input.get("subagent_type", tool_input.get("description", ""))
                lines.append(f"  Agent:      {agent}")

            lines.append(f"  Expected:   decision={r.event.expected_decision}, "
                         f"exit_code={r.event.expected_exit_code}, "
                         f"tier={r.event.expected_tier or 'n/a'}")
            lines.append(f"  Actual:     decision={r.actual_decision}, "
                         f"exit_code={r.actual_exit_code}, "
                         f"tier={r.actual_tier or 'n/a'}")

            if r.actual_stderr and len(r.actual_stderr) < 200:
                lines.append(f"  Stderr:     {r.actual_stderr.strip()}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def full_report(self, results: list[ReplayResult]) -> str:
        """Full report with stats per hook, per tier, per decision type.

        Args:
            results: List of ReplayResult instances.

        Returns:
            Comprehensive formatted report string.
        """
        if not results:
            return "No events to report on."

        lines = [self.summary(results)]

        # Stats by hook
        hooks: dict[str, list[ReplayResult]] = {}
        for r in results:
            hooks.setdefault(r.event.hook_name, []).append(r)

        lines.append("")
        lines.append("BREAKDOWN BY HOOK:")
        lines.append("-" * 40)
        for hook_name, hook_results in sorted(hooks.items()):
            total = len(hook_results)
            matched = sum(1 for r in hook_results if r.matched)
            lines.append(f"  {hook_name}: {total} events, {matched} matched, "
                         f"{total - matched} regressions")

        # Stats by tier
        tiers: dict[str, list[ReplayResult]] = {}
        for r in results:
            tier = r.event.expected_tier or "n/a"
            tiers.setdefault(tier, []).append(r)

        lines.append("")
        lines.append("BREAKDOWN BY TIER:")
        lines.append("-" * 40)
        for tier_name, tier_results in sorted(tiers.items()):
            total = len(tier_results)
            matched = sum(1 for r in tier_results if r.matched)
            lines.append(f"  {tier_name}: {total} events, {matched} matched, "
                         f"{total - matched} regressions")

        # Stats by tool
        tools: dict[str, list[ReplayResult]] = {}
        for r in results:
            tools.setdefault(r.event.tool_name, []).append(r)

        lines.append("")
        lines.append("BREAKDOWN BY TOOL:")
        lines.append("-" * 40)
        for tool_name, tool_results in sorted(tools.items()):
            total = len(tool_results)
            matched = sum(1 for r in tool_results if r.matched)
            lines.append(f"  {tool_name}: {total} events, {matched} matched, "
                         f"{total - matched} regressions")

        # Stats by source file / artifact
        sources: dict[str, list[ReplayResult]] = {}
        for r in results:
            sources.setdefault(r.event.source_file, []).append(r)

        lines.append("")
        lines.append("BREAKDOWN BY SOURCE:")
        lines.append("-" * 40)
        for source_name, source_results in sorted(sources.items()):
            total = len(source_results)
            matched = sum(1 for r in source_results if r.matched)
            lines.append(f"  {source_name}: {total} events, {matched} matched, "
                         f"{total - matched} regressions")

        limitations = sorted({
            limitation
            for r in results
            for limitation in r.event.limitations
            if limitation
        })
        if limitations:
            lines.append("")
            lines.append("SOURCE LIMITATIONS:")
            lines.append("-" * 40)
            for limitation in limitations:
                lines.append(f"  - {limitation}")

        # Show regressions if any
        regressions = [r for r in results if not r.matched]
        if regressions:
            lines.append("")
            lines.append(self.regressions_only(results))

        return "\n".join(lines)

    def save_json(self, results: list[ReplayResult], path: Path) -> None:
        """Save results as JSON for programmatic analysis.

        Args:
            results: List of ReplayResult instances.
            path: Output file path.
        """
        path.write_text(json.dumps(self.results_payload(results), indent=2, default=str))
