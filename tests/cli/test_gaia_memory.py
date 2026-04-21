"""
Tests for bin/cli/memory.py -- gaia memory subcommand.

Uses tmp_path fixtures with monkeypatched memory modules so tests
run without a real FTS5 index or episodic-memory on disk.
"""

import argparse
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Path setup -- ensure bin/ is importable
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"

if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import cli.memory as memory_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_modules(monkeypatch, *, search_results=None, episode=None,
                       count=0, conflicts=None, score=0.5):
    """Inject fake tools.memory.* modules via monkeypatch."""

    # Ensure parent namespace modules exist in sys.modules and create fresh
    # ones so that attribute lookups (from tools.memory import X) always
    # resolve to our fakes even when the real module was loaded by a
    # previous test file in the same process.
    tools_mod = types.ModuleType("tools")
    tools_memory_mod = types.ModuleType("tools.memory")
    monkeypatch.setitem(sys.modules, "tools", tools_mod)
    monkeypatch.setitem(sys.modules, "tools.memory", tools_memory_mod)

    # --- search_store ---
    fake_ss = types.ModuleType("tools.memory.search_store")
    _sr = search_results if search_results is not None else []
    fake_ss.search = lambda query, max_results=10: _sr
    fake_ss.count = lambda: count
    monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)
    # Also set as attribute so `from tools.memory import search_store` works
    tools_memory_mod.search_store = fake_ss

    # --- scoring ---
    fake_scoring = types.ModuleType("tools.memory.scoring")
    fake_scoring.score_memory = lambda days_old, retrieval_count, **kw: score
    monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)
    tools_memory_mod.scoring = fake_scoring

    # --- episodic ---
    fake_episodic = types.ModuleType("tools.memory.episodic")
    _ep = episode

    class FakeEpisodicMemory:
        def get_episode(self, episode_id):
            return _ep

    fake_episodic.EpisodicMemory = FakeEpisodicMemory
    monkeypatch.setitem(sys.modules, "tools.memory.episodic", fake_episodic)
    tools_memory_mod.episodic = fake_episodic

    # --- conflict_detector ---
    fake_cd = types.ModuleType("tools.memory.conflict_detector")
    _conflicts = conflicts if conflicts is not None else []
    fake_cd.detect_conflicts = lambda threshold=0.3, memory_dir=None: _conflicts
    monkeypatch.setitem(sys.modules, "tools.memory.conflict_detector", fake_cd)
    tools_memory_mod.conflict_detector = fake_cd

    return fake_ss, fake_scoring, fake_episodic, fake_cd


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def memory_project(tmp_path):
    """Minimal .claude/project-context/episodic-memory/ for disk-path tests."""
    em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
    em_dir.mkdir(parents=True)

    episodes = [
        {
            "episode_id": "ep_001",
            "title": "Episode One",
            "timestamp": "2026-04-01T10:00:00Z",
            "retrieval_count": 2,
            "tags": ["gaia"],
            "enriched_prompt": "Content of episode one.",
        },
        {
            "episode_id": "ep_002",
            "title": "Episode Two",
            "timestamp": "2026-04-05T12:00:00Z",
            "retrieval_count": 0,
            "tags": [],
            "enriched_prompt": "Content of episode two.",
        },
    ]

    index = {"episodes": episodes}
    (em_dir / "index.json").write_text(json.dumps(index))

    # Minimal search.db stub (empty file is enough for path-existence tests)
    (em_dir / "search.db").write_bytes(b"")

    return tmp_path


# ---------------------------------------------------------------------------
# Tests: register
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: _find_project_root — instance-root resolution
# ---------------------------------------------------------------------------

