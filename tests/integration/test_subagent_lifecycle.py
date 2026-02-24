#!/usr/bin/env python3
"""
Integration test: Full subagent lifecycle.

Validates the complete hook-driven lifecycle:
  1. pre_tool_use hook injects project context into Task prompt
  2. Skills are injected natively by Claude from agent frontmatter (`skills:`)
  3. Subagent produces output with CONTEXT_UPDATE block
  3. subagent_stop hook processes the output and updates project-context.json

This tests the REAL hook code (no mocks) against a temporary project
structure to ensure the full pipeline works end-to-end.
"""

import json
import os
import shutil
import sys
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

# Import context_writer directly for validation
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))


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
            "project_details": {
                "cluster_name": "test-cluster"
            },
            # These are empty - agents should fill them via CONTEXT_UPDATE
            "cluster_details": {},
            "infrastructure_topology": {},
            "terraform_infrastructure": {},
            "gitops_configuration": {},
            "application_services": {},
        }
    }
    (pc_dir / "project-context.json").write_text(json.dumps(pc_data, indent=2))

    return tmp_path, claude_dir


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
        assert "context-updater" in skills

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

    def test_pre_tool_use_injects_context_but_not_inline_skill_text(self, test_project):
        """
        pre_tool_use should inject project context only.
        Skill text is not concatenated by hook; Claude loads skills natively.
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

            assert isinstance(result, dict), "Task call should return updatedInput when context is injected"
            updated = result["hookSpecificOutput"]["updatedInput"]["prompt"]

            assert "# Project Context (Auto-Injected)" in updated
            assert "# User Task" in updated
            assert "Diagnose pod health in namespace test" in updated
            assert "AGENT_STATUS" not in updated, \
                "Hook should not inline agent-protocol skill text into prompt"
        finally:
            os.chdir(original_cwd)


# ============================================================================
# PHASE 2: CONTEXT_UPDATE Parsing (context_writer)
# ============================================================================

class TestPhase2ContextUpdateParsing:
    """Validate that context_writer correctly parses CONTEXT_UPDATE blocks."""

    def test_parse_valid_context_update(self):
        """A well-formed CONTEXT_UPDATE block should be parsed correctly."""
        from context_writer import parse_context_update

        agent_output = """
## Investigation Complete

Found the cluster details.

CONTEXT_UPDATE:
{
  "cluster_details": {
    "node_count": 3,
    "node_type": "e2-standard-4",
    "kubernetes_version": "1.28.5-gke.1200"
  }
}

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
CURRENT_PHASE: Complete
PENDING_STEPS: None
NEXT_ACTION: Report findings to orchestrator
<!-- /AGENT_STATUS -->
"""
        result = parse_context_update(agent_output)

        assert result is not None, "Should parse CONTEXT_UPDATE block"
        assert "cluster_details" in result
        assert result["cluster_details"]["node_count"] == 3
        assert result["cluster_details"]["kubernetes_version"] == "1.28.5-gke.1200"

    def test_parse_no_context_update(self):
        """Output without CONTEXT_UPDATE should return None."""
        from context_writer import parse_context_update

        agent_output = """
## Investigation Complete

No new data found.

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
<!-- /AGENT_STATUS -->
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

        # Exact format from real transcript (agent-af097c4.jsonl)
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
            '  "cluster_details": {\n'
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
            "<!-- AGENT_STATUS -->\n"
            "PLAN_STATUS: COMPLETE\n"
            "CURRENT_PHASE: Complete\n"
            "PENDING_STEPS: None\n"
            "NEXT_ACTION: None - task complete\n"
            "AGENT_ID: cloud-troubleshooter\n"
            "<!-- /AGENT_STATUS -->"
        )
        result = parse_context_update(agent_output)

        assert result is not None, (
            "parse_context_update must handle ```json fenced code blocks — "
            "this is the exact format from a real cloud-troubleshooter transcript"
        )
        assert result["cluster_details"]["cluster_name"] == "oci-pos-dev-cluster-01"
        pods = result["cluster_details"]["namespaces_inspected"]["test"]["pods"]
        assert len(pods) == 1
        assert pods[0]["name"] == "nginx-deployment-6fbb6bcf74-8g9gn"


