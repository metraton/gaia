#!/usr/bin/env python3
"""
TDD integration tests for context enrichment pipeline.

End-to-end tests that validate the full flow:
  Agent output with CONTEXT_UPDATE -> process_agent_output -> project-context.json updated

Modules under test:
  - hooks/modules/context/context_writer.py (process_agent_output)
  - tools/context/deep_merge.py (used internally by context_writer)
"""

import sys
import json
import shutil
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))


# ---------------------------------------------------------------------------
# Lazy import: context_writer
# ---------------------------------------------------------------------------

def _import_process_agent_output():
    """Import process_agent_output at call time so pytest can collect tests."""
    from context_writer import process_agent_output
    return process_agent_output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent_output(context_update_dict):
    """Helper to create agent output with CONTEXT_UPDATE block."""
    output = "## Agent Execution Complete\n\nTask completed successfully.\n\n"
    if context_update_dict:
        output += "CONTEXT_UPDATE:\n"
        output += json.dumps(context_update_dict, indent=2)
    return output


def _build_task_info(agent_type, context_file, config_dir):
    """Build the task_info dict expected by process_agent_output."""
    return {
        "agent_type": agent_type,
        "context_path": str(context_file),
        "config_dir": str(config_dir),
    }


def write_context(context_file: Path, data: dict) -> None:
    """Write a project-context.json file atomically."""
    context_file.write_text(json.dumps(data, indent=2))


def read_context(context_file: Path) -> dict:
    """Read and parse a project-context.json file."""
    return json.loads(context_file.read_text())


def read_audit(context_file: Path) -> list:
    """Read the audit JSONL file next to context_file."""
    audit_path = context_file.parent / "context-audit.jsonl"
    if not audit_path.exists():
        return []
    entries = []
    for line in audit_path.read_text().strip().splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_context(tmp_path):
    """Creates isolated project-context structure for testing."""
    context_dir = tmp_path / ".claude" / "project-context"
    context_dir.mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Copy the real GCP contracts file so permission checks work
    real_contracts = CONFIG_DIR / "context-contracts.gcp.json"
    if real_contracts.exists():
        shutil.copy(real_contracts, config_dir / "context-contracts.gcp.json")

    return tmp_path, context_dir, config_dir


# ============================================================================
# Scenario 1: Fresh install - first enrichment
# ============================================================================