class TestFindProjectRoot:
    """_find_project_root() must return the highest .claude/ ancestor (closest to HOME).

    Tree used:
        tmp_path/              <- instance root (.claude/ here)
        tmp_path/sub/          <- subdirectory
        tmp_path/sub/.claude/  <- nested .claude/ (should be ignored)

    When cwd is tmp_path/sub/, the function must return tmp_path, not
    tmp_path/sub/.
    """

    def test_highest_claude_root_from_nested_cwd(self, tmp_path, monkeypatch):
        """From a nested cwd with its own plain .claude/, return the Gaia root higher up.

        Tree:
          instance_root/.claude/hooks/  <- Gaia instance (has hooks/ marker)
          instance_root/sub/.claude/    <- nested plain .claude/, no markers
        """
        import pathlib

        # Build the two-level tree
        instance_root = tmp_path
        (instance_root / ".claude" / "hooks").mkdir(parents=True)
        sub_dir = instance_root / "sub"
        sub_dir.mkdir()
        (sub_dir / ".claude").mkdir()

        # Pin HOME to the instance root so the walk stops there
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: instance_root))
        monkeypatch.delenv("INIT_CWD", raising=False)
        monkeypatch.chdir(sub_dir)

        root = memory_mod._find_project_root()

        assert root == instance_root, (
            f"Expected instance root {instance_root}, got {root}"
        )

    def test_single_claude_dir_at_cwd(self, tmp_path, monkeypatch):
        """When only one .claude/ exists (at cwd, with Gaia markers), return cwd."""
        import pathlib

        (tmp_path / ".claude" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.delenv("INIT_CWD", raising=False)
        monkeypatch.chdir(tmp_path)

        root = memory_mod._find_project_root()

        assert root == tmp_path

    def test_no_claude_dir_falls_back_to_init_cwd(self, tmp_path, monkeypatch):
        """When no .claude/ found, INIT_CWD with .claude/ is honoured."""
        import pathlib

        home_dir = tmp_path / "home"
        home_dir.mkdir()
        init_cwd_dir = tmp_path / "init"
        init_cwd_dir.mkdir()
        (init_cwd_dir / ".claude").mkdir()

        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: home_dir))
        monkeypatch.setenv("INIT_CWD", str(init_cwd_dir))
        monkeypatch.chdir(home_dir)

        root = memory_mod._find_project_root()

        assert root == init_cwd_dir


class TestRegisterMemorySubcommand:
    """Plugin registration."""

    def test_register_adds_memory_to_choices(self):
        """register() must add 'memory' as a top-level subcommand."""
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        memory_mod.register(subs)

        choices = subs.choices
        assert "memory" in choices

    def test_register_nested_actions_present(self):
        """All 4 nested actions (search, stats, show, conflicts) must be registered."""
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        memory_mod.register(subs)

        mem_parser = subs.choices["memory"]
        # Find the nested subparsers action
        nested_subs = None
        for action in mem_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                nested_subs = action
                break

        assert nested_subs is not None
        for name in ("search", "stats", "show", "conflicts"):
            assert name in nested_subs.choices, f"Missing nested action: {name}"


# ---------------------------------------------------------------------------
# Tests: search
# ---------------------------------------------------------------------------