# ============================================================================
# PHASE 3: Permission Validation
# ============================================================================

class TestPhase3PermissionValidation:
    """Validate that agents can only write to authorized sections."""

    def test_cloud_troubleshooter_can_write_cluster_details(self, test_project):
        """cloud-troubleshooter should be able to write to cluster_details."""
        from context_writer import validate_permissions

        _, claude_dir = test_project
        contracts = json.loads(
            (claude_dir / "config" / "context-contracts.gcp.json").read_text()
        )

        update = {"cluster_details": {"node_count": 3}}
        allowed, rejected = validate_permissions(update, "cloud-troubleshooter", contracts)

        assert "cluster_details" in allowed
        assert len(rejected) == 0

    def test_cloud_troubleshooter_cannot_write_application_services(self, test_project):
        """cloud-troubleshooter should NOT be able to write to application_services."""
        from context_writer import validate_permissions

        _, claude_dir = test_project
        contracts = json.loads(
            (claude_dir / "config" / "context-contracts.gcp.json").read_text()
        )

        update = {"application_services": {"api_url": "http://example.com"}}
        allowed, rejected = validate_permissions(update, "cloud-troubleshooter", contracts)

        assert "application_services" not in allowed
        assert "application_services" in rejected

    def test_terraform_architect_can_write_infrastructure(self, test_project):
        """terraform-architect should be able to write terraform_infrastructure."""
        from context_writer import validate_permissions

        _, claude_dir = test_project
        contracts = json.loads(
            (claude_dir / "config" / "context-contracts.gcp.json").read_text()
        )

        update = {
            "terraform_infrastructure": {"modules_count": 12},
            "infrastructure_topology": {"vpc_id": "vpc-123"},
        }
        allowed, rejected = validate_permissions(update, "terraform-architect", contracts)

        assert "terraform_infrastructure" in allowed
        assert "infrastructure_topology" in allowed
        assert len(rejected) == 0


# ============================================================================
# PHASE 4: Full Lifecycle - Context Update Application
# ============================================================================

