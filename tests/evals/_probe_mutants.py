#!/usr/bin/env python3
"""Probe specific mutants by job_id against the current test suite.

Faithful single-mutant runner reusing the mutkill harness machinery
(mutate_and_test in an isolated clone). Pass job_ids as argv; prints
job_id -> outcome. Used to verify equivalence candidates by hand and to
confirm scoped kills without running the full harness.

USAGE:
    uv run python tests/evals/_probe_mutants.py <job_id> [<job_id> ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mutkill_approval_grants as mk  # noqa: E402

SESSION = HERE.parent.parent / "mutative-verbs.sqlite"
TOML = HERE / "mutation-mutative-verbs.toml"


def main() -> None:
    wanted = set(sys.argv[1:])
    if not wanted:
        raise SystemExit("pass job_ids as argv")
    specs = mk.load_specs(SESSION)
    chosen = [s for s in specs if s["job_id"] in wanted]
    found = {s["job_id"] for s in chosen}
    missing = wanted - found
    if missing:
        print("WARNING missing job_ids:", ", ".join(sorted(missing)), file=sys.stderr)
    test_command = mk.read_test_command(TOML)
    timeout = mk.read_timeout(TOML)
    module_rel = mk.read_module_path(TOML)
    # Run all chosen specs in one shard (one clone), serial.
    results = mk._run_shard(chosen, test_command, timeout, False, module_rel)
    rmap = {jid: outcome for jid, _op, outcome in results}
    for s in chosen:
        jid = s["job_id"]
        print("%-34s %-12s L%s %s" % (
            jid, rmap.get(jid, "?"), s["start_pos"][0], s["operator_name"]))


if __name__ == "__main__":
    main()
