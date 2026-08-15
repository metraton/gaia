#!/usr/bin/env python3
"""Tests for session_manifest -- SessionStart additionalContext (Phase 4).

Builders are fail-safe and side-effect-free; the assembler concatenates the
non-empty blocks. These tests use heavy patching to keep each unit isolated
from disk, processes, and external state.
"""

import json
import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_manifest
from modules.session.session_manifest import (
    build_environment_block,
    build_session_context,
    build_workspace_memory_block,
)


# ---------------------------------------------------------------------------
# build_environment_block
# ---------------------------------------------------------------------------

class TestBuildEnvironmentBlock:
    def test_block_includes_cwd_and_machine_minimum(self, monkeypatch):
        """Even with no workspace identity, the block must carry the basics."""
        # No project-context.json so workspace is None.
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: None
        )
        # Deterministic machine label.
        monkeypatch.setattr(
            session_manifest, "_machine_label", lambda: "host (Linux/x86_64)"
        )

        result = build_environment_block()
        assert "## Environment" in result
        assert "cwd:" in result
        assert "host (Linux/x86_64)" in result

    def test_block_includes_workspace_when_available(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: "my-workspace"
        )
        monkeypatch.setattr(
            session_manifest, "_machine_label", lambda: "host (Linux/x86_64)"
        )

        result = build_environment_block()
        assert "Gaia workspace (memory/db scope): my-workspace" in result

    def test_block_includes_version_when_available(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: None
        )
        monkeypatch.setattr(
            session_manifest, "_machine_label", lambda: "host"
        )
        monkeypatch.setattr(
            session_manifest, "_read_gaia_version", lambda: "5.0.0-rc.3"
        )

        result = build_environment_block()
        assert "Gaia: 5.0.0-rc.3" in result

    def test_block_failsafe_when_workspace_helper_raises(self, monkeypatch):
        """A subcomponent raising must not propagate -- builder returns
        either a partial block or ''. Test enforces the no-raise contract."""
        def _boom():
            raise RuntimeError("simulated context-file error")

        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", _boom
        )

        # Should not raise; result is allowed to be either "" or a
        # partial block built without the workspace line.
        result = build_environment_block()
        assert isinstance(result, str)
        # The catch is at the function boundary; we tolerate either branch
        # but must not see a Workspace line for the failing helper.
        assert "Workspace:" not in result

    def test_version_line_carries_the_local_dev_build_count(self, monkeypatch, tmp_path):
        """A `gaia dev` build ships the base semver, so the count is what
        distinguishes the pristine release from the Nth local iteration."""
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
        monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: None)
        monkeypatch.setattr(session_manifest, "_machine_label", lambda: "host")
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.3.0")

        from gaia.dev_builds import record_build
        record_build("5.3.0", "fb27693c")

        assert "Gaia: 5.3.0 (dev.1, build fb27693c)" in build_environment_block()

    def test_version_line_is_bare_when_no_dev_build_was_recorded(self, monkeypatch, tmp_path):
        """A pristine npm install has no sidecar, and must render as it always did."""
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
        monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: None)
        monkeypatch.setattr(session_manifest, "_machine_label", lambda: "host")
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.3.0")

        result = build_environment_block()
        assert "Gaia: 5.3.0" in result
        assert "dev." not in result

    def test_version_line_degrades_when_the_counter_raises(self, monkeypatch):
        """SessionStart must not be breakable by the counter.

        Same discipline as the memory block: any failure yields the display
        that existed before the counter did, never an exception and never a
        dropped Environment block.
        """
        monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: None)
        monkeypatch.setattr(session_manifest, "_machine_label", lambda: "host")
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.3.0")

        import gaia.dev_builds as dev_builds

        def _boom(_version):
            raise RuntimeError("simulated counter failure")

        monkeypatch.setattr(dev_builds, "describe_version", _boom)

        result = build_environment_block()
        assert "Gaia: 5.3.0" in result
        assert "dev." not in result

    def test_version_line_degrades_when_the_counter_module_is_absent(self, monkeypatch):
        """A partial install without gaia.dev_builds still renders the version."""
        monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: None)
        monkeypatch.setattr(session_manifest, "_machine_label", lambda: "host")
        monkeypatch.setattr(session_manifest, "_read_gaia_version", lambda: "5.3.0")
        monkeypatch.setitem(sys.modules, "gaia.dev_builds", None)

        assert "Gaia: 5.3.0" in build_environment_block()