class TestPhase4FullLifecycle:
    """End-to-end: skills loaded → agent output → context updated."""

    def test_context_update_applied_to_project_context(self, test_project):
        """
        Simulate the complete lifecycle:
        1. Verify project-context starts with empty cluster_details
        2. Process agent output containing CONTEXT_UPDATE
        3. Verify project-context.json was updated
        """
        tmp_path, claude_dir = test_project
        pc_path = claude_dir / "project-context" / "project-context.json"

        # --- BEFORE: cluster_details is empty ---
        before = json.loads(pc_path.read_text())
        assert before["sections"]["cluster_details"] == {}, \
            "cluster_details should start empty"

        # --- SIMULATE: Agent output with CONTEXT_UPDATE ---
        agent_output = """
## Cloud Troubleshooter Report

Investigated cluster `test-cluster` in GCP us-east4.

### Findings
- Cluster is running GKE 1.28.5
- 3 nodes of type e2-standard-4
- All nodes healthy

CONTEXT_UPDATE:
{
  "cluster_details": {
    "kubernetes_version": "1.28.5-gke.1200",
    "node_count": 3,
    "node_type": "e2-standard-4",
    "status": "RUNNING"
  },
  "infrastructure_topology": {
    "vpc_name": "test-vpc",
    "subnet_cidr": "10.0.0.0/20"
  }
}

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
CURRENT_PHASE: Complete
PENDING_STEPS: None
NEXT_ACTION: Report findings to orchestrator
<!-- /AGENT_STATUS -->
"""

        # --- PROCESS via context_writer ---
        from context_writer import process_agent_output

        task_info = {
            "agent_type": "cloud-troubleshooter",
            "context_path": str(pc_path),
            "config_dir": str(claude_dir / "config"),
        }

        result = process_agent_output(agent_output, task_info)

        # --- VERIFY: result indicates success ---
        assert result["updated"] is True, \
            f"Context should be updated, got: {result}"
        assert "cluster_details" in result["sections_updated"]
        assert "infrastructure_topology" in result["sections_updated"]
        assert len(result["rejected"]) == 0, \
            f"No sections should be rejected: {result['rejected']}"

        # --- AFTER: project-context.json should have the data ---
        after = json.loads(pc_path.read_text())

        cd = after["sections"]["cluster_details"]
        assert cd["kubernetes_version"] == "1.28.5-gke.1200"
        assert cd["node_count"] == 3
        assert cd["node_type"] == "e2-standard-4"
        assert cd["status"] == "RUNNING"

        it = after["sections"]["infrastructure_topology"]
        assert it["vpc_name"] == "test-vpc"
        assert it["subnet_cidr"] == "10.0.0.0/20"

        # Metadata timestamp should be updated
        assert "last_updated" in after["metadata"]

    def test_unauthorized_sections_rejected(self, test_project):
        """
        Agent trying to write to sections it doesn't own should be rejected.
        cloud-troubleshooter writing to operational_guidelines → rejected.
        (operational_guidelines is readable but not writable for cloud-troubleshooter)
        """
        tmp_path, claude_dir = test_project
        pc_path = claude_dir / "project-context" / "project-context.json"

        # Add operational_guidelines to the project-context so we can verify it's not modified
        pc = json.loads(pc_path.read_text())
        pc["sections"]["operational_guidelines"] = {"commit_standards": "conventional"}
        pc_path.write_text(json.dumps(pc, indent=2))

        agent_output = """
CONTEXT_UPDATE:
{
  "cluster_details": {
    "status": "RUNNING"
  },
  "operational_guidelines": {
    "commit_standards": "HIJACKED"
  }
}
"""

        from context_writer import process_agent_output

        task_info = {
            "agent_type": "cloud-troubleshooter",
            "context_path": str(pc_path),
            "config_dir": str(claude_dir / "config"),
        }

        result = process_agent_output(agent_output, task_info)

        # cluster_details should be updated (allowed)
        assert result["updated"] is True
        assert "cluster_details" in result["sections_updated"]

        # operational_guidelines should be rejected (cloud-troubleshooter can read but not write it)
        assert "operational_guidelines" in result["rejected"]

        # Verify operational_guidelines was NOT modified
        after = json.loads(pc_path.read_text())
        assert after["sections"]["operational_guidelines"]["commit_standards"] == "conventional", \
            "operational_guidelines should remain unchanged (rejected write)"

    def test_deep_merge_preserves_existing_data(self, test_project):
        """
        CONTEXT_UPDATE should merge with existing data, not overwrite.
        """
        tmp_path, claude_dir = test_project
        pc_path = claude_dir / "project-context" / "project-context.json"

        # First update: set initial data
        pc = json.loads(pc_path.read_text())
        pc["sections"]["cluster_details"] = {
            "kubernetes_version": "1.27.0",
            "region": "us-east4",
        }
        pc_path.write_text(json.dumps(pc, indent=2))

        # Second update: agent adds node_count (merge, not overwrite)
        agent_output = """
CONTEXT_UPDATE:
{
  "cluster_details": {
    "node_count": 5,
    "kubernetes_version": "1.28.0"
  }
}
"""

        from context_writer import process_agent_output

        task_info = {
            "agent_type": "cloud-troubleshooter",
            "context_path": str(pc_path),
            "config_dir": str(claude_dir / "config"),
        }

        result = process_agent_output(agent_output, task_info)
        assert result["updated"] is True

        after = json.loads(pc_path.read_text())
        cd = after["sections"]["cluster_details"]

        # New key added
        assert cd["node_count"] == 5
        # Updated key
        assert cd["kubernetes_version"] == "1.28.0"
        # Existing key preserved (deep merge)
        assert cd["region"] == "us-east4", \
            "Existing 'region' key should be preserved after merge"

    def test_audit_trail_created(self, test_project):
        """Context updates should create an audit trail entry."""
        tmp_path, claude_dir = test_project
        pc_path = claude_dir / "project-context" / "project-context.json"
        audit_path = claude_dir / "project-context" / "context-audit.jsonl"

        agent_output = """
CONTEXT_UPDATE:
{
  "cluster_details": {
    "status": "RUNNING"
  }
}
"""
        from context_writer import process_agent_output

        task_info = {
            "agent_type": "cloud-troubleshooter",
            "context_path": str(pc_path),
            "config_dir": str(claude_dir / "config"),
        }

        process_agent_output(agent_output, task_info)

        # Verify audit file was created
        assert audit_path.exists(), "context-audit.jsonl should be created"

        audit_entry = json.loads(audit_path.read_text().strip().split("\n")[-1])
        assert audit_entry["agent"] == "cloud-troubleshooter"
        assert audit_entry["success"] is True
        assert "cluster_details" in audit_entry["sections_updated"]


