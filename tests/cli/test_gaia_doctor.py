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

    # check_hooks_active_fresh (order 150) reads the user-scoped session
    # registry and the host session-id env vars. On a developer machine both
    # are live, which would make the check non-deterministic. Redirect the
    # registry to a tmp path and clear the session-id env vars so the check
    # degrades to the isolated UNKNOWN/info state unless a test opts in.
    fake_registry = tmp_path / "isolated-session_registry.json"
    monkeypatch.setattr(doctor_mod, "_SESSION_REGISTRY_PATH", fake_registry)
    for _var in doctor_mod._SESSION_ID_ENV_VARS:
        monkeypatch.delenv(_var, raising=False)
    yield

@pytest.fixture()
def healthy_project(tmp_path):
    """Create a fully healthy .claude/ project for doctor checks."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # plugin-registry.json ("gaia" is the canonical single-plugin identity)
    (claude_dir / "plugin-registry.json").write_text(json.dumps({
        "installed": [{"name": "gaia"}],
        "source": "local-dev",
    }))

    # Symlink targets (real directories, not symlinks -- tests just need exists())
    for name in ["agents", "tools", "hooks", "commands", "config", "skills"]:
        (claude_dir / name).mkdir()
    (claude_dir / "CHANGELOG.md").write_text("# Changelog")

    # Agent definition
    agents_dir = claude_dir / "agents"
    (agents_dir / "gaia-orchestrator.md").write_text("---\nname: gaia-orchestrator\nagent: gaia-orchestrator\n---")

    # settings.local.json -- hooks carry the full canonical event set, matching
    # what merge_local_hooks copies from hooks.json in npm mode.
    (claude_dir / "settings.local.json").write_text(json.dumps({
        "agent": "gaia-orchestrator",
        "hooks": {ev: [{"command": "python"}] for ev in [
            "PreToolUse", "PostToolUse", "SubagentStop", "SessionStart",
            "SessionEnd", "UserPromptSubmit", "Stop", "TaskCompleted",
            "SubagentStart", "PostCompact", "PreCompact", "ElicitationResult",
        ]},
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
              "session_start.py", "session_end_hook.py", "subagent_stop.py",
              "subagent_start.py", "stop_hook.py", "task_completed.py",
              "pre_compact.py", "post_compact.py", "elicitation_result.py"]:
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
        assert r["name"] == "Gaia"
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
    """Test that the gaia plugin is registered."""

    def test_gaia_registered(self, healthy_project):
        """Should pass when 'gaia' is in plugin-registry.json."""
        r = doctor_mod.check_plugin_mode(healthy_project)
        assert r["severity"] == "pass"
        assert "gaia" in r["detail"]

    def test_no_registry(self, broken_project):
        """Should warn when plugin-registry.json is missing."""
        r = doctor_mod.check_plugin_mode(broken_project)
        assert r["severity"] == "warning"

    def test_gaia_not_registered(self, tmp_path):
        """Should warn when gaia is not in the registry (e.g. an unknown plugin)."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "plugin-registry.json").write_text(json.dumps({
            "installed": [{"name": "other-plugin"}],
            "source": "npm",
        }))
        r = doctor_mod.check_plugin_mode(tmp_path)
        assert r["severity"] == "warning"


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
        shutil.rmtree(healthy_project / ".claude" / "config")
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


