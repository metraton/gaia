"""
Tests for bin/cli/status.py -- gaia status subcommand.

Uses tmp_path fixtures to create a minimal .claude/ directory structure
so tests run without a real Gaia installation.
"""

import json
import sys
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

import cli.status as status_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def project_dir(tmp_path):
    """Create a minimal .claude/ directory structure for status checks."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # project-context.json
    pc_dir = claude_dir / "project-context"
    pc_dir.mkdir()
    (pc_dir / "project-context.json").write_text(json.dumps({
        "metadata": {"last_updated": "2026-04-15T10:00:00Z"},
        "sections": {"stack": {}, "git": {}, "infrastructure": {}},
    }))

    # episodic memory
    em_dir = pc_dir / "episodic-memory"
    em_dir.mkdir()
    (em_dir / "index.json").write_text(json.dumps({
        "episodes": [
            {"agent": "developer", "timestamp": "2026-04-15T09:00:00Z", "plan_status": "COMPLETE"},
            {"agent": "terraform-architect", "timestamp": "2026-04-15T09:30:00Z", "plan_status": "BLOCKED"},
        ],
    }))

    # pending updates
    pu_dir = pc_dir / "pending-updates"
    pu_dir.mkdir()
    (pu_dir / "pending-index.json").write_text(json.dumps({"pending_count": 3}))

    # signals
    sig_dir = pc_dir / "workflow-episodic-memory" / "signals"
    sig_dir.mkdir(parents=True)
    (sig_dir / "needs_analysis.flag").write_text("{}")

    return tmp_path


@pytest.fixture()
def empty_project(tmp_path):
    """Project with .claude/ but no data files."""
    (tmp_path / ".claude").mkdir()
    return tmp_path


@pytest.fixture()
def no_project(tmp_path):
    """Directory with no .claude/ at all."""
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: _collect_status
# ---------------------------------------------------------------------------

class TestCollectStatus:
    """Tests for the internal _collect_status function."""

    def test_full_data(self, project_dir):
        """All data sources present -- should read them all correctly."""
        status = status_mod._collect_status(project_dir)

        assert status["pending_count"] == 3
        assert status["anomaly_count"] == 1
        assert status["episode_count"] == 2
        assert status["agent_session_count"] == 2
        assert status["context_updated"] == "2026-04-15T10:00:00Z"

        last = status["last_agent"]
        assert last is not None
        assert last["agent"] == "terraform-architect"
        assert last["plan_status"] == "BLOCKED"

    def test_empty_project(self, empty_project):
        """No data files -- all counts should be zero/None."""
        status = status_mod._collect_status(empty_project)

        assert status["pending_count"] == 0
        assert status["anomaly_count"] == 0
        assert status["episode_count"] == 0
        assert status["agent_session_count"] == 0
        assert status["last_agent"] is None
        assert status["context_updated"] is None
        assert status["contract_stats"] is None


# ---------------------------------------------------------------------------
# Tests: cmd_status (human output)
# ---------------------------------------------------------------------------

class TestCmdStatusHuman:
    """Test human-readable output mode."""

    def test_prints_status(self, project_dir, monkeypatch, capsys):
        """Human output should contain key labels."""
        monkeypatch.chdir(project_dir)
        args = SimpleNamespace(json=False, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 0
        out = capsys.readouterr().out

        assert "Gaia System Status" in out
        assert "Last agent:" in out
        assert "terraform-architect" in out
        assert "Pending:" in out
        assert "3 context updates" in out
        assert "Anomalies:" in out
        assert "1 active signal" in out
        assert "Memory:" in out
        assert "2 episodes" in out

    def test_no_claude_dir(self, no_project, monkeypatch, capsys):
        """Should return 1 when .claude/ is missing."""
        monkeypatch.chdir(no_project)
        args = SimpleNamespace(json=False, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 1
        out = capsys.readouterr().out
        assert "not installed" in out

    def test_empty_project_no_crash(self, empty_project, monkeypatch, capsys):
        """Should not crash on an empty .claude/ directory."""
        monkeypatch.chdir(empty_project)
        args = SimpleNamespace(json=False, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Gaia System Status" in out


# ---------------------------------------------------------------------------
# Tests: cmd_status --json
# ---------------------------------------------------------------------------

class TestCmdStatusJson:
    """Test JSON output mode."""

    def test_json_output_is_valid(self, project_dir, monkeypatch, capsys):
        """--json flag should produce valid JSON with expected keys."""
        monkeypatch.chdir(project_dir)
        args = SimpleNamespace(json=True, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)

        assert "last_agent" in data
        assert "pending_count" in data
        assert "anomaly_count" in data
        assert "episode_count" in data
        assert "agent_session_count" in data
        assert "context_updated" in data
        assert data["pending_count"] == 3

    def test_json_no_claude_dir(self, no_project, monkeypatch, capsys):
        """--json with no .claude/ should output error JSON."""
        monkeypatch.chdir(no_project)
        args = SimpleNamespace(json=True, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert "error" in data

    def test_json_empty_project(self, empty_project, monkeypatch, capsys):
        """--json on empty project should produce valid JSON with zero counts."""
        monkeypatch.chdir(empty_project)
        args = SimpleNamespace(json=True, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["pending_count"] == 0
        assert data["anomaly_count"] == 0
        assert data["last_agent"] is None


# ---------------------------------------------------------------------------
# Tests: Memory v2 stats (T5)
# ---------------------------------------------------------------------------

class TestMemoryV2Stats:
    """Test enhanced memory line with indexed count and avg_score."""

    def test_status_json_includes_indexed(self, project_dir, monkeypatch, capsys):
        """JSON output must include 'indexed' key."""
        # Monkeypatch search_store.count() to return a predictable value
        import types

        fake_search_store = types.ModuleType("tools.memory.search_store")
        fake_search_store.count = lambda: 42

        fake_scoring = types.ModuleType("tools.memory.scoring")
        fake_scoring.score_memory = lambda days_old, retrieval_count, **kw: 0.5

        monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
        monkeypatch.setitem(sys.modules, "tools.memory", types.ModuleType("tools.memory"))
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_search_store)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)

        monkeypatch.chdir(project_dir)
        args = SimpleNamespace(json=True, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "indexed" in data
        assert data["indexed"] == 42

    def test_status_memory_line_shows_indexed(self, project_dir, monkeypatch, capsys):
        """Human output memory line must contain 'indexed'."""
        import types

        fake_search_store = types.ModuleType("tools.memory.search_store")
        fake_search_store.count = lambda: 7

        fake_scoring = types.ModuleType("tools.memory.scoring")
        fake_scoring.score_memory = lambda days_old, retrieval_count, **kw: 0.75

        monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
        monkeypatch.setitem(sys.modules, "tools.memory", types.ModuleType("tools.memory"))
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_search_store)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)

        monkeypatch.chdir(project_dir)
        args = SimpleNamespace(json=False, subcommand="status")
        rc = status_mod.cmd_status(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "indexed" in out

    def test_get_memory_v2_stats_import_failure(self, project_dir, monkeypatch):
        """When tools.memory is not importable, returns safe defaults."""
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", None)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", None)

        stats = status_mod._get_memory_v2_stats(project_dir)
        assert stats["indexed"] == 0
        assert stats["avg_score"] is None


# ---------------------------------------------------------------------------
# Tests: contract stats
# ---------------------------------------------------------------------------

class TestContractStats:
    """Test contract validation stats reading."""

    def test_with_contract_data(self, project_dir):
        """Should read contract stats from session directories."""
        contract_dir = project_dir / ".claude" / "session" / "active" / "response-contract"
        for i in range(3):
            sess_dir = contract_dir / f"session-{i}"
            sess_dir.mkdir(parents=True)
            valid = i < 2  # 2 valid, 1 invalid
            (sess_dir / "last-result.json").write_text(json.dumps({
                "validation": {"valid": valid},
            }))

        status = status_mod._collect_status(project_dir)
        cs = status["contract_stats"]
        assert cs is not None
        assert cs["valid"] == 2
        assert cs["total"] == 3

    def test_no_contract_dir(self, project_dir):
        """No session directory -- contract_stats should be None."""
        status = status_mod._collect_status(project_dir)
        assert status["contract_stats"] is None


# ---------------------------------------------------------------------------
# Tests: register
# ---------------------------------------------------------------------------

class TestRegister:
    """Test plugin registration."""

    def test_register_adds_subparser(self):
        """register() should add 'status' as a subcommand."""
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        status_mod.register(subs)

        # Parsing 'status' should work
        args = parser.parse_args(["status"])
        assert args.subcommand == "status"

    def test_register_json_flag(self):
        """register() should add --json flag."""
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        status_mod.register(subs)

        args = parser.parse_args(["status", "--json"])
        assert args.json is True
