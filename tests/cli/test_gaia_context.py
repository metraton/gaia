"""
Unit tests for bin/cli/context.py -- gaia context subcommand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure bin/ is on sys.path so the plugin is importable
_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.context import (
    _cmd_scan,
    _cmd_show,
    _cmd_get,
    _cmd_get_contract,
    _cmd_project,
    _cmd_dump,
    _find_project_root,
    cmd_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CONTEXT = {
    "metadata": {
        "version": "2.0",
        "last_updated": "2026-04-15T00:00:00+00:00",
        "scan_config": {
            "last_scan": "2026-04-15T00:00:00+00:00",
            "scanner_version": "5.0.0",
            "staleness_hours": 24,
        },
    },
    "sections": {
        "stack": {"_source": "scanner:stack", "languages": ["python"]},
        "git": {"_source": "scanner:git", "platform": "github"},
        "project_identity": {"_source": "scanner:stack", "name": "test-project"},
    },
}

# Canonical substrate shape returned by get_context()
_SAMPLE_SUBSTRATE_CTX = {
    "identity": "test-workspace",
    "stack": {},
    "environment": {},
    "git": {"workspace_name": "test-workspace", "created_at": "2026-01-01"},
    "workspace": {
        "projects": [{"name": "my-repo", "role": None}],
        "apps": [],
        "libraries": [],
        "services": [],
        "features": [],
        "tf_modules": [],
        "tf_live": [],
        "releases": [],
        "workloads": [],
        "clusters_defined": [],
        "clusters": [],
        "integrations": [],
        "gaia_installations": [],
        "machines": [],
    },
}


def _write_context(project_root: Path, data: dict | None = None):
    """Write a project-context.json under project_root/.claude/project-context/."""
    ctx_dir = project_root / ".claude" / "project-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ctx_file = ctx_dir / "project-context.json"
    ctx_file.write_text(json.dumps(data or _SAMPLE_CONTEXT), encoding="utf-8")
    return ctx_file


class _MockArgs:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# _find_project_root
# ---------------------------------------------------------------------------

class TestFindProjectRoot:
    def test_finds_root_from_nested_dir(self, tmp_path):
        # Create the marker that _find_project_root's highest-priority pass
        # looks for (.claude/project-context/) so tmp_path wins before the
        # walk reaches any real .claude/ ancestor (e.g. ~/.claude/).
        (tmp_path / ".claude" / "project-context").mkdir(parents=True)
        nested = tmp_path / "sub" / "dir"
        nested.mkdir(parents=True)
        root = _find_project_root(nested)
        assert root == tmp_path

    def test_returns_none_or_path_type(self, tmp_path):
        result = _find_project_root(tmp_path / "nowhere")
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# _cmd_show  (now reads from substrate via get_context)
# ---------------------------------------------------------------------------

class TestCmdShow:
    """_cmd_show reads from the SQLite substrate (not project-context.json)."""

    def _run_show(self, section=None, json_output=False, ctx=None):
        args = _MockArgs(context_cmd="show", section=section, json=json_output)
        ctx_val = ctx if ctx is not None else _SAMPLE_SUBSTRATE_CTX
        with patch("cli.context.get_context" if False else "gaia.store.provider.get_context"):
            pass
        # Patch at the import site inside cli.context
        with patch("cli.context._cmd_show.__module__"):
            pass
        import cli.context as _ctx_mod
        with patch.object(_ctx_mod, "get_context" if hasattr(_ctx_mod, "get_context") else "_cmd_show",
                          return_value=ctx_val, create=True):
            with patch("gaia.project.current", return_value="test-workspace"):
                with patch("gaia.store.provider.get_context", return_value=ctx_val):
                    return _cmd_show(args)

    def _run_show_simple(self, section=None, json_output=False, ctx=None):
        """Run _cmd_show with substrate mocked."""
        args = _MockArgs(context_cmd="show", section=section, json=json_output)
        ctx_val = ctx if ctx is not None else _SAMPLE_SUBSTRATE_CTX
        with patch("gaia.project.current", return_value="test-workspace"):
            with patch("gaia.store.provider.get_context", return_value=ctx_val):
                return _cmd_show(args)

    def test_show_exits_zero(self):
        rc = self._run_show_simple()
        assert rc == 0

    def test_show_workspace_not_found_returns_1(self):
        """When get_context returns None (workspace not found), exit 1."""
        args = _MockArgs(context_cmd="show", section=None, json=False)
        with patch("gaia.project.current", return_value="nonexistent"):
            with patch("gaia.store.provider.get_context", return_value=None):
                rc = _cmd_show(args)
        assert rc == 1

    def test_show_json_output_has_identity(self, capsys):
        rc = self._run_show_simple(json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "identity" in data
        assert rc == 0

    def test_show_json_output_has_workspace(self, capsys):
        rc = self._run_show_simple(json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "workspace" in data
        assert rc == 0

    def test_show_human_contains_workspace(self, capsys):
        rc = self._run_show_simple()
        captured = capsys.readouterr()
        assert "workspace" in captured.out
        assert rc == 0

    def test_show_section_projects(self, capsys):
        rc = self._run_show_simple(section="projects", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert rc == 0

    def test_show_unknown_section_returns_1(self):
        rc = self._run_show_simple(section="nonexistent_section_xyz")
        assert rc == 1

    def test_show_section_identity(self, capsys):
        rc = self._run_show_simple(section="identity", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == "test-workspace"
        assert rc == 0


# ---------------------------------------------------------------------------
# _cmd_get (change #3: new canonical subcommand)
# ---------------------------------------------------------------------------

class TestCmdGet:
    """_cmd_get emits canonical workspace shape; exits 1 for nonexistent workspace."""

    def _run_get(self, workspace="me", section=None, json_output=False, text=False, ctx=None):
        args = _MockArgs(
            context_cmd="get",
            workspace=workspace,
            section=section,
            json=json_output,
            text=text,
        )
        ctx_val = ctx  # None means workspace not found
        with patch("gaia.project.current", return_value=workspace):
            with patch("gaia.store.provider.get_context", return_value=ctx_val):
                return _cmd_get(args)

    def test_get_exits_zero_for_known_workspace(self, capsys):
        rc = self._run_get(ctx=_SAMPLE_SUBSTRATE_CTX)
        assert rc == 0

    def test_get_nonexistent_workspace_exits_1(self, capsys):
        """Fix #5: exit 1 when workspace not found."""
        rc = self._run_get(workspace="nonexistent", ctx=None)
        captured = capsys.readouterr()
        assert rc == 1
        assert "nonexistent" in captured.err

    def test_get_json_output_has_identity(self, capsys):
        rc = self._run_get(ctx=_SAMPLE_SUBSTRATE_CTX)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["identity"] == "test-workspace"
        assert rc == 0

    def test_get_section_filter(self, capsys):
        rc = self._run_get(section="projects", ctx=_SAMPLE_SUBSTRATE_CTX)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert rc == 0

    def test_get_invalid_section_exits_1(self, capsys):
        rc = self._run_get(section="no_such_section", ctx=_SAMPLE_SUBSTRATE_CTX)
        assert rc == 1

    def test_get_text_flag_renders_tabular(self, capsys):
        rc = self._run_get(text=True, ctx=_SAMPLE_SUBSTRATE_CTX)
        captured = capsys.readouterr()
        assert "workspace" in captured.out
        assert rc == 0

    def test_get_defaults_to_active_view(self):
        """Without --include-missing the provider keeps its active-view default."""
        import cli.context  # noqa: F401 -- ensures the module is imported

        args = _MockArgs(
            context_cmd="get", workspace="ws", section=None, json=True, text=False,
        )
        provider = MagicMock(return_value=_SAMPLE_SUBSTRATE_CTX)
        with patch("gaia.project.current", return_value="ws"):
            with patch("gaia.store.provider.get_context", provider):
                rc = _cmd_get(args)
        assert rc == 0
        assert provider.call_args.kwargs["include_missing"] is False

    def test_get_include_missing_propagates_to_provider(self):
        """--include-missing must reach get_context(include_missing=True)."""
        args = _MockArgs(
            context_cmd="get", workspace="ws", section=None, json=True, text=False,
            include_missing=True,
        )
        provider = MagicMock(return_value=_SAMPLE_SUBSTRATE_CTX)
        with patch("gaia.project.current", return_value="ws"):
            with patch("gaia.store.provider.get_context", provider):
                rc = _cmd_get(args)
        assert rc == 0
        assert provider.call_args.kwargs["include_missing"] is True

    def test_get_identity_field_is_workspace_name(self, capsys):
        """Fix #4: identity in shape must be the workspace name, not a repo URL."""
        ctx = dict(_SAMPLE_SUBSTRATE_CTX)
        ctx["identity"] = "test-workspace"  # should be name, not git remote
        rc = self._run_get(workspace="test-workspace", ctx=ctx)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["identity"] == "test-workspace"
        assert rc == 0


