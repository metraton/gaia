# AC-3 Mutation Baseline — tiers.py (security core)

**Date:** 2026-06-26
**Brief:** fundamento-de-tests (AC-3: mutation testing on security-critical modules)
**Outcome:** Survivors closed for `tiers.py`; full-core inventory deferred (see FOLLOW-UP).

---

## Baseline score — tiers.py spike

| Metric | Value |
|--------|-------|
| Score | **62.1%** (54/87 mutants killed) |
| Survivors (raw) | 33 mutants |
| Survivors (critical, closed this AC) | 4 |
| Equivalent mutants (documented) | 3 |
| Tool | cosmic-ray 8.x with `local` distributor |
| Config | `tests/evals/mutation-security-core.toml` |
| Scope | `hooks/modules/security/tiers.py` only (spike) |

The remaining survivors (~26) were not closed in this AC because the full-core
serial run with the complete test suite is impracticable at ~1 mutant/minute
(see FOLLOW-UP).

---

## Survivors closed (4 critical mutants)

Each survivor was verified by the method: read the surviving mutant → write an
honest test that kills it → re-run cosmic-ray on that mutant to confirm death.
Tests land in `tests/hooks/modules/security/test_tiers.py`,
class `TestMutationBaselineSurvivors`.

### 1. `tiers.py:89` — `ReplaceOrWithAnd` (empty/whitespace guard)

- **Mutant:** `not command or not command.strip()` → `not command and not command.strip()`
- **Why it survived:** The public API (`classify_command_tier`) strips before
  calling the cached function, so the only path that distinguishes `or` from
  `and` requires calling `_classify_command_tier_cached` directly with a
  whitespace-only string.
- **Test:** `test_cached_classifier_whitespace_is_t3` — calls
  `_classify_command_tier_cached("   ")` and asserts `T3_BLOCKED`.
- **Companion:** `test_cached_classifier_empty_is_t3` — empty string also T3.

### 2. `tiers.py:132` — `ReplaceComparisonOperator_Eq_*` (simulation category check)

- **Mutant:** `result.category == CATEGORY_SIMULATION` → `!=` / `<` / `>` / etc.
- **Why it survived:** Commands with SIMULATION category that also matched the
  T2 regex (e.g. `terraform plan`) were re-classified T2 by the regex before
  reaching line 132, masking the mutant. The test uses commands like
  `pulumi preview` and `tool render output` that carry SIMULATION category but
  bypass the regex.
- **Test:** `test_simulation_category_command_is_t2` (parametrized: 3 commands).
- **Note:** `Eq_Is` variant (:132) is equivalent — CPython interns the short
  category string so `is` behaves identically to `==`; documented as equivalent,
  not faked.

### 3. `tiers.py:113` — `ZeroIterationForLoop` (T2-patterns loop)

- **Mutant:** T2_PATTERNS loop body never executes.
- **Why it survived:** Existing tests used commands with SIMULATION category
  that would still be T2 via line 132 even if the loop were empty.
- **Test:** `test_t2_keyword_loop_is_exercised` — uses `wc -l plan.txt`, which
  is T2 only via the `\bplan\b` regex (its verb category is READ_ONLY, so an
  empty loop drops it to T0).

### 4. `tiers.py:190` — `ZeroIterationForLoop` (blocked-patterns loop)

- **Mutant:** `blocked_patterns` loop in `classify_command_tier` never executes.
- **Why it survived:** Existing tests used commands that were also caught by
  `mutative_verbs`, so `is_mutative` remained True even with the blocked loop
  empty — the T3 result was preserved through a different path.
- **Test:** `test_blocked_pattern_loop_is_exercised` — uses `mkfs.ext4 /dev/sda1`,
  a blocked-only command (not a mutative verb); without the loop it would fall
  to T0.

---

## Equivalent mutants (3, documented — not faked)

These mutants cannot be killed by any reachable input through the public API.
They are documented in `tests/evals/mutation-security-core.toml` and must not
be counted against the mutation score.

| Location | Mutant | Reason equivalent |
|----------|--------|-------------------|
| `tiers.py:178` | `ReplaceOrWithAnd` | Outer empty/whitespace guard in `classify_command_tier`; the cached guard at :89 (now covered) masks it through the public API — any whitespace-only input is stripped before reaching :178. |
| `tiers.py:132` | `Eq_Is` | CPython interns short category strings; `is` and `==` are observationally identical for these string constants. |
| `tiers.py:134` | `Eq_*` (all variants) | Both the matched branch and the safe-by-elimination default return T0; no comparison mutation is observable. |

---

## Method validated

