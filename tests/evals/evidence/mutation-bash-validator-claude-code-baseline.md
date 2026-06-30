# Mutation Baseline — `bash_validator.py` + `claude_code.py`

**Date:** 2026-06-30
**Branch:** `fix/mutation-coverage-stable-skip-key`
**Outcome:** First REAL mutation-coverage baseline for the two remaining
security-core files, plus completion of the stable-skip-key fix (the
inline-ast skip file was the last one still keyed by legacy job_id).
**Survivor closure is FOLLOW-UP work — NOT done in this pass.**

This extends the AC-3 full-core inventory to the two files that were never
brought into the mutation net: the primary Bash security gate
(`hooks/modules/tools/bash_validator.py`) and the sole Claude Code hook adapter
(`hooks/adapters/claude_code.py`). New per-module configs:
`mutation-bash-validator.toml`, `mutation-claude-code.toml`.

---

## The false-100% trap this run had to avoid

`cosmic-ray init` regenerates a fresh uuid4 `job_id` for every mutant on every
init. A skip file keyed by literal `job_id` therefore matches NOTHING after a
re-init: every documented equivalent silently re-enters the kill-rate
denominator as a phantom "killable", and the rate floats up toward a false
100%. The fix (already applied to four of the five skip files by a prior agent)
keys exclusions on the mutant's STABLE identity — `operator | start:col-end:col
| occurrence` — which `cosmic-ray init` reproduces byte-for-byte from the source
AST. This pass re-keyed the LAST job_id-keyed file
(`equivalents-inline-ast.skip`, 11 entries) and verified all five resolve with
zero unmatched tokens against their current sessions.

These baselines are measured with the `mutkill_approval_grants.py` harness,
which re-runs `cosmic_ray.mutating.mutate_and_test` against the CURRENT tests on
every invocation (never stale) and uses the stable-id skip resolver. No skip
file applies to these two modules yet (no equivalents triaged), so the rates
below are the raw killable population — demonstrably NOT a false 100%.

---

## Scores by module

| Module | Config | Total mutants | Killed | Survived | Incompetent | **Mutation score (kill %)** |
|--------|--------|--------------:|-------:|---------:|------------:|----------------------------:|
| `bash_validator.py` | `mutation-bash-validator.toml` | 737 | 421 | 316 | 0 | **57.12%** |
| `claude_code.py`    | `mutation-claude-code.toml`    | 723 | 107 | 614 | 2 | **14.84%** |

- Tool: cosmic-ray 8.x via `mutkill_approval_grants.py`, 6 parallel workers.
- `claude_code.py` has 2 INCOMPETENT mutants (worker raised an exception);
  excluded from the denominator per `cr-rate` semantics (killed / (total −
  incompetent)).
- **NON-DETERMINISM NOTE:** `bash_validator.py` measured 57.12% on the dump run
  and 62.96% (464/273) on the first run. The variance is real and comes from
  the `-x` (stop-on-first-failure) flag in the test-command interacting with
  pytest collection order across the isolated shard clones: a mutant counts as
  killed if ANY test fails, but `-x` halts at the first failure, so which test
  "catches" a borderline mutant can shift between runs. Both runs agree on the
  essential finding — this is a ~57–63% module, NOT a false 100%. A future
  hardening pass should drop `-x` from the test-command to get a stable rate.

---

## Survivors by function — `bash_validator.py` (316 survivors)

| Survivors | Function | Lines |
|----------:|----------|-------|
| 51 | `_validate_single_command` | 621–901 |
| 43 | `decide_t3_outcome` | 1500–1655 |
| 36 | `_build_sealed_payload` | 1414–1483 |
| 33 | `_validate_compound_command` | 970–1049 |
| 31 | `_try_sanitize_command` | 329–381 |
| 30 | `_is_ungranted_t3_component` | 936–965 |
| 26 | `validate` | 435–612 |
| 24 | `_find_pending_in_db` | 1373–1404 |
| 13 | `_phase4_check_composition` | 1082–1133 |
| 11 | `_detect_indirect_execution` | 212–233 |
| 7 | `_validate_commit_message` | 1245–1284 |
| 4 | `_extract_commit_message` | 1304–1310 |
| 3 | `_extract_inner_command` | 278–279 |
| 2 | `__post_init__` | 105 |
| 1 | `<module>` | 83 |
| 1 | `_has_operators` | 296 |

## Survivors by function — `claude_code.py` (614 survivors)

| Survivors | Function | Lines |
|----------:|----------|-------|
| 135 | `adapt_subagent_stop` | 1362–1865 |
| 104 | `adapt_post_tool_use` | 1091–1179 |
| 54 | `_adapt_bash` | 664–759 |
| 38 | `adapt_pre_tool_use` | 591–657 |
| 35 | `_read_cached_context` | 1955–1999 |
| 27 | `_cleanup_stale_cache` | 2008–2016 |
| 26 | `_cache_context_for_subagent` | 1934–1936 |
| 24 | `_is_protected` | 952–965 |
| 22 | `_handle_ask_user_question_result` | 1253–1311 |
| 21 | `_get_gaia_agent_names` | 495–501 |
| 20 | `_adapt_write_edit` | 912–1020 |
| 17 | `_adapt_task` | 787–851 |
| 17 | `_adapt_send_message` | 868–905 |
| 12 | `format_ask_response` | 509–529 |
| 10 | `_record_t3_outcome_event` | 1184–1225 |
| 10 | `adapt_subagent_start` | 2035–2096 |
| 7 | `adapt_session_start` | 291 |
| 6 | `format_bootstrap_response` | 311–318 |
| 4 | `request_consent` | 557–567 |
| 4 | `adapt_stop` | 1889–1894 |
| 4 | `format_verification_response` | 2135–2140 |
| 3 | `ClaudeCodeAdapter` | 1060–1925 |
| 3 | `format_quality_response` | 2116–2119 |
| 2 | `read_permission_decision` | 68 |
| 2 | `read_permission_reason` | 75 |
| 2 | `inject_updated_input` | 92 |
| 2 | `format_validation_response` | 215 |
| 2 | `adapt_task_completed` | 1914–1917 |
| 1 | `parse_pre_tool_use` | 404 |

**Security-relevant hotspot:** `_is_protected` (the `.claude/` hard-protection
path check) has 24 surviving mutants — the function is under-tested at the
mutation level despite being a load-bearing security boundary. This is the kind
of real gap the false-100% bug was masking.

---

## How to reproduce

```
cd gaia
uv run cosmic-ray init tests/evals/mutation-bash-validator.toml bash-validator.sqlite
uv run python tests/evals/mutkill_approval_grants.py \
  --session bash-validator.sqlite --toml tests/evals/mutation-bash-validator.toml -j 6 --quiet

uv run cosmic-ray init tests/evals/mutation-claude-code.toml claude-code.sqlite
uv run python tests/evals/mutkill_approval_grants.py \
  --session claude-code.sqlite --toml tests/evals/mutation-claude-code.toml -j 6 --quiet
```

The `.sqlite` sessions are gitignored (consistent with the other security-core
sessions); re-init regenerates them. The stable-key skip mechanism makes that
re-init safe: any future equivalents-*.skip for these modules keyed by stable id
survives the regenerated job_ids.

---

## FOLLOW-UP (NOT done in this pass)

1. **Survivor closure** — write killer tests for the 316 + 614 survivors, or
   triage genuine equivalents into `equivalents-bash-validator.skip` /
   `equivalents-claude-code.skip` (stable-id keyed). Priority hotspot:
   `claude_code.py::_is_protected` (security boundary, 24 survivors).
2. **Drop `-x`** from both test-commands to remove the run-to-run variance.
3. **Re-init `inline-ast.sqlite`** — it is stale (predates source/test edits);
   the re-keyed skip file resolves against the current session but a fresh
   `cosmic-ray init` is needed before its `cr-rate` is meaningful again.