# ---------------------------------------------------------------------------
# build_session_context (assembler)
# ---------------------------------------------------------------------------

class TestBuildSessionContext:
    """Pending approvals are no longer surfaced (M2): the assembler concatenates
    Environment, Projects, Contract Index, task notifications, schedule
    reconciliation, schedule suspensions, and Workspace Memory (digest +
    anchors) -- there is no pending-approvals block in the join.
    """

    def test_retired_loop_builder_is_gone(self):
        """The agentic-loop capability was removed whole; a surviving builder
        would let the block be resurrected by a single call site."""
        assert not hasattr(session_manifest, "build_agentic_loop_block")

    def test_assembles_all_blocks_with_blank_line_separator(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: "PROJ BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_contracts_index_block", lambda: "CONTRACTS BLOCK"
        )
        # Bug 2 fix: build_workspace_memory_block is now called twice -- once
        # with no args (digest) and once with sections=["anchor"]. Assert on
        # the call args so each call renders its own distinguishable text.
        def _fake_memory(*args, **kwargs):
            if kwargs.get("sections") == ["anchor"]:
                return "ANCHOR BLOCK"
            return "DIGEST BLOCK"

        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", _fake_memory
        )
        # Neutralize the two blocks the assembler runs between the contract
        # index and workspace-memory blocks that are NOT under test here: task
        # notifications and schedule reconciliation both do live I/O (DB /
        # crontab) and must not leak environment-dependent content into this
        # deterministic join test.
        monkeypatch.setattr(
            session_manifest, "build_task_notifications_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_schedule_reconciliation_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_schedule_suspension_block", lambda: ""
        )

        result = build_session_context()
        assert result == (
            "ENV BLOCK\n\nPROJ BLOCK\n\nCONTRACTS BLOCK\n\n"
            "DIGEST BLOCK\n\nANCHOR BLOCK"
        ), (
            "Blocks must be joined with exactly one blank line separator -- "
            "markdown convention; agents render this as paragraph breaks. "
            "Project Context — Projects sits right after Environment, then "
            "the Contract Index (Bug 1 fix: wired but never called before). "
            "Workspace Memory is now two calls: the digest, then the anchors "
            "(Bug 2 fix). Pending approvals are no longer part of the "
            "manifest."
        )
        assert "[ACTIONABLE]" not in result

    def test_workspace_memory_called_twice_disjoint_sections(self, monkeypatch):
        """Bug 2: the assembler calls build_workspace_memory_block twice --
        once with no sections (digest) and once with sections=["anchor"] --
        so the orchestrator receives both without duplicating either."""
        monkeypatch.setattr(session_manifest, "build_environment_block", lambda: "")
        monkeypatch.setattr(session_manifest, "build_projects_context_block", lambda: "")
        monkeypatch.setattr(session_manifest, "build_contracts_index_block", lambda: "")
        monkeypatch.setattr(session_manifest, "build_task_notifications_block", lambda: "")
        monkeypatch.setattr(session_manifest, "build_schedule_reconciliation_block", lambda: "")
        monkeypatch.setattr(session_manifest, "build_schedule_suspension_block", lambda: "")

        calls = []

        def _fake_memory(*args, **kwargs):
            calls.append(kwargs.get("sections"))
            return "DIGEST" if kwargs.get("sections") is None else "ANCHORS"

        monkeypatch.setattr(session_manifest, "build_workspace_memory_block", _fake_memory)

        result = build_session_context()
        assert calls == [None, ["anchor"]], (
            "Expected exactly two calls: digest (no sections) then "
            "anchor-only (sections=['anchor']), in that order."
        )
        assert result == "DIGEST\n\nANCHORS"

    def test_skips_empty_blocks_in_join(self, monkeypatch):
        """Empty blocks must not leave dangling blank lines in the output."""
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_contracts_index_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_task_notifications_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_schedule_reconciliation_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_schedule_suspension_block", lambda: ""
        )
        # Called twice by the assembler (digest, then sections=["anchor"]);
        # accept both call shapes and return distinct text for each so the
        # join is unambiguous.
        monkeypatch.setattr(
            session_manifest,
            "build_workspace_memory_block",
            lambda *a, **kw: (
                "ANCHOR BLOCK" if kw.get("sections") == ["anchor"] else "DIGEST BLOCK"
            ),
        )

        result = build_session_context()
        assert result == "ENV BLOCK\n\nDIGEST BLOCK\n\nANCHOR BLOCK"
        assert "\n\n\n" not in result, (
            "Triple-newline indicates an empty block sneaked into the join."
        )

    def test_returns_empty_when_all_blocks_empty(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_contracts_index_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_task_notifications_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_schedule_reconciliation_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_schedule_suspension_block", lambda: ""
        )
        # Called twice by the assembler (digest, then sections=["anchor"]);
        # accept both call shapes.
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda *a, **kw: ""
        )

        assert build_session_context() == ""

    def test_failsafe_when_a_builder_raises(self, monkeypatch):
        """An exception in a builder must not break the assembler."""
        def _boom():
            raise RuntimeError("simulated builder failure")

        monkeypatch.setattr(
            session_manifest, "build_environment_block", _boom
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda *a, **kw: ""
        )

        # Either the assembler swallows the exception entirely (returning "")
        # or it catches around the whole pipeline and returns "". Both are
        # acceptable; what is not acceptable is propagating the exception.
        result = build_session_context()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# build_workspace_memory_block
