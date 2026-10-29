"""The WHAT-key table and the projection that keeps only what a request names."""

import copy
import re
from pathlib import Path

import pytest

from gaia.context_keys import WHAT_KEYS, project_what, what_keys

AGENTS = Path(__file__).resolve().parents[1] / "agents"

BILDWIZ_CLUSTER = {
    "gke_cluster_name": "dev-gke-standard",
    "gke_location": "us-central1",
    "gke_project": "aaxis-bildwiz-project",
    "gke_master_endpoint": "34.135.239.115",
    "kubernetes_version_observed": "1.35.8-gke.1796000",
    "namespaces": ["bildwiz-demo", "bildwiz-demo-stage"],
    "gitops_controller": {
        "tool": "flux",
        "git_repository": "bitbucket.org/aaxisdigital/bildwiz-gitops-gcp-dev",
        "reconcile_interval": "3m",
        "last_applied_revision": "master@sha1:c162d3a",
    },
    "kubeconfig_drift_note": "context pointed at a non-existent cluster",
    "active_cluster": {
        "name": "dev-gke-standard",
        "location": "us-central1",
        "project": "aaxis-bildwiz-project",
        "status": "RUNNING",
        "current_node_count": 3,
        "last_applied_revision": "master@sha1:c162d3a",
    },
    "flux_kustomizations": [{"namespace": "flux-system", "name": "releases"}],
    "live_demo": {
        "namespace": "bildwiz-demo-stage",
        "host": "bildwiz-demo-stage.aaxisdev.net",
        "workloads": ["bildwiz-frontend", "redis"],
        "database": "Cloud SQL bildwiz-dev-db via bildwiz-db-secret",
        "lb_logging": "backend logConfig enable=true sampleRate=1.0",
    },
    "scaled_to_zero_namespaces": {
        "bildwiz-demo": "all 23 Deployments at replicas 0 since 2026-09-21",
        "bildwiz-dev": "all Deployments at replicas 0",
    },
}

BRANCHKINECT_CLUSTER = {
    "cluster_name": "century-nonprod-gke",
    "provider": "gcp",
    "project_id": "branchkinect-century-nonprod",
    "region": "us-central1",
    "release_channel": "REGULAR",
    "control_plane_version": "1.35.8-gke.1036000",
    "node_pools": [{"name": "century-nonprod-gke-primary", "machine_type": "e2-standard-4"}],
    "network": {"vpc": "century-nonprod-vpc", "subnet_cidr": "10.10.0.0/20"},
    "gitops": "Argo CD (+Kargo) from branchkinect-gitops-nonprod",
    "observed_at": "2026-09-30",
}

BILDWIZ_APPS = {
    "bildwiz-backend_runtime": {
        "helm_chart": "acce-service",
        "image_repository": "us-central1-docker.pkg.dev/aaxis-bildwiz-project/bildwiz-dev/bildwiz-backend",
        "deployments": {
            "bildwiz-demo/bildwiz-service": {"image_tag": "demo-0.0.165", "helmrelease_revision": 16},
            "bildwiz-demo-stage/bildwiz-service": {"image_tag": "demo-stage-0.0.175"},
        },
    }
}

SKILL_HUB_APPS = {
    "century_skill_hub": {
        "type": "claude_code_plugin_marketplace",
        "marketplace_name": "century-skill-hub",
        "plugins": [
            {"name": "diagram-builder", "version": "1.0.0"},
            {"name": "deck-kit", "version": "0.2.0"},
        ],
        "install_flow": "/plugin marketplace add century-inc/century-skill-hub",
    }
}

BILDWIZ_GITOPS = {
    "git_remote": "git@bitbucket.org:aaxisdigital/bildwiz-gitops-gcp-dev.git",
    "operative_branch": "master",
    "controller": "flux",
    "cluster_entrypoint": "clusters/dev-gke-standard",
    "environments": ["dev", "demo", "demo-stage", "livekit"],
    "secrets_mechanism": "external-secrets-operator",
    "namespaces": ["bildwiz-dev", "bildwiz-demo"],
}

NONPROD_GITOPS = {
    "branchkinect_nonprod": {
        "repo": "century-inc/branchkinect-gitops-nonprod",
        "default_branch": "main",
        "cluster_entrypoint": "clusters/nonprod",
        "namespaces": ["demo", "dev"],
        "app_count": 14,
        "image_automation": "off",
    }
}


