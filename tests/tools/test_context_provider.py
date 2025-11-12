import pytest
import json
import subprocess
import sys
from pathlib import Path

# Calculate correct tools directory (2 levels up from tests/tools/)
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if TOOLS_DIR.is_symlink():
    TOOLS_DIR = TOOLS_DIR.resolve()

sys.path.insert(0, str(TOOLS_DIR))

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
            "application_services": [
                {"name": "frontend-app", "port": 80},
                {"name": "backend-api", "port": 5000}
            ],
            "operational_guidelines": {"commit_standards": "conventional"},
            "security_policies": {"iam": "strict"}
        }
    }
    
    context_file.write_text(json.dumps(mock_context))
    return context_file

def run_script(context_file: Path, agent: str, task: str) -> dict:
    """Helper function to run the context_provider.py script and parse its output."""
    script_path = TOOLS_DIR / "2-context" / "context_provider.py"

    if not script_path.exists():
        pytest.fail(f"context_provider.py not found at {script_path}")
    
    process = subprocess.run(
        [sys.executable, str(script_path), agent, task, "--context-file", str(context_file)],
        capture_output=True,
        text=True,
        check=True,
        cwd=context_file.parent.parent
    )
    return json.loads(process.stdout)

def test_terraform_architect_contract(temp_project_context: Path):
    """Verify the terraform-architect gets its complete contract."""
    agent = "terraform-architect"
    task = "Create a new GCS bucket."
    
    result = run_script(temp_project_context, agent, task)
    
    assert "contract" in result
    contract = result["contract"]
    
    assert "project_details" in contract
    assert "terraform_infrastructure" in contract
    assert "operational_guidelines" in contract
    assert "gitops_configuration" not in contract

def test_gitops_operator_contract(temp_project_context: Path):
    """Verify the gitops-operator gets its complete contract."""
    agent = "gitops-operator"
    task = "Deploy the frontend-app to the cluster."
    
    result = run_script(temp_project_context, agent, task)
    
    assert "contract" in result
    contract = result["contract"]
    
    assert "project_details" in contract
    assert "gitops_configuration" in contract
    assert "cluster_details" in contract
    assert "operational_guidelines" in contract
    assert "terraform_infrastructure" not in contract

def test_troubleshooter_contract(temp_project_context: Path):
    """Verify troubleshooters get the right contract (required fields only)."""
    agent = "gcp-troubleshooter"
    task = "Why is the backend-api crashing?"

    result = run_script(temp_project_context, agent, task)

    assert "contract" in result
    contract = result["contract"]

    # Check required fields per context-contracts.gcp.json
    assert "project_details" in contract
    assert "terraform_infrastructure" in contract
    assert "gitops_configuration" in contract
    # application_services is OPTIONAL per contract, not required

def test_enrichment_by_keyword(temp_project_context: Path):
    """Verify enrichment adds relevant sections based on keywords."""
    agent = "terraform-architect"
    task = "Review our security_policies for the new bucket."
    
    result = run_script(temp_project_context, agent, task)
    
    assert "enrichment" in result
    enrichment = result["enrichment"]
    
    # Should include security_policies due to keyword match
    assert "security_policies" in enrichment

def test_enrichment_by_service_name(temp_project_context: Path):
    """Verify enrichment adds services when mentioned in task."""
    agent = "gcp-troubleshooter"
    task = "Check the logs for the frontend-app."
    
    result = run_script(temp_project_context, agent, task)
    
    assert "enrichment" in result
    # Should recognize "frontend-app" and include it

def test_empty_enrichment(temp_project_context: Path):
    """Verify enrichment is empty when no keywords match."""
    agent = "terraform-architect"
    task = "Generate a new storage unit."
    
    result = run_script(temp_project_context, agent, task)
    
    assert "enrichment" in result
    # Enrichment may be empty or minimal

def test_invalid_agent(temp_project_context: Path):
    """Verify script rejects invalid agent names."""
    agent = "unknown-agent"
    task = "Do something."
    
    script_path = TOOLS_DIR / "2-context" / "context_provider.py"
    process = subprocess.run(
        [sys.executable, str(script_path), agent, task, "--context-file", str(temp_project_context)],
        capture_output=True,
        text=True
    )
    
    # Should fail with non-zero exit code
    assert process.returncode != 0
    # Error message should mention invalid agent
    assert "invalid" in process.stderr.lower() or "unknown" in process.stderr.lower()
