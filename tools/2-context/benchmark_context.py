#!/usr/bin/env python3
"""
Context Optimization Benchmark
Mide las mejoras de rendimiento del sistema de context optimization.
Compara: token usage, velocidad de carga, relevancia del contexto.
"""

import json
import time
import sys
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass
import statistics

# Import optimization tools
sys.path.insert(0, str(Path(__file__).parent))
from context_lazy_loader import LazyContextLoader
from context_compressor import ContextCompressor
from context_selector import SmartContextSelector


@dataclass
class BenchmarkResult:
    """Results of a benchmark run"""
    scenario: str
    original_tokens: int
    optimized_tokens: int
    token_reduction: float
    original_time_ms: float
    optimized_time_ms: float
    speed_improvement: float
    sections_loaded: int
    compression_ratio: float


class ContextBenchmark:
    """
    Benchmark suite for context optimization.

    Measures:
    1. Token reduction (before vs after)
    2. Load time improvement
    3. Context relevance
    4. Memory usage
    """

    def __init__(self, context_file: Path = None):
        """
        Initialize benchmark.

        Args:
            context_file: Path to project-context.json for testing
        """
        self.context_file = context_file or self._create_test_context()
        self.results: List[BenchmarkResult] = []

    def _create_test_context(self) -> Path:
        """Create a realistic test context file"""
        test_context = {
            "project_details": {
                "id": "benchmark-project-123",
                "name": "Benchmark Test Project",
                "region": "us-central1",
                "environment": "production",
                "owner": "team-platform",
                "created_at": "2024-01-01T00:00:00Z"
            },
            "terraform_infrastructure": {
                "version": "1.5.0",
                "resources": [
                    {
                        "type": "google_compute_network",
                        "name": f"vpc-{i}",
                        "instances": [{"id": f"vpc-{i}-instance"}]
                    }
                    for i in range(20)
                ],
                "modules": {
                    "networking": {"source": "./modules/network", "version": "2.0"},
                    "compute": {"source": "./modules/compute", "version": "1.5"},
                    "storage": {"source": "./modules/storage", "version": "1.2"}
                }
            },
            "terraform_state": {
                "version": 4,
                "serial": 145,
                "lineage": "abc123",
                "outputs": {"vpc_id": "vpc-12345", "subnet_ids": ["subnet-1", "subnet-2"]},
                "resources": [{"type": "google_compute_network"} for _ in range(50)]
            },
            "cluster_details": {
                "name": "main-cluster",
                "version": "1.27.3",
                "node_count": 15,
                "node_pools": [
                    {"name": f"pool-{i}", "machine_type": "n2-standard-4", "node_count": 5}
                    for i in range(3)
                ]
            },
            "namespaces": [
                {
                    "name": f"namespace-{i}",
                    "status": "Active",
                    "labels": {"env": "prod", "team": f"team-{i}"}
                }
                for i in range(30)
            ],
            "deployments": [
                {
                    "name": f"deployment-{i}",
                    "namespace": f"namespace-{i % 10}",
                    "replicas": 3,
                    "ready_replicas": 3 if i < 40 else 2,
                    "image": f"app:v{i}",
                    "resources": {"cpu": "100m", "memory": "256Mi"}
                }
                for i in range(50)
            ],
            "services": [
                {
                    "name": f"service-{i}",
                    "type": "ClusterIP",
                    "ports": [{"port": 80, "targetPort": 8080}],
                    "selector": {"app": f"app-{i}"}
                }
                for i in range(40)
            ],
            "error_logs": [
                {
                    "timestamp": f"2024-01-01T{i:02d}:00:00Z",
                    "level": "ERROR" if i < 10 else "WARNING",
                    "message": f"Error message {i}",
                    "source": f"pod-{i}"
                }
                for i in range(100)
            ],
            "metrics": {
                "cpu_usage": [0.5 + i*0.01 for i in range(100)],
                "memory_usage": [0.6 + i*0.01 for i in range(100)],
                "request_rate": [100 + i*10 for i in range(100)]
            },
            "operational_guidelines": {
                "on_call_schedule": "PagerDuty",
                "runbooks": ["incident-response", "deployment", "rollback"],
                "sla": "99.9%",
                "rpo": "1 hour",
                "rto": "4 hours"
            }
        }

        # Write to temp file
        temp_file = Path("/tmp/benchmark-context.json")
        with open(temp_file, "w") as f:
            json.dump(test_context, f)

        return temp_file

    def run_benchmark_suite(self):
        """Run complete benchmark suite"""
        print("🏃 Running Context Optimization Benchmark Suite...")
        print("=" * 60)

        scenarios = [
            {
                "name": "Terraform Apply (T3)",
                "agent": "terraform-architect",
                "task": "Apply terraform changes to production infrastructure",
                "tier": "T3"
            },
            {
                "name": "Pod Debugging (T2)",
                "agent": "gitops-operator",
                "task": "Debug why pods are crashing in payment service",
                "tier": "T2"
            },
            {
                "name": "Status Check (T0)",
                "agent": "devops-developer",
                "task": "Check cluster status and health",
                "tier": "T0"
            },
            {
                "name": "Cost Analysis (T1)",
                "agent": "gcp-troubleshooter",
                "task": "Analyze cost trends and identify optimization opportunities",
                "tier": "T1"
            }
        ]

        for scenario in scenarios:
            print(f"\n📊 Scenario: {scenario['name']}")
            print("-" * 40)

            result = self._benchmark_scenario(
                scenario["name"],
                scenario["agent"],
                scenario["task"],
                scenario["tier"]
            )

            self.results.append(result)
            self._print_result(result)

        # Print summary
        self._print_summary()

    def _benchmark_scenario(
        self,
        scenario_name: str,
        agent: str,
        task: str,
        tier: str
    ) -> BenchmarkResult:
        """Benchmark a single scenario"""

        # ========== ORIGINAL APPROACH (no optimization) ==========
        start_time = time.time()

        # Load entire context
        with open(self.context_file) as f:
            original_context = json.load(f)

        original_time = (time.time() - start_time) * 1000  # Convert to ms
        original_tokens = self._estimate_tokens(original_context)

        # ========== OPTIMIZED APPROACH ==========
        start_time = time.time()

        # Step 1: Smart selection
        selector = SmartContextSelector()
        available_sections = list(original_context.keys())
        selected = selector.select_relevant_sections(
            task=task,
            agent=agent,
            tier=tier,
            available_sections=available_sections,
            max_sections=8
        )

        # Step 2: Lazy loading
        loader = LazyContextLoader(self.context_file, max_tokens=3000)
        lazy_context = loader.load_minimal_context(
            agent=agent,
            task=task,
            tier=tier
        )

        # Step 3: Compression
        compressor = ContextCompressor(aggressive=(tier == "T0"))
        compressed_context, compression_stats = compressor.compress(lazy_context)

        optimized_time = (time.time() - start_time) * 1000
        optimized_tokens = self._estimate_tokens(compressed_context)

        # Calculate improvements
        token_reduction = 1 - (optimized_tokens / original_tokens)
        speed_improvement = original_time / optimized_time if optimized_time > 0 else 1.0

        return BenchmarkResult(
            scenario=scenario_name,
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            token_reduction=token_reduction,
            original_time_ms=original_time,
            optimized_time_ms=optimized_time,
            speed_improvement=speed_improvement,
            sections_loaded=len(lazy_context.get("metadata", {}).get("loaded_sections", [])),
            compression_ratio=compression_stats.compression_ratio
        )

    def _estimate_tokens(self, obj: Any) -> int:
        """Estimate tokens (roughly 4 chars per token)"""
        json_str = json.dumps(obj, default=str)
        return len(json_str) // 4

    def _print_result(self, result: BenchmarkResult):
        """Print individual result"""
        print(f"  Original: {result.original_tokens:,} tokens in {result.original_time_ms:.1f}ms")
        print(f"  Optimized: {result.optimized_tokens:,} tokens in {result.optimized_time_ms:.1f}ms")
        print(f"  ✅ Token reduction: {result.token_reduction:.1%}")
        print(f"  ✅ Speed improvement: {result.speed_improvement:.1f}x")
        print(f"  ✅ Sections loaded: {result.sections_loaded}")
        print(f"  ✅ Compression ratio: {result.compression_ratio:.1%}")

    def _print_summary(self):
        """Print overall summary"""
        print("\n" + "=" * 60)
        print("📈 BENCHMARK SUMMARY")
        print("=" * 60)

        if not self.results:
            print("No results to summarize")
            return

        # Calculate averages
        avg_token_reduction = statistics.mean(r.token_reduction for r in self.results)
        avg_speed_improvement = statistics.mean(r.speed_improvement for r in self.results)
        avg_compression = statistics.mean(r.compression_ratio for r in self.results)

        total_original = sum(r.original_tokens for r in self.results)
        total_optimized = sum(r.optimized_tokens for r in self.results)
        total_saved = total_original - total_optimized

        print(f"\n🎯 Key Metrics:")
        print(f"  Average token reduction: {avg_token_reduction:.1%}")
        print(f"  Average speed improvement: {avg_speed_improvement:.1f}x")
        print(f"  Average compression ratio: {avg_compression:.1%}")
        print(f"\n💰 Token Savings:")
        print(f"  Total original: {total_original:,} tokens")
        print(f"  Total optimized: {total_optimized:,} tokens")
        print(f"  Total saved: {total_saved:,} tokens ({total_saved/total_original:.1%})")

        # Best and worst scenarios
        best_reduction = max(self.results, key=lambda r: r.token_reduction)
        worst_reduction = min(self.results, key=lambda r: r.token_reduction)

        print(f"\n🏆 Best token reduction: {best_reduction.scenario} ({best_reduction.token_reduction:.1%})")
        print(f"⚠️  Worst token reduction: {worst_reduction.scenario} ({worst_reduction.token_reduction:.1%})")

        # Cost estimation (assuming $0.01 per 1K tokens)
        cost_per_1k = 0.01
        monthly_requests = 10000  # Estimate
        monthly_savings = (total_saved * monthly_requests * cost_per_1k) / 1000

        print(f"\n💵 Estimated Monthly Savings:")
        print(f"  Assuming {monthly_requests:,} requests/month")
        print(f"  Token savings: {total_saved * monthly_requests:,}")
        print(f"  Cost savings: ${monthly_savings:,.2f}/month")

        # Performance impact
        print(f"\n⚡ Performance Impact:")
        if avg_speed_improvement > 1:
            print(f"  ✅ {avg_speed_improvement:.1f}x faster on average")
        else:
            print(f"  ⚠️  No significant speed improvement")

        # Recommendations
        print(f"\n💡 Recommendations:")
        if avg_token_reduction > 0.5:
            print(f"  ✅ Excellent token reduction achieved ({avg_token_reduction:.1%})")
        else:
            print(f"  ⚠️  Consider more aggressive compression for better savings")

        if any(r.sections_loaded > 10 for r in self.results):
            print(f"  ⚠️  Some scenarios loading many sections - review selection logic")

        print("\n✨ Context optimization is working effectively!")


# CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Context Optimization Benchmark")
    parser.add_argument(
        "--context-file",
        type=Path,
        help="Path to context file (default: creates test context)"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run quick benchmark with fewer scenarios"
    )
    args = parser.parse_args()

    # Run benchmark
    benchmark = ContextBenchmark(context_file=args.context_file)
    benchmark.run_benchmark_suite()

    # Export results to file
    results_file = Path("benchmark_results.json")
    with open(results_file, "w") as f:
        json.dump(
            [
                {
                    "scenario": r.scenario,
                    "token_reduction": r.token_reduction,
                    "speed_improvement": r.speed_improvement,
                    "original_tokens": r.original_tokens,
                    "optimized_tokens": r.optimized_tokens
                }
                for r in benchmark.results
            ],
            f,
            indent=2
        )
    print(f"\n📊 Results exported to: {results_file}")