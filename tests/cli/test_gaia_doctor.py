"""
Tests for bin/cli/doctor.py -- gaia doctor subcommand.

Uses tmp_path fixtures to create controlled .claude/ directory structures
so each health check can be tested in isolation.
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

import cli.doctor as doctor_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_home_globals(tmp_path, monkeypatch):
    """Isolate doctor checks that read from $HOME.

    Pass 4 added two checks that touch ~/.gaia/: check_schema_version reads
    ~/.gaia/gaia.db and check_last_install_error reads
    ~/.gaia/last-install-error.json. Without this fixture, test runs on a
    real user machine would see the actual install state and flake.

    The fixture redirects both globals at the module level. Tests that need
    to assert *specific* states (e.g. marker present) override these via
    their own monkeypatching.
    """
    fake_marker = tmp_path / "isolated-last-install-error.json"
    fake_db = tmp_path / "isolated-gaia.db"
    monkeypatch.setattr(doctor_mod, "_INSTALL_ERROR_MARKER", fake_marker)
    monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", fake_db)
    # GAIA_DB env var is the higher-priority override read by
    # check_schema_version -- clear it so tests start from a clean slate.
    monkeypatch.delenv("GAIA_DB", raising=False)
    yield

@pytest.fixture()
def healthy_project(tmp_path):
    """Create a fully healthy .claude/ project for doctor checks."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # plugin-registry.json
    (claude_dir / "plugin-registry.json").write_text(json.dumps({
        "installed": [{"name": "gaia-ops"}],
        "source": "local-dev",
    }))

    # Symlink targets (real directories, not symlinks -- tests just need exists())
    for name in ["agents", "tools", "hooks", "commands", "templates", "config", "skills"]:
        (claude_dir / name).mkdir()
    (claude_dir / "CHANGELOG.md").write_text("# Changelog")

    # Agent definition
    agents_dir = claude_dir / "agents"
    (agents_dir / "gaia-orchestrator.md").write_text("---\nagent: gaia-orchestrator\n---")

    # settings.local.json
    (claude_dir / "settings.local.json").write_text(json.dumps({
        "agent": "gaia-orchestrator",
        "hooks": {
            "PreToolUse": [{"command": "python"}],
            "PostToolUse": [{"command": "python"}],
            "UserPromptSubmit": [{"command": "python"}],
            "SessionStart": [{"command": "python"}],
        },
        "permissions": {
            "allow": ["Bash(*)"],
            "deny": ["rm -rf /"],
        },
        "env": {
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "true",
        },
    }))

    # Hook files
    hooks_dir = claude_dir / "hooks"
    for h in ["pre_tool_use.py", "post_tool_use.py", "user_prompt_submit.py",
              "session_start.py", "subagent_stop.py", "subagent_start.py",
              "stop_hook.py", "task_completed.py", "post_compact.py",
              "elicitation_result.py"]:
        (hooks_dir / h).write_text("# hook stub")

    # project-context.json
    pc_dir = claude_dir / "project-context"
    pc_dir.mkdir()
    (pc_dir / "project-context.json").write_text(json.dumps({
        "metadata": {"version": "2.0", "created_by": "gaia-scan"},
        "sections": {
            "stack": {},
            "git": {},
            "infrastructure": {"paths": {}},
        },
    }))

    # Memory dirs
    (pc_dir / "workflow-episodic-memory").mkdir()
    (pc_dir / "episodic-memory").mkdir()

    return tmp_path


