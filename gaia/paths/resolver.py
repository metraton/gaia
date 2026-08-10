"""
gaia.paths.resolver -- Path resolution for Gaia storage substrate.

Resolves canonical paths for all Gaia state directories and files.
All functions read GAIA_DATA_DIR from the environment on each call
(no caching) so that tests using monkeypatch.setenv work correctly.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram; patterns lifted with attribution.

Public API::

    from gaia.paths.resolver import (
        data_dir,
        db_path,
        snapshot_dir,
        state_dir,
        workspaces_dir,
        logs_dir,
        events_dir,
        cache_dir,
        scratch_dir,
        evidence_dir,
        worktrees_dir,
        tmp_dir,
        rejected_turns_dir,
    )
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """Return the root Gaia data directory.

    Respects the GAIA_DATA_DIR environment variable. Falls back to
    ``~/.gaia`` when the variable is not set.

    Returns:
        Absolute Path to the root data directory.
    """
    override = os.environ.get("GAIA_DATA_DIR", "")
    if override:
        return Path(override).resolve()
    return Path.home() / ".gaia"


def db_path() -> Path:
    """Return the path to the main Gaia SQLite database.

    Returns:
        ``data_dir() / "gaia.db"``
    """
    return data_dir() / "gaia.db"


def snapshot_dir() -> Path:
    """Return the path to the snapshot directory.

    Canonical directory for DB snapshots (e.g. ``gaia uninstall --backup``).
    Uses the plural form to match every caller (``bin/cli/uninstall.py``,
    ``bin/cli/paths.py``, ``bin/cli/workspace.py``) -- there is exactly one
    snapshot directory, never a singular sibling.

    Returns:
        ``data_dir() / "snapshots"``
    """
    return data_dir() / "snapshots"


def state_dir() -> Path:
    """Return the path to the state directory.

    Returns:
        ``data_dir() / "state"``
    """
    return data_dir() / "state"


def workspaces_dir() -> Path:
    """Return the path to the workspaces directory.

    Workspace-scoped state lives here, keyed by workspace identity
    (canonical form: ``host/owner/repo``).

    Returns:
        ``data_dir() / "workspaces"``
    """
    return data_dir() / "workspaces"


def logs_dir() -> Path:
    """Return the path to the logs directory.

    Returns:
        ``data_dir() / "logs"``
    """
    return data_dir() / "logs"


def events_dir() -> Path:
    """Return the path to the events directory.

    Returns:
        ``data_dir() / "events"``
    """
    return data_dir() / "events"


def cache_dir() -> Path:
    """Return the path to the cache directory.

    Returns:
        ``data_dir() / "cache"``
    """
    return data_dir() / "cache"


def scratch_dir() -> Path:
    """Return the path to the Gaia scratch directory.

    Ephemeral working space for Gaia agents. This is the ONLY location where
    ``rm`` (including ``rm -rf``) is classified T0 (no approval required):
    the security layer grants that exception only when every target path
    resolves strictly inside this directory (see
    ``hooks/modules/security/mutative_verbs.py`` -- ``_rm_targets_only_scratch``).
    Anything outside remains T3, and the catastrophic floor is untouched.

    Lives under ``data_dir()`` so a ``GAIA_DATA_DIR`` override relocates the
    scratch directory too, keeping the T0 exception aligned with the active
    data root.

    Returns:
        ``data_dir() / "scratch"``
    """
    return data_dir() / "scratch"


def evidence_dir() -> Path:
    """Return the path to the canonical evidence blob store.

    Root for every evidence blob copied out of an agent's working tree:
    ``evidence_dir() / {workspace}/{brief_slug}/{ac_id}/{uuid4}.{ext}`` (see
    ``gaia/evidence/fs.py::blob_path_for``).

    Lives under ``data_dir()`` so a ``GAIA_DATA_DIR`` override relocates the
    evidence store too. Before this function existed, ``gaia/evidence/fs.py``
    computed this root as ``Path.home() / ".gaia" / "evidence"`` directly,
    ignoring ``GAIA_DATA_DIR`` entirely -- a test that isolates itself by
    overriding the data directory would still write blobs into the real
    per-user store instead of its own sandbox.

    Returns:
        ``data_dir() / "evidence"``
    """
    return data_dir() / "evidence"


def worktrees_dir() -> Path:
    """Return the path to Gaia's central root for agentic git worktrees.

    A worktree an agent creates for isolated repo work lives under here,
    outside every repository, keyed by repo identity and contract_id -- never
    inside the repo's own working tree, where it could be seen as untracked
    changes or (worse) committed by mistake.

    Returns:
        ``data_dir() / "worktrees"``
    """
    return data_dir() / "worktrees"


def tmp_dir() -> Path:
    """Return the path to Gaia's own temporary-file root.

    Distinct from the OS-wide ``/tmp``: this directory is under
    ``data_dir()``, so it is swept by Gaia's own retention policy and
    relocates with ``GAIA_DATA_DIR`` like every other Gaia-owned directory.

    Returns:
        ``data_dir() / "tmp"``
    """
    return data_dir() / "tmp"


def rejected_turns_dir() -> Path:
    """Return the path where rejected-turn text is preserved.

    Holds the substantive prose of a turn the contract gate rejected (see
    ``hooks/modules/agents/rejected_turn_relay.py``), so it survives the
    retry regardless of what the agent's repair message does.

    Returns:
        ``data_dir() / "rejected_turns"``
    """
    return data_dir() / "rejected_turns"
