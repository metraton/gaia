"""
Scan-core -- the atomic, pure scan engine.

This is the single núcleo of `gaia scan`. Given a *target path* and the
*workspace* it belongs to, it:

    1. runs the read-only scanners over the target tree,
    2. populates the Gaia store (projects / apps / services / ... rows) via
       :func:`tools.scan.store_populator.scan_workspace_to_store`,
    3. soft-prunes (marks missing, never deletes) project rows that vanished
       from disk -- preserving the v16 soft-delete contract,
    4. records the scan timestamp on the workspace row.

It NEVER installs. It does not create ``package.json``, run ``npm``, build a
``.claude/`` directory, install git hooks, or touch Claude Code. Installation
is a separate flow owned by ``bin/cli/install.py`` + ``cli._install_helpers``.

The function is the reusable seam: the CLI, SessionStart, hooks, and tests all
call :func:`scan_workspace` and share one code path. Install logic lives
elsewhere by design (separar scan de install).

Public API::

    scan_workspace(project_root, workspace, *, config, agent, db_path) -> ScanResult
    is_gaia_workspace(path) -> bool
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("gaia.scan.core")

# Agent identity used when scan-core populates the store. Single named constant
# so tests and future callers can refer to it.
SCAN_AGENT = "gaia-system"

# Tables the scan agent must be able to write for store population to succeed.
SCAN_TABLES = [
    "workspaces", "projects", "apps", "services", "libraries", "features",
    "integrations", "gaia_installations",
    "tf_modules", "tf_live", "releases", "workloads",
    "clusters_defined",
]


@dataclass
class ScanResult:
    """Outcome of one scan-core run.

    Attributes:
        output: The raw ``ScanOutput`` from the orchestrator (scanner results,
            warnings, errors, timings).
        populated: The dict returned by ``scan_workspace_to_store`` (keyed per
            ``"<workspace>/<project>"``), or ``None`` when population was
            skipped (scanner errors) or failed non-fatally.
        marked_missing: Total project rows soft-marked missing across all
            workspaces touched by this scan.
        failures: Per-project population failures collected during the scan
            (each ``{"workspace", "project", "path", "error"}``). Empty when
            every repo populated cleanly.
    """

    output: Any
    populated: Optional[dict] = None
    marked_missing: int = 0
    failures: list = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """True when the underlying scan reported scanner-level errors."""
        return bool(getattr(self.output, "errors", None))


# ---------------------------------------------------------------------------
# Workspace detection (used by the CLI entry points to enforce the
# "outside a workspace + no target -> clean error" contract)
# ---------------------------------------------------------------------------

def is_gaia_workspace(path: Path) -> bool:
    """Return True when ``path`` is an installed Gaia workspace.

    Reuses the canonical, mode-agnostic ``.claude/plugin-registry.json`` signal
    (the same one scan-core uses for workspace attribution), so detection stays
    consistent across the codebase. Read-only; never raises.
    """
    from tools.scan.store_populator import _is_installed_gaia_workspace
    return _is_installed_gaia_workspace(path)


# ---------------------------------------------------------------------------
# Scanner execution (pure, read-only)
# ---------------------------------------------------------------------------

def run_scanners(project_root: Path, config: Any) -> Any:
    """Run the scanner orchestrator over ``project_root`` and return ScanOutput.

    Pure read-only discovery. No store writes, no install side-effects.
    """
    from tools.scan.orchestrator import ScanOrchestrator
    from tools.scan.registry import ScannerRegistry

    registry = ScannerRegistry()
    orchestrator = ScanOrchestrator(registry=registry, config=config)
    return orchestrator.run(project_root=project_root)


# ---------------------------------------------------------------------------
# Store population (projects/apps/services/... + soft-prune)
# ---------------------------------------------------------------------------

def ensure_scan_permissions(db_path: Path | None = None) -> None:
    """Idempotently grant ``SCAN_AGENT`` write access on scanner tables.

    Mirrors the permission grant in ``tools/scan/migrate_workspace.py`` so that
    ``scan_workspace_to_store`` never hits a ``"rejected"`` response. Safe to
    call repeatedly; uses ``INSERT OR REPLACE``.
    """
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        for table in SCAN_TABLES:
            con.execute(
                "INSERT OR REPLACE INTO agent_permissions "
                "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
                (table, SCAN_AGENT),
            )
        con.commit()
    except Exception:  # pragma: no cover -- non-fatal
        try:
            con.rollback()
        except Exception:
            pass
    finally:
        con.close()


def populate_store(
    workspace: str,
    project_root: Path,
    *,
    agent: str = SCAN_AGENT,
    db_path: Path | None = None,
) -> tuple[dict, int, list]:
    """Populate the store from a scan, then soft-prune missing project rows.

    This is the single wiring point between scan-core and the store populator.
    After populating scanner-owned data, project rows that no longer exist on
    disk are **marked missing** -- NOT deleted -- via ``mark_missing_in``
    (soft-delete v16: ``status='missing'`` / ``missing_since`` set).

    Soft-delete safety constraints (preserved from the original CLI path):
      * Scoped strictly per workspace (never touches sibling workspaces).
      * Only marks rows whose project name is absent from the current scan's
        discovered set. Live projects are reactivated by ``populate_project``.
      * A project that merely vanished from a partial walk is preserved as
        ``status='missing'`` -- recoverable on reappearance, never destroyed.
      * Child tables (apps/services/features/...) are NOT touched by the
        soft-delete; they remain attached to the now-missing parent row.
      * A repo that failed to populate (transient error) is treated as a
        survivor so a one-off failure never triggers data loss.

    Returns:
        ``(populated, marked_missing, failures)`` where ``populated`` is the
        ``scan_workspace_to_store`` result dict, ``marked_missing`` is the
        total rows soft-marked across workspaces, and ``failures`` is the
        per-project failure list (empty on a clean run).
    """
    ensure_scan_permissions(db_path)

    from tools.scan.store_populator import scan_workspace_to_store
    results = scan_workspace_to_store(
        workspace=workspace,
        root=project_root,
        agent=agent,
        db_path=db_path,
    )

    # Build the surviving-project set per workspace. The canonical
    # classification rule attributes projects to the nearest installed
    # ancestor, so one scan may populate several workspaces; we must mark each
    # workspace only against the projects discovered for THAT workspace.
    surviving_by_workspace: dict[str, list[tuple]] = {}
    for key, res in results.items():
        if key in ("__workspace__", "__failures__"):
            continue
        ws_name = res.get("workspace") if isinstance(res, dict) else None
        proj = res.get("project") if isinstance(res, dict) else None
        proj_name = proj.get("name") if isinstance(proj, dict) else None
        if not ws_name or not proj_name:
            ws_name, _, proj_name = key.partition("/")
        if ws_name and proj_name:
            surviving_by_workspace.setdefault(ws_name, []).append((proj_name,))

    # Always evaluate the CLI-root workspace, so a workspace whose projects all
    # disappeared still gets a soft-prune pass.
    surviving_by_workspace.setdefault(workspace, [])

    # Per-project isolation: a repo that failed to populate did NOT contribute
    # to the survivor set, so without this it would be wrongly marked missing
    # on account of a transient failure. Treat failed-but-on-disk repos as
    # survivors so their rows keep their current status.
    failures = results.get("__failures__") if isinstance(results, dict) else None
    failures = failures or []
    for f in failures:
        ws_name = f.get("workspace") or workspace
        proj_name = f.get("project")
        if proj_name:
            surviving_by_workspace.setdefault(ws_name, []).append((proj_name,))

    from gaia.store.writer import mark_missing_in
    total_marked = 0
    for ws_name, survivors in surviving_by_workspace.items():
        marked = mark_missing_in("projects", ws_name, survivors, db_path=db_path)
        if marked:
            total_marked += marked
            logger.info(
                "marked %d project row(s) missing in workspace=%r", marked, ws_name
            )

    for f in failures:
        logger.warning(
            "project population failed (isolated, non-fatal): "
            "workspace=%r project=%r error=%s",
            f.get("workspace"), f.get("project"), f.get("error"),
        )

    return results, total_marked, failures


# ---------------------------------------------------------------------------
# The single núcleo
# ---------------------------------------------------------------------------

def scan_workspace(
    project_root: Path,
    workspace: str,
    *,
    config: Any,
    agent: str = SCAN_AGENT,
    db_path: Path | None = None,
) -> ScanResult:
    """Run scanners over ``project_root`` and sync ``workspace`` into the store.

    This is scan-core: the atomic, reusable function every entry point shares.
    It discovers, classifies, populates, and soft-prunes -- and never installs.

    Args:
        project_root: The target path to scan (a workspace root or a named
            target). Must be an existing directory; callers validate this and
            emit a clean error before reaching here.
        workspace: The workspace identity the target belongs to (resolved by
            the caller via ``gaia.project.current``).
        config: A populated ``ScanConfig`` (scanners list, verbosity, etc.).
        agent: Agent name for store permission enforcement.
        db_path: Optional explicit DB path (test override).

    Returns:
        A :class:`ScanResult`. Store population is best-effort: if it raises,
        the failure is logged and ``populated`` stays ``None`` -- the scan
        itself still succeeds. Timestamp recording is skipped only when the
        scan reported scanner-level errors.
    """
    output = run_scanners(project_root, config)

    if output.errors:
        # Errors -> do not record a "fresh" timestamp and do not populate.
        return ScanResult(output=output)

    populated: Optional[dict] = None
    marked = 0
    failures: list = []

    # Stamp the timestamp and populate. Attribution is deterministic: every
    # discovered repo belongs to the caller-provided ``workspace`` (the
    # inference / demote layer was removed -- see tools.scan.classify for the
    # deterministic --workspace-driven classifier used by the CLI).
    try:
        from gaia.store.writer import set_workspace_last_scan_at
        set_workspace_last_scan_at(workspace, db_path=db_path)
        populated, marked, failures = populate_store(
            workspace, project_root, agent=agent, db_path=db_path
        )
    except Exception as exc:  # pragma: no cover -- non-fatal
        logger.warning("store population failed (non-fatal): %s", exc)

    return ScanResult(
        output=output,
        populated=populated,
        marked_missing=marked,
        failures=failures,
    )
