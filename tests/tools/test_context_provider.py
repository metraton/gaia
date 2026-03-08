import pytest
import json
import subprocess
import sys
from pathlib import Path

# Calculate correct tools directory (2 levels up from tests/tools/)
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if TOOLS_DIR.is_symlink():
    TOOLS_DIR = TOOLS_DIR.resolve()

# Add both TOOLS_DIR and the context subdirectory for direct imports
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

@pytest.fixture
def temp_project_context(tmp_path: Path) -> Path:
    """Creates a temporary project-context.json file for isolated testing."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    context_file = claude_dir / "project-context.json"

    mock_context = {
        "metadata": {
            "version": "1.0",
            "last_updated": "2025-01-01T00:00:00Z",
            "project_name": "Test Project",
            "cloud_provider": "GCP",
            "environment": "non-prod"
        },
        "sections": {
            "project_details": {"id": "test-project", "region": "us-central1"},
            "terraform_infrastructure": {"layout": {"base_path": "infra/test"}},
            "gitops_configuration": {"repository": {"path": "gitops/test"}},
            "cluster_details": {"name": "test-cluster"},
            "infrastructure_topology": {"vpc": "test-vpc"},
            "application_services": [
                {"name": "frontend-app", "port": 80},
                {"name": "backend-api", "port": 5000}
            ],
            "application_architecture": {"framework": "nodejs"},
            "development_standards": {"linter": "eslint"},
            "operational_guidelines": {"commit_standards": "conventional"},
            "monitoring_observability": {"metrics": "prometheus"},
            "security_policies": {"iam": "strict"}
        }
    }

    context_file.write_text(json.dumps(mock_context))
    return context_file

def run_script(context_file: Path, agent: str, task: str) -> dict:
    """Helper function to run the context_provider.py script and parse its output."""
    script_path = TOOLS_DIR / "context" / "context_provider.py"

    if not script_path.exists():
        pytest.fail(f"context_provider.py not found at {script_path}")

    cmd = [sys.executable, str(script_path), agent, task, "--context-file", str(context_file)]

    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        cwd=context_file.parent.parent
    )
    return json.loads(process.stdout)


# ============================================================================
# CONTRACT TESTS
# ============================================================================

def test_terraform_architect_contract(temp_project_context: Path):
    """Verify terraform-architect gets all contracted sections."""
    result = run_script(temp_project_context, "terraform-architect", "Create a new GCS bucket.")

    assert "contract" in result
    contract = result["contract"]

    # Base contract reads
    assert "project_details" in contract
    assert "terraform_infrastructure" in contract
    assert "infrastructure_topology" in contract
    assert "operational_guidelines" in contract
    assert "cluster_details" in contract
    assert "application_services" in contract

    # Should NOT have sections outside its contract
    assert "gitops_configuration" not in contract
    assert "monitoring_observability" not in contract

def test_gitops_operator_contract(temp_project_context: Path):
    """Verify gitops-operator gets all contracted sections."""
    result = run_script(temp_project_context, "gitops-operator", "Deploy the frontend-app.")

    assert "contract" in result
    contract = result["contract"]

    assert "project_details" in contract
    assert "gitops_configuration" in contract
    assert "cluster_details" in contract
    assert "operational_guidelines" in contract
    assert "application_services" in contract

    # Should NOT have sections outside its contract
    assert "terraform_infrastructure" not in contract
    assert "monitoring_observability" not in contract

def test_troubleshooter_contract(temp_project_context: Path):
    """Verify cloud-troubleshooter gets all contracted sections (broadest read)."""
    result = run_script(temp_project_context, "cloud-troubleshooter", "Why is backend-api crashing?")

    assert "contract" in result
    contract = result["contract"]

    # Should have all 7 base sections
    assert "project_details" in contract
    assert "cluster_details" in contract
    assert "infrastructure_topology" in contract
    assert "terraform_infrastructure" in contract
    assert "gitops_configuration" in contract
    assert "application_services" in contract
    assert "monitoring_observability" in contract

def test_devops_developer_contract(temp_project_context: Path):
    """Verify devops-developer gets all contracted sections."""
    result = run_script(temp_project_context, "devops-developer", "Fix the login bug.")

    assert "contract" in result
    contract = result["contract"]

    assert "project_details" in contract
    assert "application_services" in contract
    assert "application_architecture" in contract
    assert "development_standards" in contract
    assert "operational_guidelines" in contract

    # Should NOT have infra sections
    assert "terraform_infrastructure" not in contract
    assert "gitops_configuration" not in contract
    assert "cluster_details" not in contract

def test_speckit_planner_contract(temp_project_context: Path):
    """Verify speckit-planner gets all contracted sections."""
    result = run_script(temp_project_context, "speckit-planner", "Plan the auth feature.")

    assert "contract" in result
    contract = result["contract"]

    assert "project_details" in contract
    assert "operational_guidelines" in contract
    assert "application_architecture" in contract
    assert "development_standards" in contract
    assert "application_services" in contract

def test_gaia_is_meta_agent_without_contracts(temp_project_context: Path):
    """Verify gaia (meta-agent) is not in context-contracts and context_provider rejects it."""
    script_path = TOOLS_DIR / "context" / "context_provider.py"
    cmd = [
        sys.executable, str(script_path),
        "gaia", "Update the agent definitions.",
        "--context-file", str(temp_project_context),
    ]
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=temp_project_context.parent.parent,
    )
    # gaia is a meta-agent — not in context-contracts.json, so context_provider exits non-zero
    assert process.returncode != 0, "gaia is a meta-agent and should not have context contracts"
    assert "invalid agent" in process.stderr.lower() or "gaia" in process.stderr.lower()


# ============================================================================
# PAYLOAD STRUCTURE TESTS
# ============================================================================

def test_payload_structure(temp_project_context: Path):
    """Verify the output payload has the expected structure."""
    result = run_script(temp_project_context, "terraform-architect", "check status")

    assert "contract" in result
    assert "context_update_contract" in result
    assert "rules" in result
    assert "metadata" in result
    assert "surface_routing" in result
    assert "investigation_brief" in result

    # Removed fields should not be present
    assert "enrichment" not in result
    assert "progressive_disclosure" not in result

    # Metadata structure
    metadata = result["metadata"]
    assert "cloud_provider" in metadata
    assert "contract_version" in metadata
    assert "rules_count" in metadata
    assert "historical_episodes_count" in metadata
    assert "surface_routing_version" in metadata
    assert "active_surfaces_count" in metadata
    assert "surface_routing_confidence" in metadata


def test_context_update_contract_matches_agent_write_scope(temp_project_context: Path):
    """Injected context_update_contract should reflect the agent's SSOT write scope."""
    result = run_script(temp_project_context, "terraform-architect", "Review terraform drift.")

    context_update_contract = result["context_update_contract"]
    writable_sections = set(context_update_contract["writable_sections"])
    assert {"terraform_infrastructure", "infrastructure_topology"} <= writable_sections
    assert {"gcp_services", "workload_identity", "static_ips"} <= writable_sections
    assert "terraform_infrastructure" in context_update_contract["readable_sections"]
    assert "application_services" in context_update_contract["readable_sections"]


