"""
Tool Scanner Module

Scans PATH for CLI tools defined in TOOL_DEFINITIONS, detects presence via
`command -v`, extracts version via `<tool> --version` with 2-second timeout,
and builds the tool_preferences map based on preference_priority.

Safety constraints:
- Uses `command -v` (NOT `which`) for detection
- Uses `subprocess.run(timeout=2)` for --version
- Tool that hangs or fails --version gets version "unknown"
- Does NOT execute any tool beyond --version
- All calls are READ-ONLY, no state modification
"""

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan.config import TOOL_DEFINITIONS, ToolDefinition
from tools.scan.scanners.base import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

# Timeout in seconds for --version subprocess calls
_VERSION_TIMEOUT = 2


class ToolScanner(BaseScanner):
    """Scanner that detects CLI tools available on PATH.

    Iterates over TOOL_DEFINITIONS from config.py, probes each tool with
    `command -v <name>` to check availability, then runs `<tool> <version_flag>`
    (default --version) with a 2-second timeout to extract the version string.

    Produces:
        environment.tools: List of detected tool dicts
        environment.tool_preferences: Map of preference keys to winning tool names
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "tools"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.0.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["environment.tools", "environment.tool_preferences"]

    def scan(self, root: Path) -> ScanResult:
        """Scan PATH for CLI tools and build tool_preferences.

        Args:
            root: Absolute path to the project root (unused by this scanner).

        Returns:
            ScanResult with environment section containing tools and tool_preferences.
        """
        start = time.monotonic()
        warnings: List[str] = []
        tools: List[Dict[str, str]] = []
        # preference_key -> (tool_name, priority)
        preference_winners: Dict[str, tuple[str, int]] = {}

        for tool_def in TOOL_DEFINITIONS:
            try:
                tool_info = self._probe_tool(tool_def)
            except Exception as exc:
                # Individual tool failure MUST NOT abort the scanner
                logger.warning("Failed to probe tool '%s': %s", tool_def.name, exc)
                warnings.append(f"Failed to probe {tool_def.name}: {exc}")
                continue

            if tool_info is None:
                continue

            tools.append(tool_info)

            # Update preference map if this tool has a preference_key
            if tool_def.preference_key is not None:
                current = preference_winners.get(tool_def.preference_key)
                if current is None or tool_def.preference_priority > current[1]:
                    preference_winners[tool_def.preference_key] = (
                        tool_def.name,
                        tool_def.preference_priority,
                    )

        # Build tool_preferences from winners (value is tool name or None)
        # Collect all known preference keys from TOOL_DEFINITIONS
        all_preference_keys = {
            td.preference_key
            for td in TOOL_DEFINITIONS
            if td.preference_key is not None
        }
        tool_preferences: Dict[str, Optional[str]] = {}
        for key in sorted(all_preference_keys):
            winner = preference_winners.get(key)
            tool_preferences[key] = winner[0] if winner else None

        elapsed_ms = (time.monotonic() - start) * 1000

        sections: Dict[str, Any] = {
            "environment": {
                "tools": tools,
                "tool_preferences": tool_preferences,
            },
        }

        return self.make_result(
            sections=sections,
            warnings=warnings if warnings else None,
            duration_ms=round(elapsed_ms, 2),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _probe_tool(self, tool_def: ToolDefinition) -> Optional[Dict[str, str]]:
        """Probe a single tool: check presence and extract version.

        Args:
            tool_def: Tool definition from TOOL_DEFINITIONS.

        Returns:
            Dict with name, path, version, category -- or None if not found.
        """
        tool_path = self._detect_path(tool_def.name)
        if tool_path is None:
            return None

        version = self._extract_version(tool_path, tool_def.version_flag, tool_def.version_regex)

        return {
            "name": tool_def.name,
            "path": tool_path,
            "version": version,
            "category": tool_def.category.value,
        }

    @staticmethod
    def _detect_path(name: str) -> Optional[str]:
        """Detect tool presence using `command -v`.

        Args:
            name: Binary name to look up.

        Returns:
            Absolute path string, or None if not found.
        """
        try:
            result = subprocess.run(
                ["bash", "-c", f"command -v {name}"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("command -v %s failed: %s", name, exc)
        return None

    @staticmethod
    def _extract_version(
        tool_path: str,
        version_flag: str,
        version_regex: Optional[str],
    ) -> str:
        """Extract version string from tool --version output.

        Args:
            tool_path: Absolute path to the tool binary.
            version_flag: CLI flag to get version (e.g. --version).
            version_regex: Optional regex to extract version from output.

        Returns:
            Version string, or "unknown" on failure/timeout.
        """
        try:
            result = subprocess.run(
                [tool_path, version_flag],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
            )
            # Accept both stdout and stderr (many tools print version to stderr)
            output = result.stdout.strip() or result.stderr.strip()

            if result.returncode != 0 or not output:
                return "unknown"

            if version_regex:
                match = re.search(version_regex, output)
                if match:
                    return match.group(1) if match.lastindex else match.group(0)
                return "unknown"

            # Default: return the first line
            return output.splitlines()[0].strip()

        except subprocess.TimeoutExpired:
            logger.debug("Timeout getting version for %s", tool_path)
            return "unknown"
        except (OSError, ValueError) as exc:
            logger.debug("Failed to get version for %s: %s", tool_path, exc)
            return "unknown"
