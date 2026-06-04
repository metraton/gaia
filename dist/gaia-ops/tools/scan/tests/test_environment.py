"""
Unit tests for the Environment Scanner (T025).

Tests OS detection (platform, arch, WSL), runtime detection,
env file detection (exists only, never reads), and minimal system behavior.
"""

import platform as platform_mod
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from tools.scan.scanners.environment import EnvironmentScanner


@pytest.fixture
def scanner() -> EnvironmentScanner:
    """Create an EnvironmentScanner instance."""
    return EnvironmentScanner()


# ---------------------------------------------------------------------------
# Scanner basics
# ---------------------------------------------------------------------------


class TestEnvScannerBasics:
    """Test scanner metadata and basic contract."""

    def test_scanner_name(self, scanner: EnvironmentScanner) -> None:
        assert scanner.SCANNER_NAME == "environment"

    def test_scanner_version(self, scanner: EnvironmentScanner) -> None:
        assert scanner.SCANNER_VERSION == "1.0.0"

    def test_owned_sections(self, scanner: EnvironmentScanner) -> None:
        assert "environment.runtimes" in scanner.OWNED_SECTIONS
        assert "environment.os" in scanner.OWNED_SECTIONS
        assert "environment.env_files" in scanner.OWNED_SECTIONS

    def test_source_tag(self, scanner: EnvironmentScanner) -> None:
        assert scanner.source_tag == "scanner:environment"


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------