def read_sections():
    sections = set()
    for agent in AGENTS.glob("*.md"):
        match = re.search(r"^\s+read:\s*\[(.*?)\]", agent.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            sections |= {name.strip() for name in match.group(1).split(",") if name.strip()}
    return sections


def test_every_section_an_agent_can_read_is_classified():
    assert read_sections() - set(WHAT_KEYS) == set()


def test_a_how_section_declares_no_what_keys():
    assert what_keys("environment") == ()
    assert project_what("environment", {"tools": [{"name": "aws", "path": "/usr/local/bin/aws"}]}) == {}


def test_an_unclassified_section_projects_nothing():
    assert what_keys("no_such_section") == ()
    assert project_what("no_such_section", {"cluster": "x"}) == {}


def test_cluster_details_keeps_the_named_things_and_drops_the_how():
    assert project_what("cluster_details", BILDWIZ_CLUSTER) == {
        "gke_cluster_name": "dev-gke-standard",
        "gke_location": "us-central1",
        "gke_project": "aaxis-bildwiz-project",
        "namespaces": ["bildwiz-demo", "bildwiz-demo-stage"],
        "gitops_controller": {"git_repository": "bitbucket.org/aaxisdigital/bildwiz-gitops-gcp-dev"},
        "active_cluster": {
            "name": "dev-gke-standard",
            "location": "us-central1",
            "project": "aaxis-bildwiz-project",
        },
        "live_demo": {
            "namespace": "bildwiz-demo-stage",
            "host": "bildwiz-demo-stage.aaxisdev.net",
            "workloads": ["bildwiz-frontend", "redis"],
        },
        "scaled_to_zero_namespaces": {"bildwiz-demo": {}, "bildwiz-dev": {}},
    }


def test_ephemeral_and_access_values_never_project():
    flat = repr(project_what("cluster_details", BILDWIZ_CLUSTER))
    for leaked in ("current_node_count", "last_applied_revision", "34.135.239.115", "RUNNING", "kubeconfig"):
        assert leaked not in flat


def test_cluster_details_covers_the_branchkinect_shape_too():
    assert project_what("cluster_details", BRANCHKINECT_CLUSTER) == {
        "cluster_name": "century-nonprod-gke",
        "provider": "gcp",
        "project_id": "branchkinect-century-nonprod",
        "region": "us-central1",
    }


def test_application_services_names_each_app_and_its_deployment_targets():
    assert project_what("application_services", BILDWIZ_APPS) == {
        "bildwiz-backend_runtime": {
            "image_repository": BILDWIZ_APPS["bildwiz-backend_runtime"]["image_repository"],
            "deployments": {"bildwiz-demo/bildwiz-service": {}, "bildwiz-demo-stage/bildwiz-service": {}},
        }
    }


def test_application_services_keeps_the_name_of_an_app_with_nothing_else_to_name():
    payload = {"gaia_cli_entry_point": {"file": "/home/jorge/ws/me/gaia/bin/gaia"}}
    assert project_what("application_services", payload) == {"gaia_cli_entry_point": {}}


def test_a_list_path_projects_one_entry_per_item():
    assert project_what("application_services", SKILL_HUB_APPS) == {
        "century_skill_hub": {
            "marketplace_name": "century-skill-hub",
            "plugins": [{"name": "diagram-builder"}, {"name": "deck-kit"}],
        }
    }


def test_gitops_configuration_covers_both_stored_shapes():
    assert project_what("gitops_configuration", BILDWIZ_GITOPS) == {
        "git_remote": BILDWIZ_GITOPS["git_remote"],
        "cluster_entrypoint": "clusters/dev-gke-standard",
        "environments": ["dev", "demo", "demo-stage", "livekit"],
        "namespaces": ["bildwiz-dev", "bildwiz-demo"],
    }
    assert project_what("gitops_configuration", NONPROD_GITOPS) == {
        "branchkinect_nonprod": {
            "repo": "century-inc/branchkinect-gitops-nonprod",
            "cluster_entrypoint": "clusters/nonprod",
            "namespaces": ["demo", "dev"],
        }
    }


def test_a_path_the_payload_does_not_hold_is_absent_not_null():
    assert project_what("cluster_details", {"gke_cluster_name": "x"}) == {"gke_cluster_name": "x"}
    assert project_what("cluster_details", {"live_demo": {"database": "d"}}) == {}


def test_a_payload_that_is_not_an_object_projects_nothing():
    assert project_what("cluster_details", None) == {}
    assert project_what("cluster_details", ["gke_cluster_name"]) == {}


def test_projection_leaves_the_stored_payload_untouched():
    stored = copy.deepcopy(BILDWIZ_CLUSTER)
    projected = project_what("cluster_details", stored)
    projected["live_demo"]["workloads"].append("intruder")
    projected["namespaces"].append("intruder")
    assert stored == BILDWIZ_CLUSTER


@pytest.mark.parametrize("section", sorted(WHAT_KEYS))
def test_every_registered_path_is_well_formed(section):
    for path in what_keys(section):
        steps = path.split(".")
        assert all(steps), path
        assert all(not step.startswith("[") for step in steps), path
        assert all(not step.endswith("{}") for step in steps[:-1]), path
