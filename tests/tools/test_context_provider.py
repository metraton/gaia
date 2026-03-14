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

from context_provider import get_relevant_sections  # noqa: E402

@pytest.fixture
def temp_project_context(tmp_path: Path) -> Path:
    """Creates a temporary project-context.json file for isolated testing."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    context_file = claude_dir / "project-context.json"

    mock_context = {
        "metadata": {
            "version": "3.0",
            "last_updated": "2025-01-01T00:00:00Z",
            "project_name": "Test Project",
            "cloud_provider": "GCP",
            "environment": "non-prod"
        },
        "sections": {
            "project_identity": {"name": "test-project", "type": "application"},
            "stack": {"languages": [{"name": "typescript"}], "frameworks": [{"name": "nodejs"}], "build_tools": [{"name": "npm"}]},
            "git": {"platform": "github", "remotes": [], "default_branch": "main"},
            "environment": {"runtimes": [{"name": "node", "version": "20"}], "os": {"platform": "linux"}},
            "infrastructure": {"cloud_providers": [{"name": "gcp", "project_id": "test-project", "region": "us-central1"}], "ci_cd": []},
            "orchestration": {},
            "terraform_infrastructure": {"layout": {"base_path": "infra/test"}},
            "gitops_configuration": {"repository": {"path": "gitops/test"}},
            "cluster_details": {"name": "test-cluster"},
            "infrastructure_topology": {"vpc": "test-vpc"},
            "application_services": [
                {"name": "frontend-app", "port": 80},
                {"name": "backend-api", "port": 5000}
            ],
            "operational_guidelines": {"commit_standards": "conventional"},
            "monitoring_observability": {"metrics": "prometheus"},
            "architecture_overview": {},
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
    """Verify terraform-architect gets surface-gated sections for a terraform task."""
    result = run_script(temp_project_context, "terraform-architect", "Create a new GCS bucket.")

    assert "project_knowledge" in result
    contract = result["project_knowledge"]

    # v2 base sections present in terraform_iac surface contract_sections
    assert "project_identity" in contract
    assert "stack" in contract
    assert "git" in contract
    assert "environment" in contract
    assert "infrastructure" in contract
    assert "terraform_infrastructure" in contract
    assert "infrastructure_topology" in contract
    assert "cluster_details" in contract
    assert "application_services" in contract
    assert "architecture_overview" in contract

    # Surface-gated: operational_guidelines is NOT in terraform_iac contract_sections
    assert "operational_guidelines" not in contract
    # Should NOT have sections outside its contract
    assert "monitoring_observability" not in contract

def test_gitops_operator_contract(temp_project_context: Path):
    """Verify gitops-operator gets all contracted v2 sections."""
    result = run_script(temp_project_context, "gitops-operator", "Deploy the frontend-app.")

    assert "project_knowledge" in result
    contract = result["project_knowledge"]

    assert "project_identity" in contract
    assert "stack" in contract
    assert "git" in contract
    assert "environment" in contract
    assert "infrastructure" in contract
    assert "gitops_configuration" in contract
    assert "cluster_details" in contract
    assert "operational_guidelines" in contract
    assert "application_services" in contract

    # Should NOT have sections outside its contract
    assert "terraform_infrastructure" not in contract
    assert "monitoring_observability" not in contract

def test_troubleshooter_contract(temp_project_context: Path):
    """Verify cloud-troubleshooter gets surface-gated sections for a runtime task.

    Task uses live_runtime keywords (pod, logs, kubectl) to ensure the surface
    classifier routes to live_runtime, enabling surface-gated assertions.
    """
    result = run_script(
        temp_project_context,
        "cloud-troubleshooter",
        "Check pod logs using kubectl for the backend-api runtime outage.",
    )

    assert "project_knowledge" in result
    contract = result["project_knowledge"]

    # live_runtime surface contract_sections
    assert "project_identity" in contract
    assert "stack" in contract
    assert "git" in contract
    assert "environment" in contract
    assert "infrastructure" in contract
    assert "cluster_details" in contract
    assert "infrastructure_topology" in contract
    assert "application_services" in contract
    assert "monitoring_observability" in contract
    assert "architecture_overview" in contract

    # Surface-gated: terraform_infrastructure and gitops_configuration are NOT
    # in live_runtime contract_sections
    assert "terraform_infrastructure" not in contract
    assert "gitops_configuration" not in contract

def test_devops_developer_contract(temp_project_context: Path):
    """Verify devops-developer gets all contracted v2 sections."""
    result = run_script(temp_project_context, "devops-developer", "Fix the login bug.")

    assert "project_knowledge" in result
    contract = result["project_knowledge"]

    assert "project_identity" in contract
    assert "stack" in contract
    assert "git" in contract
    assert "environment" in contract
    assert "infrastructure" in contract
    assert "application_services" in contract
    assert "operational_guidelines" in contract

    # Should NOT have infra-specific sections
    assert "terraform_infrastructure" not in contract
    assert "gitops_configuration" not in contract
    assert "cluster_details" not in contract

def test_speckit_planner_contract(temp_project_context: Path):
    """Verify speckit-planner gets all contracted v2 sections."""
    result = run_script(temp_project_context, "speckit-planner", "Plan the auth feature.")

    assert "project_knowledge" in result
    contract = result["project_knowledge"]

    assert "project_identity" in contract
    assert "stack" in contract
    assert "git" in contract
    assert "environment" in contract
    assert "infrastructure" in contract
    assert "operational_guidelines" in contract
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

    assert "project_knowledge" in result
    assert "write_permissions" in result
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


def test_write_permissions_matches_agent_write_scope(temp_project_context: Path):
    """Injected write_permissions should reflect the agent's SSOT write scope."""
    result = run_script(temp_project_context, "terraform-architect", "Review terraform drift.")

    write_perms = result["write_permissions"]
    writable_sections = set(write_perms["writable_sections"])
    assert {"terraform_infrastructure", "infrastructure_topology"} <= writable_sections
    assert {"gcp_services", "workload_identity", "static_ips"} <= writable_sections
    assert "terraform_infrastructure" in write_perms["readable_sections"]
    assert "application_services" in write_perms["readable_sections"]


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


