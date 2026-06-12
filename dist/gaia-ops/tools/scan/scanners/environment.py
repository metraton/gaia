"""
Environment Scanner

Detects OS information (platform, architecture, WSL), installed language
runtimes, and .env file patterns. Outputs environment.os, environment.runtimes,
and environment.env_files subsections.

Pure Function Contract:
- No file writes
- No state modification
- No network calls
- NEVER reads .env file contents (FR-043) -- only Path.exists() and Path.name
- Only reads: /proc/version (for WSL detection), runtime --version output
"""

import logging
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.scan.scanners.base import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

# Runtimes to detect: (binary_name, version_flag)
_RUNTIME_DEFINITIONS: List[Tuple[str, str]] = [
    ("python3", "--version"),
    ("node", "--version"),
    ("go", "version"),
    ("cargo", "--version"),
    ("java", "--version"),
]

# On Windows, python3 may not exist -- fall back to "python" if it reports 3.x.
# Maps (fallback_binary, version_flag) -> canonical_name
_RUNTIME_FALLBACKS: Dict[str, List[Tuple[str, str]]] = {
    "python3": [("python", "--version")],
}

# Env file names to check for (presence ONLY -- never read contents)
_ENV_FILE_NAMES: List[str] = [
    ".env",
    ".env.local",
    ".env.example",
    ".env.development",
    ".env.production",
]

