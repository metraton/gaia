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
    build_agentic_loop_block,
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
        assert "Workspace: my-workspace" in result

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


# ---------------------------------------------------------------------------
# build_agentic_loop_block
# ---------------------------------------------------------------------------

class TestBuildAgenticLoopBlock:
    def test_returns_detector_output_when_present(self, monkeypatch):
        """The block is a thin wrapper -- when the detector returns text,
        the builder must return it verbatim.
        """
        sentinel = "## Active Agentic Loop\nGoal: validate Y"
        import modules.context.agentic_loop_detector as detector
        monkeypatch.setattr(detector, "build_resume_context", lambda: sentinel)

        assert build_agentic_loop_block() == sentinel

    def test_returns_empty_when_detector_returns_empty(self, monkeypatch):
        import modules.context.agentic_loop_detector as detector
        monkeypatch.setattr(detector, "build_resume_context", lambda: "")

        assert build_agentic_loop_block() == ""

    def test_returns_empty_when_detector_raises(self, monkeypatch):
        import modules.context.agentic_loop_detector as detector

        def _boom():
            raise RuntimeError("simulated detector error")

        monkeypatch.setattr(detector, "build_resume_context", _boom)
        assert build_agentic_loop_block() == ""


# ---------------------------------------------------------------------------
# build_session_context (assembler)
# ---------------------------------------------------------------------------

class TestBuildSessionContext:
    """Pending approvals are no longer surfaced (M2): the assembler concatenates
    Environment, Projects, agentic-loop resume, and Workspace Memory -- there is
    no pending-approvals block in the join.
    """

    def test_assembles_all_blocks_with_blank_line_separator(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: "PROJ BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: "LOOP BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: "MEM BLOCK"
        )

        result = build_session_context()
        assert result == (
            "ENV BLOCK\n\nPROJ BLOCK\n\nLOOP BLOCK\n\nMEM BLOCK"
        ), (
            "Blocks must be joined with exactly one blank line separator -- "
            "markdown convention; agents render this as paragraph breaks. "
            "Project Context — Projects sits right after Environment. Pending "
            "approvals are no longer part of the manifest."
        )
        assert "[ACTIONABLE]" not in result

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
            session_manifest, "build_agentic_loop_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: "MEM BLOCK"
        )

        result = build_session_context()
        assert result == "ENV BLOCK\n\nMEM BLOCK"
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
            session_manifest, "build_agentic_loop_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: ""
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
            session_manifest, "build_agentic_loop_block", lambda: "LOOP"
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: ""
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
    """The extractor returns (name, path, type, description) 4-tuples so the
    Projects block can label each entry with its type and a short description."""

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
             "Terraform IaC for AOS GCP infra"),
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
        assert out == [("nfi", "/home/x/nfi", "application", "NFI app")]

    def test_missing_type_and_description_are_empty_strings(self):
        payload = {"proj": {"name": "p", "local_path": "/p"}}
        out = session_manifest._extract_projects_from_identity(
            payload, "ws", self._LOOKUP
        )
        assert out == [("p", "/p", "", "")]


class TestBuildProjectsBlockRendersTypeAndDescription:
    """The rendered Projects block includes type in parens and description
    after an em dash when present (CAMBIO 2)."""

    def _run_with_rows(self, monkeypatch, payload):
        import gaia.store.writer as _writer

        class _FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _FakeCon:
            def execute(self, sql, *a):
                if "project_context_contracts" in sql:
                    return _FakeCursor(
                        [{"workspace": "me", "payload": json.dumps(payload)}]
                    )
                return _FakeCursor([])  # projects table

            def close(self):
                pass

        monkeypatch.setattr(_writer, "_connect", lambda: _FakeCon())
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
        assert (
            "- aos-iac (terraform): /home/x/aos-iac — Terraform IaC for AOS GCP infra"
            in block
        )

    def test_no_type_no_description_stays_plain(self, monkeypatch):
        payload = {"p": {"name": "plainproj", "local_path": "/p"}}
        block = self._run_with_rows(monkeypatch, payload)
        assert "- plainproj: /p" in block
        assert "(" not in block.split("plainproj")[1].split("\n")[0]


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
        assert "more, see config/surface-routing.json" in block

    def test_real_config_has_all_surfaces(self):
        """Integration: against the shipped config, all 7 surfaces land."""
        block = session_manifest.build_contracts_index_block()
        # The shipped surface-routing.json defines these 7 surfaces.
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
            assert "more, see config/surface-routing.json" in block, (
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
        import gaia.store.writer as _writer

        class _FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _FakeCon:
            def execute(self, sql, *a):
                if "project_context_contracts" in sql:
                    return _FakeCursor(
                        [{"workspace": "me", "payload": json.dumps(payload)}]
                    )
                return _FakeCursor([])  # projects table

            def close(self):
                pass

        monkeypatch.setattr(_writer, "_connect", lambda: _FakeCon())

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
