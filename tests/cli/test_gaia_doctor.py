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
        assert len(data["checks"]) == 11  # 11 checks total

        # Each check should have name, severity, ok, detail
        for check in data["checks"]:
            assert "name" in check
            assert "severity" in check
            assert "ok" in check
            assert "detail" in check

    def test_json_healthy_status(self, healthy_project, monkeypatch, capsys):
        """Healthy project should report status=healthy."""
        monkeypatch.chdir(healthy_project)
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