1. Inspect the surviving mutant (from cosmic-ray's SQL result or diff).
2. Identify the code path that lets it survive: what precondition is required,
   what masks it through the public API.
3. Write a test that reaches the code path with the required precondition and
   asserts the correct (non-mutated) outcome.
4. Re-run cosmic-ray on the same mutant: it must now show `killed`.
5. Mark equivalent mutants explicitly rather than faking a test.

---

---

## Full-core batch — preliminary results (2026-06-26)

The three full-core modules were run using per-module toml configs with narrowed
test commands, making per-mutant time practical (~2-5 s/mutant vs ~60 s serial).

### Completeness state at snapshot time

| Module DB | Total mutants | Completed | Pending | Status |
|-----------|--------------|-----------|---------|--------|
| `mutative-verbs.sqlite` | 735 | 735 | 0 | **COMPLETE** |
| `blocked-commands.sqlite` | 157 | 157 | 0 | **COMPLETE** |
| `approval-grants.sqlite` | 653 | 513 | 140 | **PARTIAL — run still active (PID 429547)** |

### Scores (cr-rate = survival rate; mutation score = 100 - survival rate)

| Module | Survival rate (`cr-rate`) | Mutation score | Killed | Survived |
|--------|--------------------------|---------------|--------|----------|
| `mutative_verbs.py` | 44.22% | **55.78%** | 410/735 | 325/735 |
| `blocked_commands.py` | 36.94% | **63.06%** | 99/157 | 58/157 |
| `approval_grants.py` | 73.33% (partial) | ~26.67% (partial) | 135/513 | 376/513 |

Note: `approval_grants` had 2 INCOMPETENT mutants (worker EXCEPTION outcome);
those are excluded from both the survival rate and mutation score denominators.
The partial score for `approval_grants` is calculated over the 511 competent
completed mutants; it will shift as the remaining 140 mutants resolve.

### Survivor inventory — mutative_verbs.py (COMPLETE, 325 survivors)

Key surviving locations grouped by function:

**No-op / equivalent survivors (NumberReplacer, ExceptionReplacer):**
- Lines 34, 46, 55: `ExceptionReplacer` on import/constant blocks — equivalent, no reachable path distinguishes exceptions at module level.
- Lines 428:25, 428:31: `NumberReplacer` — constant substitutions that do not change test-observable behavior.
- Lines 461:20, 523:25, 524:27: `NumberReplacer` in `_extract_embedded_shell_commands` — length constants; tests do not exercise boundary values precisely.

**Logic gaps in `_mkdir_targets_sensitive_path`:**
- Line 657:23 `ReplaceFalseWithTrue` — default guard; needs a test calling the function when no sensitive path is involved.
- Lines 659:12, 663:17: comparison operator variants — loop boundary conditions not precisely tested.
- Lines 665:12, 677:12: `ReplaceContinueWithBreak` — early-exit vs full-scan semantics untested.
- Lines 667:11, 675:34, 675:43: `AddNot` / `ReplaceOrWithAnd` — compound conditions in inner loop.

**Logic gaps in `_scan_dangerous_flags`:**
- Lines 877-912: multiple `ReplaceComparisonOperator_Eq_*` and `ReplaceAndWithOr` survivors — the flag scanning loop's boundary conditions (length comparisons, multi-token flag matching) have weak assertions.

**Logic gaps in `detect_mutative_command`:**
- Lines 1013, 1193, 1238-1240: `ReplaceOrWithAnd` — conditional branches in the main detection path lack tests that distinguish `or` from `and` behavior.
- Lines 1025, 1156, 1211, 1520, 1530: `ReplaceFalseWithTrue` — return-false branches never exercised.
- Lines 1048, 1069, 1102, 1119: `ReplaceComparisonOperator_NotEq_*` — equality checks on verb category constants; many operator variants survive because tests do not assert on near-miss commands.

**Logic gaps in `_layer3_length_check`:**
- Lines 1986-1992: `ZeroIterationForLoop`, `ReplaceComparisonOperator_*`, arithmetic operator variants — the Layer 3 pipeline length analysis has broad operator survival.

**Other functions with survivors:**
- `split_camel_case` (lines 932): boundary comparisons weak.
- `_is_subcommand_identifier` (lines 983-986): ReplaceFalseWithTrue survivors.
- `_extract_python_payload` (lines 1659-1661): guard condition and index constants.
- `_read_script_content` (lines 1729): ExceptionReplacer — exception handling paths untested.
- `_check_script_file` (lines 1770-1784): comparison and logic operators.
- `_classify_script_content_by_regex` (lines 1821-1828): guard and return-value mutations.
- `_check_inline_code` (lines 1892-1910): guards and loop iteration.

**Full line-level survivor list:** see `tool-results/b55872def.txt` (persisted output) for all 325 entries.

### Survivor inventory — blocked_commands.py (COMPLETE, 58 survivors)

Key surviving locations:

**No-op / equivalent:**
- Line 74:11 `ExceptionReplacer` on `_read_only_base_cmds` — exception path unreachable in tests.
- Lines 703:8 (x2): `NumberReplacer` in `_has_unquoted_separator` — index constants.

**Logic gaps in `SemanticBlockedRule` / `matches`:**
- Line 87:18 `ReplaceTrueWithFalse` — boolean default value in dataclass field.
- Line 96:22 `ReplaceTrueWithFalse` on `SemanticBlockedRule.matches` default.
- Line 99:51 `AddNot` — guard negation in `matches`.
- Lines 111:23, 111:34: `AddNot` and `ReplaceComparisonOperator_Eq_*` — the comparison against 0 at the conclusion of the match chain; 6 operator variants survive because tests do not probe near-miss lengths.

**Logic gaps in `is_blocked_command`:**
- Line 596:19 `ReplaceOrWithAnd` — short-circuit join of blocking conditions.
- Lines 622:50, 623:23, 625:24: loop body iteration control — ZeroIterationForLoop, AddNot on guard, ReplaceBreakWithContinue.

**Logic gaps in `_is_false_positive_carrier`:**
- Lines 685:16 (x3), 685:25: comparison operator mutations and `ReplaceAndWithOr` — boolean composition in false-positive carrier detection.

**Logic gaps in `_has_unquoted_separator`:**
- Lines 705-718: extensive comparison operator and arithmetic operator survivors — the quote-state scanner has many index arithmetic mutations that survive because tests use only simple unquoted inputs without exercising the boundary counting precisely.

### approval-grants.sqlite — PARTIAL (513/653, run active)

Status at snapshot: **still advancing** (confirmed — count grew from 488 to 513 in ~15 minutes while this evidence was being collected; cosmic-ray PID 429547 is live).

Partial score: **~26.67% killed** (135/511 competent mutants killed). This is a preliminary figure; 140 mutants remain untested. The final score will be lower-bounded by the partial result and could shift significantly as the high-density untested region resolves.

If the run completes naturally, re-run `uv run cr-rate approval-grants.sqlite` from `gaia/` to get the final survival rate. No new `cosmic-ray exec` is needed — the run is already in progress.

If the run dies before completing the remaining 140 mutants, the command to continue (requiring a new T3 approval) would be:

```bash
# From gaia/ — do NOT re-init (that would wipe partial results)
# T3 — requires approval
uv run cosmic-ray exec tests/evals/mutation-approval-grants.toml approval-grants.sqlite
```

---

## FOLLOW-UP — Full-core inventory (deferred)

**Modules not yet covered by mutation testing:**

- `hooks/modules/security/mutative_verbs.py`
- `hooks/modules/security/blocked_commands.py`
- `hooks/modules/security/approval_grants.py`

**Why deferred:** The full-core serial run with the complete test suite
(`python3 -m pytest tests/hooks/modules/security/ -q -x --no-header`) processes
approximately 1 mutant/minute on the current machine. The three remaining modules
have an estimated combined mutant count of ~1,600+, making a serial run ~27 hours
— impracticable for an interactive session.

**How to make it practicable:**

Option A — Narrow test-command per module (fast):
```toml
# In mutation-security-core.toml, create per-module config files that scope
# test-command to only the relevant test module:
test-command = "python3 -m pytest tests/hooks/modules/security/test_mutative_verbs.py -q -x --no-header"
```
Each module's config runs only the tests that exercise it, reducing per-mutant
time from ~60s to ~2-5s.

Option B — `http` distributor with N parallel workers:
```toml
[cosmic-ray.distributor]
name = "http"

[cosmic-ray.distributor.http]
worker-urls = [
    "http://localhost:9876",
    "http://localhost:9877",
    "http://localhost:9878",
    "http://localhost:9879",
]
```
Start workers with:
```bash
uv run cosmic-ray http-worker tests/evals/mutation-security-core.toml security-core.sqlite --port 9876 &
# ... repeat for each worker
uv run cosmic-ray exec tests/evals/mutation-security-core.toml security-core.sqlite
```
4 workers × Option A test-command scope → estimated ~20-30 minutes for the full
remaining inventory.

**Reproduction command (from `gaia/` root):**
```bash
# Re-init (required after any config change):
uv run cosmic-ray init tests/evals/mutation-security-core.toml security-core.sqlite
# Execute (T3 — requires approval):
uv run cosmic-ray exec tests/evals/mutation-security-core.toml security-core.sqlite
# Score:
uv run cr-rate security-core.sqlite
```

Config base: `tests/evals/mutation-security-core.toml` (tracked, see inline
comments for the equivalent-mutant documentation and distributor migration note).