@pytest.fixture()
def broken_project(tmp_path):
    """A project with .claude/ but lots of missing/broken pieces."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    # No settings, no hooks, no agents, no context
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: individual checks
# ---------------------------------------------------------------------------

class TestCheckGaiaVersion:
    """Test check_gaia_version reads package.json."""

    def test_reads_package_json(self):
        """Should read version from the real package.json."""
        r = doctor_mod.check_gaia_version()
        # In the dev repo, package.json exists
        assert r["name"] == "Gaia-Ops"
        assert r["severity"] == "pass"
        assert r["detail"].startswith("v")


class TestCheckPython:
    """Test Python version check."""

    def test_python_passes(self):
        """Current Python should pass (we're running on 3.9+)."""
        r = doctor_mod.check_python()
        assert r["name"] == "Python"
        assert r["severity"] == "pass"
        assert "Python" in r["detail"]


class TestCheckPluginMode:
    """Test plugin mode detection."""

    def test_ops_mode(self, healthy_project):
        """Should detect ops mode from plugin-registry.json."""
        r = doctor_mod.check_plugin_mode(healthy_project)
        assert r["severity"] == "pass"
        assert "ops" in r["detail"]

    def test_no_registry(self, broken_project):
        """Should warn when plugin-registry.json is missing."""
        r = doctor_mod.check_plugin_mode(broken_project)
        assert r["severity"] == "warning"

    def test_security_mode(self, tmp_path):
        """Should detect security mode."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "plugin-registry.json").write_text(json.dumps({
            "installed": [{"name": "gaia-security"}],
            "source": "npm",
        }))
        r = doctor_mod.check_plugin_mode(tmp_path)
        assert r["severity"] == "pass"
        assert "security" in r["detail"]


class TestCheckSymlinks:
    """Test symlink check."""

    def test_all_present(self, healthy_project):
        """Should pass when all expected paths exist."""
        r = doctor_mod.check_symlinks(healthy_project)
        assert r["severity"] == "pass"

    def test_missing_critical(self, tmp_path):
        """Should error when critical dirs (agents, hooks, skills) are missing."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Only create non-critical ones
        (claude_dir / "tools").mkdir()
        (claude_dir / "commands").mkdir()
        r = doctor_mod.check_symlinks(tmp_path)
        assert r["severity"] == "error"

    def test_missing_non_critical(self, healthy_project):
        """Should warn (not error) when non-critical dirs are missing."""
        import shutil
        # Remove non-critical dir
        shutil.rmtree(healthy_project / ".claude" / "templates")
        r = doctor_mod.check_symlinks(healthy_project)
        # Not all valid, but no critical missing
        assert r["severity"] == "warning"


class TestCheckIdentity:
    """Test identity check."""

    def test_healthy(self, healthy_project):
        """Should pass with correct orchestrator config."""
        r = doctor_mod.check_identity(healthy_project)
        assert r["severity"] == "pass"

    def test_missing_settings(self, broken_project):
        """Should error when settings.local.json missing."""
        r = doctor_mod.check_identity(broken_project)
        assert r["severity"] == "error"

    def test_legacy_claude_md(self, healthy_project):
        """Should report info when CLAUDE.md exists."""
        (healthy_project / "CLAUDE.md").write_text("# Legacy")
        r = doctor_mod.check_identity(healthy_project)
        assert r["severity"] == "info"
        assert "Legacy" in r["detail"]


class TestCheckSettings:
    """Test settings check."""

    def test_healthy(self, healthy_project):
        """Should pass with complete settings."""
        r = doctor_mod.check_settings(healthy_project)
        assert r["severity"] == "pass"

    def test_no_deny_rules(self, healthy_project):
        """Should error when deny rules are missing."""
        settings_path = healthy_project / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data["permissions"]["deny"] = []
        settings_path.write_text(json.dumps(data))
        r = doctor_mod.check_settings(healthy_project)
        assert r["severity"] == "error"
        assert "deny" in r["detail"].lower()


class TestCheckHookFiles:
    """Test hook files check."""

    def test_all_present(self, healthy_project):
        """Should pass with all hooks present."""
        r = doctor_mod.check_hook_files(healthy_project)
        assert r["severity"] == "pass"

    def test_required_missing(self, healthy_project):
        """Should error when a required hook is missing."""
        (healthy_project / ".claude" / "hooks" / "pre_tool_use.py").unlink()
        r = doctor_mod.check_hook_files(healthy_project)
        assert r["severity"] == "error"
        assert "pre_tool_use.py" in r["detail"]

    def test_optional_missing(self, healthy_project):
        """Should warn when an optional hook is missing."""
        (healthy_project / ".claude" / "hooks" / "post_compact.py").unlink()
        r = doctor_mod.check_hook_files(healthy_project)
        assert r["severity"] == "warning"


class TestCheckProjectContext:
    """Test project-context.json check."""

    def test_valid_context(self, healthy_project):
        """Should pass or info with valid project-context.json (never warn/error)."""
        r = doctor_mod.check_project_context(healthy_project)
        # Empty paths dict triggers "info" (No paths section); that is not a problem.
        assert r["severity"] in ("pass", "info")
        assert r["ok"] is True

    def test_missing_context(self, broken_project):
        """Should warn when project-context.json is missing."""
        r = doctor_mod.check_project_context(broken_project)
        assert r["severity"] == "warning"

    def test_invalid_json(self, healthy_project):
        """Should warn when project-context.json is invalid."""
        ctx_path = healthy_project / ".claude" / "project-context" / "project-context.json"
        ctx_path.write_text("{invalid")
        r = doctor_mod.check_project_context(healthy_project)
        assert r["severity"] == "warning"


class TestCheckMemoryDirs:
    """Test memory directories check."""

    def test_all_present(self, healthy_project):
        """Should pass when all memory dirs exist."""
        r = doctor_mod.check_memory_dirs(healthy_project)
        assert r["severity"] == "pass"

    def test_workflow_missing(self, healthy_project):
        """Should warn when workflow-episodic-memory is missing."""
        import shutil
        shutil.rmtree(healthy_project / ".claude" / "project-context" / "workflow-episodic-memory")
        r = doctor_mod.check_memory_dirs(healthy_project)
        assert r["severity"] == "warning"


# ---------------------------------------------------------------------------
# Tests: Pass 4 -- check_package_integrity
# ---------------------------------------------------------------------------

class TestCheckPackageIntegrity:
    """Pass 4: presence-only verification of critical shipped files.

    Exec-bit is deliberately NOT checked (install.py:287 invokes the script
    via `bash <path>`, so the bit is not load-bearing).
    """

    def test_all_critical_files_present(self):
        """In the dev repo, all four critical files exist -> pass."""
        r = doctor_mod.check_package_integrity()
        assert r["name"] == "Package integrity"
        assert r["severity"] == "pass"
        assert "4/4" in r["detail"]

    def test_missing_critical_file_errors(self, monkeypatch, tmp_path):
        """If _package_root() points at a stub without scripts/, error with
        an actionable reinstall hint."""
        # Stub package root: only package.json present, no scripts/.
        (tmp_path / "package.json").write_text('{"name": "stub"}')
        monkeypatch.setattr(doctor_mod, "_package_root", lambda: tmp_path)

        r = doctor_mod.check_package_integrity()
        assert r["severity"] == "error"
        assert "scripts/bootstrap_database.sh" in r["detail"]
        assert "npm install" in r.get("fix", ""), "hint must guide reinstall"


# ---------------------------------------------------------------------------
# Tests: Pass 4 -- check_workspace_initialized
# ---------------------------------------------------------------------------

class TestCheckWorkspaceInitialized:
    """Pass 4: meta-check that the workspace is Gaia-aware end-to-end."""

    def test_healthy_workspace_passes(self, healthy_project):
        r = doctor_mod.check_workspace_initialized(healthy_project)
        assert r["severity"] == "pass"

    def test_missing_claude_dir_errors(self, tmp_path):
        # No .claude/ at all.
        r = doctor_mod.check_workspace_initialized(tmp_path)
        assert r["severity"] == "error"
        assert ".claude/" in r["detail"]
        assert "gaia install" in r["fix"]

    def test_missing_registry_errors(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.local.json").write_text(
            json.dumps({"hooks": {"PreToolUse": []}})
        )
        # No plugin-registry.json
        r = doctor_mod.check_workspace_initialized(tmp_path)
        assert r["severity"] == "error"
        assert "plugin-registry.json" in r["detail"]

    def test_settings_without_hooks_errors(self, tmp_path):
        """A settings.local.json that exists but carries no hooks section is
        functionally an uninitialized workspace -- error."""
        cd = tmp_path / ".claude"
        cd.mkdir()
        (cd / "plugin-registry.json").write_text("{}")
        (cd / "settings.local.json").write_text('{"agent": "x"}')  # no "hooks" key
        r = doctor_mod.check_workspace_initialized(tmp_path)
        assert r["severity"] == "error"
        assert "hooks" in r["detail"]


# ---------------------------------------------------------------------------
# Tests: Pass 4 -- check_last_install_error
# ---------------------------------------------------------------------------

class TestCheckLastInstallError:
    """Pass 4: surface the postinstall marker that `gaia install
    --postinstall` writes on scan failure."""

    def test_no_marker_passes(self, monkeypatch, tmp_path):
        """The autouse fixture already redirects the marker path to a tmp
        location that does not exist -> pass."""
        r = doctor_mod.check_last_install_error()
        assert r["severity"] == "pass"
        assert "no recent install errors" in r["detail"]

    def test_marker_present_errors_with_detail(self, monkeypatch, tmp_path):
        """A marker file should be reported as ERROR with the step, detail,
        timestamp, and workspace lifted verbatim from the JSON payload."""
        marker = tmp_path / "marker.json"
        marker.write_text(json.dumps({
            "timestamp": "2026-05-20T12:00:00+00:00",
            "step": "project scan",
            "detail": "context provider crashed",
            "workspace": "/home/x/proj",
        }))
        monkeypatch.setattr(doctor_mod, "_INSTALL_ERROR_MARKER", marker)

        r = doctor_mod.check_last_install_error()
        assert r["severity"] == "error"
        assert "project scan" in r["detail"]
        assert "context provider crashed" in r["detail"]
        assert "/home/x/proj" in r["detail"]
        assert "gaia install" in r["fix"]

    def test_unreadable_marker_warns(self, monkeypatch, tmp_path):
        """Marker exists but is not valid JSON -> warning with a manual-fix
        hint (delete + reinstall). Distinct from the error case so users can
        tell parse failure from real install failure."""
        marker = tmp_path / "marker.json"
        marker.write_text("{not valid json")
        monkeypatch.setattr(doctor_mod, "_INSTALL_ERROR_MARKER", marker)

        r = doctor_mod.check_last_install_error()
        assert r["severity"] == "warning"
        assert "unreadable" in r["detail"]


# ---------------------------------------------------------------------------
# Tests: Pass 4 -- check_schema_version
# ---------------------------------------------------------------------------

class TestCheckSchemaVersion:
    """Pass 4: verify the schema_version migration ledger matches the CLI's
    EXPECTED_SCHEMA_VERSION constant."""

    def _make_db(self, path, schema_version_rows=None):
        """Build a minimal SQLite DB with the schema_version table.

        If schema_version_rows is None, the table is created but empty.
        If a list of (version, applied_at, description) tuples, insert each.
        If schema_version_rows is the literal "no_table", omit the table.
        """
        import sqlite3
        con = sqlite3.connect(str(path))
        cur = con.cursor()
        if schema_version_rows != "no_table":
            cur.execute(
                "CREATE TABLE schema_version "
                "(version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT)"
            )
            for row in (schema_version_rows or []):
                cur.execute(
                    "INSERT INTO schema_version VALUES (?, ?, ?)", row
                )
        con.commit()
        con.close()

    def test_no_db_info(self, monkeypatch, tmp_path):
        """Fresh machine, no gaia.db yet -> info (will be created on install)."""
        fake = tmp_path / "no-such-gaia.db"
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", fake)
        r = doctor_mod.check_schema_version()
        assert r["severity"] == "info"
        assert "no DB" in r["detail"]

    def test_matching_version_passes(self, monkeypatch, tmp_path):
        """DB schema_version == EXPECTED_SCHEMA_VERSION -> pass."""
        db = tmp_path / "gaia.db"
        self._make_db(db, schema_version_rows=[
            (doctor_mod.EXPECTED_SCHEMA_VERSION, "2026-05-20T00:00:00Z", "initial"),
        ])
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db)
        r = doctor_mod.check_schema_version()
        assert r["severity"] == "pass"
        assert f"v{doctor_mod.EXPECTED_SCHEMA_VERSION}" in r["detail"]

    def test_db_lower_than_expected_warns(self, monkeypatch, tmp_path):
        """If the DB schema is older than the CLI expects, warn and tell
        the user to run `gaia install` to apply migrations."""
        db = tmp_path / "gaia.db"
        # Empty schema_version table -> MAX(version) = NULL -> treated as 0
        self._make_db(db, schema_version_rows=[])
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db)
        monkeypatch.setattr(doctor_mod, "EXPECTED_SCHEMA_VERSION", 5)
        r = doctor_mod.check_schema_version()
        assert r["severity"] == "warning"
        assert "schema_version=0" in r["detail"]
        assert "expects 5" in r["detail"]
        assert "gaia install" in r["fix"]

    def test_db_higher_than_expected_warns(self, monkeypatch, tmp_path):
        """If the DB schema is newer than the CLI expects, warn and tell
        the user to upgrade Gaia (different remedy than the lower case)."""
        db = tmp_path / "gaia.db"
        self._make_db(db, schema_version_rows=[
            (99, "2026-05-20T00:00:00Z", "future"),
        ])
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db)
        r = doctor_mod.check_schema_version()
        assert r["severity"] == "warning"
        assert "99" in r["detail"]
        assert "npm install @jaguilar87/gaia@latest" in r["fix"]

    def test_legacy_db_without_table_warns(self, monkeypatch, tmp_path):
        """A DB that predates the schema_version table -> warn, suggest
        re-running install to apply migrations."""
        db = tmp_path / "gaia.db"
        self._make_db(db, schema_version_rows="no_table")
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db)
        r = doctor_mod.check_schema_version()
        assert r["severity"] == "warning"
        assert "schema_version table missing" in r["detail"]
        assert "gaia install" in r["fix"]

    def test_gaia_db_env_var_takes_precedence(self, monkeypatch, tmp_path):
        """GAIA_DB env var should override _DEFAULT_DB_PATH so users with
        custom DB locations are not misdiagnosed."""
        db_custom = tmp_path / "custom-gaia.db"
        self._make_db(db_custom, schema_version_rows=[
            (doctor_mod.EXPECTED_SCHEMA_VERSION, "2026-05-20T00:00:00Z", "initial"),
        ])
        # Point _DEFAULT_DB_PATH at a non-existent file to prove the env
        # var is what is read.
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", tmp_path / "nope.db")
        monkeypatch.setenv("GAIA_DB", str(db_custom))
        r = doctor_mod.check_schema_version()
        assert r["severity"] == "pass"