# ============================================================================
# SURFACE-GATED CONTEXT INJECTION TESTS
# ============================================================================

@pytest.fixture
def mock_sections() -> dict:
    """Sections dict mimicking project-context.json sections."""
    return {
        "project_identity": {"name": "test"},
        "stack": {"languages": ["python"]},
        "git": {"platform": "github"},
        "environment": {"os": "linux"},
        "infrastructure": {"cloud_providers": []},
        "orchestration": {},
        "terraform_infrastructure": {"layout": {}},
        "gitops_configuration": {"repo": "gitops"},
        "cluster_details": {"name": "dev-cluster"},
        "infrastructure_topology": {"vpc": "main"},
        "application_services": [{"name": "api"}],
        "operational_guidelines": {"commit": "conventional"},
        "monitoring_observability": {"metrics": "prometheus"},
        "architecture_overview": {},
    }


@pytest.fixture
def mock_routing_config() -> dict:
    """Minimal surface-routing config with contract_sections per surface."""
    return {
        "version": "1.0",
        "surfaces": {
            "app_ci_tooling": {
                "primary_agent": "devops-developer",
                "contract_sections": [
                    "project_identity", "stack", "git", "environment",
                    "infrastructure", "application_services",
                    "operational_guidelines", "architecture_overview",
                ],
            },
            "terraform_iac": {
                "primary_agent": "terraform-architect",
                "contract_sections": [
                    "project_identity", "stack", "git", "environment",
                    "infrastructure", "orchestration",
                    "terraform_infrastructure", "infrastructure_topology",
                    "cluster_details", "application_services",
                    "architecture_overview",
                ],
            },
            "live_runtime": {
                "primary_agent": "cloud-troubleshooter",
                "contract_sections": [
                    "project_identity", "stack", "git", "environment",
                    "infrastructure", "orchestration",
                    "cluster_details", "monitoring_observability",
                    "application_services", "infrastructure_topology",
                    "architecture_overview",
                ],
            },
            "empty_surface": {
                "primary_agent": "some-agent",
                "contract_sections": [],
            },
        },
    }