class TestCmdSearch:
    """_cmd_search via cmd_memory dispatch."""

    def test_search_returns_results_json(self, monkeypatch, capsys):
        """Two hits enriched with episodic data → valid JSON with id/title/score/date/snippet."""
        _make_fake_modules(
            monkeypatch,
            search_results=[
                {"episode_id": "ep_001", "rank": 0.9},
                {"episode_id": "ep_002", "rank": 0.7},
            ],
            episode={
                "episode_id": "ep_001",
                "title": "My Episode",
                "timestamp": "2026-04-10T10:00:00Z",
                "retrieval_count": 1,
                "tags": ["gaia"],
                "enriched_prompt": "Some content here",
            },
            score=0.42,
        )

        args = SimpleNamespace(
            json=True,
            query="my query",
            limit=10,
            func=memory_mod._cmd_search,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "results" in data
        assert len(data["results"]) == 2

        first = data["results"][0]
        assert "id" in first
        assert "title" in first
        assert "score" in first
        assert "date" in first
        assert "snippet" in first
        assert first["id"] == "ep_001"
        assert first["title"] == "My Episode"

    def test_search_empty_results_ok(self, monkeypatch, capsys):
        """Empty FTS5 results → {"results": []} without error."""
        _make_fake_modules(monkeypatch, search_results=[])

        args = SimpleNamespace(
            json=True,
            query="nothing",
            limit=10,
            func=memory_mod._cmd_search,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"results": []}

    def test_search_human_output_no_crash(self, monkeypatch, capsys):
        """Human output (no --json) should print without crashing on empty results."""
        _make_fake_modules(monkeypatch, search_results=[])

        args = SimpleNamespace(
            json=False,
            query="test",
            limit=5,
            func=memory_mod._cmd_search,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "No results found" in out

    def test_search_without_search_store_returns_error(self, monkeypatch, capsys):
        """When search_store is None (ImportError), returns exit 1 + error JSON."""
        # Patch the import function directly so that the real module (which may
        # already be cached in sys.modules or as an attribute of tools.memory)
        # does not leak through when tests run after test_search_store.py.
        monkeypatch.setattr(memory_mod, "_import_search_store", lambda: None)

        args = SimpleNamespace(
            json=True,
            query="test",
            limit=10,
            func=memory_mod._cmd_search,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert "error" in data


# ---------------------------------------------------------------------------
# Tests: stats
# ---------------------------------------------------------------------------

class TestCmdStats:
    """_cmd_stats via cmd_memory dispatch."""

    def test_stats_returns_four_keys(self, memory_project, monkeypatch, capsys):
        """Stats output must contain total_episodes, indexed, avg_score, conflicts."""
        _make_fake_modules(monkeypatch, count=2, conflicts=[], score=0.4)

        monkeypatch.chdir(memory_project)

        args = SimpleNamespace(
            json=True,
            func=memory_mod._cmd_stats,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "total_episodes" in data
        assert "indexed" in data
        assert "avg_score" in data
        assert "conflicts" in data

    def test_stats_episode_count_from_index(self, memory_project, monkeypatch, capsys):
        """total_episodes must reflect the episode count in index.json."""
        _make_fake_modules(monkeypatch, count=2, score=0.5)

        monkeypatch.chdir(memory_project)

        args = SimpleNamespace(json=True, func=memory_mod._cmd_stats)
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total_episodes"] == 2

    def test_stats_indexed_from_search_store(self, memory_project, monkeypatch, capsys):
        """indexed must reflect search_store.count() return value."""
        _make_fake_modules(monkeypatch, count=7, score=0.5)

        monkeypatch.chdir(memory_project)

        args = SimpleNamespace(json=True, func=memory_mod._cmd_stats)
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["indexed"] == 7

    def test_stats_human_output_no_crash(self, memory_project, monkeypatch, capsys):
        """Human mode should print stats table without crashing."""
        _make_fake_modules(monkeypatch, count=2, score=0.3)

        monkeypatch.chdir(memory_project)

        args = SimpleNamespace(json=False, func=memory_mod._cmd_stats)
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Memory Stats" in out


# ---------------------------------------------------------------------------
# Tests: show
# ---------------------------------------------------------------------------

class TestCmdShow:
    """_cmd_show via cmd_memory dispatch."""

    def test_show_found_returns_all_keys(self, monkeypatch, capsys):
        """Existing episode_id → JSON with id/title/content/score/tags/retrieval_count/age_days."""
        _make_fake_modules(
            monkeypatch,
            episode={
                "episode_id": "ep_001",
                "title": "Test Episode",
                "timestamp": "2026-04-10T08:00:00Z",
                "retrieval_count": 3,
                "tags": ["infra", "dev"],
                "enriched_prompt": "Full content here.",
            },
            score=0.55,
        )

        args = SimpleNamespace(
            json=True,
            episode_id="ep_001",
            func=memory_mod._cmd_show,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "ep_001"
        assert data["title"] == "Test Episode"
        assert data["content"] == "Full content here."
        assert isinstance(data["score"], float)
        assert isinstance(data["tags"], list)
        assert isinstance(data["retrieval_count"], int)
        assert isinstance(data["age_days"], float)

    def test_show_not_found_returns_exit_1(self, monkeypatch, capsys):
        """Missing episode_id → exit code 1 + error JSON."""
        _make_fake_modules(monkeypatch, episode=None)

        args = SimpleNamespace(
            json=True,
            episode_id="ep_missing",
            func=memory_mod._cmd_show,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert "error" in data
        assert "ep_missing" in data["error"]

    def test_show_without_episodic_module_returns_error(self, monkeypatch, capsys):
        """When episodic module is unavailable, exit 1 with error."""
        monkeypatch.setitem(sys.modules, "tools.memory.episodic", None)

        args = SimpleNamespace(
            json=True,
            episode_id="ep_001",
            func=memory_mod._cmd_show,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert "error" in data

    def test_show_human_output_has_content(self, monkeypatch, capsys):
        """Human mode should display episode fields."""
        _make_fake_modules(
            monkeypatch,
            episode={
                "episode_id": "ep_001",
                "title": "Human Title",
                "timestamp": "2026-04-12T00:00:00Z",
                "retrieval_count": 0,
                "tags": [],
                "enriched_prompt": "Some content.",
            },
            score=0.3,
        )

        args = SimpleNamespace(json=False, episode_id="ep_001", func=memory_mod._cmd_show)
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "ep_001" in out
        assert "Human Title" in out


# ---------------------------------------------------------------------------
# Tests: conflicts
# ---------------------------------------------------------------------------

class TestCmdConflicts:
    """_cmd_conflicts via cmd_memory dispatch."""

    def test_conflicts_no_conflicts_returns_empty_list(self, monkeypatch, capsys):
        """No conflicts detected → {"conflicts": []}."""
        _make_fake_modules(monkeypatch, conflicts=[])

        args = SimpleNamespace(
            json=True,
            threshold=0.3,
            func=memory_mod._cmd_conflicts,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"conflicts": []}

    def test_conflicts_with_conflicts_normalizes_similarity_to_score(self, monkeypatch, capsys):
        """Conflicts list must expose 'score' (not 'similarity') in each entry."""
        raw_conflicts = [
            {
                "file_a": "/path/to/file_a.md",
                "file_b": "/path/to/file_b.md",
                "similarity": 0.75,
                "conflicts": [{"reason": "overlapping concepts"}],
            }
        ]
        _make_fake_modules(monkeypatch, conflicts=raw_conflicts)

        args = SimpleNamespace(
            json=True,
            threshold=0.3,
            func=memory_mod._cmd_conflicts,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "conflicts" in data
        assert len(data["conflicts"]) == 1

        entry = data["conflicts"][0]
        assert "score" in entry, "similarity must be normalized to 'score'"
        assert "similarity" not in entry, "'similarity' key must not appear in output"
        assert entry["score"] == 0.75
        assert entry["file_a"] == "/path/to/file_a.md"
        assert entry["file_b"] == "/path/to/file_b.md"
        assert "reason" in entry

    def test_conflicts_structure_has_required_keys(self, monkeypatch, capsys):
        """Each conflict entry must have file_a, file_b, score, reason."""
        raw_conflicts = [
            {
                "file_a": "/a.md",
                "file_b": "/b.md",
                "similarity": 0.5,
                "conflicts": [{"reason": "duplicate info"}],
            }
        ]
        _make_fake_modules(monkeypatch, conflicts=raw_conflicts)

        args = SimpleNamespace(json=True, threshold=0.3, func=memory_mod._cmd_conflicts)
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        entry = data["conflicts"][0]
        for key in ("file_a", "file_b", "score", "reason"):
            assert key in entry, f"Missing key: {key}"

    def test_conflicts_without_detector_module_returns_error(self, monkeypatch, capsys):
        """When conflict_detector is unavailable, exit 1 with error."""
        monkeypatch.setitem(sys.modules, "tools.memory.conflict_detector", None)

        args = SimpleNamespace(
            json=True,
            threshold=0.3,
            func=memory_mod._cmd_conflicts,
        )
        rc = memory_mod.cmd_memory(args)

        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert "error" in data

    def test_conflicts_human_output_no_crash(self, monkeypatch, capsys):
        """Human mode with no conflicts should print a message."""
        _make_fake_modules(monkeypatch, conflicts=[])

        args = SimpleNamespace(json=False, threshold=0.3, func=memory_mod._cmd_conflicts)
        rc = memory_mod.cmd_memory(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "No conflicts" in out


# ---------------------------------------------------------------------------
# Tests: json flag propagation
# ---------------------------------------------------------------------------

class TestJsonFlag:
    """Verify --json flag produces machine-readable output for all subcommands."""

    def test_search_json_is_parseable(self, monkeypatch, capsys):
        _make_fake_modules(monkeypatch, search_results=[])
        args = SimpleNamespace(json=True, query="x", limit=5, func=memory_mod._cmd_search)
        memory_mod.cmd_memory(args)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, dict)

    def test_stats_json_is_parseable(self, tmp_path, monkeypatch, capsys):
        em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
        em_dir.mkdir(parents=True)
        (em_dir / "index.json").write_text(json.dumps({"episodes": []}))
        _make_fake_modules(monkeypatch, count=0, score=0.0)
        monkeypatch.chdir(tmp_path)
        args = SimpleNamespace(json=True, func=memory_mod._cmd_stats)
        memory_mod.cmd_memory(args)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, dict)

    def test_conflicts_json_is_parseable(self, monkeypatch, capsys):
        _make_fake_modules(monkeypatch, conflicts=[])
        args = SimpleNamespace(json=True, threshold=0.3, func=memory_mod._cmd_conflicts)
        memory_mod.cmd_memory(args)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, dict)
