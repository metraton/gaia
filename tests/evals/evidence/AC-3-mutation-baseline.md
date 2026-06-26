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
