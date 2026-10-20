#!/usr/bin/env python3
"""
Integration test: Full subagent lifecycle.

Validates the complete hook-driven lifecycle:
  1. pre_tool_use hook injects project context into Task prompt
  2. Skills are injected natively by Claude from agent frontmatter (`skills:`)
  3. Subagent produces output with an update_contracts envelope clause
  4. subagent_stop hook processes the output and updates gaia.db

This tests the REAL hook code (no mocks) against a temporary project
structure to ensure the full pipeline works end-to-end.
"""

import json
import os
import shutil
import sys
import sqlite3
import pytest
from pathlib import Path

# ============================================================================
# PATH SETUP - import the actual hook modules
# ============================================================================
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.tiers import SecurityTier
from modules.tools.task_validator import AVAILABLE_AGENTS, META_AGENTS
from modules.agents.response_contract import clear_contract_dir_cache
from modules.core.paths import clear_path_cache

# Import context_writer directly for validation
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))

# DB helpers
from tests.fixtures.db_helpers import (
    bootstrap_gaia_schema,
    seed_workspace,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def test_project(tmp_path):
    """
    Create a temporary project that mirrors a real gaia installation.

    Structure:
        tmp_path/
            .claude/
                agents/          (copied from repo)
                skills/          (copied from repo)
                config/          (copied from repo)
                hooks/           (copied from repo)
                project-context/
                    project-context.json  (minimal, with empty sections)
    """
    clear_path_cache()
    clear_contract_dir_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # Copy agents
    shutil.copytree(REPO_ROOT / "agents", claude_dir / "agents")

    # Copy skills
    shutil.copytree(REPO_ROOT / "skills", claude_dir / "skills")

    # Copy config (contracts)
    shutil.copytree(REPO_ROOT / "config", claude_dir / "config")

    # Copy hooks
    shutil.copytree(REPO_ROOT / "hooks", claude_dir / "hooks")

    # Copy tools inside .claude/ (context_writer resolves deep_merge
    # relative to hooks parent, which is .claude/ in installed projects)
    shutil.copytree(REPO_ROOT / "tools", claude_dir / "tools")

    # Create project-context.json with empty writable sections
    pc_dir = claude_dir / "project-context"
    pc_dir.mkdir()
    pc_data = {
        "metadata": {
            "project_name": "test-lifecycle",
            "cloud_provider": "gcp",
            "primary_region": "us-east4",
        },
        "sections": {
            "project_identity": {"name": "test-lifecycle", "type": "application"},
            "stack": {},
            "git": {"platform": "github"},
            "environment": {"runtimes": []},
            "infrastructure": {"cloud_providers": [{"name": "gcp", "region": "us-east4"}]},
            # These are empty - agents should fill them via update_contracts
            "cluster_details": {},
            "infrastructure_topology": {},
            "terraform_infrastructure": {},
            "gitops_configuration": {},
            "application_services": {},
        }
    }
    (pc_dir / "project-context.json").write_text(json.dumps(pc_data, indent=2))

    yield tmp_path, claude_dir
    clear_path_cache()
    clear_contract_dir_cache()


@pytest.fixture
def lifecycle_db(tmp_path):
    """Isolated gaia.db with the schema and the ``global`` workspace."""
    db_path = tmp_path / "gaia_lifecycle.db"
    bootstrap_gaia_schema(db_path)
    seed_workspace(db_path, "global")
    return db_path


def read_contract(db_path: Path, workspace: str, contract_name: str):
    """Read back a contract payload from the DB; returns parsed dict or None."""
    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT payload FROM project_context_contracts WHERE workspace=? AND contract_name=?",
        (workspace, contract_name),
    ).fetchone()
    con.close()
    return json.loads(row[0]) if row else None


# ============================================================================
# PHASE 1: Skills Contract + Prompt Injection
# ============================================================================

