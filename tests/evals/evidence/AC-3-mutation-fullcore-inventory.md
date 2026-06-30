# AC-3 Mutation Full-Core Inventory — security core (3 modules)

**Date:** 2026-06-26
**Brief:** fundamento-de-tests (AC-3: mutation testing on security-critical modules)
**Plan:** #12 / grant P-6e3918cf (COMMAND_SET, full-core batch)
**Outcome:** Full-core mutation inventory captured for the three deferred modules.
**Survivor closure is FOLLOW-UP work — NOT done in this pass (see FOLLOW-UP).**

This extends `AC-3-mutation-baseline.md`, which closed the `tiers.py` spike
survivors and deferred the remaining three security-core modules. This pass runs
those three with the per-module Option-A configs and inventories the survivors;
it does **not** write tests to close them.

---

## IMPORTANT — `cr-rate` reports SURVIVAL rate, not kill score

`uv run cr-rate <db>` prints the **survival rate** —
`(1 - kills / num_results) * 100` — i.e. the percentage of mutants that
**survived**, NOT the mutation score. The mutation score (% killed) used by the
AC-3 convention is therefore `100 - cr-rate`. Both numbers are tabulated below
so the raw tool output is traceable and the score is consistent with the
baseline file.

Source: `cosmic_ray/tools/survival_rate.py::survival_rate`.

---

## Scores by module + aggregate

| Module | Config | Total mutants | Killed | Survived | Incompetent | `cr-rate` (survival %) | **Mutation score (kill %)** |
|--------|--------|--------------:|-------:|---------:|------------:|----------------------:|----------------------------:|
| `mutative_verbs.py`  | `mutation-mutative-verbs.toml`  | 735  | 410 | 325 | 0 | 44.22 | **55.78%** |
| `blocked_commands.py`| `mutation-blocked-commands.toml`| 157  | 99  | 58  | 0 | 36.94 | **63.06%** |
| `approval_grants.py` | `mutation-approval-grants.toml` | 653  | 175 | 476 | 2 | 72.89 | **26.80%** |
| **AGGREGATE**        | —                               | **1545** | **684** | **859** | **2** | **55.60** | **44.27%** |

- Tool: cosmic-ray 8.x, `local` distributor (per the three per-module configs).
- All three sqlite sessions were pre-initialized by a prior agent (`cosmic-ray init`).
- `approval_grants.py` has 2 INCOMPETENT mutants (worker raised an exception);
  cosmic-ray does not count INCOMPETENT as killed. They are listed under
  survivors-by-function context below for completeness but are an
  invalid-mutant category, distinct from a true survivor.

The standout is `approval_grants.py` at 26.80% kill — the narrowed
test-command (`test_approval_grants.py` + `test_approval_cycle.py` +
`test_batch_approval.py`) does not exercise most of the module's branches.
This is exactly the low-kill-rate case the config's inline NOTE anticipated; see
FOLLOW-UP for the broadening lever.

---

## Survivors by module (function-level inventory)

Individual survivor counts are large (859 total); the inventory below groups
survivors by the function that contains them, which is the actionable unit for
closure. Per-mutant detail (line, operator, occurrence) is recoverable from each
sqlite session via the join on cosmic-ray's per-session `job_id` column
(`mutation_specs.job_id = work_results.job_id`) filtered on
`work_results.test_outcome = 'SURVIVED'`. Note that `job_id` is regenerated on
every `cosmic-ray init` and is therefore NOT a durable identifier; the stable
key used by the skip-files is `operator|location|occurrence`, built from
`operator_name`, `start_pos_row`, and `occurrence` (see the query below).

### `mutative_verbs.py` — 325 survivors

| Survivors | Function |
|----------:|----------|
| 146 | `detect_mutative_command` |
| 63  | `_scan_dangerous_flags` |
| 36  | `_layer3_length_check` |
| 20  | `<module-level>` |
| 19  | `_mkdir_targets_sensitive_path` |
| 10  | `_find_first_non_flag` |
| 6   | `_check_script_file` |
| 5   | `_check_inline_code` |
| 4   | `_classify_script_content_by_regex` |
| 3   | `_resolve_script_argument` |
| 3   | `split_camel_case` |
| 3   | `_extract_python_payload` |
| 3   | `_is_subcommand_identifier` |
| 2   | `_read_script_content` |
| 1   | `build_t3_block_response` |
| 1   | `_extract_embedded_shell_commands` |

### `blocked_commands.py` — 58 survivors

| Survivors | Function |
|----------:|----------|
| 38 | `_has_unquoted_separator` |
| 9  | `matches` |
| 4  | `_is_false_positive_carrier` |
| 4  | `is_blocked_command` |
| 1  | `<module-level>` |
| 1  | `SemanticBlockedRule` |
| 1  | `_read_only_base_cmds` |

### `approval_grants.py` — 476 survivors (+2 incompetent)