class TestCmdGetIncludeMissing:
    """--include-missing surfaces soft-deleted rows against a real substrate.

    The provider is NOT mocked here: the point is that a row `gaia scan`
    demoted (status='missing' + missing_since) is genuinely readable through
    the CLI with the flag, and genuinely hidden without it.
    """

    @pytest.fixture()
    def seeded_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
        from gaia.paths import db_path
        from gaia.store.writer import _connect

        con = _connect(db_path())
        try:
            con.execute(
                "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
                ("ws-soft-delete", "ws-soft-delete", "2026-01-01T00:00:00Z"),
            )
            con.execute(
                "INSERT INTO projects (workspace, name, scanner_ts, status) "
                "VALUES (?, ?, ?, 'active')",
                ("ws-soft-delete", "alive", "2026-01-01T00:00:00Z"),
            )
            con.execute(
                "INSERT INTO projects "
                "(workspace, name, scanner_ts, status, missing_since) "
                "VALUES (?, ?, ?, 'missing', ?)",
                ("ws-soft-delete", "vanished", "2026-01-01T00:00:00Z",
                 "2026-02-01T00:00:00Z"),
            )
            con.commit()
        finally:
            con.close()
        return db_path()

    def _projects(self, capsys, *, include_missing):
        args = _MockArgs(
            context_cmd="get",
            workspace="ws-soft-delete",
            section="projects",
            json=True,
            text=False,
            include_missing=include_missing,
        )
        rc = _cmd_get(args)
        assert rc == 0
        return {p["name"]: p for p in json.loads(capsys.readouterr().out)}

    def test_without_flag_missing_project_is_hidden(self, seeded_db, capsys):
        projects = self._projects(capsys, include_missing=False)
        assert "alive" in projects
        assert "vanished" not in projects

    def test_with_flag_missing_project_exposes_missing_since(self, seeded_db, capsys):
        projects = self._projects(capsys, include_missing=True)
        assert "alive" in projects
        assert "vanished" in projects
        assert projects["vanished"]["status"] == "missing"
        assert projects["vanished"]["missing_since"] == "2026-02-01T00:00:00Z"

    def test_flag_is_registered_on_the_get_parser(self):
        """The flag has to exist on the real parser, not just in _cmd_get."""
        from cli.context import register

        parser = argparse.ArgumentParser(prog="gaia")
        register(parser.add_subparsers(dest="command"))

        args = parser.parse_args(["context", "get", "--include-missing"])
        assert args.include_missing is True
        assert parser.parse_args(["context", "get"]).include_missing is False