class TestPhase1SkillsInjection:
    """Validate modern skills contract (frontmatter + native injection model)."""

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """Minimal frontmatter parser for test assertions."""
        if not text.startswith("---"):
            return {}

        try:
            end = text.index("---", 3)
        except ValueError:
            return {}

        fm = text[3:end]
        result = {}
        current_key = None
        current_list = None

        for line in fm.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("- ") and current_key and current_list is not None:
                current_list.append(stripped[2:].strip())
                continue

            if ":" in stripped:
                if current_key and current_list is not None:
                    result[current_key] = current_list

                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                if value:
                    result[key] = value
                    current_key = key
                    current_list = None
                else:
                    current_key = key
                    current_list = []

        if current_key and current_list is not None:
            result[current_key] = current_list

        return result

    def test_project_agent_declares_skills_in_frontmatter(self, test_project):
        """Project agents must declare skills via frontmatter (native injection model)."""
        tmp_path, claude_dir = test_project

        agent_file = claude_dir / "agents" / "cloud-troubleshooter.md"
        assert agent_file.exists(), "cloud-troubleshooter.md must exist"

        fm = self._parse_frontmatter(agent_file.read_text())
        skills = fm.get("skills", [])

        assert isinstance(skills, list) and len(skills) > 0, \
            "cloud-troubleshooter must declare skills in frontmatter"
        assert "security-tiers" in skills
        assert "agent-protocol" in skills

    def test_all_project_agents_reference_existing_skill_files(self, test_project):
        """
        Every non-meta agent must reference existing skill directories.
        This validates the native Claude `skills:` loading contract.
        """
        _, claude_dir = test_project
        project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]

        for agent in project_agents:
            agent_file = claude_dir / "agents" / f"{agent}.md"
            if not agent_file.exists():
                continue

            fm = self._parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            assert isinstance(skills, list) and len(skills) > 0, \
                f"Agent '{agent}' should declare at least one skill in frontmatter"

            for skill in skills:
                skill_md = claude_dir / "skills" / skill / "SKILL.md"
                assert skill_md.exists(), \
                    f"Agent '{agent}' references missing skill file: {skill_md}"
                content = skill_md.read_text().strip()
                assert len(content) > 100, \
                    f"Skill '{skill}' content too short for agent '{agent}'"

    def test_pre_tool_use_task_injects_nothing_and_preloads_no_context(
        self, test_project, monkeypatch,
    ):
        """
        Dispatch-kernel contract: PreToolUse:Task returns no additionalContext (the
        subagent's payload is the dispatch kernel claimed at SubagentStart),
        and whatever the cache bridge carries is never a preloaded
        project-context snapshot.
        """
        tmp_path, claude_dir = test_project

        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            import sys as _sys
            repo_root = Path(__file__).resolve().parents[2]
            if str(repo_root) not in _sys.path:
                _sys.path.insert(0, str(repo_root))
            from tests.fixtures.pretool_adapter import (
                compat_shape,
                run_pre_tool_use,
            )

            session_marker = "sess-lifecycle-cache-contract"
            response = run_pre_tool_use(
                "Task",
                {
                    "subagent_type": "cloud-troubleshooter",
                    "prompt": "Diagnose pod health in namespace test",
                },
                session_id=session_marker,
            )

            # PreToolUse must NOT return additionalContext (it would land on
            # the orchestrator, not the subagent).
            assert compat_shape(response) is None, \
                "PreToolUse:Agent should return no payload (kernel is claimed at SubagentStart)"

            # Whatever bridge entries exist for this session, none preloads
            # project context.
            import json
            from adapters.claude_code import ClaudeCodeAdapter
            cache_dir = ClaudeCodeAdapter.CONTEXT_CACHE_DIR
            for f in cache_dir.glob(f"{session_marker}-*.json"):
                cached = json.loads(f.read_text())
                assert "# Project Context" not in cached.get("context", ""), \
                    "The cache bridge must not carry a preloaded project-context snapshot"
                f.unlink(missing_ok=True)
        finally:
            os.chdir(original_cwd)

    def test_subagent_start_does_not_persist_skill_history_jsonl(self, test_project):
        """SubagentStart no longer writes a file-based skill-history snapshot.

        The legacy ``agent-skills.jsonl`` writer was retired by the
        ``episodic-workflow-to-db`` brief. SubagentStart now only injects the
        cached project context and logs the dispatch; the agent's default
        skills snapshot (``default_skills_snapshot``) is built later by
        ``workflow_recorder.record()`` and persisted on the ``episodes`` table
        in gaia.db via ``episode_writer.write()`` -> ``store_episode()`` at
        SubagentStop, not as a JSONL file at SubagentStart.

        This test pins the current contract: SubagentStart exits cleanly and
        the retired JSONL file is never produced. The skills snapshot source
        (``load_agent_runtime_profile``) is still asserted directly so the
        test continues to guard that the agent's declared skills resolve.
        """
        tmp_path, claude_dir = test_project

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            import importlib.util

            start_hook_path = claude_dir / "hooks" / "subagent_start.py"
            spec = importlib.util.spec_from_file_location("subagent_start_runtime", str(start_hook_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            stdin_payload = json.dumps({
                "hook_event_name": "SubagentStart",
                "session_id": "sess-subagent-start-001",
                "agent_type": "cloud-troubleshooter",
                "task_description": "Investigate rollout telemetry drift",
            })

            # _handle_subagent_start now accepts a pre-parsed HookEvent,
            # so parse the payload via the adapter first.
            from adapters.claude_code import ClaudeCodeAdapter
            adapter = ClaudeCodeAdapter()
            event = adapter.parse_event(stdin_payload)

            with pytest.raises(SystemExit) as exc:
                mod._handle_subagent_start(event)

            assert exc.value.code == 0

            # The retired file-based snapshot must NOT be produced anymore.
            skills_path = (
                claude_dir
                / "project-context"
                / "workflow-episodic-memory"
                / "agent-skills.jsonl"
            )
            assert not skills_path.exists(), (
                "agent-skills.jsonl is retired; SubagentStart must not write it. "
                "The skills snapshot now lives on the episodes table in gaia.db."
            )

            # The snapshot source still resolves the agent's declared skills.
            from modules.audit.workflow_recorder import load_agent_runtime_profile
            profile = load_agent_runtime_profile("cloud-troubleshooter")
            assert "agent-protocol" in profile["skills"]
            assert profile["skills_count"] >= 1
        finally:
            os.chdir(original_cwd)


# ============================================================================
# PHASE 2: update_contracts clause parsing (contract envelope)
# ============================================================================

class TestPhase2ContextUpdateParsing:
    """Validate that the envelope path extracts update_contracts entries.

    The legacy CONTEXT_UPDATE text marker was retired; enrichment now travels
    as a top-level ``update_contracts`` array inside the agent_contract_handoff
    envelope. parse_contract() extracts the envelope and parse_update_contracts()
    returns its entries.
    """

    def test_parse_valid_context_update(self):
        """A well-formed update_contracts entry should be parsed from the envelope."""
        from modules.agents.contract_validator import (
            parse_contract,
            parse_update_contracts,
        )

        agent_output = """
## Investigation Complete

Found the cluster details.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "test-agent",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [],
    "files_checked": [],
    "commands_run": [],
    "key_outputs": [],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation_report": null,
  "update_contracts": [
    {
      "contract": "cluster_details",
      "payload": {
        "node_count": 3,
        "node_type": "e2-standard-4",
        "kubernetes_version": "1.28.5-gke.1200"
      }
    }
  ]
}
```
"""
        entries = parse_update_contracts(parse_contract(agent_output) or {})

        assert len(entries) == 1, "Should parse one update_contracts entry"
        entry = entries[0]
        assert entry["contract"] == "cluster_details"
        assert entry["payload"]["node_count"] == 3
        assert entry["payload"]["kubernetes_version"] == "1.28.5-gke.1200"

    def test_parse_no_context_update(self):
        """Envelope without update_contracts yields no entries."""
        from modules.agents.contract_validator import (
            parse_contract,
            parse_update_contracts,
        )

        agent_output = """
## Investigation Complete

No new data found.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "test-agent",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [],
    "files_checked": [],
    "commands_run": [],
    "key_outputs": [],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation_report": null
}
```
"""
        entries = parse_update_contracts(parse_contract(agent_output) or {})
        assert entries == []

    def test_parse_malformed_json(self):
        """An entry missing required keys is skipped, not raised."""
        from modules.agents.contract_validator import parse_update_contracts

        # contract dict with a structurally-broken entry (no payload key)
        contract = {"update_contracts": [{"contract": "cluster_details"}]}
        entries = parse_update_contracts(contract)
        assert entries == []

    def test_parse_context_update_with_nested_payload(self):
        """A deeply-nested payload survives the envelope parse intact.

        Reproduces the real cloud-troubleshooter enrichment shape, now carried
        as an update_contracts entry rather than the retired text marker.
        """
        from modules.agents.contract_validator import (
            parse_contract,
            parse_update_contracts,
        )

        envelope = {
            "agent_status": {
                "plan_status": "COMPLETE",
                "agent_id": "cloud-troubleshooter",
                "pending_steps": [],
                "next_action": "done",
            },
            "evidence_report": {
                "patterns_checked": [],
                "files_checked": [],
                "commands_run": [],
                "key_outputs": [],
                "verbatim_outputs": [],
                "cross_layer_impacts": [],
                "open_gaps": [],
            },
            "consolidation_report": None,
            "update_contracts": [
                {
                    "contract": "cluster_details",
                    "payload": {
                        "cluster_name": "oci-pos-dev-cluster-01",
                        "namespaces_inspected": {
                            "test": {
                                "pod_count": 1,
                                "pods": [
                                    {
                                        "name": "nginx-deployment-6fbb6bcf74-8g9gn",
                                        "ready": "2/2",
                                        "status": "Running",
                                        "restarts": 0,
                                    }
                                ],
                                "last_checked": "2026-02-17",
                            }
                        },
                    },
                }
            ],
        }
        agent_output = (
            "INVESTIGATION COMPLETE\n\n"
            "```agent_contract_handoff\n" + json.dumps(envelope, indent=2) + "\n```"
        )
        entries = parse_update_contracts(parse_contract(agent_output) or {})

        assert len(entries) == 1
        entry = entries[0]
        assert entry["contract"] == "cluster_details"
        assert entry["payload"]["cluster_name"] == "oci-pos-dev-cluster-01"
        pods = entry["payload"]["namespaces_inspected"]["test"]["pods"]
        assert len(pods) == 1
        assert pods[0]["name"] == "nginx-deployment-6fbb6bcf74-8g9gn"


# ============================================================================
# PHASE 4: Full Lifecycle - proposal at stop, write on accept (DB-backed)
# ============================================================================

class TestPhase4ProposeThenAccept:
    """A specialist's close records a proposal; apply_proposal writes it."""

    @staticmethod
    def _make_contract(contract_name, payload):
        """Build a parsed contract dict carrying one update_contracts entry."""
        return {
            "agent_status": {"plan_status": "COMPLETE"},
            "update_contracts": [{"contract": contract_name, "payload": payload}],
        }

    @staticmethod
    def _task_info(db_path):
        return {
            "agent_type": "cloud-troubleshooter",
            "db_path": db_path,
            "workspace": "global",
        }

    def test_stop_writes_nothing_and_proposes(self, lifecycle_db):
        """Processing the clause at stop lists the proposal and leaves gaia.db untouched."""
        from hooks.modules.context.context_writer import process_update_contracts

        contract = self._make_contract("cluster_details", {
            "kubernetes_version": "1.28.5-gke.1200",
            "node_count": 3,
            "node_type": "e2-standard-4",
            "status": "RUNNING",
        })

        result = process_update_contracts(contract, self._task_info(lifecycle_db))

        assert result["updated"] is False, f"Stop must not write context, got: {result}"
        assert result["rejected"] == []
        assert result["proposed"] == [{
            "contract": "cluster_details",
            "workspace": "global",
            "keys": ["kubernetes_version", "node_count", "node_type", "status"],
        }]
        assert read_contract(lifecycle_db, "global", "cluster_details") is None

    def test_accepting_the_proposal_writes_it_to_the_db(self, lifecycle_db):
        """apply_proposal performs the write the stop only announced."""
        from hooks.modules.context.context_writer import apply_proposal

        contract = self._make_contract("cluster_details", {
            "kubernetes_version": "1.28.5-gke.1200",
            "node_count": 3,
            "status": "RUNNING",
        })

        outcome = apply_proposal(
            contract["update_contracts"], "global", db_path=lifecycle_db,
        )

        assert outcome["errors"] == []
        assert outcome["applied"] == [{"contract": "cluster_details", "workspace": "global"}]
        stored = read_contract(lifecycle_db, "global", "cluster_details")
        assert stored["kubernetes_version"] == "1.28.5-gke.1200"
        assert stored["node_count"] == 3
        assert stored["status"] == "RUNNING"

    def test_dry_run_accept_writes_nothing(self, lifecycle_db):
        """A dry-run accept lists its targets and leaves gaia.db untouched."""
        from hooks.modules.context.context_writer import apply_proposal

        clause = self._make_contract("cluster_details", {"node_count": 3})["update_contracts"]

        outcome = apply_proposal(clause, "global", db_path=lifecycle_db, dry_run=True)

        assert outcome["applied"] == [{"contract": "cluster_details", "workspace": "global"}]
        assert read_contract(lifecycle_db, "global", "cluster_details") is None

    def test_second_accept_overwrites_the_scalar(self, lifecycle_db):
        """A second accepted write to the same contract upserts the changed value."""
        from hooks.modules.context.context_writer import apply_proposal

        for node_count in (3, 5):
            clause = self._make_contract(
                "cluster_details", {"node_count": node_count},
            )["update_contracts"]
            outcome = apply_proposal(clause, "global", db_path=lifecycle_db)
            assert outcome["errors"] == []

        stored = read_contract(lifecycle_db, "global", "cluster_details")
        assert stored["node_count"] == 5

    def test_accepted_write_is_reflected_in_the_contract_row(self, lifecycle_db):
        """An accepted proposal leaves exactly one stamped row for its section."""
        from hooks.modules.context.context_writer import apply_proposal

        clause = self._make_contract(
            "infrastructure_topology", {"vpc_name": "main-vpc"}
        )["update_contracts"]

        outcome = apply_proposal(clause, "global", db_path=lifecycle_db)

        assert outcome["applied"] == [
            {"contract": "infrastructure_topology", "workspace": "global"}
        ]

        con = sqlite3.connect(str(lifecycle_db))
        rows = con.execute(
            "SELECT workspace, contract_name, updated_at FROM project_context_contracts "
            "WHERE contract_name='infrastructure_topology'"
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert rows[0][0] == "global"
        assert rows[0][2] is not None  # updated_at set


# ============================================================================
# PHASE 5: subagent_stop_hook Full Processing (DB-backed)
# ============================================================================

class TestPhase5SubagentStopHook:
    """Test the subagent_stop_hook processes update_contracts end-to-end."""

    def test_subagent_stop_records_context_update_as_a_proposal(
        self, test_project, lifecycle_db,
    ):
        """
        subagent_stop_hook should:
        1. Capture metrics
        2. Route update_contracts through context_writer as a proposal
        3. Write nothing: context_updated=False and gaia.db keeps no row
        """
        tmp_path, claude_dir = test_project

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            import importlib.util
            stop_hook_path = claude_dir / "hooks" / "subagent_stop.py"
            spec = importlib.util.spec_from_file_location(
                "subagent_stop_lifecycle", str(stop_hook_path)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            os.environ["WORKFLOW_MEMORY_BASE_PATH"] = str(claude_dir)
            os.environ["GAIA_WRITE_WORKFLOW_METRICS"] = "1"

            envelope = {
                "agent_status": {
                    "plan_status": "COMPLETE",
                    "agent_id": "test-agent",
                    "pending_steps": [],
                    "next_action": "done",
                },
                "evidence_report": {
                    "patterns_checked": [],
                    "files_checked": [],
                    "commands_run": [],
                    "key_outputs": [],
                    "verbatim_outputs": [],
                    "cross_layer_impacts": [],
                    "open_gaps": [],
                },
                "consolidation_report": None,
                "update_contracts": [
                    {
                        "contract": "cluster_details",
                        "payload": {
                            "kubernetes_version": "1.28.5-gke.1200",
                            "health_status": "HEALTHY",
                            "node_count": 3,
                        },
                    }
                ],
            }
            agent_output = (
                "## Cluster Health Report\n\n"
                "All nodes healthy. Cluster version: 1.28.5-gke.1200\n\n"
                "```agent_contract_handoff\n"
                + json.dumps(envelope, indent=2)
                + "\n```\n"
            )

            task_info = {
                "task_id": "test-lifecycle-001",
                "agent_id": "test-lifecycle-001",
                "description": "Diagnose cluster health",
                "agent": "cloud-troubleshooter",
                "tier": "T0",
                "tags": ["#diagnostic"],
                "db_path": lifecycle_db,
                "workspace": "global",
            }

            processed = []
            real_process = mod.process_update_contracts

            def spy(contract_dict, info):
                processed.append(real_process(contract_dict, info))
                return processed[-1]

            mod.process_update_contracts = spy

            result = mod.subagent_stop_hook(task_info, agent_output)

            assert result["success"] is True, f"subagent_stop_hook should succeed: {result}"
            assert result["metrics_captured"] is True
            assert result["context_updated"] is False
            assert [p["contract"] for r in processed for p in r["proposed"]] == ["cluster_details"]
            assert read_contract(lifecycle_db, "global", "cluster_details") is None

        finally:
            os.chdir(original_cwd)
            os.environ.pop("WORKFLOW_MEMORY_BASE_PATH", None)
            os.environ.pop("GAIA_WRITE_WORKFLOW_METRICS", None)

    def test_subagent_stop_without_context_update(self, test_project):
        """When agent output has no update_contracts, context_updated should be False."""
        tmp_path, claude_dir = test_project

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            import importlib.util
            stop_hook_path = claude_dir / "hooks" / "subagent_stop.py"
            spec = importlib.util.spec_from_file_location(
                "subagent_stop_lifecycle2", str(stop_hook_path)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            os.environ["WORKFLOW_MEMORY_BASE_PATH"] = str(claude_dir)

            task_info = {
                "task_id": "test-lifecycle-002",
                "description": "Simple diagnostic",
                "agent": "cloud-troubleshooter",
                "tier": "T0",
                "tags": [],
            }

            agent_output = """
## Report
Everything looks fine. No changes needed.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "test-agent",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [],
    "files_checked": [],
    "commands_run": [],
    "key_outputs": [],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation_report": null
}
```
"""

            result = mod.subagent_stop_hook(task_info, agent_output)

            assert result["success"] is True
            assert result["context_updated"] is False

        finally:
            os.chdir(original_cwd)
            os.environ.pop("WORKFLOW_MEMORY_BASE_PATH", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
