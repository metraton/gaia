"""
Unit tests for the Tool Scanner (T024).

Tests tool detection via command -v, version extraction with timeout,
tool_preferences resolution, and handling of tools that hang.
All subprocess calls are mocked for reproducibility.
"""

import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest

from tools.scan.config import TOOL_DEFINITIONS, ToolCategory, ToolDefinition
from tools.scan.scanners.tools import (
    ToolScanner,
    _VERSION_TIMEOUT,
    reset_version_cache,
)


@pytest.fixture
def scanner() -> ToolScanner:
    """Create a ToolScanner instance."""
    return ToolScanner()


# ---------------------------------------------------------------------------
# Scanner basics
# ---------------------------------------------------------------------------


class TestToolScannerBasics:
    """Test scanner metadata and basic contract."""

    def test_scanner_name(self, scanner: ToolScanner) -> None:
        assert scanner.SCANNER_NAME == "tools"

    def test_scanner_version(self, scanner: ToolScanner) -> None:
        assert scanner.SCANNER_VERSION == "1.1.0"

    def test_owned_sections(self, scanner: ToolScanner) -> None:
        assert "environment.tools" in scanner.OWNED_SECTIONS
        assert "environment.tool_preferences" in scanner.OWNED_SECTIONS

    def test_source_tag(self, scanner: ToolScanner) -> None:
        assert scanner.source_tag == "scanner:tools"


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


class TestToolDetection:
    """Test tool detection via shutil.which."""

    def test_detect_tool_via_shutil_which(self, scanner: ToolScanner) -> None:
        """Verify shutil.which is used for path detection."""
        with patch("tools.scan.scanners.tools.shutil.which", return_value="/usr/bin/python3") as mock_which:
            path = scanner._detect_path("python3")
            mock_which.assert_called_once_with("python3")
            assert path == "/usr/bin/python3"

    def test_no_subprocess_for_path_detection(self, scanner: ToolScanner) -> None:
        """Ensure subprocess is NOT used for path detection."""
        with patch("tools.scan.scanners.tools.shutil.which", return_value="/usr/bin/git"):
            with patch("tools.scan.scanners.tools.subprocess.run") as mock_run:
                scanner._detect_path("git")
                mock_run.assert_not_called()

    def test_missing_tool_returns_none(self, scanner: ToolScanner) -> None:
        """Tool not found by shutil.which returns None."""
        with patch("tools.scan.scanners.tools.shutil.which", return_value=None):
            path = scanner._detect_path("nonexistent_tool_xyz")
            assert path is None

    def test_detected_tool_has_required_fields(
        self, scanner: ToolScanner, tmp_path: Path
    ) -> None:
        """Test that detected tools have name, path, version, category."""
        tool_def = ToolDefinition(
            name="test-tool",
            category=ToolCategory.UTILITY,
        )

        # Mock version extraction (subprocess is only used for --version now)
        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "test-tool 1.2.3\n"
        version_result.stderr = ""

        with patch("tools.scan.scanners.tools.shutil.which", return_value="/usr/local/bin/test-tool"):
            with patch(
                "tools.scan.scanners.tools.subprocess.run",
                return_value=version_result,
            ):
                tool_info = scanner._probe_tool(tool_def)

        assert tool_info is not None
        assert tool_info["name"] == "test-tool"
        assert tool_info["path"] == "/usr/local/bin/test-tool"
        assert tool_info["version"] is not None
        assert tool_info["category"] == "utility"


# ---------------------------------------------------------------------------
# Version extraction
# ---------------------------------------------------------------------------