# ---------------------------------------------------------------------------
# Tests: Pass 4 -- summary line carries severity counts
# ---------------------------------------------------------------------------

class TestSummaryLineFormat:
    """Pass 4: the human-readable summary line counts checks by severity
    (brew/npm doctor pattern). Tests the actionable presentation contract."""

    def test_summary_counts_present(self, healthy_project, monkeypatch, capsys):
        monkeypatch.chdir(healthy_project)
        # Same isolation pattern as test_json_healthy_status -- the memory
        # scoring import flakes under pytest sys.path pollution.
        import types
        fake_tm = types.ModuleType("tools.memory")
        fake_scoring = types.ModuleType("tools.memory.scoring")
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 0
        fake_tm.scoring = fake_scoring
        fake_tm.search_store = fake_ss
        monkeypatch.setitem(sys.modules, "tools.memory", fake_tm)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        args = SimpleNamespace(json=False, fix=False, subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        out = capsys.readouterr().out
        # Must contain the counts line
        assert "Summary:" in out
        assert "error(s)" in out
        assert "warning(s)" in out
        assert "pass" in out


# ---------------------------------------------------------------------------
# Tests: cmd_doctor (human output)
# ---------------------------------------------------------------------------

class TestCmdDoctorHuman:
    """Test human-readable doctor output."""

    def test_prints_checks(self, healthy_project, monkeypatch, capsys):
        """Human output should contain check names and status."""
        monkeypatch.chdir(healthy_project)
        args = SimpleNamespace(json=False, fix=False, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        out = capsys.readouterr().out
        assert "Health Check" in out
        assert "Python" in out
        assert "Plugin mode" in out

    def test_broken_project_errors(self, broken_project, monkeypatch, capsys):
        """Should return exit code 2 when errors found."""
        monkeypatch.chdir(broken_project)
        args = SimpleNamespace(json=False, fix=False, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        # Broken project has missing hooks, settings, etc -- should be error (2)
        assert rc == 2
        out = capsys.readouterr().out
        assert "CRITICAL" in out


# ---------------------------------------------------------------------------
# Tests: cmd_doctor --json
# ---------------------------------------------------------------------------

class TestCmdDoctorJson:
    """Test JSON output mode."""

    def test_json_output_valid(self, healthy_project, monkeypatch, capsys):
        """--json should produce valid JSON with expected structure."""
        monkeypatch.chdir(healthy_project)
        args = SimpleNamespace(json=True, fix=False, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        out = capsys.readouterr().out
        data = json.loads(out)

        assert "healthy" in data
        assert "status" in data
        assert "checks" in data
        assert isinstance(data["checks"], list)
        # 19 = 11 base + 3 memory v2 + 4 Pass 4 (package-integrity,
        # last-install-error, workspace-initialized, schema-version) +
        # 1 schema-DDL-consistency added by the migration framework rewrite.
        assert len(data["checks"]) == 19

        # Each check should have name, severity, ok, detail
        for check in data["checks"]:
            assert "name" in check
            assert "severity" in check
            assert "ok" in check
            assert "detail" in check

    def test_json_healthy_status(self, healthy_project, monkeypatch, capsys):
        """Healthy project should report status=healthy."""
        monkeypatch.chdir(healthy_project)

        # Isolate from sys.path pollution: other tests (e.g. layer1_prompt_regression)
        # insert tests/ into sys.path, which makes 'import tools.memory.scoring'
        # resolve to tests/tools/ (a package without memory/), yielding ImportError
        # and a spurious warning from check_memory_scoring. Inject fake modules so
        # the check resolves to pass without hitting the real import.
        import types
        fake_tm = types.ModuleType("tools.memory")
        fake_scoring = types.ModuleType("tools.memory.scoring")
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 0
        fake_tm.scoring = fake_scoring
        fake_tm.search_store = fake_ss
        monkeypatch.setitem(sys.modules, "tools.memory", fake_tm)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        args = SimpleNamespace(json=True, fix=False, subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "healthy"
        assert data["healthy"] is True

    def test_json_broken_project(self, broken_project, monkeypatch, capsys):
        """Broken project should report status=critical."""
        monkeypatch.chdir(broken_project)
        args = SimpleNamespace(json=True, fix=False, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        assert rc == 2
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "critical"
        assert data["healthy"] is False


# ---------------------------------------------------------------------------
# Tests: exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    """Test exit code semantics: 0=healthy, 1=warnings, 2=errors."""

    def test_exit_0_healthy(self, healthy_project, monkeypatch, capsys):
        """Healthy project should exit 0."""
        monkeypatch.chdir(healthy_project)
        args = SimpleNamespace(json=False, fix=False, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)
        # May be 0 or 1 depending on claude-code being installed
        # but should not be 2 for a healthy project
        assert rc in (0, 1)

    def test_exit_2_errors(self, broken_project, monkeypatch, capsys):
        """Broken project should exit 2."""
        monkeypatch.chdir(broken_project)
        args = SimpleNamespace(json=False, fix=False, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)
        assert rc == 2


# ---------------------------------------------------------------------------
# Tests: register
# ---------------------------------------------------------------------------

class TestRegister:
    """Test plugin registration."""

    def test_register_adds_subparser(self):
        """register() should add 'doctor' as a subcommand."""
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        doctor_mod.register(subs)

        args = parser.parse_args(["doctor"])
        assert args.subcommand == "doctor"

    def test_register_flags(self):
        """register() should add --json and --fix flags."""
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        doctor_mod.register(subs)

        args = parser.parse_args(["doctor", "--json", "--fix"])
        assert args.json is True
        assert args.fix is True


# ---------------------------------------------------------------------------
# Tests: T3 memory checks (check_memory_fts5_db, check_memory_fts5_count, check_memory_scoring)
# ---------------------------------------------------------------------------

class TestCheckMemoryFts5Db:
    """Test check_memory_fts5_db."""

    def test_missing_db_returns_info(self, tmp_path):
        """Missing search.db should return severity=info."""
        em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
        em_dir.mkdir(parents=True)
        r = doctor_mod.check_memory_fts5_db(tmp_path)
        assert r["name"] == "memory_fts5_db"
        assert r["severity"] == "info"
        assert "not found" in r["detail"]

    def test_present_db_returns_pass(self, tmp_path):
        """Present search.db should return severity=pass."""
        em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
        em_dir.mkdir(parents=True)
        db_path = em_dir / "search.db"
        db_path.write_bytes(b"fake db content")
        r = doctor_mod.check_memory_fts5_db(tmp_path)
        assert r["name"] == "memory_fts5_db"
        assert r["severity"] == "pass"
        assert "present" in r["detail"]


class TestCheckMemoryFts5Count:
    """Test check_memory_fts5_count."""

    def _make_index(self, em_dir, n_episodes):
        episodes = [{"episode_id": f"ep_{i}", "title": f"Episode {i}"} for i in range(n_episodes)]
        import json
        (em_dir / "index.json").write_text(json.dumps({"episodes": episodes}))

    def test_no_index_returns_info(self, tmp_path):
        """Missing index.json should return info."""
        em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
        em_dir.mkdir(parents=True)
        r = doctor_mod.check_memory_fts5_count(tmp_path)
        assert r["severity"] == "info"

    def test_indexed_gte_90pct_returns_pass(self, tmp_path, monkeypatch):
        """indexed >= 90% of total should return pass."""
        em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
        em_dir.mkdir(parents=True)
        self._make_index(em_dir, 10)

        # Monkeypatch search_store.count to return 10 (100%)
        import types
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 10
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)
        monkeypatch.setitem(sys.modules, "tools.memory", types.ModuleType("tools.memory"))
        sys.modules["tools.memory"].search_store = fake_ss

        r = doctor_mod.check_memory_fts5_count(tmp_path)
        assert r["severity"] == "pass"
        assert "10/10" in r["detail"]

    def test_indexed_lt_90pct_returns_warning(self, tmp_path, monkeypatch):
        """indexed < 90% of total should return warning."""
        em_dir = tmp_path / ".claude" / "project-context" / "episodic-memory"
        em_dir.mkdir(parents=True)
        self._make_index(em_dir, 10)

        import types

        # Build a fake search_store with count returning 5 (50%)
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 5  # 50% — below 90%

        # Unconditionally inject tools.memory package and search_store submodule so
        # that 'from tools.memory import search_store' inside check_memory_fts5_count
        # resolves to our fake regardless of sys.path state or prior test pollution.
        fake_tm = types.ModuleType("tools.memory")
        fake_tm.search_store = fake_ss
        monkeypatch.setitem(sys.modules, "tools.memory", fake_tm)
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        r = doctor_mod.check_memory_fts5_count(tmp_path)
        assert r["severity"] == "warning", f"Expected warning but got {r['severity']}: {r['detail']}"
        assert "5/10" in r["detail"]


class TestCheckMemoryScoring:
    """Test check_memory_scoring."""

    def test_scoring_importable_returns_pass(self, monkeypatch):
        """Importable scoring module should return pass."""
        import types
        fake_scoring = types.ModuleType("tools.memory.scoring")
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)
        r = doctor_mod.check_memory_scoring(REPO_ROOT)
        assert r["severity"] == "pass"

    def test_scoring_not_importable_returns_warning(self, monkeypatch):
        """ImportError for scoring should return warning."""
        # Remove from sys.modules if present, then mock the import to fail
        monkeypatch.delitem(sys.modules, "tools.memory.scoring", raising=False)

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _mock_import(name, *args, **kwargs):
            if name == "tools.memory.scoring" or (name == "tools.memory" and args and "scoring" in str(args)):
                raise ImportError("mocked: scoring not available")
            return original_import(name, *args, **kwargs)

        # Use monkeypatch on builtins to block the import
        import builtins
        monkeypatch.setattr(builtins, "__import__", _mock_import)
        # Remove from sys.modules so it hits the import statement
        monkeypatch.delitem(sys.modules, "tools.memory.scoring", raising=False)

        r = doctor_mod.check_memory_scoring(REPO_ROOT)
        assert r["severity"] == "warning"


# ---------------------------------------------------------------------------
# Tests: T4 --fix flow
# ---------------------------------------------------------------------------

class TestCmdDoctorFix:
    """Test --fix flow in cmd_doctor."""

    def _make_memory_project(self, tmp_path):
        """Build a healthy project with memory dirs and an incomplete FTS5 index."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        # plugin-registry.json
        (claude_dir / "plugin-registry.json").write_text(json.dumps({
            "installed": [{"name": "gaia-ops"}],
            "source": "local-dev",
        }))

        for name in ["agents", "tools", "hooks", "commands", "templates", "config", "skills"]:
            (claude_dir / name).mkdir()
        (claude_dir / "CHANGELOG.md").write_text("# Changelog")

        agents_dir = claude_dir / "agents"
        (agents_dir / "gaia-orchestrator.md").write_text("---\nagent: gaia-orchestrator\n---")

        (claude_dir / "settings.local.json").write_text(json.dumps({
            "agent": "gaia-orchestrator",
            "hooks": {
                "PreToolUse": [{"command": "python"}],
                "PostToolUse": [{"command": "python"}],
                "UserPromptSubmit": [{"command": "python"}],
                "SessionStart": [{"command": "python"}],
            },
            "permissions": {"allow": ["Bash(*)"], "deny": ["rm -rf /"]},
            "env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "true"},
        }))

        hooks_dir = claude_dir / "hooks"
        for h in ["pre_tool_use.py", "post_tool_use.py", "user_prompt_submit.py",
                  "session_start.py", "subagent_stop.py", "subagent_start.py",
                  "stop_hook.py", "task_completed.py", "post_compact.py",
                  "elicitation_result.py"]:
            (hooks_dir / h).write_text("# hook stub")

        pc_dir = claude_dir / "project-context"
        pc_dir.mkdir()
        (pc_dir / "project-context.json").write_text(json.dumps({
            "metadata": {"version": "2.0", "created_by": "gaia-scan"},
            "sections": {"stack": {}, "git": {}, "infrastructure": {"paths": {}}},
        }))

        (pc_dir / "workflow-episodic-memory").mkdir()
        em_dir = pc_dir / "episodic-memory"
        em_dir.mkdir()

        # index.json with 10 episodes but no search.db (triggers info + warning)
        episodes = [{"episode_id": f"ep_{i}", "title": f"Ep {i}"} for i in range(10)]
        (em_dir / "index.json").write_text(json.dumps({"episodes": episodes}))

        return tmp_path

    def test_fix_applies_backfill_when_count_warning(self, tmp_path, monkeypatch, capsys):
        """--fix should call backfill when fts5_count is warning, and fixes list is non-empty."""
        project = self._make_memory_project(tmp_path)
        monkeypatch.chdir(project)

        import types

        # Fake search_store: first call returns 5 (warning), second returns 10 (pass after fix)
        call_count = {"n": 0}
        fake_ss = types.ModuleType("tools.memory.search_store")

        def _count():
            call_count["n"] += 1
            return 5 if call_count["n"] == 1 else 10

        fake_ss.count = _count
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        # Fake backfill_fts5.main returns 0
        fake_bf = types.ModuleType("tools.memory.backfill_fts5")
        fake_bf.main = lambda: 0
        monkeypatch.setitem(sys.modules, "tools.memory.backfill_fts5", fake_bf)
        # Also ensure tools.memory package mock propagates
        if "tools.memory" not in sys.modules:
            monkeypatch.setitem(sys.modules, "tools.memory", types.ModuleType("tools.memory"))

        args = SimpleNamespace(json=True, fix=True, subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        fixes = data.get("fixes", [])
        fts5_fixes = [f for f in fixes if "fts5" in f.get("name", "").lower() or "backfill" in f.get("name", "").lower()]
        assert len(fts5_fixes) > 0, f"No fts5 fix found in: {fixes}"
        assert fts5_fixes[0]["status"] == "applied"

    def test_fix_noop_when_already_indexed(self, tmp_path, monkeypatch, capsys):
        """--fix should be a no-op (fixes=[]) when FTS5 index is at 100%."""
        project = self._make_memory_project(tmp_path)
        monkeypatch.chdir(project)

        import types

        # search.db exists and count == 10 (100%) — no warning triggered
        em_dir = project / ".claude" / "project-context" / "episodic-memory"
        (em_dir / "search.db").write_bytes(b"fake db")

        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 10
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)
        if "tools.memory" in sys.modules:
            monkeypatch.setattr(sys.modules["tools.memory"], "search_store", fake_ss, raising=False)

        # Ensure no stale backfill_fts5 mock from a previous test affects this run
        monkeypatch.delitem(sys.modules, "tools.memory.backfill_fts5", raising=False)

        args = SimpleNamespace(json=True, fix=True, subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        assert data.get("fixes") == [], f"Expected empty fixes but got: {data.get('fixes')}"

    def test_fix_json_includes_fixes_key_without_fix_flag(self, tmp_path, monkeypatch, capsys):
        """--json without --fix should still include fixes: [] in output."""
        project = self._make_memory_project(tmp_path)
        monkeypatch.chdir(project)

        import types
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 10
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        args = SimpleNamespace(json=True, fix=False, subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        assert "fixes" in data
        assert data["fixes"] == []

    def test_fix_agent_field_missing(self, tmp_path, monkeypatch, capsys):
        """--fix should write agent='gaia-orchestrator' when settings.local.json
        lacks an `agent` top-level field, and re-run check_identity to reflect
        the post-fix state."""
        project = self._make_memory_project(tmp_path)
        monkeypatch.chdir(project)

        # Strip the agent field so check_identity returns "No agent field" error
        settings_path = project / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data.pop("agent", None)
        settings_path.write_text(json.dumps(data))

        # Avoid spurious FTS5 fix triggering -- pre-create search.db and stub count
        import types
        em_dir = project / ".claude" / "project-context" / "episodic-memory"
        (em_dir / "search.db").write_bytes(b"fake db")
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 10
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        # Pre-condition: check_identity flags "No agent field"
        pre_check = doctor_mod.check_identity(project)
        assert pre_check["severity"] == "error"
        assert "No agent field" in pre_check["detail"]

        args = SimpleNamespace(json=True, fix=True, subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        out = json.loads(capsys.readouterr().out)
        fixes = out.get("fixes", [])
        agent_fixes = [f for f in fixes if f.get("name") == "agent_field"]
        assert len(agent_fixes) == 1, f"Expected one agent_field fix, got: {fixes}"
        assert agent_fixes[0]["status"] == "applied"

        # Verify the file actually has agent=gaia-orchestrator now
        post = json.loads(settings_path.read_text())
        assert post["agent"] == "gaia-orchestrator"

        # Verify check_identity post-fix passes (re-ran inside cmd_doctor)
        identity_result = next(c for c in out["checks"] if c["name"] == "Identity")
        assert identity_result["severity"] in ("pass", "info")

    def test_fix_agent_field_preserves_other_keys(self, tmp_path, monkeypatch, capsys):
        """The agent fix must preserve all other top-level keys in settings.local.json."""
        project = self._make_memory_project(tmp_path)
        monkeypatch.chdir(project)

        settings_path = project / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data.pop("agent", None)
        # Add custom keys to ensure they survive
        data["custom_field"] = "preserve_me"
        data["env"]["EXTRA_VAR"] = "kept"
        settings_path.write_text(json.dumps(data))

        # Stub FTS5 to avoid noise
        import types
        em_dir = project / ".claude" / "project-context" / "episodic-memory"
        (em_dir / "search.db").write_bytes(b"fake db")
        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 10
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        args = SimpleNamespace(json=True, fix=True, subcommand="doctor")
        doctor_mod.cmd_doctor(args)
        capsys.readouterr()  # drain

        post = json.loads(settings_path.read_text())
        assert post["agent"] == "gaia-orchestrator"
        assert post["custom_field"] == "preserve_me"
        assert post["env"]["EXTRA_VAR"] == "kept"
        assert post["hooks"]  # untouched
        assert post["permissions"]["deny"]  # untouched

    def test_fix_failed_backfill_reported(self, tmp_path, monkeypatch, capsys):
        """If backfill fails (rc != 0), fix status should be 'failed'."""
        project = self._make_memory_project(tmp_path)
        monkeypatch.chdir(project)

        import types

        fake_ss = types.ModuleType("tools.memory.search_store")
        fake_ss.count = lambda: 3  # 30% — warning
        monkeypatch.setitem(sys.modules, "tools.memory.search_store", fake_ss)

        fake_bf = types.ModuleType("tools.memory.backfill_fts5")
        fake_bf.main = lambda: 1  # simulate failure
        monkeypatch.setitem(sys.modules, "tools.memory.backfill_fts5", fake_bf)

        args = SimpleNamespace(json=True, fix=True, subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        fixes = data.get("fixes", [])
        assert len(fixes) == 1
        assert fixes[0]["status"] == "failed"
