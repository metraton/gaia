#!/usr/bin/env python3
"""
Integration test: Full subagent lifecycle.

Validates the complete hook-driven lifecycle:
  1. pre_tool_use hook injects project context into Task prompt
  2. Skills are injected natively by Claude from agent frontmatter (`skills:`)
  3. Subagent produces output with CONTEXT_UPDATE block
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
    seed_agent_perms,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def test_project(tmp_path):
    """
    Create a temporary project that mirrors a real gaia-ops installation.

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
            # These are empty - agents should fill them via CONTEXT_UPDATE
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
    """Isolated gaia.db with cloud-troubleshooter and terraform-architect permissions."""
    db_path = tmp_path / "gaia_lifecycle.db"
    bootstrap_gaia_schema(db_path)
    seed_workspace(db_path, "global")
    seed_agent_perms(
        db_path,
        "cloud-troubleshooter",
        reads=["cluster_details", "infrastructure_topology", "application_services",
               "monitoring_observability", "architecture_overview"],
        writes=["cluster_details", "infrastructure_topology", "application_services",
                "monitoring_observability", "architecture_overview"],
    )
    seed_agent_perms(
        db_path,
        "terraform-architect",
        reads=["terraform_infrastructure", "infrastructure_topology", "cluster_details",
               "application_services", "architecture_overview"],
        writes=["terraform_infrastructure", "infrastructure_topology"],
    )
    return db_path


