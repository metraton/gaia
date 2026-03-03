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

def test_gaia_contract(temp_project_context: Path):
    """Verify gaia gets its narrow contracted sections."""
    result = run_script(temp_project_context, "gaia", "Update the agent definitions.")

    assert "contract" in result
    contract = result["contract"]

    assert "application_architecture" in contract
    assert "development_standards" in contract

    # gaia has the narrowest read - should NOT have most sections
    assert "project_details" not in contract
    assert "terraform_infrastructure" not in contract
    assert "cluster_details" not in contract


# ============================================================================
# PAYLOAD STRUCTURE TESTS
# ============================================================================

def test_payload_structure(temp_project_context: Path):
    """Verify the output payload has the expected structure."""
    result = run_script(temp_project_context, "terraform-architect", "check status")

    assert "contract" in result
    assert "rules" in result
    assert "metadata" in result

    # Removed fields should not be present
    assert "enrichment" not in result
    assert "progressive_disclosure" not in result

    # Metadata structure
    metadata = result["metadata"]
    assert "cloud_provider" in metadata
    assert "contract_version" in metadata
    assert "rules_count" in metadata
    assert "historical_episodes_count" in metadata

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
