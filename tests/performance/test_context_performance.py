#!/usr/bin/env python3
"""
Performance benchmark tests for context enrichment pipeline.

Validates non-functional requirements:
  NFR-001: process_agent_output completes in < 200 ms on a ~50 KB context file.
  NFR-002: deep_merge handles ~50 KB without degradation (linear, not quadratic).

Modules under test:
  - hooks/modules/context/context_writer.py  (process_agent_output)
  - tools/context/deep_merge.py              (used internally)
"""

import sys
import json
import shutil
import time
import pytest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

# ---------------------------------------------------------------------------
# Lazy import
# ---------------------------------------------------------------------------

def _import_process_agent_output():
    from context_writer import process_agent_output
    return process_agent_output


def _import_deep_merge():
    from deep_merge import deep_merge
    return deep_merge


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_CONTEXT_SIZE_KB = 50
NFR_001_MAX_MS = 200
NFR_002_DEGRADATION_FACTOR = 3.0  # 2x file must not take > 3x the time

# Number of timing iterations for stable measurements
TIMING_ITERATIONS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_large_context(target_kb: int = TARGET_CONTEXT_SIZE_KB) -> dict:
    """Generate a realistic project-context.json of approximately *target_kb* KB.

    Structure mirrors real production contexts with 12 sections, nested dicts,
    and arrays of objects with ``name`` keys (exercising the named-dict merge
    path in deep_merge).
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
            # 1. project_details
            "project_details": {
                "id": "perf-benchmark-123456",
                "region": "us-central1",
                "environment": "production",
                "description": "Performance benchmark project for gaia-ops context pipeline",
                "owner": "platform-team",
                "cost_center": "CC-12345",
                "tags": {f"tag-{i}": f"value-{i}" for i in range(20)},
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

            # 9. application_architecture
            "application_architecture": {
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

            # 10. development_standards
            "development_standards": {
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


def _make_agent_output(update_dict: dict) -> str:
    """Build an agent output string containing a CONTEXT_UPDATE block."""
    return (
        "## Agent Execution Complete\n\n"
        "Task completed successfully.\n\n"
        "CONTEXT_UPDATE:\n"
        + json.dumps(update_dict, indent=2)
    )


def _build_task_info(agent_type: str, context_path: Path, config_dir: Path) -> dict:
    return {
        "agent_type": agent_type,
        "context_path": str(context_path),
        "config_dir": str(config_dir),
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
    """Create an isolated ~50 KB project-context.json and matching config dir.

    Returns (context_file, config_dir, context_data).
    """
    context_dir = tmp_path / ".claude" / "project-context"
    context_dir.mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Copy the real GCP contracts so permission checks use production rules
    real_contracts = CONFIG_DIR / "context-contracts.gcp.json"
    if real_contracts.exists():
        shutil.copy(real_contracts, config_dir / "context-contracts.gcp.json")

    # Generate and write the large context
    context_data = _generate_large_context(TARGET_CONTEXT_SIZE_KB)
    context_file = context_dir / "project-context.json"
    context_file.write_text(json.dumps(context_data, indent=2))

    return context_file, config_dir, context_data


# ============================================================================
# Sanity check: generated context size
# ============================================================================

class TestContextGeneration:
    """Verify the generated fixture is realistic and meets the size target."""

    def test_generated_context_is_approximately_50kb(self, setup_perf):
        context_file, _, _ = setup_perf
        size_kb = context_file.stat().st_size / 1024
        assert size_kb >= 45, f"Context too small: {size_kb:.1f} KB (expected >= 45 KB)"
        assert size_kb <= 120, f"Context too large: {size_kb:.1f} KB (expected <= 120 KB)"

    def test_generated_context_has_12_sections(self, setup_perf):
        _, _, context_data = setup_perf
        sections = context_data["sections"]
        assert len(sections) == 12, (
            f"Expected 12 sections, got {len(sections)}: {sorted(sections.keys())}"
        )

    def test_generated_context_has_nested_structures(self, setup_perf):
        _, _, context_data = setup_perf
        sections = context_data["sections"]

        # Nested dicts
        assert isinstance(sections["infrastructure_topology"]["vpc"], dict)
        assert isinstance(sections["monitoring_observability"]["prometheus"], dict)

        # Arrays of named dicts (exercises the named-dict merge path)
        assert isinstance(sections["application_services"]["services"], list)
        assert all("name" in svc for svc in sections["application_services"]["services"])
        assert isinstance(sections["cluster_details"]["clusters"], list)
        assert all("name" in c for c in sections["cluster_details"]["clusters"])


# ============================================================================
# NFR-001: process_agent_output < 200 ms on ~50 KB context
# ============================================================================

class TestNFR001ProcessAgentOutputLatency:
    """NFR-001: end-to-end process_agent_output must complete in < 200 ms."""

    def test_two_section_update_under_200ms(self, setup_perf):
        """Time process_agent_output updating 2 sections on a ~50 KB file.

        The update touches ``cluster_details`` (array of named dicts) and
        ``infrastructure_topology`` (nested dict), exercising both merge
        strategies.
        """
        process_agent_output = _import_process_agent_output()
        context_file, config_dir, _ = setup_perf

        update = {
            "cluster_details": {
                "clusters": [
                    {
                        "name": "gke-cluster-us-central1-production",
                        "node_count": 12,
                        "kubernetes_version": "1.30.0",
                        "status": "RECONCILING",
                    },
                ],
            },
            "infrastructure_topology": {
                "vpc": {
                    "subnets": [
                        {
                            "name": "subnet-99",
                            "cidr": "10.0.99.0/24",
                            "region": "us-central1",
                            "purpose": "new-workload",
                        },
                    ],
                },
                "dns_zones": [
                    {"name": "zone-perf.example.com", "records": 42},
                ],
            },
        }
        agent_output = _make_agent_output(update)
        task_info = _build_task_info("cloud-troubleshooter", context_file, config_dir)

        def run_once():
            # Re-write the original context each iteration to ensure a
            # consistent starting state (process_agent_output mutates the file).
            context_data = _generate_large_context(TARGET_CONTEXT_SIZE_KB)
            context_file.write_text(json.dumps(context_data, indent=2))
            process_agent_output(agent_output, task_info)

        elapsed_ms = _median_time_ms(run_once)

        assert elapsed_ms < NFR_001_MAX_MS, (
            f"NFR-001 FAILED: process_agent_output took {elapsed_ms:.1f} ms "
            f"(budget: {NFR_001_MAX_MS} ms)"
        )

    def test_single_section_scalar_update_under_200ms(self, setup_perf):
        """Simpler case: update a single scalar field in infrastructure_topology."""
        process_agent_output = _import_process_agent_output()
        context_file, config_dir, _ = setup_perf

        update = {
            "infrastructure_topology": {
                "vpc": {
                    "name": "main-vpc-v2",
                },
            },
        }
        agent_output = _make_agent_output(update)
        task_info = _build_task_info("cloud-troubleshooter", context_file, config_dir)

        def run_once():
            context_data = _generate_large_context(TARGET_CONTEXT_SIZE_KB)
            context_file.write_text(json.dumps(context_data, indent=2))
            process_agent_output(agent_output, task_info)

        elapsed_ms = _median_time_ms(run_once)

        assert elapsed_ms < NFR_001_MAX_MS, (
            f"NFR-001 FAILED: single-section update took {elapsed_ms:.1f} ms "
            f"(budget: {NFR_001_MAX_MS} ms)"
        )


# ============================================================================
# NFR-002: deep_merge scales linearly with file size
# ============================================================================

class TestNFR002DeepMergeScalability:
    """NFR-002: deep_merge on a ~50 KB section must not degrade non-linearly.

    Strategy: time the merge on 25 KB vs 50 KB data.  The 50 KB run should
    not take more than NFR_002_DEGRADATION_FACTOR times the 25 KB run
    (allowing for constant overhead, GC jitter, etc.).
    """

    def test_deep_merge_linear_scaling(self):
        deep_merge = _import_deep_merge()

        # Build two section dicts of different sizes (25 KB and 50 KB)
        small_section = _build_section_dict(target_kb=25)
        large_section = _build_section_dict(target_kb=50)

        # The update is the same small delta applied to both
        update = {
            "new_field": "added-value",
            "nested": {
                "deep_key": "deep_value",
                "list_field": ["alpha", "beta", "gamma"],
            },
        }

        def merge_small():
            deep_merge(small_section, update)

        def merge_large():
            deep_merge(large_section, update)

        time_small = _median_time_ms(merge_small, iterations=10)
        time_large = _median_time_ms(merge_large, iterations=10)

        # Guard against near-zero measurements (both should be > 0)
        assert time_small > 0, "Small merge completed in 0 ms -- clock resolution issue?"

        ratio = time_large / time_small if time_small > 0 else 999

        assert ratio < NFR_002_DEGRADATION_FACTOR, (
            f"NFR-002 FAILED: deep_merge scaled non-linearly. "
            f"25 KB: {time_small:.2f} ms, 50 KB: {time_large:.2f} ms, "
            f"ratio: {ratio:.2f}x (max: {NFR_002_DEGRADATION_FACTOR}x)"
        )

    def test_deep_merge_50kb_absolute_time(self):
        """Deep merge on a single ~50 KB section completes well under 200 ms."""
        deep_merge = _import_deep_merge()

        section = _build_section_dict(target_kb=50)
        update = {
            "services": [
                {"name": "service-0", "status": "updated"},
                {"name": "new-service-benchmark", "status": "running", "port": 9999},
            ],
        }

        def run_merge():
            deep_merge(section, update)

        elapsed_ms = _median_time_ms(run_merge, iterations=10)

        assert elapsed_ms < NFR_001_MAX_MS, (
            f"NFR-002 FAILED: deep_merge on 50 KB took {elapsed_ms:.1f} ms "
            f"(budget: {NFR_001_MAX_MS} ms)"
        )


# ============================================================================
# Correctness under load: verify merge results are still accurate
# ============================================================================

class TestCorrectnessUnderLoad:
    """Ensure that the 50 KB context update produces correct merge results,
    not just fast ones."""

    def test_two_section_update_correctness(self, setup_perf):
        process_agent_output = _import_process_agent_output()
        context_file, config_dir, original_data = setup_perf

        update = {
            "cluster_details": {
                "clusters": [
                    {
                        "name": "gke-cluster-us-central1-production",
                        "node_count": 99,
                    },
                ],
            },
            "infrastructure_topology": {
                "vpc": {
                    "name": "renamed-vpc",
                },
            },
        }
        agent_output = _make_agent_output(update)
        task_info = _build_task_info("cloud-troubleshooter", context_file, config_dir)

        result = process_agent_output(agent_output, task_info)

        # Verify the function reported success
        assert result["updated"] is True
        assert "cluster_details" in result["sections_updated"]
        assert "infrastructure_topology" in result["sections_updated"]
        assert len(result["rejected"]) == 0

        # Read back and verify merge results
        written = json.loads(context_file.read_text())

        # cluster_details.clusters: named-dict merge -- node_count updated,
        # other clusters preserved (no-delete policy)
        clusters = written["sections"]["cluster_details"]["clusters"]
        prod_cluster = next(
            c for c in clusters if c["name"] == "gke-cluster-us-central1-production"
        )
        assert prod_cluster["node_count"] == 99
        # Original fields preserved via deep merge
        assert "kubernetes_version" in prod_cluster
        # Other clusters still present
        cluster_names = [c["name"] for c in clusters]
        assert "gke-cluster-us-east1-staging" in cluster_names

        # infrastructure_topology: nested dict merge
        vpc = written["sections"]["infrastructure_topology"]["vpc"]
        assert vpc["name"] == "renamed-vpc"
        # Original VPC fields preserved
        assert vpc["cidr"] == "10.0.0.0/16"
        assert len(vpc["subnets"]) >= 15

        # Sections NOT in update are untouched
        original_services = original_data["sections"]["application_services"]["services"]
        written_services = written["sections"]["application_services"]["services"]
        assert len(written_services) == len(original_services)

    def test_permission_rejection_on_large_context(self, setup_perf):
        """cloud-troubleshooter cannot write application_services on large context."""
        process_agent_output = _import_process_agent_output()
        context_file, config_dir, original_data = setup_perf

        update = {
            "application_services": {
                "services": [
                    {"name": "evil-service", "port": 6666},
                ],
            },
        }
        agent_output = _make_agent_output(update)
        task_info = _build_task_info("cloud-troubleshooter", context_file, config_dir)

        result = process_agent_output(agent_output, task_info)

        assert result["updated"] is False
        assert "application_services" in result["rejected"]

        # File unchanged
        written = json.loads(context_file.read_text())
        service_names = [s["name"] for s in written["sections"]["application_services"]["services"]]
        assert "evil-service" not in service_names


# ---------------------------------------------------------------------------
# Helpers for NFR-002 section generation
# ---------------------------------------------------------------------------

def _build_section_dict(target_kb: int) -> dict:
    """Build a dict of approximately *target_kb* KB with realistic structure.

    Contains nested dicts and arrays of named dicts to exercise all merge
    paths in deep_merge.
    """
    section: dict = {
        "metadata": {"generated_for": "benchmark", "version": "1.0"},
        "services": [],
        "config": {},
    }

    svc_index = 0
    while len(json.dumps(section)) < target_kb * 1024:
        section["services"].append({
            "name": f"svc-{svc_index}",
            "port": 3000 + svc_index,
            "status": "running",
            "replicas": 3,
            "labels": {f"label-{j}": f"value-{j}" for j in range(5)},
            "annotations": {f"ann-{j}": f"data-{j}" for j in range(5)},
        })
        section["config"][f"key-{svc_index}"] = {
            "nested_a": f"value-a-{svc_index}",
            "nested_b": svc_index * 100,
            "nested_list": list(range(svc_index % 10)),
        }
        svc_index += 1

    return section
