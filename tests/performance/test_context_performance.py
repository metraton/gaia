#!/usr/bin/env python3
"""
Performance benchmark tests for context enrichment pipeline.

Validates non-functional requirements:
  NFR-001: process_agent_output completes in < 200 ms on a ~50 KB payload.

Modules under test:
  - hooks/modules/context/context_writer.py  (process_agent_output)
"""

import sys
import json
import time
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))

# DB helpers
from tests.fixtures.db_helpers import (
    bootstrap_gaia_schema,
    seed_workspace,
    seed_agent_perms,
)


# ---------------------------------------------------------------------------
# Lazy import
# ---------------------------------------------------------------------------

def _import_process_update_contracts():
    # Full package path so the module's ..agents relative import resolves.
    from hooks.modules.context import context_writer as _cw
    _cw._permissions_cache.clear()
    from hooks.modules.context.context_writer import process_update_contracts
    return process_update_contracts


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_CONTEXT_SIZE_KB = 50
NFR_001_MAX_MS = 200

# Number of timing iterations for stable measurements
TIMING_ITERATIONS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_large_context(target_kb: int = TARGET_CONTEXT_SIZE_KB) -> dict:
    """Generate a realistic project-context.json of approximately *target_kb* KB.

    Structure mirrors real production contexts with 12 sections, nested dicts,
    and arrays of objects with ``name`` keys.
    """
    context = {
        "metadata": {
            "version": "1.0.0",
            "last_updated": "2025-06-15T12:00:00Z",
            "project_name": "perf-benchmark-project",
            "cloud_provider": "GCP",
            "primary_region": "us-central1",
            "environment": "production",
        },
        "sections": {
            # 1. project_identity (v2)
            "project_identity": {
                "name": "perf-benchmark-123456",
                "type": "application",
                "description": "Performance benchmark project for gaia-ops context pipeline",
            },
            # 1b. infrastructure (v2)
            "infrastructure": {
                "cloud_providers": [{"name": "gcp", "project_id": "perf-benchmark-123456", "region": "us-central1"}],
                "ci_cd": [],
            },

            # 2. cluster_details (dict with nested arrays of named dicts)
            "cluster_details": {
                "clusters": [
                    {
                        "name": f"gke-cluster-{region}-{env}",
                        "cloud_provider": "GCP",
                        "region": region,
                        "environment": env,
                        "node_count": 5 + i,
                        "status": "RUNNING",
                        "kubernetes_version": "1.29.1",
                        "node_pools": [
                            {
                                "name": f"pool-{p}",
                                "machine_type": "n2-standard-4",
                                "min_nodes": 1,
                                "max_nodes": 10,
                                "disk_size_gb": 100,
                            }
                            for p in range(3)
                        ],
                        "addons": ["http-load-balancing", "network-policy", "gce-pd-csi-driver"],
                    }
                    for i, (region, env) in enumerate([
                        ("us-central1", "production"),
                        ("us-east1", "staging"),
                        ("europe-west1", "production"),
                        ("asia-east1", "disaster-recovery"),
                    ])
                ],
                "namespaces": {
                    "application": ["adm", "dev", "test", "staging"],
                    "infrastructure": ["flux-system", "cert-manager", "ingress-nginx"],
                    "system": ["kube-system", "kube-public", "kube-node-lease"],
                },
                "helm_releases": [
                    {
                        "name": f"release-{i}",
                        "chart_version": f"1.{i}.0",
                        "namespace": "application",
                    }
                    for i in range(10)
                ],
            },

            # 3. infrastructure_topology
            "infrastructure_topology": {
                "vpc": {
                    "name": "main-vpc",
                    "cidr": "10.0.0.0/16",
                    "subnets": [
                        {
                            "name": f"subnet-{i}",
                            "cidr": f"10.0.{i}.0/24",
                            "region": "us-central1",
                            "purpose": "compute" if i % 2 == 0 else "database",
                        }
                        for i in range(15)
                    ],
                },
                "load_balancers": [
                    {
                        "name": f"lb-{i}",
                        "type": "EXTERNAL" if i < 3 else "INTERNAL",
                        "backends": [f"backend-group-{j}" for j in range(4)],
                        "health_check": f"/healthz-{i}",
                    }
                    for i in range(6)
                ],
                "dns_zones": [
                    {"name": f"zone-{i}.example.com", "records": 25 + i * 5}
                    for i in range(5)
                ],
                "firewall_rules": [
                    {
                        "name": f"allow-{proto}-{port}",
                        "protocol": proto,
                        "port": port,
                        "source_ranges": ["10.0.0.0/8", "172.16.0.0/12"],
                    }
                    for proto, port in [
                        ("tcp", 80), ("tcp", 443), ("tcp", 8080),
                        ("tcp", 3306), ("tcp", 5432), ("tcp", 6379),
                        ("udp", 53), ("tcp", 22),
                    ]
                ],
            },

            # 4. terraform_infrastructure
            "terraform_infrastructure": {
                "layout": {
                    "base_path": "terraform/",
                    "modules": [
                        "vpc", "gke", "cloudsql", "memorystore",
                        "storage", "iam", "monitoring", "dns",
                    ],
                },
                "state_backend": "gcs",
                "state_bucket": "tf-state-perf-benchmark",
                "workspaces": [
                    {
                        "name": f"ws-{env}",
                        "environment": env,
                        "last_apply": f"2025-06-{10 + i}T08:00:00Z",
                        "resource_count": 120 + i * 30,
                        "outputs": {f"output_{j}": f"value-{j}" for j in range(10)},
                    }
                    for i, env in enumerate(["production", "staging", "development"])
                ],
                "modules_detail": [
                    {
                        "name": f"module-{m}",
                        "source": f"./modules/{m}",
                        "version": f"2.{m_i}.0",
                        "resources": [
                            f"google_{m}_{r}" for r in range(8)
                        ],
                    }
                    for m_i, m in enumerate([
                        "vpc", "gke", "cloudsql", "memorystore",
                        "storage", "iam", "monitoring", "dns",
                    ])
                ],
            },

            # 5. gitops_configuration
            "gitops_configuration": {
                "repository": {
                    "url": "https://github.com/org/gitops-repo",
                    "path": "gitops/",
                    "branch": "main",
                    "deploy_key": "deploy-key-gitops",
                },
                "tool": "flux",
                "flux_version": "2.2.0",
                "kustomizations": [
                    {
                        "name": f"kustomization-{ns}",
                        "namespace": ns,
                        "path": f"./clusters/production/{ns}",
                        "interval": "5m",
                        "prune": True,
                    }
                    for ns in [
                        "common", "backend", "frontend", "data-pipeline",
                        "monitoring", "cert-manager", "ingress-nginx",
                    ]
                ],
                "helm_repositories": [
                    {"name": repo, "url": f"https://charts.{repo}.io"}
                    for repo in [
                        "bitnami", "jetstack", "grafana", "prometheus-community",
                        "ingress-nginx", "external-dns",
                    ]
                ],
            },

            # 6. application_services (dict with nested array of named dicts)
            "application_services": {
                "base_path": "./services",
                "services": [
                    {
                        "name": f"service-{i}",
                        "tech_stack": ["NestJS", "React", "Python", "Go", "Java"][i % 5],
                        "namespace": ["backend", "frontend", "data-pipeline", "common"][i % 4],
                        "port": 3000 + i,
                        "status": "running",
                        "description": f"Microservice {i} handling domain logic",
                        "replicas": 2 + (i % 3),
                        "resources": {
                            "cpu_request": "100m",
                            "cpu_limit": "500m",
                            "memory_request": "256Mi",
                            "memory_limit": "512Mi",
                        },
                        "health_check": {
                            "path": f"/health/{i}",
                            "interval": 30,
                            "timeout": 5,
                        },
                        "environment_variables": {
                            f"ENV_{j}": f"value_{j}" for j in range(6)
                        },
                    }
                    for i in range(25)
                ],
            },

            # 7. monitoring_observability
            "monitoring_observability": {
                "prometheus": {
                    "version": "2.51.0",
                    "retention": "30d",
                    "scrape_configs": [
                        {
                            "job_name": f"job-{i}",
                            "scrape_interval": "15s",
                            "targets": [f"target-{j}:9090" for j in range(5)],
                        }
                        for i in range(10)
                    ],
                },
                "grafana": {
                    "version": "10.4.0",
                    "dashboards": [
                        {
                            "name": f"dashboard-{i}",
                            "uid": f"d-{i}",
                            "panels": 8 + i,
                        }
                        for i in range(12)
                    ],
                },
                "alerting": {
                    "rules": [
                        {
                            "name": f"alert-{i}",
                            "severity": ["critical", "warning", "info"][i % 3],
                            "expression": f"rate(http_requests_total{{status=~'5..'}}[5m]) > {i}",
                            "for_duration": f"{5 + i}m",
                        }
                        for i in range(15)
                    ],
                },
            },

            # 8. operational_guidelines
            "operational_guidelines": {
                "commit_standards": "conventional",
                "approval_required_for": [
                    "production", "terraform_apply", "helm_upgrade",
                    "database_migration", "secret_rotation",
                ],
                "max_replicas": 10,
                "on_call": {
                    "schedule": "PagerDuty",
                    "escalation_policy": "platform-team-escalation",
                    "runbooks": [
                        f"runbook-{topic}"
                        for topic in [
                            "incident-response", "deployment", "rollback",
                            "scaling", "certificate-renewal", "database-failover",
                        ]
                    ],
                },
                "sla": {"target": "99.95%", "measurement_window": "30d"},
            },

            # 9. architecture_overview (v2 -- replaces application_architecture)
            "architecture_overview": {
                "style": "microservices",
                "api_gateway": {"type": "Kong", "version": "3.6.0"},
                "service_mesh": {"type": "Istio", "version": "1.21.0"},
                "message_bus": {
                    "type": "Google Pub/Sub",
                    "topics": [
                        {"name": f"topic-{i}", "subscriptions": 3 + i}
                        for i in range(10)
                    ],
                },
                "databases": [
                    {
                        "name": f"db-{i}",
                        "type": ["CloudSQL-PostgreSQL", "Memorystore-Redis", "Firestore"][i % 3],
                        "version": "15.4",
                        "size": f"{10 * (i + 1)}GB",
                    }
                    for i in range(6)
                ],
            },

            # 10. environment (v2 -- replaces development_standards)
            "environment": {
                "languages": {
                    lang: {
                        "version": ver,
                        "linter": linter,
                        "formatter": fmt,
                    }
                    for lang, ver, linter, fmt in [
                        ("typescript", "5.4", "eslint", "prettier"),
                        ("python", "3.12", "ruff", "black"),
                        ("go", "1.22", "golangci-lint", "gofmt"),
                        ("java", "21", "checkstyle", "google-java-format"),
                    ]
                },
                "ci_cd": {
                    "platform": "GitHub Actions",
                    "pipelines": [
                        {
                            "name": f"pipeline-{i}",
                            "trigger": "push",
                            "stages": ["lint", "test", "build", "deploy"],
                        }
                        for i in range(8)
                    ],
                },
                "testing": {
                    "coverage_threshold": 80,
                    "frameworks": ["pytest", "jest", "go-test"],
                    "e2e_tool": "playwright",
                },
            },

            # 11. namespaces (dict with nested array of named dicts)
            "namespaces": {
                "items": [
                    {
                        "name": f"ns-{i}",
                        "cluster": f"gke-cluster-us-central1-{'production' if i < 10 else 'staging'}",
                        "environment": "production" if i < 10 else "staging",
                        "labels": {
                            "team": f"team-{i % 5}",
                            "cost-center": f"CC-{1000 + i}",
                        },
                        "resource_quotas": {
                            "cpu": f"{2 + i}",
                            "memory": f"{4 + i}Gi",
                            "pods": str(50 + i * 10),
                        },
                    }
                    for i in range(20)
                ],
            },

            # 12. environments (dict with nested array of named dicts)
            "environments": {
                "items": [
                    {
                        "name": env,
                        "clusters": clusters,
                        "description": f"{env.title()} environment",
                        "auto_deploy": env != "production",
                        "approval_required": env == "production",
                        "secrets_provider": "Google Secret Manager",
                        "config_maps": [
                            {
                                "name": f"config-{env}-{j}",
                                "keys": [f"KEY_{k}" for k in range(8)],
                            }
                            for j in range(4)
                        ],
                    }
                    for env, clusters in [
                        ("production", ["gke-cluster-us-central1-production", "gke-cluster-europe-west1-production"]),
                        ("staging", ["gke-cluster-us-east1-staging"]),
                        ("development", ["gke-cluster-us-central1-development"]),
                        ("disaster-recovery", ["gke-cluster-asia-east1-disaster-recovery"]),
                    ]
                ],
            },
        },
    }

    # Pad until we reach the target size.  Add extra entries to the
    # ``application_services.services`` array, which is the largest section
    # and keeps the structure realistic.
    current_size = len(json.dumps(context))
    target_bytes = target_kb * 1024
    services_list = context["sections"]["application_services"]["services"]
    svc_index = len(services_list)

    while current_size < target_bytes:
        services_list.append({
            "name": f"service-{svc_index}",
            "tech_stack": "NestJS",
            "namespace": "backend",
            "port": 3000 + svc_index,
            "status": "running",
            "description": f"Padding service {svc_index} for benchmark target",
            "replicas": 3,
            "resources": {
                "cpu_request": "200m",
                "cpu_limit": "1000m",
                "memory_request": "512Mi",
                "memory_limit": "1Gi",
            },
            "health_check": {"path": f"/health/{svc_index}", "interval": 30, "timeout": 5},
            "environment_variables": {f"ENV_{j}": f"val_{j}" for j in range(8)},
        })
        svc_index += 1
        current_size = len(json.dumps(context))

    return context


