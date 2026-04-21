#!/usr/bin/env python3
"""
Tests for FTS5 Search Store.

PRIORITY: HIGH - Core episodic memory search infrastructure.

Validates:
1. FTS5 virtual table creation (stdlib sqlite3 — zero deps)
2. Index + search round-trip
3. Episode count accuracy
4. BM25 ranking order for repeated terms
5. Backend selection via GAIA_TEST_NO_CHROMA env var
6. has_engram() binary check
7. Idempotent indexing (same episode_id indexed twice stays count=1)
"""

import sys
import importlib
import os
import sqlite3
import pytest
from pathlib import Path

# Add tools to path — same pattern as test_episodic.py
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import memory.search_store as search_store_module
from memory.search_store import FTS5Provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(tmp_path: Path) -> FTS5Provider:
    """Return a fresh FTS5Provider pointed at a temp DB path.

    Sets GAIA_SEARCH_DB_PATH so that _resolve_db_path() picks up the
    temp location. Returns the provider *before* any connection is opened
    (lazy init fires on first use).
    """
    os.environ["GAIA_SEARCH_DB_PATH"] = str(tmp_path / "test_search.db")
    return FTS5Provider()


# ---------------------------------------------------------------------------
# Test: FTS5 availability
# ---------------------------------------------------------------------------

class TestFTS5Available:
    """Verify that the stdlib sqlite3 build on this host supports FTS5."""

    def test_fts5_available(self, tmp_path):
        """FTS5 virtual table can be created with stdlib sqlite3."""
        db_path = tmp_path / "fts5_check.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE test_fts USING fts5(content)"
            )
            conn.commit()
            # If we reach here without an OperationalError, FTS5 is enabled.
        except sqlite3.OperationalError as exc:
            pytest.fail(f"FTS5 not available in stdlib sqlite3: {exc}")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Test: Index and search
# ---------------------------------------------------------------------------

