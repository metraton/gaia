"""
Scan Orchestrator

Runs all registered scanners in parallel, collects results, and returns
aggregated ScanOutput. gaia.db is the sole persistence layer -- this
module never reads or writes project-context.json.

Pipeline:
  1. Detect workspace type
  2. Run all scanners in parallel (ThreadPoolExecutor)
  3. Collect and combine scanner sections (handling environment sub-keys)
  4. Cross-populate derived fields
  5. Return ScanOutput
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan import __version__ as scanner_package_version
from tools.scan.config import ScanConfig
from tools.scan.merge import (
    AGENT_ENRICHED_SECTIONS,
    collect_scanner_sections,
    merge_context,
)
from tools.scan.registry import ScannerRegistry
from tools.scan.scanners.base import BaseScanner, ScanResult
from tools.scan.workspace import WorkspaceInfo, detect_workspace_type

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanOutput:
    """Aggregated output from all scanners.

    Attributes:
        context: Full merged project-context data (top-level with metadata,
                 paths, and sections).
        sections_updated: Section names that were updated by scanners.
        sections_preserved: Agent-enriched sections left untouched.
        warnings: Aggregated warnings from all scanners.
        errors: Aggregated errors from all scanners.
        duration_ms: Total scan time in milliseconds.
        scanner_results: Per-scanner ScanResult mapping.
    """

    context: Dict[str, Any] = field(default_factory=dict)
    sections_updated: List[str] = field(default_factory=list)
    sections_preserved: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    scanner_results: Dict[str, ScanResult] = field(default_factory=dict)


class ScanOrchestrator:
    """Orchestrates parallel scanner execution with fault isolation.

    Runs all scanners from a ScannerRegistry, collects their results,
    merges sections, and returns a ScanOutput. Individual scanner failures
    are caught and reported without aborting the scan.

    Args:
        registry: ScannerRegistry with discovered scanners.
        config: ScanConfig with orchestration settings.
    """

    def __init__(
        self,
        registry: Optional[ScannerRegistry] = None,
        config: Optional[ScanConfig] = None,
    ) -> None:
        self.registry = registry or ScannerRegistry()
        self.config = config or ScanConfig()

    def _run_scanner(
        self,
        scanner: BaseScanner,
        project_root: Path,
    ) -> ScanResult:
        """Run a single scanner with fault isolation.

        Args:
            scanner: Scanner instance to execute.
            project_root: Project root path.

        Returns:
            ScanResult from the scanner, or an error result on failure.
        """
        start_ms = time.monotonic() * 1000
        try:
            result = scanner.scan(project_root)
            return result
        except Exception as exc:
            elapsed_ms = (time.monotonic() * 1000) - start_ms
            error_msg = (
                f"Scanner '{scanner.SCANNER_NAME}' failed: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.warning(error_msg)
            return ScanResult(
                scanner=scanner.SCANNER_NAME,
                sections={},
                warnings=[error_msg],
                duration_ms=elapsed_ms,
            )

    def _build_metadata(self, project_root: Path) -> Dict[str, Any]:
        """Build metadata section for the scan output.

        Args:
            project_root: Project root path.

        Returns:
            Metadata dict with timestamps and version.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "version": "2.0",
            "last_updated": now_iso,
            "scan_config": {
                "last_scan": now_iso,
                "scanner_version": scanner_package_version,
                "staleness_hours": self.config.staleness_hours,
            },
        }

    def run(
        self,
        project_root: Optional[Path] = None,
    ) -> ScanOutput:
        """Run all registered scanners and return aggregated output.

        Full pipeline:
          1. Detect workspace type
          2. Run scanners in parallel (or sequentially)
          3. Collect and combine scanner sections
          4. Merge sections using ownership rules
          5. Return ScanOutput

        Args:
            project_root: Project root path. Falls back to config.project_root.

        Returns:
            ScanOutput with merged sections, warnings, errors, and timing.
        """
        root = project_root or self.config.project_root
        start_ms = time.monotonic() * 1000

        # Detect workspace type BEFORE running scanners
        workspace_info = detect_workspace_type(root)
        if workspace_info.is_multi_repo:
            logger.info(
                "Multi-repo workspace: %d repos detected",
                len(workspace_info.repo_dirs),
            )

        # Select scanners
        scanners = self.registry.get_all()
        if self.config.scanners:
            requested = set(self.config.scanners)
            scanners = [s for s in scanners if s.SCANNER_NAME in requested]

        for scanner in scanners:
            scanner.workspace_info = workspace_info

        scanner_results: Dict[str, ScanResult] = {}
        all_warnings: List[str] = []
        all_errors: List[str] = []

        if scanners and self.config.parallel:
            scanner_results, all_warnings, all_errors = self._run_parallel(
                scanners, root
            )
        else:
            scanner_results, all_warnings, all_errors = self._run_sequential(
                scanners, root
            )

        # Collect and combine scanner sections
        scan_sections = collect_scanner_sections(scanner_results)

        # Merge with empty existing context (no JSON persistence)
        section_owners = self.registry.get_section_owners()
        merged_sections = merge_context(
            existing={},
            scan_sections=scan_sections,
            section_owners=section_owners,
        )

        # Determine which sections were updated vs preserved
        sections_updated = sorted(set(scan_sections.keys()))
        sections_preserved: List[str] = []

        # Ensure architecture_overview exists as empty dict so contract
        # references are satisfied (it appears in ALL agent contracts).
        if "architecture_overview" not in merged_sections:
            merged_sections["architecture_overview"] = {}

        # Derive infrastructure.paths from scanner data
        self._derive_infrastructure_paths(merged_sections)

        # Cross-populate git.monorepo.workspace_config
        self._cross_populate_monorepo(merged_sections)

        # Remove empty {} placeholders for agent-enriched and mixed sections
        from tools.scan.merge import MIXED_SECTION_SCANNER_FIELDS
        remove_if_empty = (
            AGENT_ENRICHED_SECTIONS
            | frozenset(MIXED_SECTION_SCANNER_FIELDS.keys())
        ) - {"architecture_overview"}
        for section_name in list(merged_sections.keys()):
            if section_name in remove_if_empty:
                if merged_sections[section_name] == {}:
                    del merged_sections[section_name]

        metadata = self._build_metadata(root)
        full_context: Dict[str, Any] = {
            "metadata": metadata,
            "sections": merged_sections,
        }

        elapsed_ms = (time.monotonic() * 1000) - start_ms

        return ScanOutput(
            context=full_context,
            sections_updated=sections_updated,
            sections_preserved=sections_preserved,
            warnings=all_warnings,
            errors=all_errors,
            duration_ms=elapsed_ms,
            scanner_results=scanner_results,
        )

    @staticmethod
    def _derive_infrastructure_paths(
        merged_sections: Dict[str, Any],
    ) -> None:
        """Derive infrastructure.paths shortcuts from detected scanner data.

        Populates infrastructure.paths.gitops, .terraform, and .app_services
        from orchestration and infrastructure scanner results when the paths
        are not already set.

        Args:
            merged_sections: Merged sections dict (mutated in place).
        """
        infra = merged_sections.get("infrastructure")
        if not isinstance(infra, dict):
            return

        paths = infra.setdefault("paths", {})

        if not paths.get("gitops"):
            orch = merged_sections.get("orchestration")
            if isinstance(orch, dict):
                gitops = orch.get("gitops", {})
                if isinstance(gitops, dict) and gitops.get("config_path"):
                    paths["gitops"] = gitops["config_path"]

        if not paths.get("terraform"):
            for iac_entry in infra.get("iac", []):
                if isinstance(iac_entry, dict) and iac_entry.get("tool") in (
                    "terraform",
                    "terragrunt",
                ):
                    base_path = iac_entry.get("base_path")
                    if base_path and base_path != ".":
                        paths["terraform"] = base_path
                        break

        if not paths.get("app_services"):
            containers = infra.get("containers", [])
            dockerfile_dirs: list = []
            for container in containers:
                if not isinstance(container, dict):
                    continue
                if container.get("tool") != "docker":
                    continue
                for fpath in container.get("files", []):
                    parent = str(Path(fpath).parent)
                    if parent != ".":
                        dockerfile_dirs.append(parent)

            if dockerfile_dirs:
                from pathlib import PurePosixPath

                parts_list = [PurePosixPath(d).parts for d in dockerfile_dirs]
                common: list = []
                for segments in zip(*parts_list):
                    if len(set(segments)) == 1:
                        common.append(segments[0])
                    else:
                        break
                if common:
                    paths["app_services"] = str(PurePosixPath(*common))

        for key in list(paths.keys()):
            if paths[key] is None:
                del paths[key]

    @staticmethod
    def _cross_populate_monorepo(
        merged_sections: Dict[str, Any],
    ) -> None:
        """Cross-populate git.monorepo.workspace_config from project_identity.

        When the stack scanner detects a monorepo (project_identity.type ==
        'monorepo' and project_identity.monorepo has data), propagate the
        workspace_config to git.monorepo so both sections are consistent.

        Args:
            merged_sections: Merged sections dict (mutated in place).
        """
        identity = merged_sections.get("project_identity")
        git = merged_sections.get("git")
        if not isinstance(identity, dict) or not isinstance(git, dict):
            return

        monorepo_data = identity.get("monorepo", {})
        if not isinstance(monorepo_data, dict):
            return

        if monorepo_data.get("detected"):
            git_monorepo = git.setdefault("monorepo", {})
            if isinstance(git_monorepo, dict):
                tool = monorepo_data.get("tool")
                if tool and not git_monorepo.get("workspace_config"):
                    git_monorepo["workspace_config"] = tool

    def _run_parallel(
        self,
        scanners: List[BaseScanner],
        root: Path,
    ) -> tuple:
        """Run scanners in parallel using ThreadPoolExecutor.

        Args:
            scanners: List of scanner instances to run.
            root: Project root path.

        Returns:
            Tuple of (scanner_results, all_warnings, all_errors).
        """
        scanner_results: Dict[str, ScanResult] = {}
        all_warnings: List[str] = []
        all_errors: List[str] = []

        with ThreadPoolExecutor(
            max_workers=min(len(scanners), 8)
        ) as executor:
            future_to_scanner = {
                executor.submit(self._run_scanner, scanner, root): scanner
                for scanner in scanners
            }
            for future in as_completed(future_to_scanner):
                scanner = future_to_scanner[future]
                try:
                    result = future.result(
                        timeout=self.config.timeout_per_scanner
                    )
                except Exception as exc:
                    error_msg = (
                        f"Scanner '{scanner.SCANNER_NAME}' timed out or "
                        f"failed in executor: {type(exc).__name__}: {exc}"
                    )
                    logger.warning(error_msg)
                    result = ScanResult(
                        scanner=scanner.SCANNER_NAME,
                        sections={},
                        warnings=[error_msg],
                        duration_ms=0.0,
                    )
                    all_errors.append(error_msg)

                scanner_results[scanner.SCANNER_NAME] = result
                all_warnings.extend(result.warnings)

        return scanner_results, all_warnings, all_errors

    def _run_sequential(
        self,
        scanners: List[BaseScanner],
        root: Path,
    ) -> tuple:
        """Run scanners sequentially.

        Args:
            scanners: List of scanner instances to run.
            root: Project root path.

        Returns:
            Tuple of (scanner_results, all_warnings, all_errors).
        """
        scanner_results: Dict[str, ScanResult] = {}
        all_warnings: List[str] = []
        all_errors: List[str] = []

        for scanner in scanners:
            result = self._run_scanner(scanner, root)
            scanner_results[scanner.SCANNER_NAME] = result
            all_warnings.extend(result.warnings)

        return scanner_results, all_warnings, all_errors
