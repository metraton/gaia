"""
Tool Scanner Module

Scans PATH for CLI tools defined in TOOL_DEFINITIONS, detects presence via
`shutil.which`, extracts version via `<tool> --version` with 2-second timeout,
and builds the tool_preferences map based on preference_priority.

Performance: Uses shutil.which (pure Python) instead of subprocess for path
detection, and ThreadPoolExecutor for parallel version probing.

Safety constraints:
- Uses `shutil.which` for detection (pure Python, no subprocess)
- Uses `subprocess.run(timeout=2)` for --version
- Tool that hangs or fails --version gets version "unknown"
- Does NOT execute any tool beyond --version
- All calls are READ-ONLY, no state modification
"""

import logging
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan.config import TOOL_DEFINITIONS, ToolDefinition

# Default tool list excludes extended tools; use get_tool_definitions(full=True)
# to include all tools.
def get_tool_definitions(full: bool = False) -> list:
    """Return tool definitions, optionally including extended (low-value) tools."""
    if full:
        return TOOL_DEFINITIONS
    return [td for td in TOOL_DEFINITIONS if not td.extended]
from tools.scan.scanners.base import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

# Timeout in seconds for --version subprocess calls
_VERSION_TIMEOUT = 2

# Max parallel workers for tool probing
_MAX_WORKERS = 10


class ToolScanner(BaseScanner):
    """Scanner that detects CLI tools available on PATH.

    Uses shutil.which for fast path detection (no subprocess), then probes
    found tools in parallel with ThreadPoolExecutor for version extraction.

    Produces:
        environment.tools: List of detected tool dicts
        environment.tool_preferences: Map of preference keys to winning tool names
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "tools"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.1.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["environment.tools", "environment.tool_preferences"]

    # Class-level flag: set to True before instantiation to scan extended tools.
    # This allows the registry auto-discovery to work with default args while
    # still supporting the --full CLI flag.
    scan_extended: bool = False

    def __init__(self, full: bool = False) -> None:
        """Initialize ToolScanner.

        Args:
            full: If True, scan all tools including extended (low-value) ones.
                  Also checks class-level scan_extended flag.
        """
        self._full = full or ToolScanner.scan_extended

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

        tool_defs = get_tool_definitions(full=self._full)

        # Parallel tool probing with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._probe_tool, td): td
                for td in tool_defs
            }
            for future in as_completed(futures):
                tool_def = futures[future]
                try:
                    tool_info = future.result()
                except Exception as exc:
                    logger.warning("Failed to probe tool '%s': %s", tool_def.name, exc)
                    warnings.append(f"Failed to probe {tool_def.name}: {exc}")
                    continue

                if tool_info is None:
                    continue

                tools.append(tool_info)

                # Update preference map if this tool has a preference_key
                # Deterministic tiebreaker: when priority is equal, first
                # alphabetically wins (prevents race condition in parallel execution)
                if tool_def.preference_key is not None:
                    current = preference_winners.get(tool_def.preference_key)
                    if current is None:
                        preference_winners[tool_def.preference_key] = (
                            tool_def.name,
                            tool_def.preference_priority,
                        )
                    elif tool_def.preference_priority > current[1]:
                        preference_winners[tool_def.preference_key] = (
                            tool_def.name,
                            tool_def.preference_priority,
                        )
                    elif tool_def.preference_priority == current[1] and tool_def.name < current[0]:
                        # Same priority: alphabetically first wins for determinism
                        preference_winners[tool_def.preference_key] = (
                            tool_def.name,
                            tool_def.preference_priority,
                        )

        # Sort tools by name for deterministic output
        tools.sort(key=lambda t: t["name"])

        # Build tool_preferences from winners (value is tool name or None)
        # Collect all known preference keys from scanned tool definitions
        all_preference_keys = {
            td.preference_key
            for td in tool_defs
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
        """Probe a single tool: check presence via shutil.which, extract version.

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
        """Detect tool presence using shutil.which (pure Python, no subprocess).

        Args:
            name: Binary name to look up.

        Returns:
            Absolute path string, or None if not found.
        """
        path = shutil.which(name)
        if path:
            return path
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
            # Split version_flag to support multi-word flags like "version --client"
            cmd = [tool_path] + version_flag.split()
            result = subprocess.run(
                cmd,
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
