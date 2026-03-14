#\!/usr/bin/env python3
"""
CLI entry point for gaia-ops hook replay testing and routing analysis.

Usage:
    python3 tools/replay/cli.py                          # replay all logs
    python3 tools/replay/cli.py --logs-dir /path/to/logs # custom log dir
    python3 tools/replay/cli.py --date 2026-03-11        # specific date
    python3 tools/replay/cli.py --hook pre_tool_use      # specific hook only
    python3 tools/replay/cli.py --regressions-only       # show only failures
    python3 tools/replay/cli.py --output results.json    # save results
    python3 tools/replay/cli.py --extract-only           # extract without running
    python3 tools/replay/cli.py --simulate "prompt"       # simulate routing
    python3 tools/replay/cli.py --simulate-logs --date D  # simulate from logs
    python3 tools/replay/cli.py --skills-map             # show skills map
    python3 tools/replay/cli.py --agent-profiles         # show agent profiles
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


def _find_defaults() -> tuple[Path, Path, Path]:
    """Find default paths relative to the plugin root.

    Returns:
        (hooks_dir, logs_dir, plugin_root) tuple.
    """
    # tools/replay/cli.py -> tools/replay -> tools -> plugin_root
    cli_path = Path(__file__).resolve()
    plugin_root = cli_path.parent.parent.parent
    hooks_dir = plugin_root / "hooks"
    # Default logs location: two levels up from plugin root in .claude/logs
    logs_dir = plugin_root.parent / ".claude" / "logs"
    return hooks_dir, logs_dir, plugin_root


def _handle_simulate(prompt: str, plugin_root: Path) -> int:
    """Handle --simulate command: simulate routing for a prompt."""
    from replay.routing_simulator import RoutingSimulator, format_routing_result

    config_dir = plugin_root / "config"
    agents_dir = plugin_root / "agents"
    simulator = RoutingSimulator(config_dir=config_dir, agents_dir=agents_dir)
    result = simulator.simulate(prompt)
    print(format_routing_result(result))
    return 0


def _handle_simulate_logs(logs_dir: Path, date_filter: str, plugin_root: Path) -> int:
    """Handle --simulate-logs command: simulate routing for log events."""
    from replay.extractor import LogExtractor
    from replay.routing_simulator import RoutingSimulator, format_routing_result

    config_dir = plugin_root / "config"
    agents_dir = plugin_root / "agents"

    extractor = LogExtractor()
    events = extractor.extract_all(logs_dir, date_filter=date_filter)

    if not events:
        print("No events found matching the criteria.")
        return 0

    simulator = RoutingSimulator(config_dir=config_dir, agents_dir=agents_dir)

    # Convert ReplayEvents to dicts for simulate_from_log
    event_dicts = []
    for ev in events:
        tool_input = ev.stdin_payload.get("tool_input", {})
        prompt = ""
        if isinstance(tool_input, dict):
            prompt = tool_input.get("command", tool_input.get("description", ""))
        agent = ""
        if ev.tool_name == "Agent" and isinstance(tool_input, dict):
            agent = tool_input.get("subagent_type", "")
        if prompt:
            event_dicts.append({"prompt": prompt, "agent": agent})

    results = simulator.simulate_from_log(event_dicts)
    for result in results:
        print(format_routing_result(result))
        print()

    print("Total events simulated: " + str(len(results)))
    return 0


def _handle_skills_map(plugin_root: Path) -> int:
    """Handle --skills-map command: show skills mapping report."""
    from replay.skills_mapper import SkillsMapper

    mapper = SkillsMapper(
        agents_dir=plugin_root / "agents",
        skills_dir=plugin_root / "skills",
        config_dir=plugin_root / "config",
    )
    print(mapper.format_report())
    return 0


def _handle_agent_profiles(plugin_root: Path) -> int:
    """Handle --agent-profiles command: show agent profiles."""
    from replay.skills_mapper import SkillsMapper

    mapper = SkillsMapper(
        agents_dir=plugin_root / "agents",
        skills_dir=plugin_root / "skills",
        config_dir=plugin_root / "config",
    )
    profiles = mapper.get_agent_profiles()

    lines = []
    lines.append("=" * 60)
    lines.append("AGENT PROFILES")
    lines.append("=" * 60)

    for profile in profiles:
        lines.append("")
        lines.append(profile.agent_name + ":")
        lines.append("  Skills:      " + (", ".join(profile.skills) or "(none)"))
        lines.append("  Surfaces:    " + (", ".join(profile.surfaces) or "(none)"))
        lines.append("  Read:        " + (", ".join(profile.read_sections) or "(none)"))
        lines.append("  Write:       " + (", ".join(profile.write_sections) or "(none)"))
        lines.append("  Invocations: " + str(profile.invocation_count))

    lines.append("")
    lines.append("=" * 60)

    nl = chr(10)
    print(nl.join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the replay CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 if all matched, 1 if regressions found, 2 on error.
    """
    hooks_dir_default, logs_dir_default, plugin_root = _find_defaults()

    parser = argparse.ArgumentParser(
        description="Replay gaia-ops hook events from production logs to detect regressions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=logs_dir_default,
        help="Directory containing log files (default: " + str(logs_dir_default) + ")",
    )
    parser.add_argument(
        "--hooks-dir",
        type=Path,
        default=hooks_dir_default,
        help="Directory containing hook scripts (default: " + str(hooks_dir_default) + ")",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Filter by date (YYYY-MM-DD format)",
    )
    parser.add_argument(
        "--hook",
        type=str,
        default=None,
        help="Filter by hook name (e.g. pre_tool_use)",
    )
    parser.add_argument(
        "--regressions-only",
        action="store_true",
        help="Show only regression details",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full report with breakdowns",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Extract events from logs without running hooks",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of events to replay (0 = all)",
    )
    parser.add_argument(
        "--report-format",
        choices=("text", "json"),
        default="text",
        help="Emit text for humans or JSON for machines",
    )
    # New routing/skills commands
    parser.add_argument(
        "--simulate",
        type=str,
        default=None,
        metavar="PROMPT",
        help="Simulate surface routing for a prompt",
    )
    parser.add_argument(
        "--simulate-logs",
        action="store_true",
        help="Simulate routing for events extracted from logs",
    )
    parser.add_argument(
        "--skills-map",
        action="store_true",
        help="Show agent/skill/surface mapping report",
    )
    parser.add_argument(
        "--agent-profiles",
        action="store_true",
        help="Show detailed agent profiles",
    )

    args = parser.parse_args(argv)

    # Handle routing/skills commands first (they don't need logs/hooks validation)
    if args.simulate is not None:
        return _handle_simulate(args.simulate, plugin_root)

    if args.simulate_logs:
        if not args.logs_dir.is_dir():
            print("Error: Logs directory not found: " + str(args.logs_dir), file=sys.stderr)
            return 2
        return _handle_simulate_logs(args.logs_dir, args.date, plugin_root)

    if args.skills_map:
        return _handle_skills_map(plugin_root)

    if args.agent_profiles:
        return _handle_agent_profiles(plugin_root)

    def status(message: str = "") -> None:
        target = sys.stderr if args.report_format == "json" else sys.stdout
        print(message, file=target)

    # Validate paths
    if not args.logs_dir.is_dir():
        print("Error: Logs directory not found: " + str(args.logs_dir), file=sys.stderr)
        return 2

    if not args.extract_only and not args.hooks_dir.is_dir():
        print("Error: Hooks directory not found: " + str(args.hooks_dir), file=sys.stderr)
        return 2

    # Lazy module loading to keep CLI fast for --help
    from replay.extractor import LogExtractor
    from replay.runner import HookRunner
    from replay.reporter import ReplayReporter

    # Step 1: Extract events
    extractor = LogExtractor()
    reporter = ReplayReporter()

    status("Extracting events from: " + str(args.logs_dir))
    if args.date:
        status("Date filter: " + args.date)
    if args.hook:
        status("Hook filter: " + args.hook)

    events = extractor.extract_all(
        args.logs_dir,
        date_filter=args.date,
        hook_filter=args.hook,
    )

    if args.limit > 0:
        events = events[: args.limit]

    status("Extracted " + str(len(events)) + " events")

    if not events:
        if args.report_format == "json":
            print("[]")
        else:
            status("No events found matching the criteria.")
        return 0

    # Extract-only mode
    if args.extract_only:
        if args.report_format == "json":
            print(json.dumps(reporter.events_payload(events), indent=2, default=str))
        else:
            nl = chr(10)
            print(nl + "--- Extracted Events ---")
            for i, ev in enumerate(events, 1):
                tool_input = ev.stdin_payload.get("tool_input", {})
                if ev.tool_name == "Bash":
                    detail = tool_input.get("command", "")[:80]
                elif ev.tool_name == "Agent":
                    detail = tool_input.get("subagent_type", tool_input.get("description", ""))[:80]
                else:
                    detail = str(tool_input)[:80]
                print("  [" + str(i) + "] " + ev.timestamp + " " + ev.hook_name + " " + ev.tool_name +
                      " -> " + ev.expected_decision + " (" + ev.expected_tier + ") | " + detail)
        return 0

    # Step 2: Run hooks
    def progress(current: int, total: int) -> None:
        if total > 10 and current % 10 == 0:
            print("  Replayed " + str(current) + "/" + str(total) + " events...", file=sys.stderr)

    nl = chr(10)
    status(nl + "Replaying against hooks in: " + str(args.hooks_dir))
    runner = HookRunner(hooks_dir=args.hooks_dir)
    results = runner.run_batch(events, progress_callback=progress)
    status("Replay complete: " + str(len(results)) + " results" + nl)

    # Step 3: Report
    if args.report_format == "json":
        print(json.dumps(reporter.results_payload(results), indent=2, default=str))
    elif args.regressions_only:
        print(reporter.regressions_only(results))
    elif args.full:
        print(reporter.full_report(results))
    else:
        print(reporter.summary(results))

    # Save JSON if requested
    if args.output:
        reporter.save_json(results, args.output)
        status(nl + "Results saved to: " + str(args.output))

    # Return exit code based on regressions
    has_regressions = any(not r.matched for r in results)
    return 1 if has_regressions else 0


if __name__ == "__main__":
    sys.exit(main())
