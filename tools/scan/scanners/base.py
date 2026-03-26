"""
Base Scanner Protocol

Defines the abstract base class that all scanner modules must implement.
Each scanner is a pure function: it reads filesystem state and returns
structured section data without side effects.

Contract: contracts/scanner-interface.md
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ScanResult:
    """Immutable result from a single scanner execution.

    Attributes:
        scanner: Scanner name that produced this result.
        sections: Mapping of section names to section data.
        warnings: Non-fatal warnings encountered during scanning.
        duration_ms: Execution time in milliseconds.
    """

    scanner: str = ""
    sections: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


class BaseScanner(ABC):
    """Abstract base class for all scanner modules.

    Every scanner MUST implement:
    - SCANNER_NAME: Unique scanner identifier (e.g., "stack", "git")
    - SCANNER_VERSION: Semver string for schema tracking (e.g., "1.0.0")
    - OWNED_SECTIONS: List of section names this scanner owns in project-context.json
    - scan(root): Pure function that scans the project and returns a dict of sections

    Pure Function Contract:
    - No file writes
    - No state modification
    - No network calls
    - No command execution that modifies state
    - Only reads: filesystem reads, command -v, <tool> --version
    - MUST NOT raise exceptions to caller
    - MUST catch all internal errors and return {} or partial results
    - Individual file read failures MUST NOT abort the scanner

    Performance:
    - SHOULD complete in under 3 seconds for typical projects
    - MUST respect 2-second timeout for --version calls

    Optional workspace_info attribute:
    - Set by the orchestrator before scan() when workspace type has been
      pre-detected. Scanners can check self.workspace_info for multi-repo
      awareness. Defaults to None (single-repo assumed).
    """

    def __init__(self) -> None:
        self.workspace_info = None  # Set by orchestrator if available

    @property
    @abstractmethod
    def SCANNER_NAME(self) -> str:
        """Unique scanner identifier (e.g., 'stack', 'git')."""
        ...

    @property
    @abstractmethod
    def SCANNER_VERSION(self) -> str:
        """Scanner version for schema tracking (semver)."""
        ...

    @property
    @abstractmethod
    def OWNED_SECTIONS(self) -> List[str]:
        """Section names this scanner owns in project-context.json."""
        ...

    @abstractmethod
    def scan(self, root: Path) -> Dict[str, Any]:
        """Scan the project at root and return detected sections.

        Args:
            root: Absolute path to the project root directory.
                  MUST be validated by caller (exists, is directory).

        Returns:
            Dict mapping section names to section data.
            Empty dict on complete failure.
            Partial dict when some detection succeeds and some fails.

        Side Effects:
            NONE. This function is pure.
        """
        ...

    @property
    def source_tag(self) -> str:
        """Return the _source metadata tag for sections produced by this scanner."""
        return f"scanner:{self.SCANNER_NAME}"

    def make_result(
        self,
        sections: Dict[str, Any],
        warnings: Optional[List[str]] = None,
        duration_ms: float = 0.0,
    ) -> ScanResult:
        """Create a ScanResult with this scanner's metadata.

        Automatically injects _source tag into each section.

        Args:
            sections: Section name to section data mapping.
            warnings: Optional list of non-fatal warnings.
            duration_ms: Execution time in milliseconds.

        Returns:
            Frozen ScanResult instance.
        """
        tagged_sections = {}
        for name, data in sections.items():
            if isinstance(data, dict):
                tagged_sections[name] = {"_source": self.source_tag, **data}
            else:
                tagged_sections[name] = data

        return ScanResult(
            scanner=self.SCANNER_NAME,
            sections=tagged_sections,
            warnings=warnings or [],
            duration_ms=duration_ms,
        )