# ---------------------------------------------------------------------------

class TestBuildWorkspaceMemoryBlock:
    """The block shells out to `gaia memory get-relevant`. Tests stub the
    subprocess result to keep the unit isolated from the substrate DB."""

    def test_returns_block_when_cli_emits_content(self, monkeypatch):
        """CLI succeeds with text -> builder returns it verbatim (stripped)."""
        import subprocess

        sentinel = "## Workspace Memory (qxo)\n\nAtoms:\n- atom_x: y"

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=0,
                stdout=sentinel + "\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # Pin the workspace so the helper doesn't try to read project-context.
        result = build_workspace_memory_block(workspace="qxo")
        assert result == sentinel

    def test_returns_empty_when_no_workspace(self, monkeypatch):
        """No workspace identity -> empty block, no subprocess call."""
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: None
        )
        # If subprocess is touched, the test should still not raise.
        result = build_workspace_memory_block()
        assert result == ""

    def test_returns_empty_when_cli_nonzero_exit(self, monkeypatch):
        """CLI exits non-zero -> empty block (fail-safe)."""
        import subprocess

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=2,
                stdout="",
                stderr="oops",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == ""

    def test_returns_empty_when_cli_raises(self, monkeypatch):
        """Subprocess raises (timeout, FileNotFoundError) -> empty block."""
        import subprocess

        def _fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="gaia", timeout=5)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == ""

    def test_returns_empty_when_cli_emits_only_whitespace(self, monkeypatch):
        """CLI exits 0 but with empty stdout -> empty block."""
        import subprocess

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=0,
                stdout="   \n  \n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == ""

    def test_sections_forwarded_as_cli_flag(self, monkeypatch):
        """sections=['anchor'] -> argv carries --sections anchor (subagent cut)."""
        import subprocess

        captured = {}

        def _fake_run(*args, **kwargs):
            captured["argv"] = args[0] if args else []
            return subprocess.CompletedProcess(
                args=captured["argv"], returncode=0, stdout="BLOCK", stderr="",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo", sections=["anchor"])
        assert result == "BLOCK"
        argv = captured["argv"]
        assert "--sections" in argv
        assert argv[argv.index("--sections") + 1] == "anchor"

    def test_no_sections_omits_cli_flag(self, monkeypatch):
        """Orchestrator path (no sections) -> argv has no --sections flag."""
        import subprocess

        captured = {}

        def _fake_run(*args, **kwargs):
            captured["argv"] = args[0] if args else []
            return subprocess.CompletedProcess(
                args=captured["argv"], returncode=0, stdout="BLOCK", stderr="",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == "BLOCK"
        assert "--sections" not in captured["argv"]


# ---------------------------------------------------------------------------
# _extract_projects_from_identity -- type + description carried (CAMBIO 2)
# ---------------------------------------------------------------------------

class TestExtractProjectsCarriesTypeAndDescription:
    """The extractor returns (name, path, type, description, missing_since)
    5-tuples so the Projects block can label each entry with its type, a short
    description, and the vanished mark when the repo left the disk."""

    _LOOKUP = {"by_name": {}, "by_ws": {}}

    def test_map_shape_carries_type_and_description(self):
        payload = {
            "aos_iac": {
                "name": "aos-iac",
                "local_path": "/home/x/aos-iac",
                "type": "terraform",
                "description": "Terraform IaC for AOS GCP infra",
            },
        }
        out = session_manifest._extract_projects_from_identity(
            payload, "me", self._LOOKUP
        )
        assert out == [
            ("aos-iac", "/home/x/aos-iac", "terraform",
             "Terraform IaC for AOS GCP infra", ""),
        ]

    def test_scanner_shape_carries_type_and_description(self):
        payload = {
            "name": "nfi",
            "type": "application",
            "description": "NFI app",
        }
        out = session_manifest._extract_projects_from_identity(
            payload, "nfi", {"by_name": {}, "by_ws": {"nfi": ["/home/x/nfi"]}}
        )
        assert out == [("nfi", "/home/x/nfi", "application", "NFI app", "")]

    def test_missing_type_and_description_are_empty_strings(self):
        payload = {"proj": {"name": "p", "local_path": "/p"}}
        out = session_manifest._extract_projects_from_identity(
            payload, "ws", self._LOOKUP
        )
        assert out == [("p", "/p", "", "", "")]

    def test_vanished_entry_is_returned_with_its_mark_not_filtered(self):
        """A repo gone from disk stays in the index, carrying its mark: the
        block shows it rather than hiding it."""
        payload = {
            "ghost": {
                "name": "ghost",
                "local_path": "/x/ghost",
                "missing_since": "2026-07-01T00:00:00+00:00",
            },
        }
        out = session_manifest._extract_projects_from_identity(
            payload, "ws", self._LOOKUP
        )
        assert out == [("ghost", "/x/ghost", "", "", "2026-07-01T00:00:00+00:00")]


def _patch_connect(monkeypatch, identity_rows, proj_rows=()):
    """Point build_projects_context_block at fixed contract + projects rows."""
    import gaia.store.writer as _writer

    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _FakeCon:
        def execute(self, sql, *a):
            if "project_context_contracts" in sql:
                return _FakeCursor(list(identity_rows))
            return _FakeCursor(list(proj_rows))

        def close(self):
            pass

    monkeypatch.setattr(_writer, "_connect", lambda: _FakeCon())


class TestBuildProjectsBlockRendersTypeAndDescription:
    """The rendered Projects block groups by workspace and includes type in
    parens plus description after an em dash when present."""

    def _run_with_rows(self, monkeypatch, payload):
        _patch_connect(
            monkeypatch,
            [{"workspace": "me", "payload": json.dumps(payload)}],
        )
        return session_manifest.build_projects_context_block()

    def test_type_and_description_rendered(self, monkeypatch):
        payload = {
            "aos_iac": {
                "name": "aos-iac",
                "local_path": "/home/x/aos-iac",
                "type": "terraform",
                "description": "Terraform IaC for AOS GCP infra",
            },
        }
        block = self._run_with_rows(monkeypatch, payload)
        # The workspace group carries the root; the entry's relative path is
        # "aos-iac", identical to the name, so it is not repeated.
        assert "### me — /home/x" in block
        assert "- aos-iac (terraform) — Terraform IaC for AOS GCP infra" in block

    def test_path_shown_when_it_differs_from_the_name(self, monkeypatch):
        payload = {"p": {"name": "plainproj", "local_path": "/p"}}
        block = self._run_with_rows(monkeypatch, payload)
        assert "- plainproj: p" in block

    def test_vanished_entry_is_not_injected_at_all(self, monkeypatch):
        """A removed project is asked for, not announced every session.

        `gaia context get` still holds the whole record; nothing here deletes
        it. It just stops spending context on a question almost nobody asks.
        """
        payload = {
            "ghost": {
                "name": "ghost",
                "local_path": "/x/ghost",
                "type": "application",
                "description": "curated blurb",
                "missing_since": "2026-07-01T00:00:00+00:00",
            },
        }
        block = self._run_with_rows(monkeypatch, payload)
        assert "ghost" not in block
        assert "missing" not in block
        assert "curated blurb" not in block

    def test_a_workspace_of_only_vanished_entries_renders_no_group(
        self, monkeypatch,
    ):
        payload = {
            "ghost": {
                "name": "ghost",
                "local_path": "/x/ghost",
                "missing_since": "2026-07-01T00:00:00+00:00",
            },
        }
        block = self._run_with_rows(monkeypatch, payload)
        assert "###" not in block


class TestProjectsBlockDeduplicatesAtTheSource:
    """The same repo reached through two contract generations must render once.

    The current scan-promoted map names a repo by its uniquified SLUG; a legacy
    per-directory contract names the same repo by its DIRECTORY name and carries
    no resolvable path of its own. Keying dedup on the resolved absolute path is
    what collapses them.
    """

    _PROJ_ROWS = (
        {
            "workspace": "aaxis",
            "name": "bildwiz_2",
            "path": "/ws/aaxis/bildwiz/bildwiz-iac",
        },
    )

    def test_slug_and_directory_name_collapse_to_one_entry(self, monkeypatch):
        promoted = {
            "bildwiz_2": {
                "name": "bildwiz-2",
                "local_path": "/ws/aaxis/bildwiz/bildwiz-iac",
                "type": "application",
            },
        }
        legacy = {
            "name": "bildwiz-platform",
            "workspace_repos": [{"name": "bildwiz-iac", "path": "bildwiz-iac"}],
        }
        _patch_connect(
            monkeypatch,
            [
                {"workspace": "aaxis", "payload": json.dumps(promoted)},
                {"workspace": "bildwiz", "payload": json.dumps(legacy)},
            ],
            self._PROJ_ROWS,
        )
        block = session_manifest.build_projects_context_block()

        entries = [l for l in block.splitlines() if l.startswith("- ")]
        assert len(entries) == 1, f"expected one entry, got {entries}"
        # It lands under the workspace that owns the projects row, not under the
        # legacy contract's own workspace key.
        assert "### aaxis" in block
        assert "### bildwiz" not in block

    def test_merge_keeps_metadata_carried_by_only_one_side(self, monkeypatch):
        """The promoted side has the type; the legacy side has the description.
        Neither may be lost when the two collapse."""
        promoted = {
            "bildwiz_2": {
                "name": "bildwiz-2",
                "local_path": "/ws/aaxis/bildwiz/bildwiz-iac",
                "type": "application",
            },
        }
        legacy = {
            "name": "bildwiz-platform",
            "workspace_repos": [
                {
                    "name": "bildwiz-iac",
                    "path": "bildwiz-iac",
                    "description": "only the legacy row has this",
                }
            ],
        }
        _patch_connect(
            monkeypatch,
            [
                {"workspace": "aaxis", "payload": json.dumps(promoted)},
                {"workspace": "bildwiz", "payload": json.dumps(legacy)},
            ],
            self._PROJ_ROWS,
        )
        block = session_manifest.build_projects_context_block()
        assert "(application)" in block
        assert "only the legacy row has this" in block

    def test_ambiguous_basename_is_not_guessed(self, monkeypatch):
        """Two repos sharing a directory name make the basename useless as a
        key; the legacy entry must stay unresolved rather than bind to one."""
        legacy = {
            "name": "w",
            "workspace_repos": [{"name": "terraform", "path": "terraform"}],
        }
        _patch_connect(
            monkeypatch,
            [{"workspace": "legacy", "payload": json.dumps(legacy)}],
            (
                {"workspace": "a", "name": "a1", "path": "/ws/a/terraform"},
                {"workspace": "b", "name": "b1", "path": "/ws/b/terraform"},
            ),
        )
        block = session_manifest.build_projects_context_block()
        assert "unresolved (1): terraform" in block

    def test_workspace_identity_row_is_not_listed_as_a_project(self, monkeypatch):
        """A flat contract whose name IS its workspace key, with no path
        resolvable anywhere, is a workspace-identity record -- not a project."""
        _patch_connect(
            monkeypatch,
            [
                {
                    "workspace": "nfi",
                    "payload": json.dumps({"name": "nfi", "type": "application"}),
                },
                {
                    "workspace": "aaxis",
                    "payload": json.dumps(
                        {"nfi": {"name": "nfi", "local_path": "/ws/aaxis/nfi/nfi-oro-com"}}
                    ),
                },
            ],
            ({"workspace": "aaxis", "name": "nfi", "path": "/ws/aaxis/nfi/nfi-oro-com"},),
        )
        block = session_manifest.build_projects_context_block()
        entries = [l for l in block.splitlines() if l.startswith("- ")]
        assert len(entries) == 1, f"expected one entry, got {entries}"
        assert "unresolved" not in block
        assert "### nfi" not in block


# ---------------------------------------------------------------------------
# build_contracts_index_block
# ---------------------------------------------------------------------------

class TestBuildContractsIndexBlock:
    """Static surface -> contract_sections index read from surface-routing.json.

    Tests patch _load_surface_routing to keep the unit isolated from disk.
    """

    def test_renders_surface_to_sections(self, monkeypatch):
        data = {
            "surfaces": {
                "iac": {
                    "primary_agent": "platform-architect",
                    "contract_sections": ["project_identity", "stack", "git"],
                },
                "workspace": {
                    "primary_agent": "gaia-operator",
                    "contract_sections": ["project_identity", "workspace_repos"],
                },
            }
        }
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: data
        )
        block = session_manifest.build_contracts_index_block()
        assert "## Project Context — Contract Index (per surface)" in block
        assert "- iac (platform-architect) → project_identity, stack, git" in block
        assert (
            "- workspace (gaia-operator) → project_identity, workspace_repos"
            in block
        )
        # Section CONTENTS are never emitted -- only the names. Sanity: the
        # block is short (names only), not a dump of section bodies.
        assert "→" in block

    def test_skips_surface_without_contract_sections(self, monkeypatch):
        data = {
            "surfaces": {
                "iac": {
                    "primary_agent": "platform-architect",
                    "contract_sections": ["project_identity"],
                },
                "broken": {"primary_agent": "x"},  # no contract_sections
            }
        }
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: data
        )
        block = session_manifest.build_contracts_index_block()
        assert "iac" in block
        assert "broken" not in block

    def test_agent_optional(self, monkeypatch):
        data = {
            "surfaces": {
                "iac": {"contract_sections": ["project_identity"]},
            }
        }
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: data
        )
        block = session_manifest.build_contracts_index_block()
        assert "- iac → project_identity" in block

    def test_empty_config_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: {}
        )
        assert session_manifest.build_contracts_index_block() == ""

    def test_no_surfaces_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: {"version": "1"}
        )
        assert session_manifest.build_contracts_index_block() == ""

    def test_failsafe_when_loader_raises(self, monkeypatch):
        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(session_manifest, "_load_surface_routing", _boom)
        assert session_manifest.build_contracts_index_block() == ""

    def test_overflow_drops_tail_with_footer(self, monkeypatch):
        # Many surfaces with long section lists to force the budget trim.
        surfaces = {
            f"surface_{i}": {
                "primary_agent": f"agent_{i}",
                "contract_sections": [f"section_{j}" for j in range(12)],
            }
            for i in range(20)
        }
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: {"surfaces": surfaces}
        )
        block = session_manifest.build_contracts_index_block(max_chars=600)
        assert len(block) <= 600
        assert "more, inspect the DB-backed surface_routing registry" in block

    def test_real_config_has_all_surfaces(self, tmp_path, monkeypatch):
        """Integration: against a DB seeded from the real agent frontmatters,
        all 7 surfaces land.

        Routing moved from config/surface-routing.json (retired, git-rm'd) to
        the surface_routing table, seeded from each agent's `routing:`
        frontmatter block. Seed a temp DB the same way tests/tools/test_surface_router.py
        does (bootstrap_gaia_schema + seed_surface_routing_from_agents) and
        point GAIA_DATA_DIR at it so _load_surface_routing's real DB-backed
        loader resolves it, exercising the production path end to end.
        """
        import sys as _sys

        repo_root = Path(__file__).resolve().parents[4]
        if str(repo_root) not in _sys.path:
            _sys.path.insert(0, str(repo_root))
        from tests.fixtures.db_helpers import (
            bootstrap_gaia_schema,
            seed_surface_routing_from_agents,
        )

        db = tmp_path / "gaia.db"
        bootstrap_gaia_schema(db)
        seed_surface_routing_from_agents(db)
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

        block = session_manifest.build_contracts_index_block()
        # The seeded surface_routing table defines these 7 surfaces.
        for surface in (
            "live_runtime", "gitops_desired_state", "iac", "app_ci_tooling",
            "planning_specs", "gaia_system", "workspace",
        ):
            assert surface in block, f"missing surface {surface}"

    def test_overflow_footer_reserved_even_when_tight(self, monkeypatch):
        """FIX (b): the footer must land even when the cap is so tight that the
        old ``if len(block)+len(footer) <= max_chars`` guard would have dropped
        it. Footer space is reserved BEFORE trimming, so a silent tail-drop with
        no footer can never happen. Regression for the drop-without-footer bug.
        """
        surfaces = {
            f"surface_{i}": {
                "primary_agent": f"agent_{i}",
                "contract_sections": [f"section_{j}" for j in range(30)],
            }
            for i in range(40)
        }
        monkeypatch.setattr(
            session_manifest, "_load_surface_routing", lambda: {"surfaces": surfaces}
        )
        # A cap that leaves almost no slack after the last kept entry.
        for cap in (120, 200, 350, 500):
            block = session_manifest.build_contracts_index_block(max_chars=cap)
            assert block, f"cap={cap} produced empty block"
            assert "more, inspect the DB-backed surface_routing registry" in block, (
                f"cap={cap}: overflow dropped entries WITHOUT a footer"
            )
            assert len(block) <= cap, f"cap={cap}: block exceeded cap"


