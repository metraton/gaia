"""
Unit tests for the DETERMINISTIC gaia scan surface.

Covers the new scan (post inference-removal), driven by a single REQUIRED
``--workspace <name>`` parameter:

  * bin/cli/scan.py -- the thin CLI front-end (register / cmd_scan / rendering).
  * tools/scan/classify.py -- the deterministic classifier (R1-R6).

The 6 confirmed validation cases (see TestValidationCases) anchor the ruleset:

  1. aaxis/aos/aos-iac  --workspace aaxis        -> (aaxis, aos, aos-iac)
  2. github-repos/engram --workspace github-repos -> collapse (project = repo)
  3. me/gaia            --workspace me            -> collapse (project = repo)
  4. organic: aos itself as the workspace         -> collapse (project = repo)
  5. no-match: --workspace acme                   -> error-as-text (structured)
  6. deeper-than-3 nesting                         -> ambiguity returned as data

Test isolation:
  * Every scan that writes runs against an explicit temp DB (db_path=...); the
    real ~/.gaia/gaia.db is never touched. Classification-only tests (apply=False
    / classify_repo) never open a DB at all.
  * Git repos are created as bare ``.git`` marker directories -- classification
    keys on the presence of ``.git``, not on real git history.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure bin/ and the repo root are importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
for _p in (str(_BIN_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cli.scan as scan_mod
from tools.scan import classify as classify_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockArgs:
    """Minimal argparse.Namespace substitute matching the new scan surface."""

    def __init__(self, **kwargs):
        defaults = {
            "workspace": None,
            "root": None,
            "dry_run": False,
            "json": False,
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)


def _mk_repo(base: Path, *segments: str) -> Path:
    """Create ``base/segments.../.git`` and return the repo dir (the parent of
    ``.git``). Segments build the nesting used to exercise the ruleset."""
    repo = base.joinpath(*segments)
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to a temp dir and return the isolated db path.

    Guarantees no test in this module can reach the real ~/.gaia/gaia.db.
    """
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


# ---------------------------------------------------------------------------
# register() -- parser wiring for the new surface
# ---------------------------------------------------------------------------

class TestRegister:
    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        scan_mod.register(subparsers)
        return parser

    def test_register_returns_subparser(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        sp = scan_mod.register(subparsers)
        assert isinstance(sp, argparse.ArgumentParser)

    def test_workspace_is_required(self):
        """--workspace is REQUIRED: bare `scan` must fail to parse."""
        parser = self._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["scan"])

    def test_workspace_flag_parses(self):
        parser = self._build_parser()
        ns = parser.parse_args(["scan", "--workspace", "aaxis"])
        assert ns.workspace == "aaxis"
        assert ns.root is None
        assert ns.dry_run is False
        assert ns.json is False

    def test_positional_root_parses(self):
        parser = self._build_parser()
        ns = parser.parse_args(["scan", "--workspace", "aaxis", "/tmp/target"])
        assert ns.workspace == "aaxis"
        assert ns.root == "/tmp/target"

    def test_flags_parse(self):
        parser = self._build_parser()
        ns = parser.parse_args(
            ["scan", "--workspace", "me", "--dry-run", "--json"]
        )
        assert ns.dry_run is True
        assert ns.json is True

    def test_project_flag_retired(self):
        """The old --project flag is retired: the classifier derives the
        project deterministically from the path, so scan must reject it."""
        parser = self._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["scan", "--workspace", "me", "--project", "x"])

    def test_workspace_name_flag_retired(self):
        """The old --workspace-name flag is retired (replaced by --workspace)."""
        parser = self._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["scan", "--workspace-name", "x"])


# ---------------------------------------------------------------------------
# --help smoke
# ---------------------------------------------------------------------------

class TestHelpSmoke:
    def test_gaia_scan_help_exit_zero(self):
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "gaia"), "scan", "--help"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "--workspace" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--json" in result.stdout


# ---------------------------------------------------------------------------
# cmd_scan: guards + dry-run
# ---------------------------------------------------------------------------