# Architecture mapping from platform.machine() to canonical names
_ARCH_MAP: Dict[str, str] = {
    "x86_64": "x64",
    "AMD64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

# Platform mapping from sys.platform to canonical names
_PLATFORM_MAP: Dict[str, str] = {
    "linux": "linux",
    "darwin": "darwin",
    "win32": "win32",
    "cygwin": "win32",
}


class EnvironmentScanner(BaseScanner):
    """Scanner for OS, runtime, and env file detection.

    Detects:
    - OS: platform (linux/darwin/win32), architecture (x64/arm64), WSL
    - Runtimes: python3, node, go, cargo, java versions via --version
    - Env files: .env, .env.local, .env.example, etc. by Path.exists() ONLY

    CRITICAL: NEVER calls open() or read() on any .env file.
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "environment"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.0.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["environment.runtimes", "environment.os", "environment.env_files"]

    def scan(self, root: Path) -> ScanResult:
        """Scan for OS info, runtimes, and env files.

        Args:
            root: Absolute path to the project root directory.

        Returns:
            ScanResult with 'environment' section containing os, runtimes,
            and env_files subsections.
        """
        start = time.monotonic()
        warnings: List[str] = []

        os_info = self._detect_os(warnings)
        runtimes = self._detect_runtimes(warnings)
        env_files = self._detect_env_files(root, warnings)

        elapsed_ms = (time.monotonic() - start) * 1000

        sections: Dict[str, Any] = {
            "environment": {
                "os": os_info,
                "runtimes": runtimes,
                "env_files": env_files,
            }
        }

        return self.make_result(
            sections=sections,
            warnings=warnings,
            duration_ms=elapsed_ms,
        )

    def _detect_os(self, warnings: List[str]) -> Dict[str, Any]:
        """Detect OS platform, architecture, and WSL status.

        Platform and architecture are always available via stdlib.
        WSL detection reads /proc/version if on Linux.
        """
        raw_platform = sys.platform
        canonical_platform = _PLATFORM_MAP.get(raw_platform, raw_platform)

        raw_arch = platform.machine()
        canonical_arch = _ARCH_MAP.get(raw_arch, raw_arch)

        wsl = False
        wsl_version: Optional[str] = None

        if canonical_platform == "linux":
            wsl, wsl_version = self._detect_wsl(warnings)

        return {
            "platform": canonical_platform,
            "architecture": canonical_arch,
            "wsl": wsl,
            "wsl_version": wsl_version,
        }

    def _detect_wsl(self, warnings: List[str]) -> Tuple[bool, Optional[str]]:
        """Detect WSL by reading /proc/version for 'microsoft' or 'WSL'.

        Returns:
            Tuple of (is_wsl, wsl_version). wsl_version is '1' or '2' when
            detectable, None otherwise.
        """
        proc_version_path = Path("/proc/version")

        try:
            if not proc_version_path.exists():
                return False, None

            content = proc_version_path.read_text()
            content_lower = content.lower()

            if "microsoft" not in content_lower and "wsl" not in content_lower:
                return False, None

            # WSL2 uses a real Linux kernel and identifies as "microsoft-standard-WSL2"
            # WSL1 uses "Microsoft" in the version string but not "WSL2"
            wsl_version: Optional[str] = None
            if "wsl2" in content_lower:
                wsl_version = "2"
            elif "microsoft" in content_lower:
                # Could be WSL1 or WSL2 without explicit marker
                # WSL2 kernels typically contain "microsoft-standard-WSL2"
                # WSL1 kernels contain "Microsoft" but not "WSL2"
                wsl_version = "1"

            return True, wsl_version

        except OSError as exc:
            warnings.append(f"WSL detection failed: {exc}")
            return False, None

    def _detect_runtimes(self, warnings: List[str]) -> List[Dict[str, str]]:
        """Detect installed language runtimes via --version commands.

        Uses shutil.which() to find binaries, then subprocess with 2s timeout
        to get version strings.  For runtimes with fallbacks (e.g. python3 ->
        python on Windows), the fallback is tried when the primary is missing
        and the result is reported under the canonical name.
        """
        runtimes: List[Dict[str, str]] = []
        detected_canonical: set = set()

        for binary_name, version_flag in _RUNTIME_DEFINITIONS:
            try:
                binary_path = shutil.which(binary_name)
                if binary_path is None:
                    # Try fallbacks (e.g. python -> python3 on Windows)
                    fallbacks = _RUNTIME_FALLBACKS.get(binary_name, [])
                    for fb_binary, fb_flag in fallbacks:
                        fb_path = shutil.which(fb_binary)
                        if fb_path is None:
                            continue
                        version = self._get_version(fb_binary, fb_flag, warnings)
                        if version is not None and version.startswith("3."):
                            runtimes.append({
                                "name": binary_name,  # canonical name
                                "version": version,
                                "path": fb_path,
                            })
                            detected_canonical.add(binary_name)
                            break
                    continue

                version = self._get_version(binary_name, version_flag, warnings)
                if version is not None:
                    runtimes.append({
                        "name": binary_name,
                        "version": version,
                        "path": binary_path,
                    })
                    detected_canonical.add(binary_name)

            except Exception as exc:
                warnings.append(f"Runtime detection failed for {binary_name}: {exc}")

        return runtimes

    def _get_version(
        self, binary: str, flag: str, warnings: List[str]
    ) -> Optional[str]:
        """Run '<binary> <flag>' and extract version string.

        Args:
            binary: Binary name to execute.
            flag: Version flag (e.g., '--version', 'version').
            warnings: List to append non-fatal warnings to.

        Returns:
            Version string or None on failure.
        """
        try:
            result = subprocess.run(
                [binary, flag],
                capture_output=True,
                text=True,
                timeout=2,
            )

            # Some tools output version to stderr (e.g., java --version)
            output = result.stdout.strip() or result.stderr.strip()

            if not output:
                return "unknown"

            # Extract the first line and clean it
            first_line = output.splitlines()[0].strip()

            # Try to extract version number from common patterns
            version = self._parse_version(first_line)
            return version if version else first_line

        except subprocess.TimeoutExpired:
            warnings.append(f"{binary} {flag} timed out after 2s")
            return "unknown"
        except FileNotFoundError:
            return None
        except OSError as exc:
            warnings.append(f"{binary} {flag} failed: {exc}")
            return "unknown"

    @staticmethod
    def _parse_version(line: str) -> Optional[str]:
        """Extract a version number from a version string line.

        Handles common formats:
        - 'Python 3.11.5'         -> '3.11.5'
        - 'v20.10.0'              -> '20.10.0'
        - 'go version go1.21.0 linux/amd64' -> '1.21.0'
        - 'cargo 1.72.0 (103a7ff2e 2023-08-15)' -> '1.72.0'
        - 'openjdk 21.0.1 2023-10-17' -> '21.0.1'

        Returns:
            Cleaned version string or None if no version pattern found.
        """
        import re

        # Match 'go1.21.0' pattern (go-specific)
        go_match = re.search(r"go(\d+\.\d+(?:\.\d+)?)", line)
        if go_match:
            return go_match.group(1)

        # Match standard version patterns: v1.2.3, 1.2.3, 1.2
        version_match = re.search(r"v?(\d+\.\d+(?:\.\d+)?(?:[._-]\w+)*)", line)
        if version_match:
            return version_match.group(1)

        return None

    def _detect_env_files(
        self, root: Path, warnings: List[str]
    ) -> List[Dict[str, str]]:
        """Detect .env file patterns by existence check ONLY.

        CRITICAL: NEVER calls open() or read() on any .env file.
        Only uses Path.exists() and Path.name.

        Args:
            root: Project root directory.
            warnings: List to append non-fatal warnings to.

        Returns:
            List of dicts with 'name' and 'path' for each found env file.
        """
        env_files: List[Dict[str, str]] = []

        for env_name in _ENV_FILE_NAMES:
            try:
                env_path = root / env_name
                if env_path.exists():
                    env_files.append({
                        "name": env_path.name,
                        "path": str(env_path.relative_to(root)),
                    })
            except (OSError, ValueError) as exc:
                warnings.append(f"Env file check failed for {env_name}: {exc}")

        return env_files


def scan(root: Path) -> Dict[str, Any]:
    """Module-level convenience function for environment scanning.

    This function provides backward compatibility with the module-level scan()
    pattern used by the scanner registry auto-discovery.

    Args:
        root: Absolute path to the project root directory.

    Returns:
        Dict with 'environment' section containing os, runtimes, and env_files.
    """
    scanner = EnvironmentScanner()
    result = scanner.scan(root)
    return result.sections
