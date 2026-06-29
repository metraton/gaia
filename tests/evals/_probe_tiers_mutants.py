#!/usr/bin/env python3
"""Probe specific tiers.py mutants by job_id against the current test suite.

Per-mutant, deterministic single-mutant runner reusing the mutkill harness
machinery (each spec applied via cosmic_ray.mutating.mutate_and_test in an
isolated clone, serial in one shard). Pass job_id prefixes (>= 8 chars) or full
32-char job_ids as argv; prints job_id -> outcome.

Used to verify tiers equivalence candidates by hand and to confirm scoped kills
WITHOUT running the full harness (whose `pytest -x` test-command gives a
non-deterministic measurement under sharding). This runner is deterministic:
each mutant is applied alone and the full security test suite is run against it.

USAGE:
    uv run python tests/evals/_probe_tiers_mutants.py <job_id_prefix> [...]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mutkill_approval_grants as mk  # noqa: E402

SESSION = HERE.parent.parent / "tiers-spike.sqlite"
TOML = HERE / "mutation-security-core.toml"
MODULE_REL = "hooks/modules/security/tiers.py"


def main() -> None:
    wanted = list(sys.argv[1:])
    if not wanted:
        raise SystemExit("pass job_id prefixes (>=8 chars) or full job_ids as argv")
    specs = mk.load_specs(SESSION)

    def matches(job_id: str) -> bool:
        return any(job_id == w or job_id.startswith(w) for w in wanted)

    chosen = [s for s in specs if matches(s["job_id"])]
    found_prefixes = set()
    for s in chosen:
        for w in wanted:
            if s["job_id"] == w or s["job_id"].startswith(w):
                found_prefixes.add(w)
    missing = set(wanted) - found_prefixes
    if missing:
        print("WARNING no spec matched:", ", ".join(sorted(missing)), file=sys.stderr)
    if not chosen:
        raise SystemExit("no matching specs")

    test_command = mk.read_test_command(TOML)
    timeout = mk.read_timeout(TOML)
    # Run all chosen specs in one shard (one clone), serial -- deterministic.
    results = mk._run_shard(chosen, test_command, timeout, False, MODULE_REL)
    rmap = {jid: outcome for jid, _op, outcome in results}
    for s in chosen:
        jid = s["job_id"]
        print("%-34s %-12s L%s %s" % (
            jid, rmap.get(jid, "?"), s["start_pos"][0], s["operator_name"]))


if __name__ == "__main__":
    main()