# ============================================================================
# PHASE 5: subagent_stop_hook Full Processing
# ============================================================================

class TestPhase5SubagentStopHook:
    """Test the subagent_stop_hook processes CONTEXT_UPDATE end-to-end."""

    def test_subagent_stop_processes_context_update(self, test_project):
        """
        subagent_stop_hook should:
        1. Capture metrics
        2. Process CONTEXT_UPDATE via context_writer
        3. Return context_updated=True
        """
        tmp_path, claude_dir = test_project
        pc_path = claude_dir / "project-context" / "project-context.json"

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Import subagent_stop from the copied hooks
            import importlib.util
            stop_hook_path = claude_dir / "hooks" / "subagent_stop.py"
            spec = importlib.util.spec_from_file_location(
                "subagent_stop_lifecycle", str(stop_hook_path)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Set env var so metrics go to tmp dir
            os.environ["WORKFLOW_MEMORY_BASE_PATH"] = str(claude_dir)

            task_info = {
                "task_id": "test-lifecycle-001",
                "description": "Diagnose cluster health",
                "agent": "cloud-troubleshooter",
                "tier": "T0",
                "tags": ["#diagnostic"],
            }

            agent_output = """
## Cluster Health Report

All nodes healthy. Cluster version: 1.28.5-gke.1200

CONTEXT_UPDATE:
{
  "cluster_details": {
    "kubernetes_version": "1.28.5-gke.1200",
    "health_status": "HEALTHY",
    "node_count": 3
  }
}

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
CURRENT_PHASE: Complete
PENDING_STEPS: None
NEXT_ACTION: Report to orchestrator
<!-- /AGENT_STATUS -->
"""

            result = mod.subagent_stop_hook(task_info, agent_output)

            # Verify hook succeeded
            assert result["success"] is True, \
                f"subagent_stop_hook should succeed: {result}"
            assert result["metrics_captured"] is True

            # Verify context was updated
            assert result["context_updated"] is True, \
                f"Context should be marked as updated: {result}"

            # Verify project-context.json was actually modified
            after = json.loads(pc_path.read_text())
            cd = after["sections"]["cluster_details"]
            assert cd["kubernetes_version"] == "1.28.5-gke.1200"
            assert cd["health_status"] == "HEALTHY"
            assert cd["node_count"] == 3

        finally:
            os.chdir(original_cwd)
            os.environ.pop("WORKFLOW_MEMORY_BASE_PATH", None)

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

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
<!-- /AGENT_STATUS -->
"""

            result = mod.subagent_stop_hook(task_info, agent_output)

            assert result["success"] is True
            assert result["context_updated"] is False

        finally:
            os.chdir(original_cwd)
            os.environ.pop("WORKFLOW_MEMORY_BASE_PATH", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
