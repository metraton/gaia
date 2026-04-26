#!/usr/bin/env python3
"""
FTS5-Backed Search Store for GAIA-OPS

Provides full-text search over episodic memory using SQLite FTS5 (default)
with an optional Chroma vector-search backend.

Zero external dependencies for core operation — stdlib only (sqlite3, shutil,
os, pathlib, typing, abc).  chromadb is imported lazily and only used if
available and not suppressed via GAIA_TEST_NO_CHROMA.

Architecture:
- SearchProvider ABC defines the interface
- FTS5Provider   wraps SQLite FTS5 (always available)
- ChromaProvider stub that activates only when chromadb is importable and
                 GAIA_TEST_NO_CHROMA is not set
- Module-level get_backend() / index_episode() / search() / count()
  delegate to the active provider (resolved once at import time)
- Lazy init: DB/table created on first FTS5 call
- Fail-safe: all public functions wrapped in try/except

Environment:
    GAIA_SEARCH_DB_PATH:    Override the default SQLite DB path.
    GAIA_TEST_NO_CHROMA:    Set to any non-empty value to force FTS5 backend.

Functions:
    index_episode   -- Insert or ignore an episode into the active backend
    search          -- Query backend, returns ranked results
    count           -- Count indexed episodes
    get_backend     -- Returns "chroma" or "fts5"
    has_engram      -- Returns True if engram binary is on PATH
"""

import abc
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# DB path resolution (used by FTS5Provider)
# ---------------------------------------------------------------------------

_DEFAULT_RELATIVE_PATH = ".claude/project-context/episodic-memory/search.db"


def _resolve_db_path() -> Path:
    """Resolve the search DB path.

    Priority:
        1. GAIA_SEARCH_DB_PATH environment variable
        2. Highest ancestor with a .claude/ directory (closest to HOME),
           so that a nested .claude/ in a sub-repository or dev checkout
           never shadows the real Gaia instance.
        3. Bare relative path fallback (last resort, same as before).
    """
    env_path = os.environ.get("GAIA_SEARCH_DB_PATH")
    if env_path:
        return Path(env_path)

    try:
        from memory.paths import find_highest_claude_root
        root = find_highest_claude_root()
        if root is not None:
            return root / _DEFAULT_RELATIVE_PATH
    except ImportError:
        pass

    # Fallback: original first-match walk (keeps behaviour if paths.py is
    # somehow unavailable, e.g. during isolated unit tests that only add
    # the tools/ root to sys.path after import).
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".claude").is_dir():
            return parent / _DEFAULT_RELATIVE_PATH

    return Path(_DEFAULT_RELATIVE_PATH)


# ---------------------------------------------------------------------------
# SearchProvider ABC
# ---------------------------------------------------------------------------

class SearchProvider(abc.ABC):
    """Abstract base for search backends."""

    @abc.abstractmethod
    def index(self, episode_id: str, text: str, **kwargs) -> None:
        """Insert or update an episode in the index."""

    @abc.abstractmethod
    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        """Return a ranked list of dicts with at least 'episode_id' key."""

    @abc.abstractmethod
    def count(self) -> int:
        """Return the total number of indexed episodes."""


# ---------------------------------------------------------------------------
# FTS5Provider
# ---------------------------------------------------------------------------