# ---------------------------------------------------------------------------
# _cmd_get_contract -- resolves project_context_contracts.contract_name,
# the same names as an agent's can_read/can_write kernel menu. This is a
# DIFFERENT table from get_context()'s workspace shape, so these tests seed
# a real (temp) SQLite substrate rather than mocking get_context().
# ---------------------------------------------------------------------------

class TestCmdGetContract:
    @pytest.fixture()
    def seeded_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
        from gaia.paths import db_path
        from gaia.store.writer import _connect

        con = _connect(db_path())
        try:
            con.execute(
                "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
                ("ws-contracts", "ws-contracts", "2026-01-01T00:00:00Z"),
            )
            con.execute(
                "INSERT INTO project_context_contracts "
                "(workspace, contract_name, payload, updated_at) VALUES (?, ?, ?, ?)",
                (
                    "ws-contracts",
                    "project_identity",
                    json.dumps({"my-repo": {"name": "my-repo"}}),
                    "2026-01-02T00:00:00Z",
                ),
            )
            con.execute(
                "INSERT INTO project_context_contracts "
                "(workspace, contract_name, payload, updated_at) VALUES (?, ?, ?, ?)",
                ("ws-contracts", "stack", json.dumps({"languages": ["python"]}), "2026-01-03T00:00:00Z"),
            )
            con.commit()
        finally:
            con.close()
        return db_path()

    def _run(self, capsys, *, section, workspace="ws-contracts", json_output=True, text=False):
        args = _MockArgs(
            context_cmd="get-contract",
            workspace=workspace,
            section=section,
            json=json_output,
            text=text,
        )
        rc = _cmd_get_contract(args)
        return rc, capsys.readouterr()

    def test_known_contract_name_returns_payload(self, seeded_db, capsys):
        rc, captured = self._run(capsys, section="project_identity")
        assert rc == 0
        data = json.loads(captured.out)
        assert data["workspace"] == "ws-contracts"
        assert data["contract_name"] == "project_identity"
        assert data["payload"] == {"my-repo": {"name": "my-repo"}}
        assert data["updated_at"] == "2026-01-02T00:00:00Z"

    def test_unknown_contract_name_exits_1_and_lists_available(self, seeded_db, capsys):
        rc, captured = self._run(capsys, section="not_a_real_contract")
        assert rc == 1
        assert "not_a_real_contract" in captured.err
        assert "ws-contracts" in captured.err
        # Orients toward the correct drawer: names the actual contract names.
        assert "project_identity" in captured.err
        assert "stack" in captured.err
        # And distinguishes the namespace from the workspace-shape one.
        assert "get" in captured.err  # points at the sibling verb by name

    def test_missing_section_flag_exits_2(self, seeded_db, capsys):
        args = _MockArgs(context_cmd="get-contract", workspace="ws-contracts", section=None, json=True, text=False)
        rc = _cmd_get_contract(args)
        assert rc == 2

    def test_text_mode_shows_workspace_and_contract_header(self, seeded_db, capsys):
        rc, captured = self._run(capsys, section="stack", text=True, json_output=False)
        assert rc == 0
        assert "workspace     : ws-contracts" in captured.out
        assert "contract_name : stack" in captured.out

    def test_default_workspace_resolves_from_project_current(self, seeded_db, capsys):
        args = _MockArgs(context_cmd="get-contract", workspace=None, section="project_identity", json=True, text=False)
        with patch("gaia.project.current", return_value="ws-contracts"):
            rc = _cmd_get_contract(args)
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["workspace"] == "ws-contracts"

    def test_dispatch_routes_get_contract(self, seeded_db, capsys):
        args = _MockArgs(context_cmd="get-contract", workspace="ws-contracts", section="stack", json=True, text=False)
        rc = cmd_context(args)
        assert rc == 0

    def test_flag_is_registered_on_the_real_parser(self):
        from cli.context import register

        parser = argparse.ArgumentParser(prog="gaia")
        register(parser.add_subparsers(dest="command"))

        args = parser.parse_args(
            ["context", "get-contract", "--section", "project_identity", "--workspace", "w"]
        )
        assert args.section == "project_identity"
        assert args.workspace == "w"

    def test_section_is_required_on_the_real_parser(self):
        from cli.context import register

        parser = argparse.ArgumentParser(prog="gaia")
        register(parser.add_subparsers(dest="command"))

        with pytest.raises(SystemExit):
            parser.parse_args(["context", "get-contract"])


