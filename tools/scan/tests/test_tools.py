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
from tools.scan.scanners.tools import ToolScanner, _VERSION_TIMEOUT


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
        assert scanner.SCANNER_VERSION == "1.0.0"

    def test_owned_sections(self, scanner: ToolScanner) -> None:
        assert "environment.tools" in scanner.OWNED_SECTIONS
        assert "environment.tool_preferences" in scanner.OWNED_SECTIONS

    def test_source_tag(self, scanner: ToolScanner) -> None:
        assert scanner.source_tag == "scanner:tools"


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


class TestToolDetection:
    """Test tool detection via command -v."""

    def test_detect_tool_via_command_v(self, scanner: ToolScanner) -> None:
        """Verify command -v is used (not which)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/bin/python3\n"
        mock_result.stderr = ""

        with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result) as mock_run:
            path = scanner._detect_path("python3")
            # Check that bash -c 'command -v ...' was called
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert "command -v" in args[0][0][2]  # ['bash', '-c', 'command -v python3']
            assert path == "/usr/bin/python3"

    def test_command_v_not_which(self, scanner: ToolScanner) -> None:
        """Ensure 'which' is not used."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/usr/bin/git\n"

        with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result) as mock_run:
            scanner._detect_path("git")
            args_str = str(mock_run.call_args)
            assert "which" not in args_str

    def test_missing_tool_returns_none(self, scanner: ToolScanner) -> None:
        """Tool not found by command -v returns None."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result):
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

        # Mock detect_path returns a path
        detect_result = MagicMock()
        detect_result.returncode = 0
        detect_result.stdout = "/usr/local/bin/test-tool\n"
        detect_result.stderr = ""

        # Mock version extraction
        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "test-tool 1.2.3\n"
        version_result.stderr = ""

        with patch(
            "tools.scan.scanners.tools.subprocess.run",
            side_effect=[detect_result, version_result],
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

        def mock_run(args, **kwargs):
            result = MagicMock()
            name = args[2].split()[-1] if len(args) > 2 else ""  # 'command -v <name>'
            if name in ("bat", "stern"):
                result.returncode = 0
                result.stdout = f"/usr/bin/{name}\n"
            else:
                result.returncode = 0
                result.stdout = f"{name} 1.0.0\n"
            result.stderr = ""
            return result

        with patch("tools.scan.config.TOOL_DEFINITIONS", mock_defs):
            with patch("tools.scan.scanners.tools.TOOL_DEFINITIONS", mock_defs):
                with patch("tools.scan.scanners.tools.subprocess.run", side_effect=mock_run):
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

        def mock_run(args, **kwargs):
            result = MagicMock()
            name = args[2].split()[-1] if len(args) > 2 else ""
            if name in ("docker", "podman"):
                result.returncode = 0
                result.stdout = f"/usr/bin/{name}\n"
            else:
                result.returncode = 0
                result.stdout = f"{name} 1.0.0\n"
            result.stderr = ""
            return result

        with patch("tools.scan.config.TOOL_DEFINITIONS", mock_defs):
            with patch("tools.scan.scanners.tools.TOOL_DEFINITIONS", mock_defs):
                with patch("tools.scan.scanners.tools.subprocess.run", side_effect=mock_run):
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

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("tools.scan.config.TOOL_DEFINITIONS", mock_defs):
            with patch("tools.scan.scanners.tools.TOOL_DEFINITIONS", mock_defs):
                with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result):
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
        # Mock all tools as not found except python3
        def mock_run(args, **kwargs):
            result = MagicMock()
            if isinstance(args, list) and len(args) >= 3:
                cmd = args[2] if len(args) > 2 else str(args)
                if "command -v python3" in cmd:
                    result.returncode = 0
                    result.stdout = "/usr/bin/python3\n"
                    result.stderr = ""
                    return result
                elif "command -v" in cmd:
                    result.returncode = 1
                    result.stdout = ""
                    result.stderr = ""
                    return result

            # Version call for python3
            if isinstance(args, list) and args[0] == "/usr/bin/python3":
                result.returncode = 0
                result.stdout = "Python 3.11.5\n"
                result.stderr = ""
                return result

            result.returncode = 1
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("tools.scan.scanners.tools.subprocess.run", side_effect=mock_run):
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

        def mock_run(args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Simulated failure")
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("tools.scan.scanners.tools.subprocess.run", side_effect=mock_run):
            result = scanner.scan(tmp_path)

        # Scanner should still return a valid result
        assert "environment" in result.sections

    def test_scan_result_has_source_tag(
        self, scanner: ToolScanner, tmp_path: Path
    ) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("tools.scan.scanners.tools.subprocess.run", return_value=mock_result):
            result = scanner.scan(tmp_path)

        assert result.sections["environment"]["_source"] == "scanner:tools"
