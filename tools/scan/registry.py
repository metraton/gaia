"""
Scanner Auto-Discovery Registry

Auto-discovers scanner modules from tools/scan/scanners/ directory.
Every concrete BaseScanner subclass found in a module (other than base.py and
names starting with "_") is registered. Registration rejects a duplicate
scanner name or a section already owned by another scanner.
"""

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Dict, List, Optional

from tools.scan.scanners.base import BaseScanner

logger = logging.getLogger(__name__)


class ScannerRegistry:
    """Registry for auto-discovered scanner modules.

    Discovery runs in the constructor. A module that fails to import is
    logged as a warning and skipped.
    """

    def __init__(self) -> None:
        self._scanners: Dict[str, BaseScanner] = {}
        self._section_owners: Dict[str, str] = {}
        self._discover()

    def _discover(self) -> None:
        """Import each scanner module and register its concrete BaseScanner subclasses."""
        scanners_dir = Path(__file__).parent / "scanners"

        if not scanners_dir.is_dir():
            logger.warning("Scanners directory not found: %s", scanners_dir)
            return

        scanners_package = "tools.scan.scanners"

        for module_info in pkgutil.iter_modules([str(scanners_dir)]):
            if module_info.name.startswith("_") or module_info.name == "base":
                continue

            try:
                module = importlib.import_module(
                    f"{scanners_package}.{module_info.name}"
                )

                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseScanner)
                        and attr is not BaseScanner
                    ):
                        try:
                            scanner_instance = attr()
                            self.register(scanner_instance)
                        except TypeError:
                            # Cannot instantiate (still abstract)
                            pass

            except Exception as exc:
                logger.warning(
                    "Failed to load scanner module '%s': %s",
                    module_info.name,
                    exc,
                )

    def register(self, scanner: BaseScanner) -> None:
        """Register a scanner, validating section ownership uniqueness.

        Args:
            scanner: Scanner instance to register.

        Raises:
            ValueError: If scanner name is duplicate or section ownership overlaps.
        """
        name = scanner.SCANNER_NAME

        if name in self._scanners:
            raise ValueError(
                f"Duplicate scanner name: '{name}' is already registered"
            )

        for section in scanner.OWNED_SECTIONS:
            if section in self._section_owners:
                existing_owner = self._section_owners[section]
                raise ValueError(
                    f"Section ownership overlap: section '{section}' is owned by "
                    f"'{existing_owner}', cannot be claimed by '{name}'"
                )

        self._scanners[name] = scanner
        for section in scanner.OWNED_SECTIONS:
            self._section_owners[section] = name

        logger.debug("Registered scanner: %s (sections: %s)", name, scanner.OWNED_SECTIONS)

    def get_all(self) -> List[BaseScanner]:
        """Return all registered scanners."""
        return list(self._scanners.values())

    def get_by_name(self, name: str) -> Optional[BaseScanner]:
        """Get a scanner by name.

        Args:
            name: Scanner name to look up.

        Returns:
            Scanner instance or None if not found.
        """
        return self._scanners.get(name)

    def get_section_owners(self) -> Dict[str, str]:
        """Return mapping of section name to owning scanner name."""
        return dict(self._section_owners)

    def list_names(self) -> List[str]:
        """Return list of all registered scanner names."""
        return list(self._scanners.keys())