class TestOSDetection:
    """Test OS platform, architecture, and WSL detection."""

    def test_detect_linux_platform(self, scanner: EnvironmentScanner) -> None:
        with patch.object(sys, "platform", "linux"):
            warnings: list = []
            os_info = scanner._detect_os(warnings)
            assert os_info["platform"] == "linux"

    def test_detect_darwin_platform(self, scanner: EnvironmentScanner) -> None:
        with patch.object(sys, "platform", "darwin"):
            warnings: list = []
            os_info = scanner._detect_os(warnings)
            assert os_info["platform"] == "darwin"

    def test_detect_win32_platform(self, scanner: EnvironmentScanner) -> None:
        with patch.object(sys, "platform", "win32"):
            warnings: list = []
            os_info = scanner._detect_os(warnings)
            assert os_info["platform"] == "win32"

    def test_detect_x64_architecture(self, scanner: EnvironmentScanner) -> None:
        with patch.object(platform_mod, "machine", return_value="x86_64"):
            warnings: list = []
            os_info = scanner._detect_os(warnings)
            assert os_info["architecture"] == "x64"

    def test_detect_arm64_architecture(self, scanner: EnvironmentScanner) -> None:
        with patch.object(platform_mod, "machine", return_value="aarch64"):
            warnings: list = []
            os_info = scanner._detect_os(warnings)
            assert os_info["architecture"] == "arm64"

    def test_wsl_detected_from_proc_version(
        self, scanner: EnvironmentScanner
    ) -> None:
        with patch.object(sys, "platform", "linux"):
            with patch.object(
                Path,
                "exists",
                return_value=True,
            ):
                with patch.object(
                    Path,
                    "read_text",
                    return_value="Linux version 5.15.90.1-microsoft-standard-WSL2 (gcc)",
                ):
                    warnings: list = []
                    os_info = scanner._detect_os(warnings)
                    assert os_info["wsl"] is True

    def test_wsl_version_2_detected(self, scanner: EnvironmentScanner) -> None:
        with patch.object(sys, "platform", "linux"):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(
                    Path,
                    "read_text",
                    return_value="Linux version 5.15.90.1-microsoft-standard-WSL2",
                ):
                    warnings: list = []
                    os_info = scanner._detect_os(warnings)
                    assert os_info["wsl_version"] in ("1", "2")

    def test_non_wsl_linux(self, scanner: EnvironmentScanner) -> None:
        with patch.object(sys, "platform", "linux"):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(
                    Path,
                    "read_text",
                    return_value="Linux version 6.1.0-generic (gcc version 12.3)",
                ):
                    warnings: list = []
                    os_info = scanner._detect_os(warnings)
                    assert os_info["wsl"] is False
                    assert os_info["wsl_version"] is None

    def test_os_always_returns_platform_and_arch(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        os_info = result.sections["environment"]["os"]
        assert "platform" in os_info
        assert "architecture" in os_info
        assert os_info["platform"] is not None
        assert os_info["architecture"] is not None


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------


class TestRuntimeDetection:
    """Test runtime version detection."""

    def test_detect_python3_runtime(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        """python3 should be detectable on the test system."""
        result = scanner.scan(empty_project)
        runtimes = result.sections["environment"]["runtimes"]
        runtime_names = [r["name"] for r in runtimes]
        assert "python3" in runtime_names

    def test_runtime_has_version(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        runtimes = result.sections["environment"]["runtimes"]
        python_rt = [r for r in runtimes if r["name"] == "python3"]
        assert len(python_rt) >= 1
        assert python_rt[0]["version"] is not None

    def test_runtime_has_path(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        runtimes = result.sections["environment"]["runtimes"]
        python_rt = [r for r in runtimes if r["name"] == "python3"]
        assert len(python_rt) >= 1
        assert python_rt[0]["path"] is not None

    def test_runtime_timeout_returns_unknown(
        self, scanner: EnvironmentScanner
    ) -> None:
        with patch(
            "tools.scan.scanners.environment.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="slow", timeout=2),
        ):
            warnings: list = []
            version = scanner._get_version("slow-runtime", "--version", warnings)
            assert version == "unknown"

    def test_runtime_not_found_returns_none(
        self, scanner: EnvironmentScanner
    ) -> None:
        with patch(
            "tools.scan.scanners.environment.subprocess.run",
            side_effect=FileNotFoundError("not found"),
        ):
            warnings: list = []
            version = scanner._get_version("missing-runtime", "--version", warnings)
            assert version is None


# ---------------------------------------------------------------------------
# Env file detection
# ---------------------------------------------------------------------------


class TestEnvFileDetection:
    """Test .env file detection -- exists only, never reads contents."""

    def test_detect_env_files(
        self, scanner: EnvironmentScanner, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text("SECRET=value\n")
        (tmp_path / ".env.local").write_text("LOCAL_SECRET=value\n")
        result = scanner.scan(tmp_path)
        env_files = result.sections["environment"]["env_files"]
        file_names = [f["name"] for f in env_files]
        assert ".env" in file_names
        assert ".env.local" in file_names

    def test_detect_env_example(
        self, scanner: EnvironmentScanner, tmp_path: Path
    ) -> None:
        (tmp_path / ".env.example").write_text("KEY=\n")
        result = scanner.scan(tmp_path)
        env_files = result.sections["environment"]["env_files"]
        file_names = [f["name"] for f in env_files]
        assert ".env.example" in file_names

    def test_detect_env_development_and_production(
        self, scanner: EnvironmentScanner, tmp_path: Path
    ) -> None:
        (tmp_path / ".env.development").write_text("DEV=1\n")
        (tmp_path / ".env.production").write_text("PROD=1\n")
        result = scanner.scan(tmp_path)
        env_files = result.sections["environment"]["env_files"]
        file_names = [f["name"] for f in env_files]
        assert ".env.development" in file_names
        assert ".env.production" in file_names

    def test_no_env_files_detected(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        env_files = result.sections["environment"]["env_files"]
        assert env_files == []

    def test_env_file_contents_never_read(
        self, scanner: EnvironmentScanner, tmp_path: Path
    ) -> None:
        """Critical: verify that .env file contents are NEVER read.

        We create .env files and then monkey-patch open() on the Path to
        raise an error if called on any .env file. The scanner should
        only use Path.exists() and Path.name.
        """
        env_path = tmp_path / ".env"
        env_path.write_text("SUPER_SECRET=should_not_be_read\n")

        original_open = Path.open

        def guarded_open(self_path, *args, **kwargs):
            if self_path.name.startswith(".env"):
                raise AssertionError(
                    f"Scanner attempted to open() env file: {self_path}"
                )
            return original_open(self_path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            # Also guard the built-in open
            original_builtin_open = open

            def guarded_builtin_open(path, *args, **kwargs):
                path_str = str(path)
                if ".env" in Path(path_str).name and Path(path_str).name.startswith(".env"):
                    raise AssertionError(
                        f"Scanner attempted to read env file: {path}"
                    )
                return original_builtin_open(path, *args, **kwargs)

            with patch("builtins.open", guarded_builtin_open):
                result = scanner.scan(tmp_path)

        # Verify .env was detected by existence
        env_files = result.sections["environment"]["env_files"]
        file_names = [f["name"] for f in env_files]
        assert ".env" in file_names


# ---------------------------------------------------------------------------
# ScanResult contract
# ---------------------------------------------------------------------------


class TestEnvResultContract:
    """Test scan result follows expected contract."""

    def test_source_tag_present(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        assert result.sections["environment"]["_source"] == "scanner:environment"

    def test_result_has_duration(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        assert result.duration_ms >= 0

    def test_environment_section_has_all_subsections(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        env = result.sections["environment"]
        assert "os" in env
        assert "runtimes" in env
        assert "env_files" in env

    def test_minimal_system_returns_os(
        self, scanner: EnvironmentScanner, empty_project: Path
    ) -> None:
        """Even on a minimal system, platform and arch are always available."""
        result = scanner.scan(empty_project)
        os_info = result.sections["environment"]["os"]
        assert os_info["platform"] in ("linux", "darwin", "win32")
        assert os_info["architecture"] in ("x64", "arm64", platform_mod.machine())
