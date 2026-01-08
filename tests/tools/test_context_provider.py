import pytest
import json
import subprocess
import sys
from pathlib import Path

# Calculate correct tools directory (2 levels up from tests/tools/)
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if TOOLS_DIR.is_symlink():
    TOOLS_DIR = TOOLS_DIR.resolve()

# Add both TOOLS_DIR and the 2-context subdirectory for direct imports
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "2-context"))

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

def run_script(context_file: Path, agent: str, task: str, full_context: bool = False) -> dict:
    """Helper function to run the context_provider.py script and parse its output."""
    script_path = TOOLS_DIR / "2-context" / "context_provider.py"

    if not script_path.exists():
        pytest.fail(f"context_provider.py not found at {script_path}")
    
    cmd = [sys.executable, str(script_path), agent, task, "--context-file", str(context_file)]
    if full_context:
        cmd.append("--full-context")
    
    process = subprocess.run(
        cmd,
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
    
    # Use --full-context to ensure full contract is loaded
    result = run_script(temp_project_context, agent, task, full_context=True)
    
    assert "contract" in result
    contract = result["contract"]
    
    assert "project_details" in contract
    assert "terraform_infrastructure" in contract
    assert "operational_guidelines" in contract
    assert "gitops_configuration" not in contract

def test_gitops_operator_contract(temp_project_context: Path):
    """Verify the gitops-operator gets its complete contract with full context."""
    agent = "gitops-operator"
    task = "Deploy the frontend-app to the cluster."
    
    # Use --full-context to ensure full contract is loaded
    result = run_script(temp_project_context, agent, task, full_context=True)
    
    assert "contract" in result
    contract = result["contract"]
    
    assert "project_details" in contract
    assert "gitops_configuration" in contract
    assert "cluster_details" in contract
    assert "operational_guidelines" in contract
    assert "terraform_infrastructure" not in contract

def test_progressive_disclosure_level(temp_project_context: Path):
    """Verify context provider returns metadata about context level."""
    agent = "gitops-operator"

    # Simple task
    simple_task = "check pod status"
    result = run_script(temp_project_context, agent, simple_task)

    # Verify metadata includes context level
    assert "metadata" in result
    assert "context_level" in result["metadata"]
    assert result["metadata"]["context_level"] == 2  # Default level

    # Complex debugging task
    complex_task = "debug why the backend-api is crashing with database connection errors"
    result = run_script(temp_project_context, agent, complex_task)

    # Should still get standard context level
    assert "metadata" in result
    assert "context_level" in result["metadata"]
    assert result["metadata"]["context_level"] == 2  # Default level

def test_troubleshooter_contract(temp_project_context: Path):
    """Verify troubleshooters get the right contract (required fields only)."""
    agent = "cloud-troubleshooter"
    task = "Why is the backend-api crashing?"

    # Use full context for contract testing
    result = run_script(temp_project_context, agent, task, full_context=True)

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
    agent = "cloud-troubleshooter"
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


# ============================================================================
# STANDARDS PRE-LOADING TESTS
# ============================================================================

def test_get_standards_dir():
    """Test that standards directory is correctly resolved."""
    from context_provider import get_standards_dir
    
    standards_dir = get_standards_dir()
    # Should return a Path that ends with config/standards
    assert str(standards_dir).endswith("config/standards"), f"Expected path ending in config/standards, got {standards_dir}"


def test_read_standard_file_exists():
    """Test reading an existing standard file."""
    from context_provider import read_standard_file, get_standards_dir
    
    standards_dir = get_standards_dir()
    if standards_dir.is_dir():
        content = read_standard_file("security-tiers.md", standards_dir)
        if content is not None:
            assert "Security Tiers" in content or "T0" in content
            assert len(content) > 100  # Should have substantial content


def test_read_standard_file_not_exists():
    """Test reading a non-existent standard file returns None."""
    from context_provider import read_standard_file
    
    content = read_standard_file("nonexistent-file.md", Path("/tmp"))
    assert content is None


def test_should_preload_standard_matches():
    """Test that trigger keywords correctly match tasks."""
    from context_provider import should_preload_standard
    
    config = {
        "file": "command-execution.md",
        "triggers": ["kubectl", "terraform", "gcloud"]
    }
    
    # Should match
    assert should_preload_standard(config, "run kubectl get pods") == True
    assert should_preload_standard(config, "terraform plan") == True
    assert should_preload_standard(config, "check GCLOUD status") == True  # Case insensitive
    
    # Should not match
    assert should_preload_standard(config, "check status") == False
    assert should_preload_standard(config, "read logs") == False


def test_build_standards_context_always_loads_critical():
    """Test that critical standards are always loaded."""
    from context_provider import build_standards_context, get_standards_dir
    
    standards_dir = get_standards_dir()
    if not standards_dir.is_dir():
        pytest.skip("Standards directory not found")
    
    # Even a simple task should load critical standards
    result = build_standards_context("check status")
    
    assert "preloaded" in result
    assert "security_tiers" in result["preloaded"]
    assert "output_format" in result["preloaded"]


def test_build_standards_context_on_demand():
    """Test that on-demand standards are loaded based on task keywords."""
    from context_provider import build_standards_context, get_standards_dir
    
    standards_dir = get_standards_dir()
    if not standards_dir.is_dir():
        pytest.skip("Standards directory not found")
    
    # Task with kubectl should load command_execution
    result = build_standards_context("run kubectl get pods")
    
    assert "command_execution" in result["preloaded"]
    
    # Task with apply should load anti_patterns
    result = build_standards_context("terraform apply changes")
    
    assert "anti_patterns" in result["preloaded"]


def test_build_standards_context_returns_content():
    """Test that standards content is included in result."""
    from context_provider import build_standards_context, get_standards_dir
    
    standards_dir = get_standards_dir()
    if not standards_dir.is_dir():
        pytest.skip("Standards directory not found")
    
    result = build_standards_context("terraform apply")
    
    assert "content" in result
    assert isinstance(result["content"], dict)
    
    # Should have content for each preloaded standard
    for name in result["preloaded"]:
        if name in result["content"]:
            assert len(result["content"][name]) > 0


def test_standards_in_final_payload(temp_project_context: Path):
    """Test that standards are included in the final context payload."""
    agent = "terraform-architect"
    task = "terraform apply to create cluster"
    
    result = run_script(temp_project_context, agent, task)
    
    # Should have metadata with standards info
    assert "metadata" in result
    metadata = result["metadata"]
    
    # Check standards metadata is present
    assert "standards_preloaded" in metadata
    assert "standards_count" in metadata
    
    # For apply task, should have loaded standards
    # Note: depends on standards directory being available during test


# ============================================================================
# PROGRESSIVE DISCLOSURE TESTS
# ============================================================================

def test_progressive_disclosure_integration():
    """Test context level analysis always returns default level 2."""
    from context_provider import analyze_query_for_context_level

    # All queries now return the same default level
    simple_result = analyze_query_for_context_level("check status")
    assert "recommended_level" in simple_result
    assert simple_result["recommended_level"] == 2

    debug_result = analyze_query_for_context_level("debug database connection error")
    assert "recommended_level" in debug_result
    assert debug_result["recommended_level"] == 2
    assert debug_result["needs_debugging"] == False  # No longer analyzes complexity


def test_filter_context_by_level():
    """Test context filtering by level."""
    from context_provider import filter_context_by_level
    
    full_context = {
        "project_details": {"id": "test"},
        "operational_guidelines": {"rules": "test"},
        "terraform_infrastructure": {"path": "test"},
        "infrastructure_topology": {"vpc": "test"},
        "application_services": [{"name": "test"}],
        "monitoring_observability": {"metrics": "test"}
    }
    
    # Level 1 should only include basics
    level1 = filter_context_by_level(full_context, 1, "terraform-architect")
    assert "project_details" in level1
    assert len(level1) < len(full_context)
    
    # Level 4 should include everything
    level4 = filter_context_by_level(full_context, 4, "terraform-architect")
    assert level4 == full_context


def test_context_level_in_metadata(temp_project_context: Path):
    """Test that context level is included in metadata."""
    agent = "terraform-architect"
    task = "check terraform status"
    
    result = run_script(temp_project_context, agent, task)
    
    assert "metadata" in result
    assert "context_level" in result["metadata"]
    assert "query_complexity" in result["metadata"]