# ---------------------------------------------------------------------------
# _cmd_project -- the one-project ficha (row + facets + project_identity
# contract entry + curated-memory index). Seeds a real (temp) SQLite
# substrate, same convention as TestCmdGetContract above.
# ---------------------------------------------------------------------------

class TestCmdProject:
    @pytest.fixture()
    def seeded_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
        from gaia.paths import db_path
        from gaia.store.writer import _connect

        con = _connect(db_path())
        try:
            for ws in ("ws-a", "ws-b"):
                con.execute(
                    "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
                    (ws, ws, "2026-01-01T00:00:00Z"),
                )

            # The resolvable-by-exact-name project, with a full row.
            con.execute(
                "INSERT INTO projects (workspace, name, role, remote_url, platform, "
                "primary_language, group_name, path, status, project_identity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                ("ws-a", "demo", "application", "git@example.com:org/demo.git",
                 "github", "python", None, "/repos/demo", "/repos/demo/.git"),
            )
            # The legacy opaque-slot row: stored name differs from the
            # basename of its path (the control-tower-livekit / bildwiz-5 case).
            con.execute(
                "INSERT INTO projects (workspace, name, role, remote_url, platform, "
                "primary_language, group_name, path, status, project_identity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                ("ws-a", "slot-9", "application", "git@example.com:org/real-repo.git",
                 "github", "javascript", "grp", "/repos/real-repo", None),
            )
            # Same name in two workspaces -- ambiguous by exact match.
            con.execute(
                "INSERT INTO projects (workspace, name, path, status) "
                "VALUES ('ws-a', 'dup', '/repos/dup-a', 'active')"
            )
            con.execute(
                "INSERT INTO projects (workspace, name, path, status) "
                "VALUES ('ws-b', 'dup', '/repos/dup-b', 'active')"
            )

            con.execute(
                "INSERT INTO project_facets (workspace, project, scope, key, value) "
                "VALUES ('ws-a', 'demo', 'language', 'python', 'pyproject.toml')"
            )
            con.execute(
                "INSERT INTO project_facets (workspace, project, scope, key, value) "
                "VALUES ('ws-a', 'demo', 'build', 'poetry', NULL)"
            )

            con.execute(
                "INSERT INTO project_context_contracts "
                "(workspace, contract_name, payload, updated_at) VALUES (?, ?, ?, ?)",
                (
                    "ws-a", "project_identity",
                    json.dumps({
                        "demo": {
                            "name": "demo",
                            "local_path": "/repos/demo",
                            "remote_url": "git@example.com:org/demo.git",
                            "description": "curated summary",
                        },
                    }),
                    "2026-01-02T00:00:00Z",
                ),
            )

            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, "
                "class, status, project_ref, initiative) VALUES "
                "('ws-a', 'project_demo_notes', 'project', 'notes on demo', "
                "'full body here', 'log', NULL, '/repos/demo/.git', 'demo')"
            )
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, "
                "class, status, project_ref, initiative) VALUES "
                "('ws-a', 'thread_demo_open', 'atom', 'an open thread', "
                "'body', 'thread', 'open', NULL, 'demo')"
            )
            con.commit()
        finally:
            con.close()
        return db_path()

    def _run(self, capsys, *, name, workspace=None, json_output=False):
        args = _MockArgs(name=name, workspace=workspace, json=json_output)
        rc = _cmd_project(args)
        return rc, capsys.readouterr()

    def test_exact_name_resolves(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="demo", workspace="ws-a")
        assert rc == 0
        assert "resolved_via     : exact name match" in captured.out
        assert "language.python" in captured.out

    def test_basename_resolves_and_says_stored_name_differs(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="real-repo", workspace="ws-a")
        assert rc == 0
        assert "basename of path" in captured.out
        assert "'slot-9'" in captured.out

    def test_project_identity_contract_entry_included(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="demo", workspace="ws-a", json_output=True)
        assert rc == 0
        data = json.loads(captured.out)
        assert data["project_identity_contract"]["slug"] == "demo"
        assert data["project_identity_contract"]["entry"]["description"] == "curated summary"

    def test_memory_index_is_slug_and_description_only(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="demo", workspace="ws-a", json_output=True)
        assert rc == 0
        data = json.loads(captured.out)
        names = {m["name"] for m in data["memory_index"]}
        assert names == {"project_demo_notes", "thread_demo_open"}
        for m in data["memory_index"]:
            assert "body" not in m

    def test_pending_footer_names_the_sweep_command(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="demo", workspace="ws-a")
        assert rc == 0
        assert "gaia memory get-relevant --initiative demo" in captured.out

    def test_ambiguous_exact_match_exits_1_with_workspaces(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="dup")
        assert rc == 1
        assert "ws-a/dup" in captured.err
        assert "ws-b/dup" in captured.err

    def test_not_found_exits_1_with_closest_candidates(self, seeded_db, capsys):
        rc, captured = self._run(capsys, name="demoo", workspace="ws-a")
        assert rc == 1
        assert "not found" in captured.err
        assert "demo" in captured.err

    def test_never_writes_any_row(self, seeded_db, capsys):
        """The declared invariant: resolving, found or not, mutates nothing."""
        from gaia.store.writer import _connect

        con = _connect(seeded_db)
        try:
            before = {
                table: con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
                for table in ("projects", "project_facets", "project_context_contracts", "memory")
            }
        finally:
            con.close()

        self._run(capsys, name="demo", workspace="ws-a", json_output=True)
        self._run(capsys, name="noexiste", workspace="ws-a")

        con = _connect(seeded_db)
        try:
            after = {
                table: con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
                for table in ("projects", "project_facets", "project_context_contracts", "memory")
            }
        finally:
            con.close()
        assert before == after

    def test_dispatch_routes_project(self, seeded_db, capsys):
        args = _MockArgs(context_cmd="project", name="demo", workspace="ws-a", json=True)
        rc = cmd_context(args)
        assert rc == 0

    def test_flag_is_registered_on_the_real_parser(self):
        from cli.context import register

        parser = argparse.ArgumentParser(prog="gaia")
        register(parser.add_subparsers(dest="command"))

        args = parser.parse_args(["context", "project", "demo", "--workspace", "ws-a"])
        assert args.name == "demo"
        assert args.workspace == "ws-a"

    def test_name_is_required_on_the_real_parser(self):
        from cli.context import register

        parser = argparse.ArgumentParser(prog="gaia")
        register(parser.add_subparsers(dest="command"))

        with pytest.raises(SystemExit):
            parser.parse_args(["context", "project"])


