import pytest
import json
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

# ============================================================================
# NOTE: Tests that exercised the retired context-contracts.json pipeline
# (run_script subprocess tests, payload-structure tests, surface-routing
# integration tests, invalid-agent test) were removed in task #5. They tested
# the sectional contract pipeline replaced by DB-backed agent_permissions in
# gaia.db. TestGetRelevantSections below covers the surface-gated filtering
# logic that is still in use.
# ============================================================================


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
                "primary_agent": "developer",
                "contract_sections": [
                    "project_identity", "stack", "git", "environment",
                    "infrastructure", "application_services",
                    "operational_guidelines", "architecture_overview",
                ],
            },
            "iac": {
                "primary_agent": "platform-architect",
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

        # All contract_keys for developer are in app_ci_tooling contract_sections
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
            "active_surfaces": ["app_ci_tooling", "iac"],
            "primary_surface": "app_ci_tooling",
        }

        result = get_relevant_sections(
            mock_sections, broad_keys,
            surface_routing=routing,
            routing_config=mock_routing_config,
        )

        # Union of app_ci_tooling + iac should include:
        # terraform_infrastructure (from iac)
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