class FTS5Provider(SearchProvider):
    """SQLite FTS5 backend — always available, zero external dependencies."""

    def __init__(self) -> None:
        self._connection: Optional[sqlite3.Connection] = None

    # -- internal helpers ---------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection

        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                episode_id,
                prompt,
                enriched_prompt,
                tags,
                title
            )
            """
        )
        conn.commit()
        self._connection = conn
        return self._connection

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Append * wildcard to each word for FTS5 prefix matching.

        Uses prefix matching instead of exact quoted tokens so that
        "approval" matches "approvals", "approving", etc.
        Special characters that would break FTS5 syntax are stripped.

        Hyphens are replaced with spaces before tokenisation so that
        queries like "brief-spec" or "context-v5" are treated as two
        separate prefix terms ("brief*" "spec*") rather than a single
        phrase that FTS5 cannot match (FTS5 treats hyphens as token
        separators at index time, so the stored tokens never contain
        hyphens).
        """
        # Replace hyphens with spaces so "brief-spec" → "brief spec"
        query = query.replace("-", " ")
        words = query.split()
        # Strip characters that break FTS5 syntax, then append wildcard
        safe = [w.replace('"', '').replace("'", '').strip('*') for w in words if w]
        return " ".join(w + "*" for w in safe if w)

    # -- SearchProvider interface ------------------------------------------

    def index(self, episode_id: str, text: str, **kwargs) -> None:
        """Insert episode into FTS5 table (no-op if already present).

        Keyword arguments are mapped to FTS5 columns:
            enriched_prompt, tags, title
        The ``text`` positional argument maps to the ``prompt`` column.
        """
        enriched_prompt = kwargs.get("enriched_prompt", "")
        tags = kwargs.get("tags", "")
        title = kwargs.get("title", "")
        try:
            conn = self._get_connection()
            existing = conn.execute(
                "SELECT rowid FROM episodes_fts WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            if existing is not None:
                return
            conn.execute(
                "INSERT INTO episodes_fts"
                "(episode_id, prompt, enriched_prompt, tags, title) "
                "VALUES (?, ?, ?, ?, ?)",
                (episode_id, text, enriched_prompt, tags, title),
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        if not query or not query.strip():
            return []
        try:
            conn = self._get_connection()
            sanitized = self._sanitize_query(query.strip())
            rows = conn.execute(
                "SELECT episode_id, rank FROM episodes_fts "
                "WHERE episodes_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (sanitized, max_results),
            ).fetchall()
            return [{"episode_id": row[0], "rank": row[1]} for row in rows]
        except Exception:  # noqa: BLE001
            return []

    def count(self) -> int:
        """Return the row count in the FTS5 index.

        Returns
        -------
        int
            ``>= 0`` — the live row count when the DB is reachable.
            ``-1``   — sentinel indicating path resolution / connection
                       failure. Callers (e.g. ``gaia memory stats``) use
                       the sentinel to surface a visible warning so that
                       broken ``.claude/*`` symlinks do not masquerade as
                       an empty-but-healthy index.
        """
        try:
            conn = self._get_connection()
            row = conn.execute("SELECT COUNT(*) FROM episodes_fts").fetchone()
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001
            return -1


# ---------------------------------------------------------------------------
# ChromaProvider (stub)
# ---------------------------------------------------------------------------

class ChromaProvider(SearchProvider):
    """Chroma vector-search backend.

    Only instantiated when:
    - ``chromadb`` is importable, AND
    - ``GAIA_TEST_NO_CHROMA`` env var is NOT set

    Raises ``ImportError`` in __init__ if either condition is not met so
    that the factory can fall back to FTS5Provider transparently.
    """

    def __init__(self) -> None:
        if os.environ.get("GAIA_TEST_NO_CHROMA"):
            raise ImportError("GAIA_TEST_NO_CHROMA is set — Chroma disabled")
        import chromadb  # noqa: F401 -- intentional optional import
        # Future: initialise a persistent Chroma client and collection here.
        # For now this is a stub; raise to signal not-yet-implemented.
        raise NotImplementedError(
            "ChromaProvider is a stub — not yet fully implemented"
        )

    def index(self, episode_id: str, text: str, **kwargs) -> None:  # pragma: no cover
        raise NotImplementedError

    def search(self, query: str, max_results: int = 10) -> List[Dict]:  # pragma: no cover
        raise NotImplementedError

    def count(self) -> int:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Provider resolution (module-level singleton)
# ---------------------------------------------------------------------------

def _resolve_provider() -> SearchProvider:
    """Return the best available SearchProvider.

    Tries ChromaProvider first; falls back to FTS5Provider silently on any
    ImportError or NotImplementedError.
    """
    try:
        return ChromaProvider()
    except (ImportError, NotImplementedError):
        return FTS5Provider()


_provider: SearchProvider = _resolve_provider()


# ---------------------------------------------------------------------------
# Public API — delegates to active provider
# ---------------------------------------------------------------------------

def index_episode(
    episode_id: str,
    prompt: str,
    enriched_prompt: str = "",
    tags: str = "",
    title: str = "",
) -> None:
    """Insert an episode into the active backend (no-op if already present).

    Parameters
    ----------
    episode_id:
        Unique identifier for the episode.
    prompt:
        Original user prompt text.
    enriched_prompt:
        Expanded / enriched version of the prompt (may be empty).
    tags:
        Space- or comma-separated tags string (may be empty).
    title:
        Short title for the episode (may be empty).
    """
    _provider.index(
        episode_id,
        prompt,
        enriched_prompt=enriched_prompt,
        tags=tags,
        title=title,
    )


def search(query: str, max_results: int = 10) -> List[Dict]:
    """Search the active backend using the configured ranking strategy.

    Parameters
    ----------
    query:
        Free-text search query.
    max_results:
        Maximum number of results to return (default: 10).

    Returns
    -------
    list of dict
        Each dict contains at minimum ``{"episode_id": str}``. FTS5 backend
        also includes ``{"rank": float}``. Returns empty list on error.
    """
    return _provider.search(query, max_results)


def count() -> int:
    """Return the total number of episodes indexed in the active backend.

    Returns
    -------
    int
        Row count on success, or ``-1`` as a sentinel when the backend
        could not be reached (e.g. broken path resolution, connection
        failure).  Callers should treat ``-1`` as "unknown" rather than
        "empty" and surface a warning to the user.
    """
    return _provider.count()


def get_backend() -> str:
    """Return the active search backend identifier.

    Returns
    -------
    str
        ``"chroma"`` if ChromaProvider is active, ``"fts5"`` otherwise.
    """
    if isinstance(_provider, ChromaProvider):
        return "chroma"
    return "fts5"


def has_engram() -> bool:
    """Check whether the engram binary is available on PATH.

    Returns
    -------
    bool
        True if ``engram`` is found via shutil.which, False otherwise.
        Never raises an exception.
    """
    try:
        return shutil.which("engram") is not None
    except Exception:  # noqa: BLE001
        return False
