"""The WHAT-key table and the projection that keeps only what a request names."""

import copy
import re
from pathlib import Path

import pytest

from gaia.context_keys import WHAT_KEYS, is_what_path, project_what, what_keys

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
        "workloads": ["bildwiz-frontend", "bildwiz-service (image bildwiz-backend)", "redis"],
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
    "cluster_entrypoint": "clusters/dev-gke-standard (kustomization.yaml -> flux-system, releases.yaml)",
    "environments": ["dev", "demo", "demo-stage", "livekit"],
    "secrets_mechanism": "external-secrets-operator",
    "namespaces": ["bildwiz-dev", "bildwiz-demo"],
}

NONPROD_GITOPS = {
    "branchkinect_nonprod": {
        "repo": "century-inc/branchkinect-gitops-nonprod",
        "default_branch": "main",
        "cluster_entrypoint": "argocd/bootstrap/root.yaml recursing argocd/apps",
        "namespaces": ["demo", "dev"],
        "app_count": 14,
        "image_automation": "off",
    }
}


AAXIS_APPS = {
    "base_path": ".",
    "services": [
        {"name": "env-manager", "path": "bildwiz-gitops-gcp-dev/tools/env-manager", "has_dockerfile": True},
        {"name": "bildwiz-backend", "path": "bildwiz-platform/packages/bildwiz-backend", "has_dockerfile": True},
        {"name": "newco-pitot", "path": "newco-pitot", "has_dockerfile": True},
    ],
    "bildwiz-backend_runtime": {
        "helm_chart": "acce-service",
        "image_repository": "us-central1-docker.pkg.dev/aaxis-bildwiz-project/bildwiz-dev/bildwiz-backend",
        "deployments": {"bildwiz-demo/bildwiz-service": {"image_tag": "demo-0.0.165"}},
    },
}

AOS_TOPOLOGY = {
    "aos_prod_ssl_scope": {
        "instance": "aos-pg16-prod",
        "ssl_mode": "ENCRYPTED_ONLY",
        "private_ip": "10.11.0.5",
        "affected_services": {
            "aos-api-prod": {"db_ssl_live": "false", "iac_file": "envs/prod/runtime/cloud-run-aos.tf"},
            "aos-keycloak-prod": {"ssl_in_url": False, "iac_line": 31},
        },
    },
    "aos_dev_ssl_scope": {"instance": "aos-pg16", "note": "dev accepts unencrypted"},
}

CENTURY_TOPOLOGY = {
    "branchkinect_artifacts_repo": {
        "local_path": "/home/user/ws/century-inc/branchkinect-artifacts",
        "remote": "century-inc/branchkinect-artifacts",
        "structure": ["platform-iac/", "helm-charts/"],
    },
    "branchkinect_platform": {
        "iac_repo_path": "/home/user/ws/century-inc/branchkinect-artifacts",
        "github_slug": "century-inc/branchkinect-artifacts",
        "visibility": "private",
        "target_project": "branchkinect-artifacts (org-root 145362834906, billing 012858-140391-C81803)",
        "state_bucket_planned": "branchkinect-platform-tfstate",
    },
    "century_iac": {
        "repo_path": "/home/user/ws/century-inc/branchkinect-century-iac",
        "tool": "terragrunt",
        "seed_project": "branchkinect-century-core",
    },
    "branchkinect_factory": {
        "repo": "/home/user/ws/century-inc/branchkinect-factory",
        "factory_project": {"project_id": "branchkinect-factory-gcp", "project_number": "580381776210"},
    },
}

AAXIS_IDENTITY = {
    "rnd_2": {
        "local_path": "/home/user/ws/aaxis/rnd/ai-project-guidance",
        "remote_url": "git@bitbucket.org:aaxisdigital/ai-project-guidance.git",
        "platform": "bitbucket",
        "name": "rnd-2",
        "type": "monorepo",
    },
    "bildwiz_7": {
        "local_path": "/home/user/ws/aaxis/bildwiz/newco-pitot",
        "remote_url": "https://github.com/gpalagan-aaxis/newco-pitot",
        "name": "bildwiz-7",
        "language": "python",
    },
    "ai_ways_of_working": {
        "local_path": "/home/user/ws/aaxis/rnd/ai-ways-of-working",
        "name": "ai-ways-of-working",
        "type": "application",
    },
    "aos": {
        "name": "AOS -- Accounting Operating System",
        "remote_url": "git@bitbucket.org:aaxisdigital/aos.git",
        "local_path": "/home/user/ws/aaxis/aos/aos",
        "package_manager": "pnpm@10.15.1",
        "apps": {"@aos/web": "apps/web", "@aos/server": "apps/server"},
    },
}

CENTURY_IDENTITY = {
    "century_skill_hub": {
        "local_path": "/home/user/ws/century-inc/century-skill-hub",
        "remote_url": "https://github.com/century-inc/century-skill-hub.git",
        "name": "century-skill-hub",
    },
    "branchkinect_iac": {
        "local_path": "/home/user/ws/century-inc/branchkinect-iac",
        "remote_url": "https://github.com/century-inc/branchkinect-iac.git",
        "missing_since": "2026-07-28T07:51:52Z",
    },
    "branchkinect_modelb_fixture": {
        "local_path": "/home/user/ws/century-inc/branchkinect-modelb-fixture",
        "name": "branchkinect-modelb-fixture",
        "missing_since": "2026-07-26T19:58:00Z",
    },
}