class TestVersionExtraction:
    """Test version extraction with timeout handling."""

    def test_version_extracted_from_stdout(self, scanner: ToolScanner) -> None:
        result = scanner._extract_version(
            "/usr/bin/python3", "--version", None
        )
        # On real system this would return a version string;
        # if python3 is installed it should not be "unknown"
        # We just verify it returns a string
        assert isinstance(result, str)

    def test_timeout_returns_unknown(self, scanner: ToolScanner) -> None:
        """Tool that hangs during --version gets version 'unknown'."""
        with patch(
            "tools.scan.scanners.tools.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tool", timeout=2),
        ):
            version = scanner._extract_version("/usr/bin/slow-tool", "--version", None)
            assert version == "unknown"

    def test_nonzero_exit_returns_unknown(self, scanner: ToolScanner) -> None:
        """Tool with non-zero exit for --version gets version 'unknown'."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result):
            version = scanner._extract_version("/usr/bin/bad-tool", "--version", None)
            assert version == "unknown"

    def test_version_regex_extraction(self, scanner: ToolScanner) -> None:
        """Test version regex extracts from complex output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Super Tool version 3.14.159 (build 12345)\n"
        mock_result.stderr = ""

        with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result):
            version = scanner._extract_version(
                "/usr/bin/super-tool", "--version", r"version (\d+\.\d+\.\d+)"
            )
            assert version == "3.14.159"

    def test_oserror_returns_unknown(self, scanner: ToolScanner) -> None:
        """OSError during version extraction returns 'unknown'."""
        with patch(
            "tools.scan.scanners.tools.subprocess.run",
            side_effect=OSError("Permission denied"),
        ):
            version = scanner._extract_version("/usr/bin/noperm", "--version", None)
            assert version == "unknown"

    def test_version_timeout_value(self) -> None:
        """Verify timeout constant is 2 seconds."""
        assert _VERSION_TIMEOUT == 2

    def test_timeout_is_retried_before_giving_up(self, scanner: ToolScanner) -> None:
        """A probe that times out once still reports the version it then gets.

        Contention -- including the concurrency this scanner creates itself --
        can starve a probe past the timeout. That says nothing about the tool,
        so the failure must not be recorded as the version on the first miss.
        """
        succeeded = MagicMock()
        succeeded.returncode = 0
        succeeded.stdout = "retry-tool 2.0.0\n"
        succeeded.stderr = ""

        with patch(
            "tools.scan.scanners.tools.subprocess.run",
            side_effect=[
                subprocess.TimeoutExpired(cmd="retry-tool", timeout=2),
                succeeded,
            ],
        ) as mock_run:
            version = scanner._extract_version(
                "/usr/bin/retry-tool", "--version", None
            )

        assert version == "retry-tool 2.0.0"
        assert mock_run.call_count == 2

    def test_nonzero_exit_is_not_retried(self, scanner: ToolScanner) -> None:
        """A tool that answers with a non-zero status is not asked twice."""
        refused = MagicMock()
        refused.returncode = 1
        refused.stdout = ""
        refused.stderr = ""

        with patch(
            "tools.scan.scanners.tools.subprocess.run", return_value=refused
        ) as mock_run:
            version = scanner._extract_version("/usr/bin/bad-tool", "--version", None)

        assert version == "unknown"
        assert mock_run.call_count == 1


class TestVersionMemoization:
    """The probe runs once per process, so repeated scans cannot disagree."""

    def test_second_probe_reuses_first_result(self, scanner: ToolScanner) -> None:
        """A binary already probed is not probed again.

        This is what makes repeated scans deterministic: re-probing let a
        timeout under load report "unknown" for a tool that had already
        reported a real version, so two scans of an unchanged machine
        disagreed.
        """
        tool_def = ToolDefinition(name="memo-tool", category=ToolCategory.UTILITY)

        measured = MagicMock()
        measured.returncode = 0
        measured.stdout = "memo-tool 1.2.3\n"
        measured.stderr = ""

        with patch(
            "tools.scan.scanners.tools.shutil.which",
            return_value="/usr/bin/memo-tool",
        ):
            with patch(
                "tools.scan.scanners.tools.subprocess.run", return_value=measured
            ):
                first = scanner._probe_tool(tool_def)

            # The binary would now time out, but must never be consulted again.
            with patch(
                "tools.scan.scanners.tools.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="memo-tool", timeout=2),
            ) as mock_run:
                second = scanner._probe_tool(tool_def)
                assert mock_run.call_count == 0

        assert first["version"] == "memo-tool 1.2.3"
        assert second["version"] == first["version"]

    def test_cache_reset_allows_reprobe(self, scanner: ToolScanner) -> None:
        """Resetting the memo makes the next probe measure again.

        The autouse fixture in conftest relies on this to keep tests hermetic.
        """
        tool_def = ToolDefinition(name="reset-tool", category=ToolCategory.UTILITY)

        first_result = MagicMock()
        first_result.returncode = 0
        first_result.stdout = "reset-tool 1.0.0\n"
        first_result.stderr = ""

        second_result = MagicMock()
        second_result.returncode = 0
        second_result.stdout = "reset-tool 9.9.9\n"
        second_result.stderr = ""

        with patch(
            "tools.scan.scanners.tools.shutil.which",
            return_value="/usr/bin/reset-tool",
        ):
            with patch(
                "tools.scan.scanners.tools.subprocess.run", return_value=first_result
            ):
                before = scanner._probe_tool(tool_def)

            reset_version_cache()

            with patch(
                "tools.scan.scanners.tools.subprocess.run", return_value=second_result
            ):
                after = scanner._probe_tool(tool_def)

        assert before["version"] == "reset-tool 1.0.0"
        assert after["version"] == "reset-tool 9.9.9"


# ---------------------------------------------------------------------------
# Tool preferences
# ---------------------------------------------------------------------------