def _clear_writer_cache():
    try:
        import context_writer as _cw
        _cw._permissions_cache.clear()
    except Exception:
        pass


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

    def test_terraform_architect_declares_terraform_patterns_skill(self, test_project):
        """terraform-architect frontmatter must include terraform-patterns."""
        _, claude_dir = test_project

        agent_file = claude_dir / "agents" / "terraform-architect.md"
        assert agent_file.exists(), "terraform-architect.md must exist"

        fm = self._parse_frontmatter(agent_file.read_text())
        skills = fm.get("skills", [])
        assert "terraform-patterns" in skills, \
            "terraform-architect should reference terraform-patterns skill"

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

    def test_pre_tool_use_caches_context_for_subagent_start(self, test_project):
        """
        pre_tool_use should cache project context for SubagentStart (not return
        additionalContext directly, which would go to the orchestrator).
        """
        tmp_path, claude_dir = test_project

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            import importlib.util
            pre_hook_path = claude_dir / "hooks" / "pre_tool_use.py"
            spec = importlib.util.spec_from_file_location("pre_tool_use_contract", str(pre_hook_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            result = mod.pre_tool_use_hook(
                "Task",
                {
                    "subagent_type": "cloud-troubleshooter",
                    "prompt": "Diagnose pod health in namespace test",
                },
            )

            # PreToolUse should NOT return additionalContext (that goes to orchestrator)
            assert result is None, \
                "PreToolUse:Agent should return None (context cached for SubagentStart)"

            # Verify context was cached
            from pathlib import Path
            cache_dir = Path("/tmp/gaia-context-cache")
            cache_files = list(cache_dir.glob("*.json"))
            assert len(cache_files) > 0, \
                "Context should be cached for SubagentStart to consume"

            import json
            cached = json.loads(cache_files[-1].read_text())
            assert "# Project Context" in cached["context"], \
                "Cached context should contain project context"
            assert "AGENT_STATUS" not in cached["context"], \
                "Hook should not inline agent-protocol skill text into context"

            # Clean up cache files
            for f in cache_files:
                f.unlink(missing_ok=True)
        finally:
            os.chdir(original_cwd)

    def test_subagent_start_records_runtime_skill_history(self, test_project):
        """SubagentStart should persist the agent's default skills snapshot."""
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

            skills_path = (
                claude_dir
                / "project-context"
                / "workflow-episodic-memory"
                / "agent-skills.jsonl"
            )
            assert skills_path.exists(), "SubagentStart should persist agent-skills.jsonl"

            skill_entry = json.loads(skills_path.read_text().strip().splitlines()[-1])
            assert skill_entry["agent"] == "cloud-troubleshooter"
            assert skill_entry["session_id"] == "sess-subagent-start-001"
            assert "agent-protocol" in skill_entry["skills"]
            assert skill_entry["skills_count"] >= 1
        finally:
            os.chdir(original_cwd)


# ============================================================================
# PHASE 2: CONTEXT_UPDATE Parsing (context_writer)
# ============================================================================

class TestPhase2ContextUpdateParsing:
    """Validate that context_writer correctly parses CONTEXT_UPDATE blocks."""

    def test_parse_valid_context_update(self):
        """A well-formed CONTEXT_UPDATE block (contract/payload) should be parsed."""
        from context_writer import parse_context_update

        agent_output = """
## Investigation Complete

Found the cluster details.

CONTEXT_UPDATE:
{
  "contract": "cluster_details",
  "payload": {
    "node_count": 3,
    "node_type": "e2-standard-4",
    "kubernetes_version": "1.28.5-gke.1200"
  }
}

```json:contract
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
        result = parse_context_update(agent_output)

        assert result is not None, "Should parse CONTEXT_UPDATE block"
        assert result["contract"] == "cluster_details"
        assert result["payload"]["node_count"] == 3
        assert result["payload"]["kubernetes_version"] == "1.28.5-gke.1200"

    def test_parse_no_context_update(self):
        """Output without CONTEXT_UPDATE should return None."""
        from context_writer import parse_context_update

        agent_output = """
## Investigation Complete

No new data found.

```json:contract
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
        result = parse_context_update(agent_output)
        assert result is None

    def test_parse_malformed_json(self):
        """Malformed JSON after CONTEXT_UPDATE should return None."""
        from context_writer import parse_context_update

        agent_output = """
CONTEXT_UPDATE:
{not valid json}
"""
        result = parse_context_update(agent_output)
        assert result is None

    def test_parse_context_update_with_markdown_code_fence(self):
        """Real LLM output: CONTEXT_UPDATE JSON wrapped in ```json fence.

        This test reproduces the exact bug observed in production on 2026-02-17.
        The cloud-troubleshooter agent emitted CONTEXT_UPDATE with markdown
        code fences, causing parse_context_update() to fail with:
          'Malformed JSON in CONTEXT_UPDATE block: Expecting value: line 1 column 1 (char 0)'
        """
        from context_writer import parse_context_update

        # Format from a real transcript, now wrapped in {contract, payload}.
        agent_output = (
            "INVESTIGATION COMPLETE\n"
            "\n"
            "**Cluster:** oci-pos-dev-cluster-01\n"
            "**Namespace:** test\n"
            "\n"
            "**Pod Count:** 1\n"
            "\n"
            "| Pod Name | Ready | Status | Restarts | Age |\n"
            "|----------|-------|--------|----------|-----|\n"
            "| nginx-deployment-6fbb6bcf74-8g9gn | 2/2 | Running | 0 | 8h |\n"
            "\n"
            "**Summary:**\n"
            "- There is **1 pod** running in the `test` namespace.\n"
            "\n"
            "CONTEXT_UPDATE:\n"
            "```json\n"
            "{\n"
            '  "contract": "cluster_details",\n'
            '  "payload": {\n'
            '    "cluster_name": "oci-pos-dev-cluster-01",\n'
            '    "namespaces_inspected": {\n'
            '      "test": {\n'
            '        "pod_count": 1,\n'
            '        "pods": [\n'
            "          {\n"
            '            "name": "nginx-deployment-6fbb6bcf74-8g9gn",\n'
            '            "ready": "2/2",\n'
            '            "status": "Running",\n'
            '            "restarts": 0\n'
            "          }\n"
            "        ],\n"
            '        "last_checked": "2026-02-17"\n'
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
            "```\n"
            "\n"
            "```json:contract\n"
            "{\n"
            '  "agent_status": {\n'
            '    "plan_status": "COMPLETE",\n'
            '    "agent_id": "cloud-troubleshooter",\n'
            '    "pending_steps": [],\n'
            '    "next_action": "done"\n'
            "  },\n"
            '  "evidence_report": {\n'
            '    "patterns_checked": [],\n'
            '    "files_checked": [],\n'
            '    "commands_run": [],\n'
            '    "key_outputs": [],\n'
            '    "verbatim_outputs": [],\n'
            '    "cross_layer_impacts": [],\n'
            '    "open_gaps": []\n'
            "  },\n"
            '  "consolidation_report": null\n'
            "}\n"
            "```"
        )
        result = parse_context_update(agent_output)

        assert result is not None, (
            "parse_context_update must handle ```json fenced code blocks — "
            "this is the exact format from a real cloud-troubleshooter transcript"
        )
        assert result["contract"] == "cluster_details"
        assert result["payload"]["cluster_name"] == "oci-pos-dev-cluster-01"
        pods = result["payload"]["namespaces_inspected"]["test"]["pods"]
        assert len(pods) == 1
        assert pods[0]["name"] == "nginx-deployment-6fbb6bcf74-8g9gn"


# ============================================================================
# PHASE 3: Permission Validation (DB-backed)
# ============================================================================

class TestPhase3PermissionValidation:
    """Validate that agents can only write to authorized contracts via DB."""

    def test_cloud_troubleshooter_can_write_cluster_details(self, lifecycle_db):
        """cloud-troubleshooter should be able to write to cluster_details."""
        _clear_writer_cache()
        from context_writer import validate_permission

        update = {"contract": "cluster_details", "payload": {"node_count": 3}}
        allowed, msg = validate_permission(update, "cloud-troubleshooter", db_path=lifecycle_db)

        assert allowed is True
        assert msg == ""

    def test_cloud_troubleshooter_cannot_write_gitops_configuration(self, lifecycle_db):
        """cloud-troubleshooter should NOT be able to write to gitops_configuration."""
        _clear_writer_cache()
        from context_writer import validate_permission

        update = {"contract": "gitops_configuration", "payload": {"repo_url": "http://example.com"}}
        allowed, msg = validate_permission(update, "cloud-troubleshooter", db_path=lifecycle_db)

        assert allowed is False
        assert "gitops_configuration" in msg

    def test_terraform_architect_can_write_infrastructure(self, lifecycle_db):
        """terraform-architect should be able to write terraform_infrastructure and infrastructure_topology."""
        _clear_writer_cache()
        from context_writer import validate_permission

        update_tf = {"contract": "terraform_infrastructure", "payload": {"modules_count": 12}}
        update_topo = {"contract": "infrastructure_topology", "payload": {"vpc_id": "vpc-123"}}

        allowed_tf, _ = validate_permission(update_tf, "terraform-architect", db_path=lifecycle_db)
        allowed_topo, _ = validate_permission(update_topo, "terraform-architect", db_path=lifecycle_db)

        assert allowed_tf is True
        assert allowed_topo is True


# ============================================================================
# PHASE 4: Full Lifecycle - Context Update Application (DB-backed)
# ============================================================================

class TestPhase4FullLifecycle:
    """End-to-end: process_agent_output writes to gaia.db."""

    def test_context_update_applied_to_db(self, lifecycle_db):
        """
        Simulate the complete lifecycle:
        1. Process agent output containing a CONTEXT_UPDATE block
        2. Verify gaia.db project_context_contracts was updated
        """
        _clear_writer_cache()
        from context_writer import process_agent_output

        agent_output = (
            "## Cloud Troubleshooter Report\n\n"
            "Investigated cluster `test-cluster` in GCP us-east4.\n\n"
            "CONTEXT_UPDATE:\n"
            + json.dumps({
                "contract": "cluster_details",
                "payload": {
                    "kubernetes_version": "1.28.5-gke.1200",
                    "node_count": 3,
                    "node_type": "e2-standard-4",
                    "status": "RUNNING",
                }
            }, indent=2)
        )

        task_info = {
            "agent_type": "cloud-troubleshooter",
            "db_path": lifecycle_db,
            "workspace": "global",
        }

        result = process_agent_output(agent_output, task_info)

        assert result["updated"] is True, f"Context should be updated, got: {result}"
        assert result["contract"] == "cluster_details"
        assert result["rejected"] == []

        stored = read_contract(lifecycle_db, "global", "cluster_details")
        assert stored is not None
        assert stored["kubernetes_version"] == "1.28.5-gke.1200"
        assert stored["node_count"] == 3
        assert stored["status"] == "RUNNING"

    def test_unauthorized_contract_rejected(self, lifecycle_db):
        """
        Agent trying to write to a contract it doesn't own should be rejected.
        cloud-troubleshooter writing to operational_guidelines → rejected.
        """
        _clear_writer_cache()
        from context_writer import process_agent_output

        agent_output = (
            "CONTEXT_UPDATE:\n"
            + json.dumps({
                "contract": "operational_guidelines",
                "payload": {"commit_standards": "HIJACKED"}
            })
        )

        task_info = {
            "agent_type": "cloud-troubleshooter",
            "db_path": lifecycle_db,
            "workspace": "global",
        }

        result = process_agent_output(agent_output, task_info)

        assert result["updated"] is False
        assert "operational_guidelines" in result["rejected"]

        stored = read_contract(lifecycle_db, "global", "operational_guidelines")
        assert stored is None, "Rejected contract must not be written to DB"

    def test_second_write_replaces_contract(self, lifecycle_db):
        """Second write to the same contract replaces the payload (upsert)."""
        _clear_writer_cache()
        from context_writer import process_agent_output

        def write(node_count):
            output = (
                "CONTEXT_UPDATE:\n"
                + json.dumps({
                    "contract": "cluster_details",
                    "payload": {"node_count": node_count}
                })
            )
            return process_agent_output(
                output,
                {"agent_type": "cloud-troubleshooter", "db_path": lifecycle_db, "workspace": "global"}
            )

        r1 = write(3)
        r2 = write(5)

        assert r1["updated"] is True
        assert r2["updated"] is True

        stored = read_contract(lifecycle_db, "global", "cluster_details")
        assert stored["node_count"] == 5

    def test_audit_record_written_on_success(self, lifecycle_db):
        """A successful CONTEXT_UPDATE is reflected in the DB contract row."""
        _clear_writer_cache()
        from context_writer import process_agent_output

        output = (
            "CONTEXT_UPDATE:\n"
            + json.dumps({
                "contract": "infrastructure_topology",
                "payload": {"vpc_name": "main-vpc"}
            })
        )
        task_info = {
            "agent_type": "cloud-troubleshooter",
            "db_path": lifecycle_db,
            "workspace": "global",
        }

        result = process_agent_output(output, task_info)

        assert result["updated"] is True
        assert result["contract"] == "infrastructure_topology"

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
    """Test the subagent_stop_hook processes CONTEXT_UPDATE end-to-end."""

    def test_subagent_stop_processes_context_update(self, test_project, lifecycle_db):
        """
        subagent_stop_hook should:
        1. Capture metrics
        2. Process CONTEXT_UPDATE via context_writer (DB write)
        3. Return context_updated=True
        """
        _clear_writer_cache()
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

            agent_output = (
                "## Cluster Health Report\n\n"
                "All nodes healthy. Cluster version: 1.28.5-gke.1200\n\n"
                "CONTEXT_UPDATE:\n"
                + json.dumps({
                    "contract": "cluster_details",
                    "payload": {
                        "kubernetes_version": "1.28.5-gke.1200",
                        "health_status": "HEALTHY",
                        "node_count": 3,
                    }
                }, indent=2)
                + "\n\n"
                "```json:contract\n"
                '{\n'
                '  "agent_status": {\n'
                '    "plan_status": "COMPLETE",\n'
                '    "agent_id": "test-agent",\n'
                '    "pending_steps": [],\n'
                '    "next_action": "done"\n'
                '  },\n'
                '  "evidence_report": {\n'
                '    "patterns_checked": [],\n'
                '    "files_checked": [],\n'
                '    "commands_run": [],\n'
                '    "key_outputs": [],\n'
                '    "verbatim_outputs": [],\n'
                '    "cross_layer_impacts": [],\n'
                '    "open_gaps": []\n'
                '  },\n'
                '  "consolidation_report": null\n'
                '}\n'
                "```\n"
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

            result = mod.subagent_stop_hook(task_info, agent_output)

            assert result["success"] is True, f"subagent_stop_hook should succeed: {result}"
            assert result["metrics_captured"] is True
            assert result["context_updated"] is True, f"Context should be marked as updated: {result}"

            stored = read_contract(lifecycle_db, "global", "cluster_details")
            assert stored is not None
            assert stored["kubernetes_version"] == "1.28.5-gke.1200"
            assert stored["health_status"] == "HEALTHY"
            assert stored["node_count"] == 3

        finally:
            os.chdir(original_cwd)
            os.environ.pop("WORKFLOW_MEMORY_BASE_PATH", None)
            os.environ.pop("GAIA_WRITE_WORKFLOW_METRICS", None)

    def test_subagent_stop_without_context_update(self, test_project):
        """When agent output has no CONTEXT_UPDATE, context_updated should be False."""
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

```json:contract
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