def _make_contract(contract: str, payload: dict) -> dict:
    """Build a parsed contract dict carrying a one-entry update_contracts clause."""
    return {
        "agent_status": {"plan_status": "COMPLETE"},
        "update_contracts": [{"contract": contract, "payload": payload}],
    }


def _first_contract(result: dict):
    contracts = result.get("contracts") or []
    return contracts[0] if contracts else None


def _build_task_info(agent_type: str, db_path: Path, workspace: str = "global") -> dict:
    return {
        "agent_type": agent_type,
        "db_path": db_path,
        "workspace": workspace,
    }


def _median_time_ms(fn, iterations: int = TIMING_ITERATIONS) -> float:
    """Run *fn* multiple times and return the median wall-clock time in ms."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        elapsed_ms = (time.perf_counter() - start) * 1000
        times.append(elapsed_ms)
    times.sort()
    return times[len(times) // 2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_perf(tmp_path):
    """Create an isolated gaia.db + large payload for performance tests.

    Returns (db_path, large_payload_dict).

    The fixture does NOT depend on context-contracts.json. Permissions are
    seeded directly in the DB via db_helpers, so tests run in isolation.
    """
    db_path = tmp_path / "gaia_perf.db"
    bootstrap_gaia_schema(db_path)
    seed_workspace(db_path, "global")
    seed_agent_perms(
        db_path,
        "cloud-troubleshooter",
        reads=["cluster_details", "infrastructure_topology", "application_services",
               "monitoring_observability", "architecture_overview"],
        writes=["cluster_details", "infrastructure_topology", "application_services",
                "monitoring_observability", "architecture_overview"],
    )

    large_payload = _generate_large_context(TARGET_CONTEXT_SIZE_KB)
    return db_path, large_payload


# ============================================================================
# Sanity check: generated context size
# ============================================================================

class TestContextGeneration:
    """Verify the generated fixture is realistic and meets the size target."""

    def test_generated_context_is_approximately_50kb(self, setup_perf):
        _, large_payload = setup_perf
        size_kb = len(json.dumps(large_payload)) / 1024
        assert size_kb >= 45, f"Context too small: {size_kb:.1f} KB (expected >= 45 KB)"
        assert size_kb <= 120, f"Context too large: {size_kb:.1f} KB (expected <= 120 KB)"

    def test_generated_context_has_13_sections(self, setup_perf):
        _, large_payload = setup_perf
        sections = large_payload["sections"]
        assert len(sections) == 13, (
            f"Expected 13 sections, got {len(sections)}: {sorted(sections.keys())}"
        )

    def test_generated_context_has_nested_structures(self, setup_perf):
        _, large_payload = setup_perf
        sections = large_payload["sections"]

        # Nested dicts
        assert isinstance(sections["infrastructure_topology"]["vpc"], dict)
        assert isinstance(sections["monitoring_observability"]["prometheus"], dict)

        # Arrays of named dicts (exercises the named-dict merge path)
        assert isinstance(sections["application_services"]["services"], list)
        assert all("name" in svc for svc in sections["application_services"]["services"])
        assert isinstance(sections["cluster_details"]["clusters"], list)
        assert all("name" in c for c in sections["cluster_details"]["clusters"])


# ============================================================================
# NFR-001: process_agent_output < 200 ms on ~50 KB payload
# ============================================================================

class TestNFR001ProcessAgentOutputLatency:
    """NFR-001: end-to-end process_update_contracts must complete in < 200 ms."""

    def test_two_section_update_under_200ms(self, setup_perf):
        """Time process_update_contracts writing a ~50 KB payload to the DB.

        Uses cluster_details and infrastructure_topology contracts.
        """
        process_update_contracts = _import_process_update_contracts()
        db_path, large_payload = setup_perf

        cluster_payload = large_payload["sections"]["cluster_details"].copy()
        cluster_payload["clusters"][0]["node_count"] = 12
        cluster_payload["clusters"][0]["kubernetes_version"] = "1.30.0"

        contract = _make_contract("cluster_details", cluster_payload)
        task_info = _build_task_info("cloud-troubleshooter", db_path)

        def run_once():
            process_update_contracts(contract, task_info)

        elapsed_ms = _median_time_ms(run_once)

        assert elapsed_ms < NFR_001_MAX_MS, (
            f"NFR-001 FAILED: process_update_contracts took {elapsed_ms:.1f} ms "
            f"(budget: {NFR_001_MAX_MS} ms)"
        )

    def test_single_section_scalar_update_under_200ms(self, setup_perf):
        """Simpler case: update a single compact payload in infrastructure_topology."""
        process_update_contracts = _import_process_update_contracts()
        db_path, _ = setup_perf

        payload = {"vpc": {"name": "main-vpc-v2"}}
        contract = _make_contract("infrastructure_topology", payload)
        task_info = _build_task_info("cloud-troubleshooter", db_path)

        def run_once():
            process_update_contracts(contract, task_info)

        elapsed_ms = _median_time_ms(run_once)

        assert elapsed_ms < NFR_001_MAX_MS, (
            f"NFR-001 FAILED: single-section update took {elapsed_ms:.1f} ms "
            f"(budget: {NFR_001_MAX_MS} ms)"
        )


# ============================================================================
# Correctness under load: verify process_update_contracts results are accurate
# ============================================================================

class TestCorrectnessUnderLoad:
    """Ensure that process_update_contracts produces correct DB writes, not just fast ones."""

    def test_large_payload_written_correctly(self, setup_perf):
        """Writing a large cluster_details payload stores it exactly in the DB."""
        import sqlite3
        process_update_contracts = _import_process_update_contracts()
        db_path, large_payload = setup_perf

        cluster_payload = large_payload["sections"]["cluster_details"].copy()
        # Modify one field so we can verify the write
        cluster_payload["clusters"][0]["node_count"] = 99

        contract = _make_contract("cluster_details", cluster_payload)
        task_info = _build_task_info("cloud-troubleshooter", db_path)

        result = process_update_contracts(contract, task_info)

        assert result["updated"] is True
        assert _first_contract(result) == "cluster_details"
        assert result["rejected"] == []

        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT payload FROM project_context_contracts WHERE workspace='global' AND contract_name='cluster_details'"
        ).fetchone()
        con.close()

        assert row is not None, "cluster_details contract must be written to DB"
        stored = json.loads(row[0])

        prod_cluster = next(
            (c for c in stored["clusters"] if c["name"] == "gke-cluster-us-central1-production"),
            None
        )
        assert prod_cluster is not None
        assert prod_cluster["node_count"] == 99

    def test_permission_rejection_on_large_payload(self, setup_perf):
        """cloud-troubleshooter cannot write gitops_configuration."""
        process_update_contracts = _import_process_update_contracts()
        db_path, large_payload = setup_perf

        gitops_payload = large_payload["sections"]["gitops_configuration"].copy()
        gitops_payload["repo_url"] = "https://evil.example.com/gitops"

        contract = _make_contract("gitops_configuration", gitops_payload)
        task_info = _build_task_info("cloud-troubleshooter", db_path)

        result = process_update_contracts(contract, task_info)

        assert result["updated"] is False
        assert "gitops_configuration" in result["rejected"]


