"""
Replay testing module for gaia-ops hooks.

Extracts real hook events from production logs, replays them against the current
hooks, and detects regressions.

Modules:
    extractor          - Log parser: ReplayEvent + LogExtractor
    runner             - Hook executor: ReplayResult + HookRunner
    reporter           - Results formatter: ReplayReporter
    routing_simulator  - Surface routing simulation: RoutingSimulator
    skills_mapper      - Agent/skill/surface mapping: SkillsMapper
    cli                - Command-line entry point
"""

from replay.extractor import LogExtractor, ReplayEvent
from replay.runner import HookRunner, ReplayResult
from replay.reporter import ReplayReporter
from replay.routing_simulator import RoutingSimulator, RoutingResult
from replay.skills_mapper import SkillsMapper, SkillMapping, AgentProfile

__all__ = [
    "LogExtractor",
    "ReplayEvent",
    "HookRunner",
    "ReplayResult",
    "ReplayReporter",
    "RoutingSimulator",
    "RoutingResult",
    "SkillsMapper",
    "SkillMapping",
    "AgentProfile",
]