| Survivors | Function |
|----------:|----------|
| 120 | `activate_db_pending_by_prefix` |
| 58  | `create_command_set_grant` |
| 54  | `match_command_set_grant` |
| 29  | `find_pending_for_file` |
| 27  | `_get_grants_dir` |
| 27  | `confirm_grant` |
| 26  | `_db_row_to_pending_dict` |
| 19  | `_is_ttl_expired` |
| 18  | `consume_session_grants` |
| 16  | `check_approval_grant` |
| 14  | `find_pending_for_command` |
| 11  | `<module-level>` |
| 10  | `load_pending_by_nonce_prefix` |
| 8   | `write_pending_approval_for_file` |
| 8   | `consume_grant` |
| 7   | `cleanup_expired_grants` |
| 6   | `get_pending_approvals_for_session` |
| 5   | `check_approval_grant_for_file` |
| 4   | `_run_git_query` |
| 3   | `ApprovalGrant` |
| 3   | `_grant_ttl_minutes` |
| 1   | `is_valid` |
| 1   | `get_signature` |
| 1   | `capture_environment_snapshot` |

**INCOMPETENT (2, not survivors, not killed):**

| Function | Line | Operator |
|----------|-----:|----------|
| `_db_row_to_pending_dict` | 732 | `core/ExceptionReplacer` |
| `find_pending_for_file`   | 1027 | `core/ExceptionReplacer` |

---

## Reproduction (from `gaia/` root)

The three sessions were already initialized. To re-run from scratch:

```bash
# Re-init each session (required after any config change):
uv run cosmic-ray init tests/evals/mutation-mutative-verbs.toml mutative-verbs.sqlite
uv run cosmic-ray init tests/evals/mutation-blocked-commands.toml blocked-commands.sqlite
uv run cosmic-ray init tests/evals/mutation-approval-grants.toml approval-grants.sqlite

# Execute (T3 — requires approval):
uv run cosmic-ray exec tests/evals/mutation-mutative-verbs.toml mutative-verbs.sqlite
uv run cosmic-ray exec tests/evals/mutation-blocked-commands.toml blocked-commands.sqlite
uv run cosmic-ray exec tests/evals/mutation-approval-grants.toml approval-grants.sqlite

# Score (prints SURVIVAL rate; mutation score = 100 - this):
uv run cr-rate mutative-verbs.sqlite
uv run cr-rate blocked-commands.sqlite
uv run cr-rate approval-grants.sqlite
```

Per-mutant survivor query for any session. The join uses cosmic-ray's
per-session `job_id` column, but the durable identity for cross-run tracking is
the stable key `operator_name|start_pos_row|occurrence` (the three selected
columns, in that order):

```sql
SELECT ms.start_pos_row, ms.operator_name, ms.occurrence, ms.definition_name
FROM mutation_specs ms
JOIN work_results wr ON wr.job_id = ms.job_id  -- job_id: per-session only, not durable
WHERE wr.test_outcome = 'SURVIVED'
ORDER BY ms.start_pos_row;
```

---

## FOLLOW-UP — Survivor closure (deferred, NOT done in this pass)

This pass is an **inventory only**. No tests were written and no survivors were
closed. Closure is the next unit of work and should follow the method validated
in `AC-3-mutation-baseline.md`:

1. Inspect the surviving mutant (from the session's `work_results.diff`).
2. Identify the code path that lets it survive (what masks it through the
   public API).
3. Write an honest test that reaches that path and asserts the non-mutated
   outcome.
4. Re-run cosmic-ray on the same mutant: it must now show `killed`.
5. Mark genuinely-equivalent mutants explicitly rather than faking a test.

**Priority order suggested by the inventory:**

1. **`approval_grants.py` (kill 26.80%)** — the steepest gap, and the most
   security-load-bearing module (it is the consent layer). Before writing
   tests, broaden the test-command per the config's inline NOTE — the run
   already includes `test_approval_cycle.py` and `test_batch_approval.py`, yet
   the kill rate stays low, so the gap is genuine test coverage, not config.
   Hotspots: `activate_db_pending_by_prefix` (120), `create_command_set_grant`
   (58), `match_command_set_grant` (54). Resolve the 2 INCOMPETENT
   `ExceptionReplacer` mutants (lines 732, 1027) separately — confirm whether
   they are equivalent or a real gap.
2. **`mutative_verbs.py` (kill 55.78%)** — hotspot `detect_mutative_command`
   (146 survivors) is the central dispatch; `_scan_dangerous_flags` (63) and
   `_layer3_length_check` (36) follow.
3. **`blocked_commands.py` (kill 63.06%)** — smallest gap; `_has_unquoted_separator`
   (38) dominates, the rest are low single digits.

**Equivalent-mutant accounting:** the aggregate score is computed against ALL
mutants. Some survivors will be genuinely equivalent (unkillable through any
reachable input) and must be documented and excluded from the score, exactly as
the baseline did for `tiers.py`. Until that triage happens, treat the kill
percentages above as a raw floor, not the final score.