AAXIS_TERRAFORM = {
    "newco_pitot_bootstrap": {
        "path": "/home/user/ws/aaxis/bildwiz/newco-pitot/infra/terraform/00-bootstrap",
        "files": ["versions.tf", "apis.tf"],
        "applied": False,
        "note": "Declarative documentation of resources provisioned via gcloud in Step 1.",
    }
}

AAXIS_GIT = {
    "_source": "scanner:git",
    "platform": "bitbucket",
    "workspace_type": "multi-repo",
    "repos": [
        {
            "name": "bildwiz-iac",
            "path": "bildwiz-iac",
            "remote_url": "git@bitbucket.org:aaxisdigital/bildwiz-iac.git",
            "default_branch": "main",
        },
        {
            "name": "newco-pitot",
            "path": "newco-pitot",
            "remote_url": "https://github.com/gpalagan-aaxis/newco-pitot",
            "default_branch": "main",
        },
    ],
    "branch_strategy": {"detected": False, "pattern": None},
}

AAXIS_STACK = {
    "_source": "scanner:stack",
    "languages": [{"name": "python", "manifest": "control-tower-livekit/pyproject.toml", "primary": False}],
    "frameworks": [{"name": "fastapi", "language": "python", "version": "0.115"}],
    "build_tools": [{"name": "npm", "detected_by": "lock_file", "lock_file": "package-lock.json"}],
}

