"""
Scan Orchestrator

Runs all registered scanners in parallel, collects results, combines them
with existing project-context.json using the merge rules, updates metadata,
and performs an atomic write.

Pipeline:
  1. Load existing project-context.json (if present)
  2. Run all scanners in parallel (ThreadPoolExecutor)
  3. Collect and combine scanner sections (handling environment sub-keys)
  4. Merge with existing context (section ownership model)
  5. Update metadata (last_updated, last_scan, scanner_version)
  6. Atomic write to project-context.json
  7. Return ScanOutput

Contract: specs/002-gaia-scan/data-model.md section 4
         specs/002-gaia-scan/contracts/merge-behavior.md
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.scan import __version__ as scanner_package_version
from tools.scan.config import CONTRACT_CONFIG_PATH, ScanConfig
from tools.scan.merge import (
    AGENT_ENRICHED_SECTIONS,
    collect_scanner_sections,
    merge_context,
)
from tools.scan.registry import ScannerRegistry
from tools.scan.scanners.base import BaseScanner, ScanResult

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
    merges sections with existing context, applies backward compatibility,
    and returns a ScanOutput. Individual scanner failures are caught and
    reported without aborting the scan.

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

    def _load_existing_context(self, output_path: Path) -> Dict[str, Any]:
        """Load existing project-context.json if present.

        Args:
            output_path: Path to project-context.json.

        Returns:
            Parsed JSON dict, or empty structure if file does not exist.
        """
        if not output_path.is_file():
            return {}

        try:
            with open(output_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load existing context: %s", exc)
            return {}

    def _resolve_output_path(self, project_root: Path) -> Path:
        """Resolve the output path for project-context.json.

        Args:
            project_root: Project root path.

        Returns:
            Absolute path to project-context.json.
        """
        if self.config.output_path:
            return self.config.output_path
        return project_root / ".claude" / "project-context" / "project-context.json"

    def _build_metadata(
        self,
        existing_metadata: Dict[str, Any],
        project_root: Path,
    ) -> Dict[str, Any]:
        """Build updated metadata section (Rule 6: always update).

        Preserves user-set fields (environment, cloud_provider, etc.) while
        updating timestamps and scanner version.

        Args:
            existing_metadata: Existing metadata from project-context.json.
            project_root: Project root path.

        Returns:
            Updated metadata dict.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        metadata = dict(existing_metadata) if existing_metadata else {}
        metadata["version"] = metadata.get("version", "2.0")
        metadata["last_updated"] = now_iso

        # Read contract_version from context-contracts.json
        contract_version = self._read_contract_version()
        if contract_version:
            metadata["contract_version"] = contract_version

        # Ensure scan_config sub-section exists
        scan_config = metadata.get("scan_config", {})
        if not isinstance(scan_config, dict):
            scan_config = {}
        scan_config["last_scan"] = now_iso
        scan_config["scanner_version"] = scanner_package_version
        scan_config["staleness_hours"] = self.config.staleness_hours
        metadata["scan_config"] = scan_config

        return metadata

    @staticmethod
    def _read_contract_version() -> Optional[str]:
        """Read the version field from config/context-contracts.json.

        Returns:
            Version string (e.g. "3.0"), or None if file is missing or unreadable.
        """
        try:
            if CONTRACT_CONFIG_PATH.is_file():
                with open(CONTRACT_CONFIG_PATH, "r") as f:
                    data = json.load(f)
                version = data.get("version")
                if isinstance(version, str) and version:
                    return version
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read contract version: %s", exc)
        return None

    def _atomic_write(self, output_path: Path, data: Dict[str, Any]) -> None:
        """Atomically write data to JSON file.

        Writes to a temp file in the same directory, then renames.
        This prevents corruption from concurrent reads or crashes.

        Args:
            output_path: Target file path.
            data: Dict to serialize as JSON.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(".tmp")

        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2, sort_keys=False)
                f.write("\n")
            os.rename(str(tmp_path), str(output_path))
        except OSError as exc:
            logger.error("Atomic write failed: %s", exc)
            # Clean up temp file if rename failed
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    def run(
        self,
        project_root: Optional[Path] = None,
        write_output: bool = True,
    ) -> ScanOutput:
        """Run all registered scanners and return aggregated output.

        Full pipeline:
          1. Load existing project-context.json
          2. Run scanners in parallel (or sequentially)
          3. Collect and combine scanner sections
          4. Merge with existing context using ownership rules
          5. Update metadata
          6. Atomic write to project-context.json (if write_output=True)
          7. Return ScanOutput

        Args:
            project_root: Project root path. Falls back to config.project_root.
            write_output: Whether to write the result to disk (default True).

        Returns:
            ScanOutput with merged sections, warnings, errors, and timing.
        """
        root = project_root or self.config.project_root
        start_ms = time.monotonic() * 1000

        # Step 1: Load existing context
        output_path = self._resolve_output_path(root)
        existing_full = self._load_existing_context(output_path)
        existing_sections = existing_full.get("sections", {})
        existing_metadata = existing_full.get("metadata", {})
        existing_paths = existing_full.get("paths", {})

        # Step 2: Run all scanners
        scanners = self.registry.get_all()
        if self.config.scanners:
            requested = set(self.config.scanners)
            scanners = [s for s in scanners if s.SCANNER_NAME in requested]

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

        # Step 3: Collect and combine scanner sections
        scan_sections = collect_scanner_sections(scanner_results)

        # Step 4: Merge with existing context
        section_owners = self.registry.get_section_owners()

        # Merge (no backward-compat sections -- consumers read v2 directly)
        merged_sections = merge_context(
            existing=existing_sections,
            scan_sections=scan_sections,
            section_owners=section_owners,
        )

        # Step 5: Build metadata
        metadata = self._build_metadata(existing_metadata, root)

        # Determine which sections were updated vs preserved
        sections_updated = sorted(set(scan_sections.keys()))
        sections_preserved = sorted(
            name for name in existing_sections
            if name in AGENT_ENRICHED_SECTIONS
        )

        # Build full output document
        full_context: Dict[str, Any] = {
            "metadata": metadata,
            "paths": existing_paths,
            "sections": merged_sections,
        }

        # Update paths from infrastructure if available
        infra = merged_sections.get("infrastructure", {})
        if isinstance(infra, dict):
            infra_paths = infra.get("paths", {})
            if isinstance(infra_paths, dict) and infra_paths:
                full_context["paths"] = {**existing_paths, **infra_paths}

        # Step 7: Atomic write
        if write_output:
            self._atomic_write(output_path, full_context)

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