class TestGetRelevantSections:
    """Unit tests for get_relevant_sections surface-gated filtering."""

    def test_single_surface_filters_to_surface_sections(
        self, mock_sections, mock_routing_config
    ):
        """Single active surface should restrict to that surface's contract_sections."""
        contract_keys = [
            "project_identity", "stack", "git", "environment",
            "infrastructure", "application_services",
            "operational_guidelines", "architecture_overview",
        ]
        routing = {
            "active_surfaces": ["app_ci_tooling"],
            "primary_surface": "app_ci_tooling",
        }

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        # All contract_keys for devops-developer are in app_ci_tooling contract_sections
        assert set(result.keys()) == set(contract_keys)

    def test_single_surface_omits_sections_not_in_surface(
        self, mock_sections, mock_routing_config
    ):
        """Agent with broad read perms should have irrelevant sections omitted."""
        # cloud-troubleshooter has very broad read, but if surface is app_ci_tooling
        # only app_ci_tooling contract_sections should be returned
        broad_keys = [
            "project_identity", "stack", "git", "environment",
            "infrastructure", "orchestration",
            "terraform_infrastructure", "gitops_configuration",
            "cluster_details", "infrastructure_topology",
            "application_services", "monitoring_observability",
            "architecture_overview",
        ]
        routing = {
            "active_surfaces": ["app_ci_tooling"],
            "primary_surface": "app_ci_tooling",
        }

        result = get_relevant_sections(
            mock_sections, broad_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        # app_ci_tooling does NOT include: orchestration, terraform_infrastructure,
        # gitops_configuration, cluster_details, infrastructure_topology,
        # monitoring_observability
        assert "terraform_infrastructure" not in result
        assert "gitops_configuration" not in result
        assert "cluster_details" not in result
        assert "monitoring_observability" not in result
        assert "orchestration" not in result
        # But these should be present
        assert "project_identity" in result
        assert "application_services" in result
        assert "stack" in result

    def test_multi_surface_unions_sections(
        self, mock_sections, mock_routing_config
    ):
        """Multiple active surfaces should union their contract_sections."""
        broad_keys = [
            "project_identity", "stack", "git", "environment",
            "infrastructure", "orchestration",
            "terraform_infrastructure", "infrastructure_topology",
            "cluster_details", "application_services",
            "monitoring_observability", "architecture_overview",
            "operational_guidelines",
        ]
        routing = {
            "active_surfaces": ["app_ci_tooling", "terraform_iac"],
            "primary_surface": "app_ci_tooling",
        }

        result = get_relevant_sections(
            mock_sections, broad_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        # Union of app_ci_tooling + terraform_iac should include:
        # terraform_infrastructure (from terraform_iac)
        # operational_guidelines (from app_ci_tooling)
        # But NOT monitoring_observability (neither surface)
        assert "terraform_infrastructure" in result
        assert "operational_guidelines" in result
        assert "monitoring_observability" not in result

    def test_no_active_surfaces_returns_all_readable(
        self, mock_sections, mock_routing_config
    ):
        """When no active surfaces, all readable sections should be returned."""
        contract_keys = [
            "project_identity", "stack", "git", "application_services",
            "monitoring_observability",
        ]
        routing = {
            "active_surfaces": [],
            "primary_surface": "",
        }

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        assert set(result.keys()) == {"project_identity", "stack", "git",
                                       "application_services", "monitoring_observability"}

    def test_no_routing_returns_all_readable(
        self, mock_sections, mock_routing_config
    ):
        """When surface_routing is None, all readable sections should be returned."""
        contract_keys = ["project_identity", "stack", "monitoring_observability"]

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=None,
            routing_config=mock_routing_config,
        )

        assert set(result.keys()) == {"project_identity", "stack", "monitoring_observability"}

    def test_no_routing_config_returns_all_readable(
        self, mock_sections,
    ):
        """When routing_config is None, all readable sections should be returned."""
        contract_keys = ["project_identity", "stack", "monitoring_observability"]
        routing = {"active_surfaces": ["app_ci_tooling"]}

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=routing,
            routing_config=None,
        )

        assert set(result.keys()) == {"project_identity", "stack", "monitoring_observability"}

    def test_empty_contract_sections_returns_all_readable(
        self, mock_sections, mock_routing_config
    ):
        """Surface with empty contract_sections should fall back to all readable."""
        contract_keys = ["project_identity", "stack", "monitoring_observability"]
        routing = {
            "active_surfaces": ["empty_surface"],
            "primary_surface": "empty_surface",
        }

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        assert set(result.keys()) == {"project_identity", "stack", "monitoring_observability"}

    def test_no_intersection_returns_all_readable(
        self, mock_sections, mock_routing_config
    ):
        """If agent perms and surface sections don't intersect, fall back to all."""
        # Agent can only read monitoring_observability, but the surface
        # (app_ci_tooling) doesn't include it
        contract_keys = ["monitoring_observability"]
        routing = {
            "active_surfaces": ["app_ci_tooling"],
            "primary_surface": "app_ci_tooling",
        }

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        # Fallback: return all readable
        assert set(result.keys()) == {"monitoring_observability"}

    def test_unknown_surface_returns_all_readable(
        self, mock_sections, mock_routing_config
    ):
        """Unknown surface name (not in config) should fall back gracefully."""
        contract_keys = ["project_identity", "stack", "monitoring_observability"]
        routing = {
            "active_surfaces": ["nonexistent_surface"],
            "primary_surface": "nonexistent_surface",
        }

        result = get_relevant_sections(
            mock_sections, contract_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        # Unknown surface has no contract_sections -> relevant is empty -> fallback
        assert set(result.keys()) == {"project_identity", "stack", "monitoring_observability"}