AAXIS_STATIC_IPS = {
    "bildwiz-demo_gateway_external_http": "34.110.234.82",
    "bildwiz-dev_gateway_external_http": "34.36.244.117",
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
            "workloads": ["bildwiz-frontend", "bildwiz-service", "redis"],
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
    payload = {"gaia_cli_entry_point": {"file": "/home/user/ws/me/gaia/bin/gaia"}}
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
            "cluster_entrypoint": "argocd/bootstrap/root.yaml",
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


def test_scanned_application_services_name_the_apps_not_the_scalar_and_list_keys():
    assert project_what("application_services", AAXIS_APPS) == {
        "services": [{"name": "env-manager"}, {"name": "bildwiz-backend"}, {"name": "newco-pitot"}],
        "bildwiz-backend_runtime": {
            "image_repository": AAXIS_APPS["bildwiz-backend_runtime"]["image_repository"],
            "deployments": {"bildwiz-demo/bildwiz-service": {}},
        },
    }


def test_a_database_instance_and_its_affected_services_are_named_things():
    assert project_what("infrastructure_topology", AOS_TOPOLOGY) == {
        "aos_prod_ssl_scope": {
            "instance": "aos-pg16-prod",
            "affected_services": {"aos-api-prod": {}, "aos-keycloak-prod": {}},
        },
        "aos_dev_ssl_scope": {"instance": "aos-pg16"},
    }


def test_a_repo_or_project_the_user_names_is_what_and_a_local_path_is_how():
    assert project_what("infrastructure_topology", CENTURY_TOPOLOGY) == {
        "branchkinect_artifacts_repo": {"remote": "century-inc/branchkinect-artifacts"},
        "branchkinect_platform": {
            "github_slug": "century-inc/branchkinect-artifacts",
            "target_project": "branchkinect-artifacts",
        },
        "century_iac": {"seed_project": "branchkinect-century-core"},
        "branchkinect_factory": {"factory_project": {"project_id": "branchkinect-factory-gcp"}},
    }


def test_a_project_value_carrying_a_billing_account_projects_only_the_project_id():
    flat = repr(project_what("infrastructure_topology", CENTURY_TOPOLOGY))
    for leaked in ("billing", "012858-140391-C81803", "org-root", "145362834906"):
        assert leaked not in flat


def test_project_identity_names_a_repo_by_its_remote_never_by_alias_or_absolute_path():
    projected = project_what("project_identity", AAXIS_IDENTITY)
    assert projected["rnd_2"] == {"remote_url": "git@bitbucket.org:aaxisdigital/ai-project-guidance.git"}
    assert projected["bildwiz_7"] == {"remote_url": "https://github.com/gpalagan-aaxis/newco-pitot"}
    assert projected["aos"]["apps"] == {"@aos/web": {}, "@aos/server": {}}
    assert all("name" not in entry for entry in projected.values())
    assert "/home/" not in repr(projected)


def test_a_project_without_a_remote_is_named_by_its_folder_alone():
    assert project_what("project_identity", AAXIS_IDENTITY)["ai_ways_of_working"] == {
        "local_path": "ai-ways-of-working"
    }


def test_a_gone_checkout_with_a_remote_keeps_the_remote_and_one_without_is_left_out():
    assert project_what("project_identity", CENTURY_IDENTITY) == {
        "century_skill_hub": {"remote_url": "https://github.com/century-inc/century-skill-hub.git"},
        "branchkinect_iac": {"remote_url": "https://github.com/century-inc/branchkinect-iac.git"},
    }


def test_git_repos_are_named_by_their_folder_name_and_remote():
    assert project_what("git", AAXIS_GIT) == {
        "repos": [
            {"name": "bildwiz-iac", "remote_url": "git@bitbucket.org:aaxisdigital/bildwiz-iac.git"},
            {"name": "newco-pitot", "remote_url": "https://github.com/gpalagan-aaxis/newco-pitot"},
        ]
    }


def test_static_ips_name_the_gateways_and_the_scanner_sections_are_declared_how():
    assert project_what("static_ips", AAXIS_STATIC_IPS) == {key: {} for key in AAXIS_STATIC_IPS}
    for section in ("monitoring_observability", "orchestration", "stack"):
        assert WHAT_KEYS[section] == ()
    assert project_what("stack", AAXIS_STACK) == {}


def test_terraform_infrastructure_names_its_units_and_nothing_inside_them():
    assert project_what("terraform_infrastructure", AAXIS_TERRAFORM) == {"newco_pitot_bootstrap": {}}


@pytest.mark.parametrize(
    "section, path, expected",
    [
        ("cluster_details", "live_demo.namespace", True),
        ("cluster_details", "live_demo.workloads[1]", True),
        ("cluster_details", "live_demo.database", False),
        ("cluster_details", "live_demo", False),
        ("cluster_details", "active_cluster.current_node_count", False),
        ("cluster_details", "scaled_to_zero_namespaces.bildwiz-demo", True),
        ("cluster_details", "scaled_to_zero_namespaces.bildwiz-demo.replicas", False),
        ("application_services", "services[3].name", True),
        ("application_services", "services[3].path", False),
        ("application_services", "bildwiz-backend_runtime", True),
        ("application_services", "bildwiz-backend_runtime.deployments.bildwiz-demo/bildwiz-service", True),
        ("application_services", "bildwiz-backend_runtime.deployments.bildwiz-demo/bildwiz-service.image_tag", False),
        ("infrastructure_topology", "century_iac.repo_path", False),
        ("infrastructure_topology", "branchkinect_platform.github_slug", True),
        ("git", "repos[0].name", True),
        ("git", "repos.name", False),
        ("environment", "tools[0].name", False),
        ("no_such_section", "cluster", False),
    ],
)
def test_is_what_path_answers_for_a_concrete_path(section, path, expected):
    assert is_what_path(section, path) is expected


def test_a_guarded_key_needs_the_payload_to_know_its_siblings():
    assert is_what_path("project_identity", "rnd_2.local_path") is True
    assert is_what_path("project_identity", "rnd_2.local_path", AAXIS_IDENTITY) is False
    assert is_what_path("project_identity", "ai_ways_of_working.local_path", AAXIS_IDENTITY) is True
    assert is_what_path("project_identity", "rnd_2.remote_url", AAXIS_IDENTITY) is True
    assert is_what_path("project_identity", "branchkinect_iac.remote_url", CENTURY_IDENTITY) is True
    assert is_what_path("project_identity", "branchkinect_modelb_fixture.local_path", CENTURY_IDENTITY) is False


def test_an_object_only_key_needs_the_payload_to_tell_an_app_from_a_scalar():
    assert is_what_path("application_services", "base_path") is True
    assert is_what_path("application_services", "base_path", AAXIS_APPS) is False
    assert is_what_path("application_services", "services", AAXIS_APPS) is False
    assert is_what_path("application_services", "bildwiz-backend_runtime", AAXIS_APPS) is True
    assert is_what_path("application_services", "absent", AAXIS_APPS) is False


@pytest.mark.parametrize(
    "section, payload",
    [
        ("cluster_details", BILDWIZ_CLUSTER),
        ("cluster_details", BRANCHKINECT_CLUSTER),
        ("application_services", AAXIS_APPS),
        ("application_services", SKILL_HUB_APPS),
        ("gitops_configuration", NONPROD_GITOPS),
        ("infrastructure_topology", AOS_TOPOLOGY),
        ("infrastructure_topology", CENTURY_TOPOLOGY),
        ("project_identity", AAXIS_IDENTITY),
        ("project_identity", CENTURY_IDENTITY),
        ("terraform_infrastructure", AAXIS_TERRAFORM),
        ("git", AAXIS_GIT),
    ],
)
def test_the_projection_and_the_predicate_agree_on_every_path_it_keeps(section, payload):
    def paths(value, prefix):
        if isinstance(value, dict) and value:
            for key, item in value.items():
                yield from paths(item, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, list) and value:
            for index, item in enumerate(value):
                yield from paths(item, f"{prefix}[{index}]")
        else:
            yield prefix

    kept = list(paths(project_what(section, payload), ""))
    assert kept and all(is_what_path(section, path) for path in kept), kept


@pytest.mark.parametrize("section", sorted(WHAT_KEYS))
def test_every_registered_path_is_well_formed(section):
    for path in what_keys(section):
        steps = path.split(".")
        assert all(steps), path
        assert all(not step.startswith("[") for step in steps), path
        assert all(not step.endswith("{}") for step in steps[:-1]), path