class TestIndexAndSearch:
    """Basic index → search round-trip."""

    def test_index_and_search(self, tmp_path, monkeypatch):
        """Index 3 episodes; search for keyword present in only one."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_001", "deploy terraform infrastructure to production")
        provider.index("ep_002", "fix broken kubectl pods in the cluster")
        provider.index("ep_003", "write a python script for data processing")

        results = provider.search("kubectl")

        assert len(results) == 1, f"Expected 1 result, got {len(results)}: {results}"
        assert results[0]["episode_id"] == "ep_002"

    def test_search_empty_query_returns_empty(self, tmp_path, monkeypatch):
        """Empty query returns empty list without raising."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()
        provider.index("ep_001", "some content here")

        results = provider.search("")
        assert results == []

    def test_search_no_match_returns_empty(self, tmp_path, monkeypatch):
        """Query that matches nothing returns empty list."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()
        provider.index("ep_001", "deploy terraform infrastructure")

        results = provider.search("xyznonexistenttoken")
        assert results == []


# ---------------------------------------------------------------------------
# Test: count()
# ---------------------------------------------------------------------------

class TestCount:
    """Verify count() reflects the number of indexed episodes."""

    def test_count_matches_expected(self, tmp_path, monkeypatch):
        """After indexing 3 episodes, count() returns 3."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        assert provider.count() == 0

        provider.index("ep_001", "first episode content")
        provider.index("ep_002", "second episode content")
        provider.index("ep_003", "third episode content")

        assert provider.count() == 3

    def test_count_empty_store(self, tmp_path, monkeypatch):
        """Fresh provider returns count of 0."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()
        assert provider.count() == 0


# ---------------------------------------------------------------------------
# Test: search ranking
# ---------------------------------------------------------------------------

class TestSearchRanking:
    """Verify BM25 ranking — more occurrences ranks higher (lower rank value)."""

    def test_search_ranking(self, tmp_path, monkeypatch):
        """Episode with 'terraform' 3× ranks higher than episode with it 1×.

        FTS5 uses BM25 scoring where rank values are negative — more relevant
        results have a more negative (smaller) rank value. ORDER BY rank ASC
        means the most-relevant result is first in the list.
        """
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        # ep_high: "terraform" appears 3 times
        provider.index(
            "ep_high",
            "terraform plan terraform apply terraform destroy infrastructure",
        )
        # ep_low: "terraform" appears once
        provider.index(
            "ep_low",
            "run terraform to provision resources",
        )

        results = provider.search("terraform")

        assert len(results) == 2, f"Expected 2 results, got {results}"
        # First result should be the one with more mentions
        assert results[0]["episode_id"] == "ep_high", (
            f"Expected ep_high first (more mentions), got {results[0]['episode_id']}"
        )


# ---------------------------------------------------------------------------
# Test: get_backend() with GAIA_TEST_NO_CHROMA
# ---------------------------------------------------------------------------

class TestGetBackendFTS5:
    """Verify get_backend() returns 'fts5' when GAIA_TEST_NO_CHROMA is set."""

    def test_get_backend_fts5(self, monkeypatch):
        """With GAIA_TEST_NO_CHROMA=1, get_backend() returns 'fts5'."""
        monkeypatch.setenv("GAIA_TEST_NO_CHROMA", "1")

        # Re-resolve the provider for the assertion via the module's own logic.
        # We test the resolution function directly to avoid mutating the
        # module-level singleton (which was already resolved at import time).
        from memory.search_store import _resolve_provider, ChromaProvider
        provider = _resolve_provider()
        assert not isinstance(provider, ChromaProvider), (
            "Expected FTS5Provider when GAIA_TEST_NO_CHROMA is set"
        )
        # Also verify via the type-check that would be used by get_backend()
        assert not isinstance(provider, ChromaProvider)

    def test_get_backend_returns_string(self, monkeypatch):
        """get_backend() always returns a non-empty string."""
        result = search_store_module.get_backend()
        assert isinstance(result, str)
        assert result in ("fts5", "chroma")


# ---------------------------------------------------------------------------
# Test: has_engram()
# ---------------------------------------------------------------------------

class TestHasEngram:
    """Verify has_engram() behaves correctly."""

    def test_has_engram_returns_bool(self):
        """has_engram() returns a bool and does not raise."""
        from memory.search_store import has_engram
        result = has_engram()
        assert isinstance(result, bool), f"Expected bool, got {type(result)}"

    def test_has_engram_false_when_not_on_path(self, monkeypatch):
        """has_engram() returns False when PATH contains no 'engram' binary."""
        monkeypatch.setenv("PATH", "/nonexistent_path_for_test")
        from memory.search_store import has_engram
        result = has_engram()
        assert result is False


# ---------------------------------------------------------------------------
# Test: hyphen query preprocessing
# ---------------------------------------------------------------------------

class TestHyphenQueryPreprocessing:
    """Verify that hyphenated queries find content stored as space-separated tokens."""

    def test_hyphen_query_matches_spaced_content(self, tmp_path, monkeypatch):
        """search('brief-spec') must find an episode whose text contains 'brief spec'.

        FTS5 tokenises stored text on hyphens, so the index never stores
        'brief-spec' as a single token.  The fix pre-processes the query by
        replacing hyphens with spaces before passing it to FTS5 MATCH.
        """
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_brief", "working on brief spec for the new feature")
        provider.index("ep_other", "unrelated kubernetes deployment work")

        results = provider.search("brief-spec")

        assert len(results) == 1, (
            f"Expected 1 result for 'brief-spec', got {len(results)}: {results}"
        )
        assert results[0]["episode_id"] == "ep_brief"

    def test_plain_query_still_works_after_hyphen_fix(self, tmp_path, monkeypatch):
        """Non-hyphenated queries must continue to return correct results (no regression)."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_deploy", "deploy terraform infrastructure to production")
        provider.index("ep_memory", "gaia memory episodic search store fix")

        results = provider.search("terraform")

        assert len(results) == 1
        assert results[0]["episode_id"] == "ep_deploy"

    def test_multi_word_hyphen_query(self, tmp_path, monkeypatch):
        """search('context-v5') (multi-part concept) returns the relevant episode."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_ctx", "migrated context v5 to sqlite backend")
        provider.index("ep_other", "fixed approval workflow state machine")

        results = provider.search("context-v5")

        assert len(results) >= 1, (
            f"Expected at least 1 result for 'context-v5', got {len(results)}: {results}"
        )
        assert results[0]["episode_id"] == "ep_ctx"


# ---------------------------------------------------------------------------
# Test: idempotent indexing
# ---------------------------------------------------------------------------

class TestIdempotentIndex:
    """Indexing the same episode_id twice must not create duplicate rows."""

    def test_idempotent_index(self, tmp_path, monkeypatch):
        """Index same episode_id twice; count() must be 1, not 2."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_dup", "original content for the duplicate test")
        provider.index("ep_dup", "original content for the duplicate test")

        assert provider.count() == 1, (
            f"Expected count=1 after two identical index calls, got {provider.count()}"
        )

    def test_idempotent_index_with_different_text(self, tmp_path, monkeypatch):
        """Re-indexing same episode_id with different text is still a no-op (insert-or-ignore)."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_dup", "first version of content")
        provider.index("ep_dup", "completely different second version")

        assert provider.count() == 1, (
            f"Expected count=1 after re-index with different text, got {provider.count()}"
        )

    def test_multiple_different_episodes_not_deduplicated(self, tmp_path, monkeypatch):
        """Different episode_ids are each counted once."""
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", str(tmp_path / "test_search.db"))
        provider = FTS5Provider()

        provider.index("ep_aaa", "content for episode aaa")
        provider.index("ep_bbb", "content for episode bbb")
        provider.index("ep_aaa", "duplicate of ep_aaa should be ignored")

        assert provider.count() == 2, (
            f"Expected count=2 for 2 distinct episode_ids, got {provider.count()}"
        )


# ---------------------------------------------------------------------------
# Test: _resolve_db_path picks highest .claude/ (instance-root fix)
# ---------------------------------------------------------------------------

class TestResolveDbPathHighestRoot:
    """Verify _resolve_db_path() returns the highest Gaia .claude/ in the hierarchy.

    Tree: tmp_path/.claude/hooks/  (Gaia instance root — has hooks/ marker)
          tmp_path/sub/.claude/    (nested plain .claude/, no Gaia markers)

    When cwd is tmp_path/sub/, the resolved DB path must point to
    tmp_path/.claude/ — NOT to tmp_path/sub/.claude/.
    """

    def _make_tree(self, tmp_path: Path):
        """Create a two-level .claude/ tree rooted at tmp_path.

        The instance root has a hooks/ marker; the nested sub/ dir has a
        bare .claude/ without Gaia markers (simulating an accidental nested
        .claude/ that should not shadow the real instance).
        """
        # Instance root: .claude/ with hooks/ marker (Gaia-qualified)
        (tmp_path / ".claude" / "hooks").mkdir(parents=True)
        # Nested sub-directory: bare .claude/, no Gaia markers
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".claude").mkdir()
        return tmp_path, sub

    def test_resolve_db_path_uses_highest_root(self, tmp_path, monkeypatch):
        """From a nested cwd, _resolve_db_path must point to the Gaia instance root."""
        from memory.search_store import _resolve_db_path

        instance_root, sub_dir = self._make_tree(tmp_path)

        # Override HOME so that find_highest_claude_root stops at tmp_path
        monkeypatch.setenv("HOME", str(instance_root))
        monkeypatch.delenv("GAIA_SEARCH_DB_PATH", raising=False)

        # Patch Path.home() to return our controlled root
        import pathlib
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: instance_root))

        monkeypatch.chdir(sub_dir)

        db_path = _resolve_db_path()

        assert str(db_path).startswith(str(instance_root)), (
            f"Expected db_path under instance root {instance_root}, got {db_path}"
        )
        assert str(sub_dir) not in str(db_path), (
            f"db_path must NOT be under nested sub dir {sub_dir}, got {db_path}"
        )

    def test_resolve_db_path_env_var_overrides_walk(self, tmp_path, monkeypatch):
        """GAIA_SEARCH_DB_PATH env var must bypass the walk entirely."""
        from memory.search_store import _resolve_db_path

        custom = str(tmp_path / "custom" / "search.db")
        monkeypatch.setenv("GAIA_SEARCH_DB_PATH", custom)

        db_path = _resolve_db_path()
        assert str(db_path) == custom