class TestCmdScanGuards:
    def test_empty_workspace_errors(self, capsys):
        args = _MockArgs(workspace="   ", json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "error"
        assert "workspace" in data["error"].lower()

    def test_missing_root_errors(self, tmp_path, capsys):
        bogus = tmp_path / "does-not-exist"
        args = _MockArgs(workspace="me", root=str(bogus), json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "error"
        assert "not found" in data["error"]

    def test_no_repos_under_root_is_clean_error(self, tmp_path, capsys):
        """A root with no git repos returns a structured error, not a crash."""
        empty = tmp_path / "empty"
        empty.mkdir()
        args = _MockArgs(workspace="me", root=str(empty), json=True)
        rc = scan_mod.cmd_scan(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert "no git repos" in data["error"]

    def test_dry_run_does_not_touch_db(self, tmp_path, monkeypatch):
        """--dry-run must not create or write any DB file."""
        gaia_dir = tmp_path / "gaia-data"
        gaia_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(gaia_dir))

        # aaxis/aos/aos-iac tree so classification has real work to do.
        _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")

        args = _MockArgs(
            workspace="aaxis",
            root=str(tmp_path / "aaxis"),
            dry_run=True,
            json=True,
        )
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        assert list(gaia_dir.iterdir()) == [], (
            f"--dry-run wrote to the data dir: "
            f"{[p.name for p in gaia_dir.iterdir()]}"
        )

    def test_dry_run_reports_would_apply(self, tmp_path, monkeypatch, capsys):
        gaia_dir = tmp_path / "gaia-data"
        gaia_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(gaia_dir))
        _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")

        args = _MockArgs(
            workspace="aaxis",
            root=str(tmp_path / "aaxis"),
            dry_run=True,
            json=True,
        )
        rc = scan_mod.cmd_scan(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["resolved_workspace"] == "aaxis"
        assert data["projects"], "dry-run must still report classified projects"
        assert data["projects"][0]["applied"] is False


# ---------------------------------------------------------------------------
# The 6 confirmed validation cases (classifier, R1-R6)
# ---------------------------------------------------------------------------

class TestValidationCases:
    """Anchors the confirmed ruleset. Uses classify_repo (pure, no DB) for the
    per-repo cases and classify.scan(apply=False) for the report shape."""

    def test_case1_aaxis_aos_aos_iac(self, tmp_path):
        """aaxis/aos/aos-iac --workspace aaxis -> name='aos-iac', container='aos'.

        The workspace is the matched ancestor 'aaxis', the project NAME is now
        the repo's own basename ('aos-iac'), and the container 'aos' (the folder
        that groups sibling repos) is recorded separately in group_name.
        """
        repo = _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
        c = classify_mod.classify_repo(repo, "aaxis")
        assert c.matched
        assert c.workspace == "aaxis"
        assert c.project == "aos-iac"  # name = repo basename (R1)
        assert c.container == "aos"    # container -> group_name (R2)
        assert c.repo == "aos-iac"
        assert c.ambiguity is None

    def test_case2_github_repos_engram_collapse(self, tmp_path):
        """github-repos/engram --workspace github-repos -> collapse.

        The workspace is the direct parent of the repo, so there is nothing
        between them: project collapses to the repo name (R4)."""
        repo = _mk_repo(tmp_path, "github-repos", "engram")
        c = classify_mod.classify_repo(repo, "github-repos")
        assert c.matched
        assert c.workspace == "github-repos"
        assert c.project == "engram"  # name = repo basename
        assert c.container is None     # R4 collapse: no grouping folder
        assert c.repo == "engram"
        assert c.ambiguity is None

    def test_case3_me_gaia_collapse(self, tmp_path):
        """me/gaia --workspace me -> collapse (project = repo = 'gaia')."""
        repo = _mk_repo(tmp_path, "me", "gaia")
        c = classify_mod.classify_repo(repo, "me")
        assert c.matched
        assert c.workspace == "me"
        assert c.project == "gaia"
        assert c.container is None  # R4 collapse: repo directly under workspace
        assert c.repo == "gaia"
        assert c.ambiguity is None

    def test_case4_organic_repo_as_workspace(self, tmp_path):
        """Organic: the repo's own direct parent is named as the workspace.

        e.g. .../aos/<repo>  --workspace aos. The parent IS the workspace, so
        project collapses to the repo name (R4). This is the 'a project CAN be
        a workspace' case read from the parent side."""
        repo = _mk_repo(tmp_path, "aos", "aos-server")
        c = classify_mod.classify_repo(repo, "aos")
        assert c.matched
        assert c.workspace == "aos"
        assert c.project == "aos-server"  # name = repo basename
        assert c.container is None         # R4 collapse: parent IS the workspace
        assert c.repo == "aos-server"

    def test_case5_no_match_error_as_text(self, tmp_path):
        """no-match: --workspace acme against a tree with no 'acme' segment
        yields a structured error (error-as-text), never a crash, and no
        project."""
        repo = _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
        c = classify_mod.classify_repo(repo, "acme")
        assert not c.matched
        assert c.project is None
        assert c.error is not None
        assert c.error["W"] == "acme"
        assert "acme" in c.error["suggestion"]
        # The suggestion names the real ancestor segments so the user can pick.
        assert "aos" in c.error["suggestion"]

    def test_case6_deeper_than_3_ambiguity_as_data(self, tmp_path):
        """deeper-than-3 nesting -> name = repo basename, container = the
        segment immediately before the repo, and the levels ABOVE that
        container are returned as ambiguity DATA (never guessed)."""
        # W / extra1 / extra2 / container / repo  (2 levels above the container)
        repo = _mk_repo(tmp_path, "org", "team", "group", "svc", "svc-api")
        c = classify_mod.classify_repo(repo, "org")
        assert c.matched
        assert c.workspace == "org"
        assert c.project == "svc-api"  # name = repo basename (R1)
        assert c.container == "svc"    # immediate container -> group_name
        assert c.repo == "svc-api"
        assert c.ambiguity is not None
        assert c.ambiguity["repo"] == "svc-api"
        # extra_levels are the segments between the workspace and the container.
        assert c.ambiguity["extra_levels"] == ["team", "group"]


# ---------------------------------------------------------------------------
# classify.scan: report shape + reconcile (R5/R6) against a temp DB
# ---------------------------------------------------------------------------

class TestScanReport:
    def test_scan_report_shape_dry_run(self, tmp_path):
        """apply=False produces a full ScanReport with applied=False and no
        DB access."""
        _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
        report = classify_mod.scan(tmp_path / "aaxis", "aaxis", apply=False)
        d = report.to_dict()
        assert d["resolved_workspace"] == "aaxis"
        assert d["error"] is None
        assert len(d["projects"]) == 1
        assert d["projects"][0]["project"] == "aos-iac"  # name = repo basename
        assert d["projects"][0]["container"] == "aos"    # container -> group_name
        assert d["projects"][0]["applied"] is False
        assert d["marked_missing"] == 0

    def test_scan_mixed_match_and_collapse(self, tmp_path):
        """A root with a nested repo and a loose repo both matching 'aaxis':
        both project names are the repo basename; the nested one records its
        container in group_name, the loose one has no container."""
        _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")   # name=aos-iac, container=aos
        _mk_repo(tmp_path, "aaxis", "loose-repo")       # name=loose-repo, container=None
        report = classify_mod.scan(tmp_path / "aaxis", "aaxis", apply=False)
        by_repo = {p["repo"]: p["project"] for p in report.projects}
        assert by_repo["aos-iac"] == "aos-iac"
        assert by_repo["loose-repo"] == "loose-repo"
        by_container = {p["repo"]: p["container"] for p in report.projects}
        assert by_container["aos-iac"] == "aos"
        assert by_container["loose-repo"] is None
        assert report.errors == []

    def test_scan_all_no_match_is_error_report(self, tmp_path):
        """When no repo matches W, the report carries errors and no projects,
        and resolved_workspace stays None (non-crashing)."""
        _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
        report = classify_mod.scan(tmp_path / "aaxis", "acme", apply=False)
        assert report.projects == []
        assert report.resolved_workspace is None
        assert len(report.errors) == 1
        assert report.errors[0]["W"] == "acme"

    def test_scan_persists_and_reconciles(self, tmp_db, tmp_path):
        """apply=True writes projects rows, then a second scan with one repo
        gone soft-deletes the missing project (R5).

        Runs entirely against the temp DB (tmp_db fixture)."""
        import sqlite3
        import shutil

        # First scan: two repos under workspace 'aaxis'.
        root = tmp_path / "aaxis"
        _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
        _mk_repo(tmp_path, "aaxis", "other", "other-repo")

        r1 = classify_mod.scan(root, "aaxis", db_path=tmp_db, apply=True)
        assert r1.error is None
        applied = [p for p in r1.projects if p["applied"]]
        assert len(applied) == 2, f"expected 2 applied rows, got {r1.projects}"

        con = sqlite3.connect(str(tmp_db))
        try:
            rows = con.execute(
                "SELECT name, status FROM projects WHERE workspace = ?",
                ("aaxis",),
            ).fetchall()
        finally:
            con.close()
        names = {n for n, _ in rows}
        # Names are now the repo basenames (not the container segment).
        assert names == {"aos-iac", "other-repo"}
        assert all(s == "active" for _, s in rows)

        # Second scan: remove the 'other' subtree -> its project soft-deleted.
        shutil.rmtree(tmp_path / "aaxis" / "other")

        r2 = classify_mod.scan(root, "aaxis", db_path=tmp_db, apply=True)
        assert r2.error is None
        assert r2.marked_missing >= 1

        con = sqlite3.connect(str(tmp_db))
        try:
            status_by_name = dict(
                con.execute(
                    "SELECT name, status FROM projects WHERE workspace = ?",
                    ("aaxis",),
                ).fetchall()
            )
        finally:
            con.close()
        assert status_by_name.get("aos-iac") == "active", "surviving repo stays active"
        assert status_by_name.get("other-repo") == "missing", (
            "removed repo's project must be soft-deleted (status=missing), "
            f"got {status_by_name.get('other-repo')!r}"
        )

    def test_scan_identity_collapse_same_repo_two_roots(self, tmp_db, tmp_path):
        """The SAME physical repo scanned from two roots collapses to ONE
        projects row (writer identity-collapse UPSERT keyed on
        project_identity)."""
        import sqlite3
        import shutil

        # Build aaxis/aos/aos-iac and initialise a real git repo so
        # resolve_project_identity returns a stable git-common-dir identity.
        repo = _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")
        shutil.rmtree(repo / ".git")
        subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)

        # Scan from the workspace root, then again from a deeper root that still
        # contains the same repo (project resolves to the repo name there).
        classify_mod.scan(tmp_path / "aaxis", "aaxis", db_path=tmp_db, apply=True)
        classify_mod.scan(tmp_path / "aaxis" / "aos", "aos", db_path=tmp_db, apply=True)

        con = sqlite3.connect(str(tmp_db))
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM projects WHERE project_identity IS NOT NULL"
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1, (
            "the same physical repo must collapse to a single projects row, "
            f"got {count}"
        )

    def test_scan_populates_primary_language_and_platform(self, tmp_db, tmp_path):
        """The LIVE scan (classify._upsert) must populate the scan-owned scalars
        `primary_language` (dominant language from disk manifests) and
        `platform` (from the git remote host), for parity with
        store_populator.populate_project. Regression: these were left NULL, so
        promotion carried no `language`/`platform` into the project_identity
        contract even when the data was on disk.
        """
        import sqlite3
        import shutil

        # R4 collapse (repo directly under the workspace) -> project == repo
        # basename, so the row is addressable by name "svc".
        repo = _mk_repo(tmp_path, "aaxis", "svc")
        # Multi-language repo: package.json + requirements.txt. The dominant
        # scalar is deterministic (package.json -> javascript wins by priority),
        # independent of the multi-language facets.
        (repo / "package.json").write_text('{"name": "svc"}')
        (repo / "requirements.txt").write_text("fastapi\n")
        # Real git repo with an origin remote so platform derives to 'github'.
        shutil.rmtree(repo / ".git")
        subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "git@github.com:aaxis/svc.git"],
            cwd=str(repo), check=True,
        )

        rep = classify_mod.scan(tmp_path / "aaxis", "aaxis",
                                db_path=tmp_db, apply=True)
        assert rep.error is None

        con = sqlite3.connect(str(tmp_db))
        try:
            row = con.execute(
                "SELECT primary_language, platform, role FROM projects "
                "WHERE workspace = ? AND name = ?",
                ("aaxis", "svc"),
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "svc project row must exist"
        assert row[0] == "javascript", (
            f"dominant language must be populated, got {row[0]!r}"
        )
        assert row[1] == "github", (
            f"platform must derive from the github remote, got {row[1]!r}"
        )
        # role must be populated too, so promotion can seed the contract `type`.
        assert row[2] == "application", (
            f"role must be populated (deterministic), got {row[2]!r}"
        )


# ---------------------------------------------------------------------------
# Basename-naming forward-fix: project NAME = repo basename (never a numeric
# suffix from container-name collisions); the container goes to group_name.
# ---------------------------------------------------------------------------

class TestBasenameNamingForwardFix:
    """Guards the forward-fix that made `projects.name` the repo basename and
    moved the container folder into `projects.group_name`. Asserts the
    PERSISTED columns, not just the report shape."""

    def _persisted(self, db_path, workspace):
        import sqlite3
        con = sqlite3.connect(str(db_path))
        try:
            return {
                r[0]: r[1]
                for r in con.execute(
                    "SELECT name, group_name FROM projects WHERE workspace = ?",
                    (workspace,),
                ).fetchall()
            }
        finally:
            con.close()

    def test_multi_repo_container_names_by_basename_group_is_container(
        self, tmp_db, tmp_path
    ):
        """A multi-repo layout under one container persists each repo by its
        OWN basename (no -2/-3 suffix) with group_name = the container."""
        # aaxis/bildwiz/{bildwiz-iac, newco-pitot, control-tower-livekit}
        _mk_repo(tmp_path, "aaxis", "bildwiz", "bildwiz-iac")
        _mk_repo(tmp_path, "aaxis", "bildwiz", "newco-pitot")
        _mk_repo(tmp_path, "aaxis", "bildwiz", "control-tower-livekit")

        report = classify_mod.scan(
            tmp_path / "aaxis", "aaxis", db_path=tmp_db, apply=True
        )
        assert report.error is None, report.error

        persisted = self._persisted(tmp_db, "aaxis")
        # Meaningful, significant names -- no numeric-suffix disambiguation.
        assert set(persisted) == {
            "bildwiz-iac",
            "newco-pitot",
            "control-tower-livekit",
        }, persisted
        assert not any(
            n.endswith(("-2", "-3", "-4")) for n in persisted
        ), f"basename naming must not produce numeric suffixes: {persisted}"
        # The container is preserved in group_name for every repo.
        assert set(persisted.values()) == {"bildwiz"}, persisted

    def test_single_repo_direct_under_workspace_has_no_group(
        self, tmp_db, tmp_path
    ):
        """The 1-repo-directly-under-workspace case (e.g. workspace 'me' with a
        repo like 'gaia' as a direct child) still works: name = basename,
        group_name = None (R4 collapse, unchanged)."""
        _mk_repo(tmp_path, "me", "gaia")

        report = classify_mod.scan(
            tmp_path / "me", "me", db_path=tmp_db, apply=True
        )
        assert report.error is None, report.error

        persisted = self._persisted(tmp_db, "me")
        assert persisted == {"gaia": None}, persisted


# ---------------------------------------------------------------------------
# match_workspace_index -- the segment matcher (R3)
# ---------------------------------------------------------------------------

class TestMatchWorkspaceIndex:
    def test_last_occurrence_wins(self):
        segs = ["aaxis", "sub", "aaxis", "proj", "repo"]
        # The deepest 'aaxis' (index 2) is the most specific boundary.
        assert classify_mod.match_workspace_index(segs, "aaxis") == 2

    def test_repo_itself_never_matches(self):
        """The repo segment (segs[-1]) is never eligible to be the workspace."""
        segs = ["a", "b", "repo"]
        assert classify_mod.match_workspace_index(segs, "repo") is None

    def test_nested_token_split_match(self):
        segs = ["aaxis", "aos", "proj", "repo"]
        assert classify_mod.match_workspace_index(segs, "aaxis/aos") == 1

    def test_no_match_returns_none(self):
        segs = ["a", "b", "repo"]
        assert classify_mod.match_workspace_index(segs, "zzz") is None
