"""
gaia.paths.resolver -- Path resolution for Gaia storage substrate.

Resolves canonical paths for all Gaia state directories and files.
All functions read GAIA_DATA_DIR from the environment on each call
(no caching) so that tests using monkeypatch.setenv work correctly.
``db_path`` additionally honors GAIA_DB, which outranks GAIA_DATA_DIR for the
database file alone -- see that function for the full precedence ladder and the
reason the two variables must not be resolved independently of each other.

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
import sys
from pathlib import Path

# The two environment variables that can relocate the database, in precedence
# order (highest first). Named here so the resolver, its warning text, and the
# tests that pin the precedence all refer to one definition.
DB_PATH_ENV = "GAIA_DB"
DATA_DIR_ENV = "GAIA_DATA_DIR"

# (GAIA_DB, GAIA_DATA_DIR) pairs already warned about in this process. Keyed by
# the pair rather than a bare flag so a test that re-points the environment
# mid-process is warned again for each genuinely different conflict, while a
# long-lived process warns once per conflict instead of once per DB access.
_WARNED_CONFLICTS: set[tuple[str, str]] = set()


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

    Precedence, highest first (pinned by tests/paths/test_db_path_precedence.py):

    1. ``GAIA_DB``       -- FILE-scoped: names the database file itself.
    2. ``GAIA_DATA_DIR`` -- ROOT-scoped: the database is ``<root>/gaia.db``.
    3. ``~/.gaia/gaia.db``.

    ``GAIA_DB`` outranks ``GAIA_DATA_DIR`` because it is the more specific of the
    two, and because every other database-path resolver in the tree already
    ranks it that way -- ``scripts/bootstrap_database.py`` (where the variable
    originated), ``bin/cli/doctor.py``, ``bin/cli/_converge.py::default_db_path``,
    ``bin/gaia::_resolve_db_path``, ``bin/validate-sandbox.sh`` and the CI
    workflow, which sets ``GAIA_DB`` alone. This resolver was the sole holdout,
    and the disagreement was silent in the worst possible way: ``bin/gaia``
    bootstrapped a complete database at ``$GAIA_DB`` while every store read and
    write resolved to ``data_dir()/gaia.db``. A caller asking for isolation got a
    fully schema'd decoy database AND its writes in the real user database, with
    a success message and no warning -- and the decoy is what made it
    undetectable, since inspecting it afterwards shows a populated schema and
    suggests the isolation worked.

    ``GAIA_DB`` stays FILE-scoped on purpose: it relocates only the database,
    never the sibling directories (scratch, evidence, logs, worktrees), which
    keep resolving under ``data_dir()``. Relocating the whole substrate is
    ``GAIA_DATA_DIR``'s job, and collapsing the two would silently move state
    that callers setting ``GAIA_DB`` alone (CI, the sandbox validator) expect to
    stay where it is.

    Returns:
        Absolute Path to the database file.
    """
    override = os.environ.get(DB_PATH_ENV, "")
    if not override:
        return data_dir() / "gaia.db"

    resolved = Path(override).expanduser().resolve()
    _warn_on_conflicting_db_env(resolved)
    return resolved


def _warn_on_conflicting_db_env(resolved_db: Path) -> None:
    """Warn once when GAIA_DB and GAIA_DATA_DIR disagree about the database.

    Setting both is the tree's own established idiom for isolating a sandbox
    (see ``tests/ci/windows_smoke.py``, which points the store at a data dir and
    the bootstrapper at the same file), so the ordinary case -- ``GAIA_DB``
    inside ``GAIA_DATA_DIR`` -- is silent. The case worth a warning is the one
    where they name different places: whichever the caller believed governs the
    database, one of the two requests is not being honored, and staying quiet
    about that is the very defect this precedence was introduced to remove. A
    warning rather than an exception because this resolver is on the path of
    every read and write, where raising would convert a misconfigured
    environment into total failure.
    """
    data_override = os.environ.get(DATA_DIR_ENV, "")
    if not data_override:
        return

    expected = Path(data_override).expanduser().resolve() / "gaia.db"
    if expected == resolved_db:
        return

    key = (str(resolved_db), str(expected))
    if key in _WARNED_CONFLICTS:
        return
    _WARNED_CONFLICTS.add(key)

    print(
        f"gaia: warning: {DB_PATH_ENV} and {DATA_DIR_ENV} name different "
        f"databases. {DB_PATH_ENV} wins (it is file-scoped): using "
        f"{resolved_db}, NOT {expected}. Point both at the same file, or unset "
        f"one, to remove the ambiguity.",
        file=sys.stderr,
    )


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