def test_surface_routing_single_surface_for_terraform_task(temp_project_context: Path):
    """Context payload should classify a Terraform task into terraform_iac."""
    result = run_script(
        temp_project_context,
        "terraform-architect",
        "Review terraform module changes for the VPC service account policy.",
    )

    routing = result["surface_routing"]
    brief = result["investigation_brief"]

    assert routing["primary_surface"] == "terraform_iac"
    assert "terraform_iac" in routing["active_surfaces"]
    assert routing["dispatch_mode"] == "single_surface"
    assert "terraform-architect" in routing["recommended_agents"]

    assert brief["primary_surface"] == "terraform_iac"
    assert brief["agent_role"] == "primary"
    assert "terraform_infrastructure" in brief["contract_sections_to_anchor"]
    assert "PATTERNS_CHECKED" in brief["evidence_required"]


def test_surface_routing_detects_multi_surface_task(temp_project_context: Path):
    """Context payload should detect multiple active surfaces when signals cross layers."""
    result = run_script(
        temp_project_context,
        "devops-developer",
        "Investigate why the CI pipeline changed the image tag, the deployment rollout failed, and kubectl logs now show runtime errors.",
    )

    routing = result["surface_routing"]
    brief = result["investigation_brief"]

    assert routing["multi_surface"] is True
    assert "app_ci_tooling" in routing["active_surfaces"]
    assert "gitops_desired_state" in routing["active_surfaces"]
    assert "live_runtime" in routing["active_surfaces"]
    assert routing["dispatch_mode"] == "parallel"

    assert brief["cross_check_required"] is True
    assert brief["consolidation_required"] is True
    assert "gitops_desired_state" in brief["adjacent_surfaces"]
    assert "live_runtime" in brief["adjacent_surfaces"]
    assert "CROSS_LAYER_IMPACTS" in brief["evidence_required"]
    assert "OWNERSHIP_ASSESSMENT" in brief["consolidation_fields"]

def test_invalid_agent(temp_project_context: Path):
    """Verify script rejects invalid agent names."""
    script_path = TOOLS_DIR / "context" / "context_provider.py"
    process = subprocess.run(
        [sys.executable, str(script_path), "unknown-agent", "Do something.",
         "--context-file", str(temp_project_context)],
        capture_output=True,
        text=True
    )

    assert process.returncode != 0
    assert "invalid" in process.stderr.lower() or "unknown" in process.stderr.lower()