# ---------------------------------------------------------------------------
# build_projects_context_block -- no silent drop (FIX a) + footer (FIX b)
# ---------------------------------------------------------------------------

class TestBuildProjectsBlockNoSilentDrop:
    """The projects index is a routing surface -- entries must never vanish
    silently. FIX (a): the default cap fits the full realistic set including
    type+description tails. FIX (b): any forced overflow always ends in a
    footer stating the dropped count.
    """

    def _patch_rows(self, monkeypatch, payload):
        _patch_connect(
            monkeypatch,
            [{"workspace": "me", "payload": json.dumps(payload)}],
        )

    def _payload_17(self):
        # 17 projects, each with a type and a realistic description tail --
        # mirrors the field shape that pushed the block past the old 1400 cap.
        return {
            f"proj_{i}": {
                "name": f"project-name-number-{i}",
                "local_path": f"/home/jorge/ws/aaxis/group/project-name-number-{i}",
                "type": "terraform" if i % 2 else "application",
                "description": (
                    f"Project {i}: a reasonably descriptive summary line that "
                    f"explains what this repository is responsible for in prose"
                ),
            }
            for i in range(17)
        }

    def test_all_17_projects_land_at_default_cap(self, monkeypatch):
        self._patch_rows(monkeypatch, self._payload_17())
        block = session_manifest.build_projects_context_block()
        entries = [l for l in block.splitlines() if l.startswith("- ")]
        assert len(entries) == 17, f"expected 17 entries, got {len(entries)}"
        # The tail entries (the ones the old 1400 cap dropped) must be present.
        assert any("project-name-number-16" in l for l in entries)
        assert any("project-name-number-15" in l for l in entries)
        assert "... (" not in block  # no truncation footer -- full set landed

    def test_overflow_always_ends_in_footer(self, monkeypatch):
        self._patch_rows(monkeypatch, self._payload_17())
        for cap in (150, 300, 600, 1000):
            block = session_manifest.build_projects_context_block(max_chars=cap)
            assert block, f"cap={cap} produced empty block"
            assert "more, use 'gaia context get')" in block, (
                f"cap={cap}: overflow dropped projects WITHOUT a footer"
            )
            assert len(block) <= cap, f"cap={cap}: block exceeded cap"
            # Footer count must equal the number actually omitted.
            kept = len([l for l in block.splitlines() if l.startswith("- ")])
            import re
            m = re.search(r"\.\.\. \((\d+) more", block)
            assert m, f"cap={cap}: footer count missing"
            assert int(m.group(1)) == 17 - kept, (
                f"cap={cap}: footer says {m.group(1)} more but {17 - kept} were dropped"
            )