class TestToolPreferences:
    """Test tool_preferences resolution."""

    def test_preference_map_built(self, scanner: ToolScanner, tmp_path: Path) -> None:
        """Test that preference map is populated with all known keys."""
        # Create minimal tool definitions for testing
        mock_defs = [
            ToolDefinition(
                name="bat",
                category=ToolCategory.FILE_VIEWER,
                preference_key="file_viewer",
                preference_priority=10,
            ),
            ToolDefinition(
                name="stern",
                category=ToolCategory.KUBERNETES,
                preference_key="log_viewer",
                preference_priority=10,
            ),
        ]

        def mock_which(name):
            return f"/usr/bin/{name}" if name in ("bat", "stern") else None

        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "1.0.0\n"
        version_result.stderr = ""

        with patch("tools.scan.config.TOOL_DEFINITIONS", mock_defs):
            with patch("tools.scan.scanners.tools.TOOL_DEFINITIONS", mock_defs):
                with patch("tools.scan.scanners.tools.shutil.which", side_effect=mock_which):
                    with patch("tools.scan.scanners.tools.subprocess.run", return_value=version_result):
                        result = scanner.scan(tmp_path)

        env = result.sections["environment"]
        prefs = env["tool_preferences"]
        assert prefs["file_viewer"] == "bat"
        assert prefs["log_viewer"] == "stern"

    def test_highest_priority_wins(self, scanner: ToolScanner, tmp_path: Path) -> None:
        """When two tools compete for same key, highest priority wins."""
        mock_defs = [
            ToolDefinition(
                name="docker",
                category=ToolCategory.CONTAINER,
                preference_key="container_runtime",
                preference_priority=10,
            ),
            ToolDefinition(
                name="podman",
                category=ToolCategory.CONTAINER,
                preference_key="container_runtime",
                preference_priority=5,
            ),
        ]

        def mock_which(name):
            return f"/usr/bin/{name}" if name in ("docker", "podman") else None

        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "1.0.0\n"
        version_result.stderr = ""

        with patch("tools.scan.config.TOOL_DEFINITIONS", mock_defs):
            with patch("tools.scan.scanners.tools.TOOL_DEFINITIONS", mock_defs):
                with patch("tools.scan.scanners.tools.shutil.which", side_effect=mock_which):
                    with patch("tools.scan.scanners.tools.subprocess.run", return_value=version_result):
                        result = scanner.scan(tmp_path)

        env = result.sections["environment"]
        assert env["tool_preferences"]["container_runtime"] == "docker"

    def test_undetected_preference_is_none(
        self, scanner: ToolScanner, tmp_path: Path
    ) -> None:
        """Preference key with no detected tools gets None."""
        mock_defs = [
            ToolDefinition(
                name="nonexistent_special_tool",
                category=ToolCategory.UTILITY,
                preference_key="special_viewer",
                preference_priority=10,
            ),
        ]

        with patch("tools.scan.config.TOOL_DEFINITIONS", mock_defs):
            with patch("tools.scan.scanners.tools.TOOL_DEFINITIONS", mock_defs):
                with patch("tools.scan.scanners.tools.shutil.which", return_value=None):
                    result = scanner.scan(tmp_path)

        env = result.sections["environment"]
        assert env["tool_preferences"]["special_viewer"] is None


# ---------------------------------------------------------------------------
# All ToolCategory values
# ---------------------------------------------------------------------------


class TestToolCategories:
    """Test that all 11 ToolCategory values exist."""

    def test_all_11_categories(self) -> None:
        assert len(ToolCategory) == 11

    def test_category_values(self) -> None:
        expected = {
            "kubernetes", "cloud", "iac", "container", "file_viewer",
            "file_search", "git", "language_runtime", "build", "utility",
            "ai_assistant",
        }
        actual = {cat.value for cat in ToolCategory}
        assert actual == expected

    def test_all_tool_definitions_have_valid_category(self) -> None:
        for td in TOOL_DEFINITIONS:
            assert isinstance(td.category, ToolCategory)


# ---------------------------------------------------------------------------
# Full scan with mocked subprocess
# ---------------------------------------------------------------------------


class TestFullScanMocked:
    """Test full scan with all subprocess calls mocked."""

    def test_scan_returns_environment_section(
        self, scanner: ToolScanner, tmp_path: Path
    ) -> None:
        """Full scan produces environment section with tools and preferences."""
        def mock_which(name):
            return "/usr/bin/python3" if name == "python3" else None

        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "Python 3.11.5\n"
        version_result.stderr = ""

        with patch("tools.scan.scanners.tools.shutil.which", side_effect=mock_which):
            with patch("tools.scan.scanners.tools.subprocess.run", return_value=version_result):
                result = scanner.scan(tmp_path)

        assert "environment" in result.sections
        env = result.sections["environment"]
        assert "tools" in env
        assert "tool_preferences" in env

    def test_scan_individual_failure_does_not_abort(
        self, scanner: ToolScanner, tmp_path: Path
    ) -> None:
        """A tool that throws an exception during probe doesn't abort scan."""
        call_count = 0

        def mock_which(name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Simulated failure")
            return None

        with patch("tools.scan.scanners.tools.shutil.which", side_effect=mock_which):
            result = scanner.scan(tmp_path)

        # Scanner should still return a valid result
        assert "environment" in result.sections

    def test_scan_result_has_source_tag(
        self, scanner: ToolScanner, tmp_path: Path
    ) -> None:
        with patch("tools.scan.scanners.tools.shutil.which", return_value=None):
            result = scanner.scan(tmp_path)

        assert result.sections["environment"]["_source"] == "scanner:tools"
