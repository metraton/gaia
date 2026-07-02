"""
REMOVED: workspace-root anchoring tests (M1-T3, inference layer).

This module previously tested two behaviors that belonged to the scan
INFERENCE layer:

  * ``cli.scan._find_workspace_root`` -- walking up from a subdirectory to the
    nearest INSTALLED Gaia workspace ancestor (via the
    ``.claude/plugin-registry.json`` signal) and anchoring the scan there.
  * ``cli.scan._run_scan`` -- the old scan driver that resolved workspace
    identity implicitly and ran scan-core.

Both were removed when scan became DETERMINISTIC: ``gaia scan`` now takes a
single REQUIRED ``--workspace <name>`` and an explicit ``root`` positional, and
classifies each repo with :mod:`tools.scan.classify` (ruleset R1-R6). There is
no install-signal detection and no "nearest installed ancestor" anchoring, so
``_find_workspace_root`` / ``_run_scan`` no longer exist and these tests
asserted removed behavior.

The new deterministic surface (arg parsing, guards, the 6 validation cases,
persistence + reconcile against a temp DB) is covered by
``tests/cli/test_scan.py``. This file is kept as a tombstone documenting the
removed behavior; it intentionally defines no tests.
"""

from __future__ import annotations