class TestFreshInstallFirstEnrichment:
    """Scenario 1: project-context exists with empty cluster_details,
    agent discovers namespaces and writes them for the first time."""

    def test_fresh_install_first_enrichment(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "cluster_details": {}
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        update = {
            "cluster_details": {
                "namespaces": {
                    "application": ["adm", "dev", "test"],
                    "infrastructure": ["flux-system", "cert-manager"],
                    "system": ["kube-system", "kube-public"]
                }
            }
        }
        agent_output = make_agent_output(update)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        # Verify namespaces are populated
        updated = read_context(context_file)
        namespaces = updated["sections"]["cluster_details"]["namespaces"]
        assert sorted(namespaces["application"]) == ["adm", "dev", "test"]
        assert sorted(namespaces["infrastructure"]) == ["cert-manager", "flux-system"]
        assert sorted(namespaces["system"]) == ["kube-public", "kube-system"]

        # Verify result indicates success
        assert result["updated"] is True
        assert "cluster_details" in result["sections_updated"]

        # Verify audit entry on disk
        audit = read_audit(context_file)
        assert len(audit) > 0


# ============================================================================
# Scenario 2: Incremental enrichment - new namespace discovered
# ============================================================================

class TestIncrementalEnrichment:
    """Scenario 2: namespaces already exist, agent discovers a new one.
    Union merge: no duplicates, existing entries preserved."""

    def test_incremental_enrichment_new_namespace(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "cluster_details": {
                    "namespaces": {
                        "application": ["adm", "dev", "test"],
                        "infrastructure": ["flux-system", "cert-manager"],
                        "system": ["kube-system"]
                    }
                }
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        update = {
            "cluster_details": {
                "namespaces": {
                    "application": ["adm", "dev", "test", "nova-auth-dev"]
                }
            }
        }
        agent_output = make_agent_output(update)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        updated = read_context(context_file)
        app_ns = updated["sections"]["cluster_details"]["namespaces"]["application"]

        # Union: 4 unique entries, sorted, no duplicates
        assert len(app_ns) == 4
        assert "nova-auth-dev" in app_ns
        assert "adm" in app_ns
        assert sorted(app_ns) == app_ns  # sorted

        # Infrastructure and system arrays preserved (not in update)
        infra_ns = updated["sections"]["cluster_details"]["namespaces"]["infrastructure"]
        sys_ns = updated["sections"]["cluster_details"]["namespaces"]["system"]
        assert "flux-system" in infra_ns
        assert "cert-manager" in infra_ns
        assert "kube-system" in sys_ns


# ============================================================================
# Scenario 3: Drift detection - version update
# ============================================================================

class TestDriftDetection:
    """Scenario 3: agent detects a helm release version has changed.
    Scalar overwrite with audit trail of old -> new."""

    def test_drift_detection_version_update(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "cluster_details": {
                    "helm_releases": [
                        {"name": "orders-service", "chart_version": "0.53.0", "namespace": "application"},
                        {"name": "payments-api", "chart_version": "1.2.0", "namespace": "application"}
                    ]
                }
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        update = {
            "cluster_details": {
                "helm_releases": [
                    {"name": "orders-service", "chart_version": "0.54.0"}
                ]
            }
        }
        agent_output = make_agent_output(update)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        updated = read_context(context_file)
        releases = updated["sections"]["cluster_details"]["helm_releases"]

        # Version updated for orders-service
        orders = next(r for r in releases if r["name"] == "orders-service")
        assert orders["chart_version"] == "0.54.0"

        # payments-api preserved (no-delete policy)
        payments = next(r for r in releases if r["name"] == "payments-api")
        assert payments["chart_version"] == "1.2.0"

        # Audit trail on disk records the change
        audit = read_audit(context_file)
        assert len(audit) > 0
        audit_str = json.dumps(audit)
        assert "0.53.0" in audit_str or "0.54.0" in audit_str


# ============================================================================
# Scenario 4: Permission rejection
# ============================================================================

class TestPermissionRejection:
    """Scenario 4: agent tries to write a section it has no write access to.
    Contracts enforce per-agent write permissions."""

    def test_permission_rejection(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "application_services": {
                    "base_path": "./services",
                    "services": [
                        {"name": "frontend", "port": 3000}
                    ]
                },
                "cluster_details": {
                    "namespaces": {"application": ["dev"]}
                }
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        # cloud-troubleshooter tries to write application_services
        # Per context-contracts.gcp.json, cloud-troubleshooter can only
        # write: ["cluster_details", "infrastructure_topology"]
        update = {
            "application_services": {
                "services": [
                    {"name": "frontend", "port": 3000},
                    {"name": "evil-backdoor", "port": 9999}
                ]
            }
        }
        agent_output = make_agent_output(update)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        # application_services must NOT be modified
        updated = read_context(context_file)
        services = updated["sections"]["application_services"]["services"]
        assert len(services) == 1
        assert services[0]["name"] == "frontend"
        assert not any(s["name"] == "evil-backdoor" for s in services)

        # Result should report rejected sections
        assert "application_services" in result.get("rejected", [])


# ============================================================================
# Scenario 5: No CONTEXT_UPDATE - backward compatibility
# ============================================================================

class TestBackwardCompatibility:
    """Scenario 5: agent output contains no CONTEXT_UPDATE marker.
    System must not crash, context must remain untouched."""

    def test_no_context_update_backward_compat(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "cluster_details": {"status": "RUNNING"}
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        # Agent output with NO CONTEXT_UPDATE
        agent_output = (
            "## Agent Execution Complete\n\n"
            "Checked all pods. Everything looks healthy.\n"
            "No issues found.\n"
        )

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        # Context must be unchanged
        updated = read_context(context_file)
        assert updated == initial_context

        # No audit entry created
        audit = read_audit(context_file)
        assert len(audit) == 0

        # Result indicates no update
        assert result["updated"] is False


# ============================================================================
# Scenario 6: Malformed JSON - graceful handling
# ============================================================================

class TestMalformedJson:
    """Scenario 6: agent output has CONTEXT_UPDATE with invalid JSON.
    Must not crash, context must remain untouched."""

    def test_malformed_json_graceful(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "cluster_details": {"status": "RUNNING"}
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        # Agent output with malformed JSON after CONTEXT_UPDATE
        agent_output = (
            "## Agent Execution Complete\n\n"
            "Task completed.\n\n"
            "CONTEXT_UPDATE:\n"
            '{invalid json, "missing": brackets'
        )

        # Must not raise an exception
        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        # Context must be unchanged
        updated = read_context(context_file)
        assert updated == initial_context

        # Result indicates no update (parse failed gracefully)
        assert result["updated"] is False


# ============================================================================
# Scenario 7: Multi-section update (atomic write)
# ============================================================================

class TestMultiSectionUpdate:
    """Scenario 7: agent updates two sections in a single CONTEXT_UPDATE.
    Both must be applied in a single atomic write."""

    def test_multi_section_update(self, setup_context):
        process_agent_output = _import_process_agent_output()
        project_root, context_dir, config_dir = setup_context

        initial_context = {
            "metadata": {
                "version": "1.0",
                "cloud_provider": "gcp"
            },
            "sections": {
                "project_details": {"project_id": "my-project"},
                "cluster_details": {
                    "namespaces": {"application": ["dev"]},
                    "status": "RUNNING"
                },
                "infrastructure_topology": {
                    "vpc": "default-vpc"
                }
            }
        }
        context_file = context_dir / "project-context.json"
        write_context(context_file, initial_context)

        # cloud-troubleshooter updates both cluster_details AND
        # infrastructure_topology (both in its write permissions)
        update = {
            "cluster_details": {
                "namespaces": {
                    "application": ["dev", "staging"]
                }
            },
            "infrastructure_topology": {
                "subnets": ["10.0.0.0/24", "10.0.1.0/24"]
            }
        }
        agent_output = make_agent_output(update)

        result = process_agent_output(
            agent_output,
            _build_task_info("cloud-troubleshooter", context_file, config_dir),
        )

        updated = read_context(context_file)

        # cluster_details updated
        app_ns = updated["sections"]["cluster_details"]["namespaces"]["application"]
        assert "staging" in app_ns
        assert "dev" in app_ns
        assert updated["sections"]["cluster_details"]["status"] == "RUNNING"  # preserved

        # infrastructure_topology updated
        subnets = updated["sections"]["infrastructure_topology"]["subnets"]
        assert "10.0.0.0/24" in subnets
        assert "10.0.1.0/24" in subnets
        assert updated["sections"]["infrastructure_topology"]["vpc"] == "default-vpc"  # preserved

        # Audit trail on disk
        audit = read_audit(context_file)
        assert len(audit) > 0

        # Result shows both sections updated
        assert result["updated"] is True
        assert "cluster_details" in result["sections_updated"]
        assert "infrastructure_topology" in result["sections_updated"]


# ============================================================================
# Scenario 8: Skill file existence and content
# ============================================================================

class TestSkillFileExists:
    """Scenario 8: verify the context-updater skill exists and documents
    the CONTEXT_UPDATE format agents must follow."""

    def test_skill_loaded_correctly(self):
        skill_file = SKILLS_DIR / "domain" / "context-updater" / "SKILL.md"

        assert skill_file.exists(), (
            f"Skill file not found at {skill_file}. "
            "This file must be created as part of the context enrichment feature."
        )

        content = skill_file.read_text()

        # Must document the CONTEXT_UPDATE format
        assert "CONTEXT_UPDATE" in content, (
            "SKILL.md must document the CONTEXT_UPDATE format "
            "that agents use to emit context updates."
        )