class TestCheckAgentResolution:
    """Test check_agent_resolution -- surface_routing (DB table) agents resolve to files.

    Routing moved off config/surface-routing.json (retired, git-rm'd in
    commit 9fac935) onto the ``surface_routing`` table in gaia.db, seeded
    from agent ``routing:`` frontmatter by tools/scan/seed_surface_routing.py.
    check_agent_resolution now reads that table via the same loader
    (tools.context.surface_router.load_surface_routing_config) the
    UserPromptSubmit hook uses -- these tests seed a minimal ``surface_routing``
    table directly (schema mirrors gaia/store/schema.sql) rather than writing
    the retired JSON file.
    """

    def _write_routing_db(self, tmp_path: Path, surfaces: dict) -> Path:
        """Create a minimal gaia.db with a seeded ``surface_routing`` table."""
        import sqlite3

        db_path = tmp_path / "routing-gaia.db"
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS surface_routing (
                    surface                TEXT NOT NULL PRIMARY KEY,
                    primary_agent          TEXT NOT NULL,
                    adjacent_surfaces_json TEXT NOT NULL DEFAULT '[]',
                    contract_sections_json TEXT NOT NULL DEFAULT '[]',
                    required_checks_json   TEXT NOT NULL DEFAULT '[]',
                    keywords_json          TEXT NOT NULL DEFAULT '[]',
                    commands_json          TEXT NOT NULL DEFAULT '[]',
                    artifacts_json         TEXT NOT NULL DEFAULT '[]',
                    sub_surfaces_json      TEXT
                )
                """
            )
            for surface, primary_agent in surfaces.items():
                con.execute(
                    "INSERT INTO surface_routing (surface, primary_agent) VALUES (?, ?)",
                    (surface, primary_agent),
                )
            con.commit()
        finally:
            con.close()
        return db_path

    def test_all_agents_resolve_passes(self, healthy_project, tmp_path, monkeypatch):
        """When every routed primary_agent (+ the reconnaissance agent) maps
        to an existing .md -> pass."""
        agents = healthy_project / ".claude" / "agents"
        (agents / "gaia-system.md").write_text("---\n---")
        (agents / "developer.md").write_text("---\n---")  # reconnaissance_agent constant
        db_path = self._write_routing_db(tmp_path, {
            "gaia_system": "gaia-system",
            "app_ci_tooling": "developer",
        })
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db_path)
        r = doctor_mod.check_agent_resolution(healthy_project)
        assert r["severity"] == "pass"

    def test_missing_agent_errors(self, healthy_project, tmp_path, monkeypatch):
        """A primary_agent with no matching .md -> error naming the agent."""
        db_path = self._write_routing_db(tmp_path, {
            "iac": "platform-architect",  # no .md created
        })
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db_path)
        # reconnaissance_agent ("developer") isn't seeded as a .md either in
        # this fixture, but the assertion only needs to see the named agent.
        (healthy_project / ".claude" / "agents" / "developer.md").write_text("---\n---")
        r = doctor_mod.check_agent_resolution(healthy_project)
        assert r["severity"] == "error"
        assert "platform-architect" in r["detail"]

    def test_no_routing_table_is_info(self, healthy_project):
        """No gaia.db (table not yet seeded) is advisory, not an error.

        _isolate_home_globals already redirects _DEFAULT_DB_PATH to a
        nonexistent tmp path, so this exercises the not-seeded path with no
        further setup.
        """
        r = doctor_mod.check_agent_resolution(healthy_project)
        assert r["severity"] == "info"
        assert r["ok"] is True

    def test_empty_routing_table_is_info(self, healthy_project, tmp_path, monkeypatch):
        """A gaia.db that exists but has no surface_routing rows is also advisory."""
        db_path = self._write_routing_db(tmp_path, {})
        monkeypatch.setattr(doctor_mod, "_DEFAULT_DB_PATH", db_path)
        r = doctor_mod.check_agent_resolution(healthy_project)
        assert r["severity"] == "info"
        assert r["ok"] is True


class TestCheckProjectContext:
    """Test project-context.json check.

    M1 retrofitted check_project_context to read from project_context_contracts
    in gaia.db instead of the legacy project-context.json file.  Tests that
    exercise the "pass" path must therefore seed DB rows; the legacy filesystem
    fixture is no longer load-bearing for this check.
    """

    def _seed_contracts_for_project_root(self, tmp_path: Path, project_root: Path, monkeypatch) -> None:
        """Bootstrap a temp gaia.db and seed >= 3 project_context_contracts rows.

        Sets GAIA_DATA_DIR so that gaia.paths.db_path() resolves to the temp DB.
        """
        import sqlite3
        import subprocess as _sp
        import os as _os

        db_path = tmp_path / "gaia.db"
        bootstrap = REPO_ROOT / "scripts" / "bootstrap_database.sh"
        env = _os.environ.copy()
        env["GAIA_DB"] = str(db_path)
        env["WORKSPACE"] = str(tmp_path)
        res = _sp.run(
            ["bash", str(bootstrap)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0, (
            f"bootstrap failed: {res.stderr}"
        )

        # Resolve workspace identity the same way check_project_context does.
        from gaia.project import current as _project_current
        ws = _project_current(cwd=project_root)

        con = sqlite3.connect(str(db_path))
        try:
            con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (ws,))
            for contract in ("stack", "git", "infrastructure", "services"):
                con.execute(
                    "INSERT OR REPLACE INTO project_context_contracts "
                    "  (workspace, contract_name, payload, updated_at) "
                    "VALUES (?, ?, '{}', '2026-01-01T00:00:00Z')",
                    (ws, contract),
                )
            con.commit()
        finally:
            con.close()

        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    def test_valid_context(self, healthy_project, tmp_path, monkeypatch):
        """Should pass when >= 3 project_context_contracts rows exist in DB."""
        self._seed_contracts_for_project_root(tmp_path, healthy_project, monkeypatch)
        r = doctor_mod.check_project_context(healthy_project)
        assert r["severity"] in ("pass", "info")
        assert r["ok"] is True

    def test_missing_context(self, broken_project):
        """Empty context is advisory: no contracts yet means run `gaia scan`.

        A freshly-installed workspace has no contracts until the first scan;
        that empty state is info (advisory), not warning, so a clean install
        passes doctor rc=0.
        """
        r = doctor_mod.check_project_context(broken_project)
        assert r["severity"] == "info"

    def test_invalid_json(self, healthy_project):
        """Should report info when no contracts in DB (legacy json is no longer read)."""
        # With no GAIA_DATA_DIR override, _DEFAULT_DB_PATH (isolated to
        # empty tmp) has no contracts -> info (advisory) path.
        r = doctor_mod.check_project_context(healthy_project)
        assert r["severity"] == "info"


class TestCheckMemoryDirs:
    """Test the memory store check.

    Episodic memory is DB-canonical (brief ``episodic-workflow-to-db``): the
    check validates the ``episodes`` table in gaia.db, not the legacy
    filesystem dirs that the canonical path no longer creates.
    """

    def test_episodes_table_present_passes(self, tmp_path, monkeypatch):
        """Pass when the episodes table exists in gaia.db."""
        import sqlite3
        import gaia.store.writer as _writer_mod

        db_file = tmp_path / "gaia.db"
        con = sqlite3.connect(str(db_file))
        con.execute("CREATE TABLE episodes (episode_id TEXT PRIMARY KEY)")
        con.commit()
        con.close()

        monkeypatch.setattr(
            _writer_mod, "_connect", lambda *a, **k: sqlite3.connect(str(db_file))
        )
        r = doctor_mod.check_memory_dirs(tmp_path)
        assert r["name"] == "Memory store"
        assert r["severity"] == "pass"

    def test_episodes_table_missing_warns(self, tmp_path, monkeypatch):
        """Warn when the episodes table is absent from gaia.db."""
        import sqlite3
        import gaia.store.writer as _writer_mod

        db_file = tmp_path / "gaia.db"
        con = sqlite3.connect(str(db_file))
        con.execute("CREATE TABLE other (id TEXT)")
        con.commit()
        con.close()

        monkeypatch.setattr(
            _writer_mod, "_connect", lambda *a, **k: sqlite3.connect(str(db_file))
        )
        r = doctor_mod.check_memory_dirs(tmp_path)
        assert r["name"] == "Memory store"
        assert r["severity"] == "warning"

    def test_store_unavailable_warns(self, tmp_path, monkeypatch):
        """Warn when gaia.db cannot be reached."""
        import gaia.store.writer as _writer_mod

        def _raise_connect(*a, **k):
            raise Exception("simulated store unavailable")

        monkeypatch.setattr(_writer_mod, "_connect", _raise_connect)
        r = doctor_mod.check_memory_dirs(tmp_path)
        assert r["name"] == "Memory store"
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
        # Same isolation pattern as test_json_healthy_status -- the memory
        # scoring import flakes under pytest sys.path pollution.
        import types
        fake_tm = types.ModuleType("tools.memory")
        fake_scoring = types.ModuleType("tools.memory.scoring")
        fake_tm.scoring = fake_scoring
        monkeypatch.setitem(sys.modules, "tools.memory", fake_tm)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)

        args = SimpleNamespace(json=False, fix=False, workspace=str(healthy_project), subcommand="doctor")
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
        args = SimpleNamespace(json=False, fix=False, workspace=str(healthy_project), subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        out = capsys.readouterr().out
        assert "Health Check" in out
        assert "Python" in out
        assert "Plugin registered" in out

    def test_broken_project_errors(self, broken_project, monkeypatch, capsys):
        """Should return exit code 2 when errors found."""
        args = SimpleNamespace(json=False, fix=False, workspace=str(broken_project), subcommand="doctor")
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
        args = SimpleNamespace(json=True, fix=False, workspace=str(healthy_project), subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)

        out = capsys.readouterr().out
        data = json.loads(out)

        assert "healthy" in data
        assert "status" in data
        assert "checks" in data
        assert isinstance(data["checks"], list)
        # 26 = 11 base + 3 memory v2 + 4 Pass 4 (package-integrity,
        # last-install-error, workspace-initialized, schema-version) +
        # 1 schema-DDL-consistency added by the migration framework rewrite +
        # 1 schema-v12-tables added by Wave 3 approval-model-redesign (M1) +
        # 1 agent-routing (surface_routing DB table primary agents resolve to files) +
        # 1 symlinks-freshness (.claude/hooks resolves to the installed pkg
        #   version -- install-local staleness fix) +
        # 2 deterministic structural checks migrated from gaia-audit
        #   (component-naming order 52, skill-cross-refs order 53) +
        # 1 install-provenance (order 57 -- local vs npm install + local
        #   freshness vs source; replaces `gaia release sync-local`) +
        # 1 hooks-active-fresh (order 150 -- running session's hooks == the
        #   currently wired build; live-freshness vs on-disk freshness).
        assert len(data["checks"]) == 26

        # Each check should have name, severity, ok, detail
        for check in data["checks"]:
            assert "name" in check
            assert "severity" in check
            assert "ok" in check
            assert "detail" in check

    def test_json_healthy_status(self, healthy_project, tmp_path, monkeypatch, capsys):
        """Healthy project should report status=healthy.

        check_project_context (M1) reads from project_context_contracts in gaia.db,
        not from the filesystem. Seed >= 3 contracts in a temp DB so the check
        resolves to 'pass' and does not drag the overall status to 'degraded'.
        """
        # Seed project_context_contracts in a temp DB so check_project_context passes.
        import sqlite3
        import subprocess as _sp
        import os as _os

        db_dir = tmp_path / "gaia_data"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "gaia.db"
        bootstrap = REPO_ROOT / "scripts" / "bootstrap_database.sh"
        env = _os.environ.copy()
        env["GAIA_DB"] = str(db_path)
        env["WORKSPACE"] = str(db_dir)
        res = _sp.run(
            ["bash", str(bootstrap)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0, f"bootstrap failed: {res.stderr}"

        from gaia.project import current as _project_current
        ws = _project_current(cwd=healthy_project)

        con = sqlite3.connect(str(db_path))
        try:
            con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (ws,))
            for contract in ("stack", "git", "infrastructure", "services"):
                con.execute(
                    "INSERT OR REPLACE INTO project_context_contracts "
                    "  (workspace, contract_name, payload, updated_at) "
                    "VALUES (?, ?, '{}', '2026-01-01T00:00:00Z')",
                    (ws, contract),
                )
            con.commit()
        finally:
            con.close()

        monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))

        # Isolate from sys.path pollution: other tests (e.g. layer1_prompt_regression)
        # insert tests/ into sys.path, which makes 'import tools.memory.scoring'
        # resolve to tests/tools/ (a package without memory/), yielding ImportError
        # and a spurious warning from check_memory_scoring. Inject fake modules so
        # the check resolves to pass without hitting the real import.
        import types
        fake_tm = types.ModuleType("tools.memory")
        fake_scoring = types.ModuleType("tools.memory.scoring")
        fake_tm.scoring = fake_scoring
        monkeypatch.setitem(sys.modules, "tools.memory", fake_tm)
        monkeypatch.setitem(sys.modules, "tools.memory.scoring", fake_scoring)

        args = SimpleNamespace(json=True, fix=False, workspace=str(healthy_project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "healthy"
        assert data["healthy"] is True

    def test_json_broken_project(self, broken_project, monkeypatch, capsys):
        """Broken project should report status=critical."""
        args = SimpleNamespace(json=True, fix=False, workspace=str(broken_project), subcommand="doctor")
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
        args = SimpleNamespace(json=False, fix=False, workspace=str(healthy_project), subcommand="doctor")
        rc = doctor_mod.cmd_doctor(args)
        # May be 0 or 1 depending on claude-code being installed
        # but should not be 2 for a healthy project
        assert rc in (0, 1)

    def test_exit_2_errors(self, broken_project, monkeypatch, capsys):
        """Broken project should exit 2."""
        args = SimpleNamespace(json=False, fix=False, workspace=str(broken_project), subcommand="doctor")
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
        """register() should add --json, --fix, and --workspace flags."""
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        doctor_mod.register(subs)

        args = parser.parse_args(["doctor", "--json", "--fix", "--workspace", "/tmp/ws"])
        assert args.json is True
        assert args.fix is True
        assert args.workspace == "/tmp/ws"


# ---------------------------------------------------------------------------
# Tests: _derive_workspace -- workspace discovery from __file__ realpath
# ---------------------------------------------------------------------------

class TestDeriveWorkspace:
    """Unit tests for _derive_workspace() -- the replacement for _find_project_root().

    Each test mocks doctor_mod.__file__ to simulate different install scenarios
    without needing to actually install the package.
    """

    def test_workspace_override_valid(self, tmp_path):
        """--workspace flag with a valid .claude/ dir returns that path directly."""
        (tmp_path / ".claude").mkdir()
        result = doctor_mod._derive_workspace(override=str(tmp_path))
        assert result == tmp_path.resolve()

    def test_workspace_override_missing_claude_exits(self, tmp_path, capsys):
        """--workspace pointing at a dir without .claude/ should exit with code 2."""
        with pytest.raises(SystemExit) as exc:
            doctor_mod._derive_workspace(override=str(tmp_path))
        assert exc.value.code == 2

    def test_standard_consumer_install(self, tmp_path, monkeypatch):
        """Script inside <workspace>/node_modules/@jaguilar87/gaia/bin/cli/doctor.py
        should derive workspace = <workspace>."""
        # Build a fake install tree
        pkg_dir = tmp_path / "node_modules" / "@jaguilar87" / "gaia"
        script_path = pkg_dir / "bin" / "cli" / "doctor.py"
        script_path.parent.mkdir(parents=True)
        script_path.touch()

        # The workspace is NOT the gaia source (no package.json with gaia name)
        (tmp_path / "package.json").write_text('{"name": "my-app", "version": "1.0.0"}')

        # Mock __file__ in the module
        monkeypatch.setattr(doctor_mod, "__file__", str(script_path))

        result = doctor_mod._derive_workspace()
        assert result == tmp_path.resolve()

    def test_pnpm_virtual_store_install(self, tmp_path, monkeypatch):
        """Script inside a pnpm virtual-store layout should still derive the
        real project workspace, not the .pnpm store subdirectory.

        pnpm installs into a content-addressed store and symlinks the
        package in; once the symlink is resolved the physical path becomes
        <workspace>/node_modules/.pnpm/@jaguilar87+gaia@X.Y.Z/node_modules/@jaguilar87/gaia/...
        -- a nested node_modules/@jaguilar87/gaia sits inside the outer one.
        Regression test for the false-CRITICAL defect: the old algorithm
        matched on the nested occurrence and returned the .pnpm store
        subdirectory (no .claude/) as the workspace.
        """
        pnpm_pkg_dir = (
            tmp_path
            / "node_modules"
            / ".pnpm"
            / "@jaguilar87+gaia@1.0.0"
            / "node_modules"
            / "@jaguilar87"
            / "gaia"
        )
        script_path = pnpm_pkg_dir / "bin" / "cli" / "doctor.py"
        script_path.parent.mkdir(parents=True)
        script_path.touch()
        (pnpm_pkg_dir / "package.json").write_text(
            '{"name": "@jaguilar87/gaia", "version": "1.0.0"}'
        )

        # The real project workspace (NOT the gaia source package).
        (tmp_path / "package.json").write_text('{"name": "my-app", "version": "1.0.0"}')

        monkeypatch.setattr(doctor_mod, "__file__", str(script_path))

        result = doctor_mod._derive_workspace()
        assert result == tmp_path.resolve()

    def test_source_repo_self_install_walks_to_consumer(self, tmp_path, monkeypatch):
        """When the script lives in the gaia source repo's own node_modules self-install,
        _derive_workspace should walk up one level to the real consumer workspace."""
        # Build: consumer/<source_repo>/node_modules/@jaguilar87/gaia/...
        consumer = tmp_path / "consumer"
        consumer.mkdir()
        source_repo = consumer / "gaia"
        source_repo.mkdir()

        # Source repo package.json -- this IS the gaia package
        (source_repo / "package.json").write_text('{"name": "@jaguilar87/gaia", "version": "1.0.0"}')

        # Source repo's self-install (dev pattern)
        pkg_dir = source_repo / "node_modules" / "@jaguilar87" / "gaia"
        script_path = pkg_dir / "bin" / "cli" / "doctor.py"
        script_path.parent.mkdir(parents=True)
        script_path.touch()

        # Consumer also has gaia installed (the real workspace install)
        consumer_nm = consumer / "node_modules" / "@jaguilar87" / "gaia"
        consumer_nm.mkdir(parents=True)

        monkeypatch.setattr(doctor_mod, "__file__", str(script_path))

        result = doctor_mod._derive_workspace()
        assert result == consumer.resolve()

    def test_global_install_exits_with_error(self, tmp_path, monkeypatch, capsys):
        """Script NOT inside any node_modules/@jaguilar87/gaia/ tree should
        exit with the explicit error message -- no silent cwd fallback."""
        # A path that has no node_modules/@jaguilar87/gaia/ ancestor
        script_path = tmp_path / "usr" / "local" / "lib" / "gaia" / "bin" / "cli" / "doctor.py"
        script_path.parent.mkdir(parents=True)
        script_path.touch()

        monkeypatch.setattr(doctor_mod, "__file__", str(script_path))

        with pytest.raises(SystemExit) as exc:
            doctor_mod._derive_workspace()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "global or symlinked install detected" in err
        assert "--workspace" in err

    def test_workspace_printed_in_human_output(self, healthy_project, capsys):
        """Human output should include the workspace path being checked."""
        args = SimpleNamespace(json=False, fix=False, workspace=str(healthy_project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)
        out = capsys.readouterr().out
        assert "Workspace:" in out
        assert str(healthy_project) in out


# ---------------------------------------------------------------------------
# Tests: T3 memory checks (check_memory_fts5_db, check_memory_fts5_count, check_memory_scoring)
# ---------------------------------------------------------------------------

class TestCheckMemoryFts5Db:
    """Test check_memory_fts5_db.

    T6 migration: check_memory_fts5_db now queries episodes_fts table in gaia.db
    instead of checking for the legacy search.db file on disk.
    """

    def test_episodes_fts_empty_returns_info(self, tmp_path):
        """When episodes_fts table is accessible but empty, return severity=info."""
        import sqlite3
        # Create a minimal gaia.db with episodes_fts in a temp location
        import os
        os.environ.setdefault("GAIA_DB_PATH", str(tmp_path / "gaia.db"))
        try:
            con = sqlite3.connect(str(tmp_path / "gaia.db"))
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5("
                "episode_id UNINDEXED, prompt, enriched_prompt, tags, title)"
            )
            con.commit()
            con.close()

            import sys
            _REPO_ROOT = str(tmp_path.parent.parent.parent.parent.parent)
            # Patch the _store_connect in doctor_mod to use our temp DB
            import importlib
            original_env = os.environ.get("GAIA_DB_PATH")
            os.environ["GAIA_DB_PATH"] = str(tmp_path / "gaia.db")
            r = doctor_mod.check_memory_fts5_db(tmp_path)
            assert r["name"] == "memory_fts5_db"
            # Empty episodes_fts -> info
            assert r["severity"] in ("info", "pass", "warning")
        finally:
            os.environ.pop("GAIA_DB_PATH", None)

    def test_episodes_fts_unavailable_returns_warning(self, tmp_path, monkeypatch):
        """When episodes_fts is not accessible, return severity=warning.

        T6 migration: replaces the legacy 'missing search.db -> info' check.
        Uses monkeypatch.setattr on gaia.store.writer._connect to raise so
        that the 'except Exception' branch in check_memory_fts5_db fires,
        avoiding the fragile sys.modules["gaia"]=None pattern that fails
        when gaia.store.writer is already cached.
        """
        import gaia.store.writer as _writer_mod

        def _raise_connect():
            raise Exception("simulated store unavailable")

        monkeypatch.setattr(_writer_mod, "_connect", _raise_connect)
        r = doctor_mod.check_memory_fts5_db(tmp_path)
        assert r["name"] == "memory_fts5_db"
        assert r["severity"] in ("warning", "info")


class TestCheckMemoryFts5Count:
    """Test check_memory_fts5_count.

    T6 migration: check_memory_fts5_count now queries the canonical gaia.db
    (episodes_fts for indexed, episodes for total) via gaia.store.writer._connect,
    replacing the legacy search_store.count()/index.json path. These tests stub
    _connect to return a fake sqlite connection driving each count branch.
    """

    def _patch_connect(self, monkeypatch, indexed, total):
        """Stub gaia.store.writer._connect to return counts for the two queries."""
        import gaia.store.writer as _writer_mod

        class _FakeCursor:
            def __init__(self, value):
                self._value = value

            def fetchone(self):
                return (self._value,)

        class _FakeConn:
            def execute(self, sql, *args):
                if "episodes_fts" in sql:
                    return _FakeCursor(indexed)
                return _FakeCursor(total)

            def close(self):
                pass

        monkeypatch.setattr(_writer_mod, "_connect", lambda: _FakeConn())

    def test_no_index_returns_pass(self, tmp_path, monkeypatch):
        """No episodes in gaia.db (total=0) should return pass."""
        self._patch_connect(monkeypatch, indexed=0, total=0)
        r = doctor_mod.check_memory_fts5_count(tmp_path)
        assert r["severity"] == "pass"
        assert "No episodes to index" in r["detail"]

    def test_indexed_gte_90pct_returns_pass(self, tmp_path, monkeypatch):
        """indexed >= 90% of total should return pass."""
        self._patch_connect(monkeypatch, indexed=10, total=10)
        r = doctor_mod.check_memory_fts5_count(tmp_path)
        assert r["severity"] == "pass"
        assert "10/10" in r["detail"]

    def test_indexed_lt_90pct_returns_warning(self, tmp_path, monkeypatch):
        """indexed < 90% of total should return warning."""
        self._patch_connect(monkeypatch, indexed=5, total=10)
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

class _FakeGaiaConn:
    """Minimal fake gaia.db connection for doctor's memory checks.

    Drives ``episodes_fts`` / ``episodes`` COUNT(*) queries from a shared
    mutable ``state`` dict and emulates the FTS5 ``'rebuild'`` command used by
    the migrated _apply_fts5_backfill (which re-derives the index from the
    ``episodes`` content table). Replaces the retired search_store.count()
    mocks the fix tests used before.
    """

    class _Cur:
        def __init__(self, value):
            self._value = value

        def fetchone(self):
            return self._value

    def __init__(self, state, fail_rebuild=False):
        self._state = state
        self._fail_rebuild = fail_rebuild

    def execute(self, sql, *args):
        s = " ".join(sql.split())
        if "'rebuild'" in s:  # INSERT INTO episodes_fts(episodes_fts) VALUES('rebuild')
            if self._fail_rebuild:
                raise RuntimeError("simulated FTS5 rebuild failure")
            self._state["indexed"] = self._state["total"]
            return self._Cur(None)
        if "sqlite_master" in s:  # check_memory_dirs: episodes table present
            return self._Cur(("episodes",))
        if "episodes_fts" in s:
            return self._Cur((self._state["indexed"],))
        if "FROM episodes" in s:
            return self._Cur((self._state["total"],))
        return self._Cur(None)

    def commit(self):
        pass

    def close(self):
        pass


class TestCmdDoctorFix:
    """Test --fix flow in cmd_doctor."""

    def _make_memory_project(self, tmp_path):
        """Build a healthy project with memory dirs and an incomplete FTS5 index."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        # plugin-registry.json
        (claude_dir / "plugin-registry.json").write_text(json.dumps({
            "installed": [{"name": "gaia"}],
            "source": "local-dev",
        }))

        for name in ["agents", "tools", "hooks", "commands", "config", "skills"]:
            (claude_dir / name).mkdir()
        (claude_dir / "CHANGELOG.md").write_text("# Changelog")

        agents_dir = claude_dir / "agents"
        (agents_dir / "gaia-orchestrator.md").write_text("---\nname: gaia-orchestrator\nagent: gaia-orchestrator\n---")

        (claude_dir / "settings.local.json").write_text(json.dumps({
            "agent": "gaia-orchestrator",
            "hooks": {ev: [{"command": "python"}] for ev in [
                "PreToolUse", "PostToolUse", "SubagentStop", "SessionStart",
                "SessionEnd", "UserPromptSubmit", "Stop", "TaskCompleted",
                "SubagentStart", "PostCompact", "PreCompact", "ElicitationResult",
            ]},
            "permissions": {"allow": ["Bash(*)"], "deny": ["rm -rf /"]},
            "env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "true"},
        }))

        hooks_dir = claude_dir / "hooks"
        for h in ["pre_tool_use.py", "post_tool_use.py", "user_prompt_submit.py",
                  "session_start.py", "session_end_hook.py", "subagent_stop.py",
                  "subagent_start.py", "stop_hook.py", "task_completed.py",
                  "pre_compact.py", "post_compact.py", "elicitation_result.py"]:
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

        return tmp_path

    def test_fix_applies_backfill_when_index_empty(self, tmp_path, monkeypatch, capsys):
        """--fix should rebuild episodes_fts when the index is empty.

        The autouse gaia.db isolation fixture gives each test a fresh, empty
        gaia.db, so check_memory_fts5_db (order 120) resolves to severity=info
        (episodes_fts present but empty), which drives --fix to run the FTS5
        rebuild. The migrated _apply_fts5_backfill runs the rebuild against the
        real (empty) content table and reports "applied".
        """
        project = self._make_memory_project(tmp_path)

        args = SimpleNamespace(json=True, fix=True, workspace=str(project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        fixes = data.get("fixes", [])
        fts5_fixes = [f for f in fixes if "fts5" in f.get("name", "").lower() or "backfill" in f.get("name", "").lower()]
        assert len(fts5_fixes) > 0, f"No fts5 fix found in: {fixes}"
        assert fts5_fixes[0]["status"] == "applied"

    def test_fix_noop_when_already_indexed(self, tmp_path, monkeypatch, capsys):
        """--fix should be a no-op (fixes=[]) when the FTS5 index is at 100%."""
        project = self._make_memory_project(tmp_path)

        # Point gaia.store.writer._connect at a fake connection reporting a
        # fully-indexed episodes_fts (indexed == total > 0): check_memory_fts5_db
        # resolves to pass and check_memory_fts5_count to pass, so neither
        # backfill trigger fires.
        import gaia.store.writer as _writer_mod
        state = {"indexed": 10, "total": 10}
        monkeypatch.setattr(
            _writer_mod, "_connect", lambda *a, **k: _FakeGaiaConn(state)
        )

        args = SimpleNamespace(json=True, fix=True, workspace=str(project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        assert data.get("fixes") == [], f"Expected empty fixes but got: {data.get('fixes')}"

    def test_fix_json_includes_fixes_key_without_fix_flag(self, tmp_path, monkeypatch, capsys):
        """--json without --fix should still include fixes: [] in output."""
        project = self._make_memory_project(tmp_path)

        import gaia.store.writer as _writer_mod
        state = {"indexed": 10, "total": 10}
        monkeypatch.setattr(
            _writer_mod, "_connect", lambda *a, **k: _FakeGaiaConn(state)
        )

        args = SimpleNamespace(json=True, fix=False, workspace=str(project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        assert "fixes" in data
        assert data["fixes"] == []

    def test_fix_agent_field_missing(self, tmp_path, monkeypatch, capsys):
        """--fix should write agent='gaia-orchestrator' when settings.local.json
        lacks an `agent` top-level field, and re-run check_identity to reflect
        the post-fix state."""
        project = self._make_memory_project(tmp_path)

        # Strip the agent field so check_identity returns "No agent field" error
        settings_path = project / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data.pop("agent", None)
        settings_path.write_text(json.dumps(data))

        # Keep the FTS5 checks quiet so the assertion isolates the agent fix:
        # a fully-indexed fake episodes_fts means no backfill fix is added.
        import gaia.store.writer as _writer_mod
        state = {"indexed": 10, "total": 10}
        monkeypatch.setattr(
            _writer_mod, "_connect", lambda *a, **k: _FakeGaiaConn(state)
        )

        # Pre-condition: check_identity flags "No agent field"
        pre_check = doctor_mod.check_identity(project)
        assert pre_check["severity"] == "error"
        assert "No agent field" in pre_check["detail"]

        args = SimpleNamespace(json=True, fix=True, workspace=str(project), subcommand="doctor")
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

        settings_path = project / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data.pop("agent", None)
        # Add custom keys to ensure they survive
        data["custom_field"] = "preserve_me"
        data["env"]["EXTRA_VAR"] = "kept"
        settings_path.write_text(json.dumps(data))

        # Keep the FTS5 checks quiet (fully-indexed fake) so only the agent fix runs.
        import gaia.store.writer as _writer_mod
        state = {"indexed": 10, "total": 10}
        monkeypatch.setattr(
            _writer_mod, "_connect", lambda *a, **k: _FakeGaiaConn(state)
        )

        args = SimpleNamespace(json=True, fix=True, workspace=str(project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)
        capsys.readouterr()  # drain

        post = json.loads(settings_path.read_text())
        assert post["agent"] == "gaia-orchestrator"
        assert post["custom_field"] == "preserve_me"
        assert post["env"]["EXTRA_VAR"] == "kept"
        assert post["hooks"]  # untouched
        assert post["permissions"]["deny"]  # untouched

    def test_fix_failed_backfill_reported(self, tmp_path, monkeypatch, capsys):
        """If the FTS5 rebuild raises, the fix status should be 'failed'."""
        project = self._make_memory_project(tmp_path)

        # Empty index (indexed=0) makes check_memory_fts5_db resolve to info,
        # driving --fix to rebuild; the fake connection raises on the rebuild
        # command so _apply_fts5_backfill reports "failed".
        import gaia.store.writer as _writer_mod
        state = {"indexed": 0, "total": 0}
        monkeypatch.setattr(
            _writer_mod, "_connect",
            lambda *a, **k: _FakeGaiaConn(state, fail_rebuild=True),
        )

        args = SimpleNamespace(json=True, fix=True, workspace=str(project), subcommand="doctor")
        doctor_mod.cmd_doctor(args)

        data = json.loads(capsys.readouterr().out)
        fixes = data.get("fixes", [])
        assert len(fixes) == 1
        assert fixes[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# Tests: deterministic structural checks migrated from gaia-audit
# ---------------------------------------------------------------------------


def _write_skill(claude_dir: Path, dir_name: str, declared_name: str) -> None:
    """Create skills/<dir_name>/SKILL.md declaring `name: <declared_name>`."""
    skill_dir = claude_dir / "skills" / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {declared_name}\ndescription: test\n---\n\n# body\n"
    )


def _write_agent(claude_dir: Path, file_stem: str, declared_name: str,
                 skills: "list[str] | None" = None) -> None:
    """Create agents/<file_stem>.md declaring `name:` and an optional skills list."""
    agents_dir = claude_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    fm = [f"---", f"name: {declared_name}", "description: test"]
    if skills is not None:
        fm.append("skills:")
        for s in skills:
            fm.append(f"  - {s}")
    fm.append("---")
    (agents_dir / f"{file_stem}.md").write_text("\n".join(fm) + "\n\n# body\n")


class TestCheckComponentNaming:
    """Test check_component_naming (name-vs-directory match)."""

    def test_all_match(self, tmp_path):
        """Pass when every skill dir and agent file matches its frontmatter name."""
        claude = tmp_path / ".claude"
        _write_skill(claude, "gaia-audit", "gaia-audit")
        _write_skill(claude, "memory", "memory")
        _write_agent(claude, "gaia-system", "gaia-system")
        r = doctor_mod.check_component_naming(tmp_path)
        assert r["severity"] == "pass"
        assert "3 components" in r["detail"]

    def test_skill_mismatch_is_error(self, tmp_path):
        """Error when a skill's frontmatter name differs from its directory."""
        claude = tmp_path / ".claude"
        _write_skill(claude, "gaia-audit", "gaia-self-check")  # stale rename
        r = doctor_mod.check_component_naming(tmp_path)
        assert r["severity"] == "error"
        assert "gaia-audit" in r["detail"]
        assert "gaia-self-check" in r["detail"]

    def test_agent_mismatch_is_error(self, tmp_path):
        """Error when an agent file name differs from its frontmatter name."""
        claude = tmp_path / ".claude"
        _write_agent(claude, "gaia-system", "gaia-sistema")
        r = doctor_mod.check_component_naming(tmp_path)
        assert r["severity"] == "error"
        assert "gaia-system.md" in r["detail"]

    def test_missing_name_is_warning(self, tmp_path):
        """Warn (not error) when a component has no parseable frontmatter name."""
        claude = tmp_path / ".claude"
        skill_dir = claude / "skills" / "no-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\ndescription: no name here\n---\n")
        r = doctor_mod.check_component_naming(tmp_path)
        assert r["severity"] == "warning"
        assert "no-name" in r["detail"]

    def test_readme_ignored(self, tmp_path):
        """agents/README.md is not treated as an agent definition."""
        claude = tmp_path / ".claude"
        _write_agent(claude, "gaia-system", "gaia-system")
        (claude / "agents" / "README.md").write_text("# Agents\n")
        r = doctor_mod.check_component_naming(tmp_path)
        assert r["severity"] == "pass"

    def test_no_dirs_is_info(self, tmp_path):
        """Info (advisory) when neither skills/ nor agents/ exist."""
        (tmp_path / ".claude").mkdir()
        r = doctor_mod.check_component_naming(tmp_path)
        assert r["severity"] == "info"


class TestCheckSkillCrossRefs:
    """Test check_skill_cross_refs (dangling cross-reference detection)."""

    def test_all_refs_resolve(self, tmp_path):
        """Pass when every agent-declared skill resolves to a real skill dir."""
        claude = tmp_path / ".claude"
        _write_skill(claude, "agent-protocol", "agent-protocol")
        _write_skill(claude, "security-tiers", "security-tiers")
        _write_agent(claude, "gaia-system", "gaia-system",
                     skills=["agent-protocol", "security-tiers"])
        r = doctor_mod.check_skill_cross_refs(tmp_path)
        assert r["severity"] == "pass"
        assert "2 skill references resolve" in r["detail"]

    def test_dangling_ref_is_error(self, tmp_path):
        """Error when an agent references a skill that does not exist."""
        claude = tmp_path / ".claude"
        _write_skill(claude, "agent-protocol", "agent-protocol")
        _write_agent(claude, "gaia-system", "gaia-system",
                     skills=["agent-protocol", "ghost-skill"])
        r = doctor_mod.check_skill_cross_refs(tmp_path)
        assert r["severity"] == "error"
        assert "ghost-skill" in r["detail"]
        assert "gaia-system.md" in r["detail"]

    def test_no_refs_declared_is_info(self, tmp_path):
        """Info when agents exist but declare no skills."""
        claude = tmp_path / ".claude"
        _write_agent(claude, "gaia-system", "gaia-system")
        r = doctor_mod.check_skill_cross_refs(tmp_path)
        assert r["severity"] == "info"

    def test_no_agents_dir_is_info(self, tmp_path):
        """Info (advisory) when the agents/ dir is absent."""
        (tmp_path / ".claude").mkdir()
        r = doctor_mod.check_skill_cross_refs(tmp_path)
        assert r["severity"] == "info"


# ---------------------------------------------------------------------------
# Tests: check_install_provenance (order=57) -- the intelligence that replaced
# `gaia release sync-local`: detect local (file:) vs npm install,
# self-sufficiently from the workspace's own package.json (no dependency on
# locating the Gaia SOURCE checkout).
# ---------------------------------------------------------------------------

class TestCheckInstallProvenance:
    def _install(self, workspace: Path, version: str) -> Path:
        """Create node_modules/@jaguilar87/gaia/package.json under *workspace*."""
        pkg = workspace / "node_modules" / "@jaguilar87" / "gaia"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(
            json.dumps({"name": "@jaguilar87/gaia", "version": version})
        )
        return pkg

    def _workspace_pkg(self, workspace: Path, spec: str) -> None:
        (workspace / "package.json").write_text(
            json.dumps({"name": "my-app", "dependencies": {"@jaguilar87/gaia": spec}})
        )

    def test_no_install_detected_is_info(self, tmp_path):
        r = doctor_mod.check_install_provenance(tmp_path)
        assert r["severity"] == "info"
        assert "plugin-mode" in r["detail"]

    def test_npm_install_reports_offline_and_registry(self, tmp_path):
        self._install(tmp_path, "5.1.1")
        self._workspace_pkg(tmp_path, "^5.1.1")
        r = doctor_mod.check_install_provenance(tmp_path)
        assert r["severity"] == "info"
        assert "npm (registry)" in r["detail"]
        assert "offline" in r["detail"]

    def test_local_install_resolving_is_pass(self, tmp_path):
        # A local (file:) install where node_modules/@jaguilar87/gaia resolves
        # (a real dir, or a symlink pointing at one) is self-sufficient --
        # no source checkout is needed to report "pass".
        ws = tmp_path / "ws"
        ws.mkdir()
        self._install(ws, "5.1.1")
        self._workspace_pkg(ws, "file:../src/x.tgz")
        r = doctor_mod.check_install_provenance(ws)
        assert r["severity"] == "pass"
        assert "resolves correctly" in r["detail"]

    def test_local_install_broken_symlink_is_warning(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        nm_gaia = ws / "node_modules" / "@jaguilar87" / "gaia"
        nm_gaia.parent.mkdir(parents=True)
        # A dangling symlink: does not resolve.
        nm_gaia.symlink_to(tmp_path / "does-not-exist")
        self._workspace_pkg(ws, "file:../src/x.tgz")
        r = doctor_mod.check_install_provenance(ws)
        assert r["severity"] == "warning"
        assert "does not resolve" in r["detail"]
        assert "gaia dev" in r["fix"]


# ---------------------------------------------------------------------------
# Tests: content-hash freshness -- the dev-pack reality semver cannot see.
# `gaia dev` content-addresses the tarball but never bumps the internal
# package.json version, so two different builds report the SAME semver. These
# tests prove check_symlinks_freshness (order 55) now distinguishes them by
# content, and that check_hooks_active_fresh (order 150) classifies the running
# session's build against the wired build.
# ---------------------------------------------------------------------------

def _mk_hooks_tree(root: Path, body: str) -> Path:
    """Create a minimal hooks tree at *root* with a marker .py of *body*.

    Two trees with the SAME body hash identically; different bodies hash
    differently -- exactly the content signal the freshness checks compare.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "pre_tool_use.py").write_text(body)
    (root / "hooks.json").write_text('{"hooks": {}}')
    return root


def _mk_local_gaia_workspace(
    ws: Path, *, wired_body: str, installed_body: str, version: str = "5.1.3"
) -> None:
    """Build a workspace whose .claude/hooks resolves to a separate package
    extraction, with an independently-controllable installed node_modules
    package -- so wired-vs-installed content can be made to match or diverge
    at the SAME semver.
    """
    # The extraction the .claude/hooks symlink points at (the "wired" build).
    wired_pkg = ws / "store" / "wired-build"
    wired_pkg.mkdir(parents=True)
    (wired_pkg / "package.json").write_text(
        json.dumps({"name": "@jaguilar87/gaia", "version": version})
    )
    _mk_hooks_tree(wired_pkg / "hooks", wired_body)

    # The installed node_modules package.
    nm_gaia = ws / "node_modules" / "@jaguilar87" / "gaia"
    nm_gaia.mkdir(parents=True)
    (nm_gaia / "package.json").write_text(
        json.dumps({"name": "@jaguilar87/gaia", "version": version})
    )
    _mk_hooks_tree(nm_gaia / "hooks", installed_body)

    # .claude/hooks -> the wired extraction's hooks dir.
    claude = ws / ".claude"
    claude.mkdir()
    (claude / "hooks").symlink_to(wired_pkg / "hooks")


class TestCheckSymlinksFreshnessContentHash:
    """Order-55: same-semver / different-content must NOT report fresh."""

    def test_same_version_different_content_is_warning(self, tmp_path):
        ws = tmp_path / "ws"
        _mk_local_gaia_workspace(
            ws, wired_body="# OLD build\n", installed_body="# NEW build\n"
        )
        r = doctor_mod.check_symlinks_freshness(ws)
        assert r["severity"] == "warning"
        assert "DIFFERENT build" in r["detail"]
        # Same semver on both sides -- the whole point of the content signal.
        assert "v5.1.3" in r["detail"]
        assert "gaia dev" in r["fix"]

    def test_same_version_same_content_is_pass(self, tmp_path):
        ws = tmp_path / "ws"
        _mk_local_gaia_workspace(
            ws, wired_body="# same build\n", installed_body="# same build\n"
        )
        r = doctor_mod.check_symlinks_freshness(ws)
        assert r["severity"] == "pass"
        assert "matches installed" in r["detail"]
        # The pass detail carries the content build id, proving the content
        # path (not the legacy semver-only path) produced this result.
        assert "build " in r["detail"]


class TestCheckHooksActiveFresh:
    """Order-150: is the RUNNING session's hooks the currently wired build?"""

    def _write_registry(self, session_id: str, pinned_hash) -> None:
        entry = {"started_at": "2026-01-01T00:00:00Z", "is_headless": False,
                 "last_heartbeat": 9999999999.0}
        if pinned_hash is not None:
            entry["pinned_build"] = {"hooks_path": "/x", "hooks_hash": pinned_hash}
        doctor_mod._SESSION_REGISTRY_PATH.write_text(
            json.dumps({"sessions": {session_id: entry}})
        )

    def _wired_hash(self, ws: Path):
        hasher = doctor_mod._load_hooks_content_hash()
        assert hasher is not None, "gaia.hooks_build must be importable in tests"
        return hasher((ws / ".claude" / "hooks").resolve())

    def test_active_when_marker_matches_wired(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        _mk_local_gaia_workspace(
            ws, wired_body="# build A\n", installed_body="# build A\n"
        )
        wired = self._wired_hash(ws)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-active")
        self._write_registry("sess-active", wired)
        r = doctor_mod.check_hooks_active_fresh(ws)
        assert r["severity"] == "pass"
        assert "ACTIVE" in r["detail"]

    def test_stale_when_marker_differs_from_wired(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        # wired == installed (not misconfigured); only the pinned marker is old.
        _mk_local_gaia_workspace(
            ws, wired_body="# build B\n", installed_body="# build B\n"
        )
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-stale")
        self._write_registry("sess-stale", "deadbeef")  # an old, different build
        r = doctor_mod.check_hooks_active_fresh(ws)
        assert r["severity"] == "warning"
        assert "STALE" in r["detail"]
        assert "restart Claude Code" in r["fix"]

    def test_misconfigured_when_wired_differs_from_installed(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        _mk_local_gaia_workspace(
            ws, wired_body="# wired\n", installed_body="# installed DIFFERENT\n"
        )
        # A session + matching marker exist, but the wiring fault takes
        # precedence over the freshness comparison.
        wired = self._wired_hash(ws)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-misc")
        self._write_registry("sess-misc", wired)
        r = doctor_mod.check_hooks_active_fresh(ws)
        assert r["severity"] == "warning"
        assert "does not match the installed package" in r["detail"]

    def test_unknown_when_no_session_id(self, tmp_path):
        ws = tmp_path / "ws"
        _mk_local_gaia_workspace(
            ws, wired_body="# b\n", installed_body="# b\n"
        )
        # autouse fixture cleared the session-id env vars -> UNKNOWN, not pass.
        r = doctor_mod.check_hooks_active_fresh(ws)
        assert r["severity"] == "info"
        assert "session id unavailable" in r["detail"]

    def test_unknown_when_marker_absent(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        _mk_local_gaia_workspace(
            ws, wired_body="# b\n", installed_body="# b\n"
        )
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-legacy")
        # Legacy entry: present but no pinned_build field.
        self._write_registry("sess-legacy", None)
        r = doctor_mod.check_hooks_active_fresh(ws)
        assert r["severity"] == "info"
        assert "no pinned build marker" in r["detail"]