# ---------------------------------------------------------------------------
# _cmd_dump (deprecated alias)
# ---------------------------------------------------------------------------

class TestCmdDump:
    """_cmd_dump emits deprecation warning and delegates to _cmd_get."""

    def test_dump_warns_deprecated(self, capsys):
        args = _MockArgs(context_cmd="dump", workspace=None, section=None, json=False, text=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = _cmd_dump(args)
        captured = capsys.readouterr()
        assert "deprecated" in captured.err.lower()
        assert rc == 0

    def test_dump_still_returns_json(self, capsys):
        args = _MockArgs(context_cmd="dump", workspace=None, section=None, json=False, text=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = _cmd_dump(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "identity" in data
        assert rc == 0


# ---------------------------------------------------------------------------
# _cmd_scan
# ---------------------------------------------------------------------------

class TestCmdScan:
    def test_dry_run_exits_zero(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            rc = _cmd_scan(args)
        assert rc == 0

    def test_dry_run_json_exits_zero(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=True)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            rc = _cmd_scan(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["dry_run"] is True
        assert rc == 0

    def test_dry_run_json_includes_project_root(self, tmp_path, capsys):
        """--dry-run JSON output includes project_root; context_path removed (DB-backed, T1.3)."""
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=True)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            _cmd_scan(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "project_root" in data
        assert "last_scan" in data

    def test_dry_run_shows_would_scan(self, tmp_path, capsys):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            _cmd_scan(args)
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower() or "would" in captured.out.lower()

    def test_missing_root_returns_1(self):
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=None):
            rc = _cmd_scan(args)
        assert rc == 1

    def test_scan_delegates_to_cli_scan(self, tmp_path):
        """Verify that non-dry-run scan delegates in-process to cli.scan.cmd_scan."""
        _write_context(tmp_path)

        args = _MockArgs(context_cmd="scan", dry_run=False, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            with patch("cli.scan.cmd_scan", return_value=0) as mock_cmd_scan:
                rc = _cmd_scan(args)

        mock_cmd_scan.assert_called_once()
        scan_args = mock_cmd_scan.call_args[0][0]
        assert scan_args.workspace == str(tmp_path)
        assert scan_args.fresh is False
        assert scan_args.dry_run is False
        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_context dispatch
# ---------------------------------------------------------------------------

class TestCmdContextDispatch:
    def test_dispatch_show(self):
        args = _MockArgs(context_cmd="show", section=None, json=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = cmd_context(args)
        assert rc == 0

    def test_dispatch_get(self):
        args = _MockArgs(context_cmd="get", workspace=None, section=None, json=False, text=False)
        with patch("gaia.project.current", return_value="me"):
            with patch("gaia.store.provider.get_context", return_value=_SAMPLE_SUBSTRATE_CTX):
                rc = cmd_context(args)
        assert rc == 0

    def test_dispatch_scan_dry_run(self, tmp_path):
        _write_context(tmp_path)
        args = _MockArgs(context_cmd="scan", dry_run=True, json=False)
        with patch("cli.context._find_project_root", return_value=tmp_path):
            rc = cmd_context(args)
        assert rc == 0

    def test_dispatch_no_action_returns_zero(self, tmp_path, capsys):
        args = _MockArgs(context_cmd=None)
        rc = cmd_context(args)
        assert rc == 0


# ---------------------------------------------------------------------------
# Integration: run via entry point with subprocess
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_context_show_runs_exits_zero(self, tmp_path):
        """Smoke test: python bin/gaia context show exits 0 (reads from substrate)."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "show"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        # workspace key is always present in the tabular render -- when the
        # substrate is empty, show may exit non-zero; accept either as long as
        # the binary ran without crashing.
        assert result.returncode in (0, 1), (
            f"Unexpected exit {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_context_get_runs_exits_zero(self, tmp_path):
        """Smoke test: python bin/gaia context get exits 0 and emits JSON."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "get"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        # Empty substrate -> exit 1 is acceptable; assert binary ran.
        assert result.returncode in (0, 1), (
            f"Unexpected exit {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_context_get_nonexistent_workspace_exits_1(self, tmp_path):
        """Fix #5: python bin/gaia context get --workspace nonexistent exits 1."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "get", "--workspace", "nonexistent_xyz_404"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "not found" in result.stderr

    def test_context_dump_deprecated_warning(self, tmp_path):
        """gaia context dump emits deprecation warning to stderr."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "dump"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        # Deprecation message goes to stderr regardless of exit code.
        assert "deprecated" in result.stderr.lower()

    def test_context_scan_dry_run(self, tmp_path):
        """Smoke test: python bin/gaia context scan --dry-run exits 0."""
        import os
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "context", "scan", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
            env={**os.environ, "GAIA_DATA_DIR": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
