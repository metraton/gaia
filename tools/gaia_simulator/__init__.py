"""
Gaia simulator module for gaia-ops hooks.

Extracts real hook events from production logs, replays them against the current
hooks, and detects regressions. Also provides routing simulation and skills mapping.

Modules:
    extractor          - Log parser: ReplayEvent + LogExtractor
    runner             - Hook executor: ReplayResult + HookRunner
    reporter           - Results formatter: ReplayReporter
    routing_simulator  - Surface routing simulation: RoutingSimulator
    skills_mapper      - Agent/skill/surface mapping: SkillsMapper
    cli                - Command-line entry point
"""

from gaia_simulator.extractor import LogExtractor, ReplayEvent
from gaia_simulator.runner import HookRunner, ReplayResult
from gaia_simulator.reporter import ReplayReporter
from gaia_simulator.routing_simulator import RoutingSimulator, RoutingResult
from gaia_simulator.skills_mapper import SkillsMapper, SkillMapping, AgentProfile

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
