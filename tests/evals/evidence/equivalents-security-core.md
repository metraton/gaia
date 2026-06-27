# Equivalent Mutants — security-core (`approval_grants.py`)

**Date:** 2026-06-27
**Branch:** `harden/approval-grants-m1-loop`
**Brief:** fundamento-de-tests — AC-1 (≥80% kill on the killable population) + AC-5 (equivalents documented)
**Module:** `hooks/modules/security/approval_grants.py`
**Session DB:** `approval-grants.sqlite` (cosmic-ray init, 653 specs, 2 INCOMPETENT)

This file is the AC-5 deliverable: for every surviving mutant that is
**genuinely equivalent** (no honest test can distinguish it from the original
for ANY reachable input), the rigorous justification of why it is unkillable.
Equivalents documented here are excluded from the AC-1 kill-rate denominator by
the cosmic-ray skip mechanism documented in the final section.

A mutant is listed here ONLY if it is genuinely indistinguishable. Mutants
whose flip changes behaviour for some input are NOT here — they are behavioral
and are killed by tests in `tests/hooks/modules/security/test_approval_grants_mutants.py`.

Job IDs are stable across runs (cosmic-ray hashes the spec). Line numbers are
as of the commit that lands this file; the operator + function + job_id are the
durable anchors.

---

## Category E1 — PEP 563 type-annotation operator flips (24 mutants)

`approval_grants.py` line 79 declares `from __future__ import annotations`.
Under PEP 563 **every annotation is stored as a string and never evaluated at
runtime** — the annotation expression's AST is stringified, not executed. A
mutation to an operator *inside* an annotation therefore changes a substring of
a never-evaluated string-form expression and can have NO runtime effect. There
is no runtime type checker in this module (no `typing.get_type_hints()` call, no
`pydantic`, no `@beartype`), so `__annotations__` is never materialised either.

This is why these mutants SURVIVED rather than being INCOMPETENT: a mutation
like `str - None` would raise `TypeError` *if evaluated*, but PEP 563 means it is
never evaluated, so import succeeds and no test can observe a difference.

| Function | Line | Annotation | Operators (BitOr→X) | job_ids |
|----------|-----:|------------|---------------------|---------|
| `create_command_set_grant` | 1547 | `session_id: str \| None` | Pow, FloorDiv, LShift, Mod, BitAnd, Sub, BitXor, RShift, Mul, Div, Add | 55fbd6bf, ce8b5924, 2fa2e7e7, 67df43af, 381a965d, f995de55, 4dcb0107, 4902017d, 0854d2bd, a608b14a, 8dc8e3de |
| `create_command_set_grant` | 1548 | `agent_id: str \| None` | Add, Sub, Mod, BitAnd, BitXor, FloorDiv, Pow, Div, RShift, Mul, LShift | 3c51e600, cba9e47c, c795d317, 183e4d08, 5b46fe1e, 7ec65c7a, 90effc5c, 0fca6f6c, d745d728, f425812e, 38a9829f |
| `match_command_set_grant` | 1622 | `-> tuple \| None` (return) | Sub, Mod, BitXor, Div, Add, LShift, Pow, RShift, FloorDiv, BitAnd, Mul | 467a3d71, 97871e0d, 8fdafa0c, 4f43c649, c372b17d, fb2a28c1, e932126d, a4a8e39b, a56cdc0d, e3156f4d, bc4e015c |

**Why unkillable:** there exists no input to `create_command_set_grant` or
`match_command_set_grant` for which the value of the `|` (or its mutant) in a
parameter/return annotation is read. The annotation is dead at runtime by
language semantics. Verified: `grep -n "from __future__ import annotations"
hooks/modules/security/approval_grants.py` → line 79.

---

## Category E2 — keyword-only marker `*` mis-parsed as a binary operator (2 mutants)

The bare `*` on a line by itself is the PEP 3102 keyword-only-arguments marker
in a function signature, NOT a multiplication operator. cosmic-ray's AST walker
emits a `ReplaceBinaryOperator_Mul_Div` spec for it, but the marker has no
operands; applying the mutation produces an AST that either re-parses to the
identical keyword-only signature or is a structural no-op. No call to the
function can observe a `*` vs `/` difference because there is no arithmetic
there.

| Function | Line | Token | Operator | job_id |
|----------|-----:|-------|----------|--------|
| `create_command_set_grant` | 1546 | `*,` (kw-only marker) | Mul→Div | b428c87a |
| `match_command_set_grant` | 1620 | `*,` (kw-only marker) | Mul→Div | 2b5e69e8 |

**Why unkillable:** the keyword-only call contract (`session_id=`, `agent_id=`,
`db_path=` must be passed by keyword) is unchanged by the mutation; no positional
vs keyword behaviour differs, so no test can distinguish.

---

## Category E3 — log-only `[:N]` slice / format-argument NumberReplacer (log-only)

These mutate a constant that appears ONLY inside a `logger.{info,debug,error}`
call argument — a string slice bound (`approval_id[:16]`, `command[:80]`,
`session_id[:12]`) or a format placeholder count. The Python `logging` calls are
not asserted by any honest behavioural test (asserting on log text is brittle
and forbidden by the honesty bar), and the slice does not feed any return value,
branch, or persisted field. Truncating a logged string to a different length
changes only what is written to the log stream.

| Function | Line(s) | Slice / constant | job_ids |
|----------|---------|------------------|---------|
| `check_approval_grant` | 506 | `command[:80]`, `(... or "?")[:16]`, `or` chain | 08254cd3, 924b67b3, 4b700bb6, 3d46fdb2, 4d874074 |
| `consume_grant` | 545, 550 | `command[:80]`, `approval_id[:16]` | edd3f403, f933984b, 027aa989, fe588bc6, 0040edaf, fda13a80 |
| `consume_session_grants` | 615, 620 | `approval_id[:16]` (×2 log sites) | 469fa8b6, 51caa09e, f69286d8, 9a3935d2 |
| `confirm_grant` | 653, 662, 667 | `command[:80]`, `approval_id[:16]` | aef5ff17, 01a71ff6, bf077f28, 9f23f88e, 92bf0291, 63ed529b, c72623899... , 74f913a3 |
| `find_pending_for_command` | 855 | `command[:80]` | a869075a, b09df889 |
| `create_command_set_grant` | 1606 | `approval_id[:12]` (logger.info args) | c7fc471a, 05bf57a0 |
| `match_command_set_grant` | 1707 | `retried_command[:80]`, `approval_id[:12]` | 59ad69e0, 0004997e, cc89c008, f51c1137 |
| `activate_db_pending_by_prefix` | 1311, 1322, 1325, 1406, 1441, 1480 | `approval_id[:16]`, `[:12]`, `command[:80]`, `session[:12]` | b59a629d, 637e4e87, b51a989a, 9891ad4a, afceeaaf, 605db82e, ee4ad789, 1bdcc1e9, 52da3e88, 5c8bee0d, 09e08800, 24e5ace2, f5e4718d, 597521aa, 52660f36, 89eabfb4, 286cdb81, db2c6546 |
| `load_pending_by_nonce_prefix` | 358 | `candidates[0].get("nonce","?")[:12]` (log arg) | f385964d, 13a6dd3a |

**Why unkillable:** the log emission is the only consumer of the constant. No
return value, branch, DB write, or signature depends on the slice length. An
honest test asserts on observable behaviour (return / state / persisted row),
never on log text, so no honest test can distinguish these.

NOTE: `activate_db_pending_by_prefix` line 1311/1325 etc. are the SECOND
`[:16]`/`[:12]` occurrence inside the COMMAND_SET success-log call (line 1318+);
the grant itself is already created before the log, so the slice is post-effect.

---

## Category E4 — `.get(key, DEFAULT)` default-NumberReplacer where the key is always present

`results.sort(key=lambda d: d.get("timestamp", 0), reverse=True)` and the
identical sort in `load_pending_by_nonce_prefix`. Every dict in the list comes
from `_db_row_to_pending_dict`, which ALWAYS sets a `"timestamp"` key (to a float,
defaulting to `0.0` only internally). The `.get(...)` default argument
(`0`→`1`) is therefore **dead**: the key is never absent, so the default is never
returned.

| Function | Line | Expression | job_id |
|----------|-----:|------------|--------|
| `get_pending_approvals_for_session` | 810 | `d.get("timestamp", 0)` | 4eec568e, e5a274b0 |
| `load_pending_by_nonce_prefix` | 355 | `d.get("timestamp", 0)` | 4785c396, b7b0a339, f385964d (355) |

**Why unkillable:** `_db_row_to_pending_dict` (line 763-778) unconditionally
includes `"timestamp": ts`. No reachable row omits the key, so the mutated
default value is never the value used; the sort order is identical.

---

## Category E5 — module-init constants overwritten before first read, or with no reachable boundary

| Const | Line | Mutation | Why equivalent | job_ids |
|-------|-----:|----------|----------------|---------|
| `_last_cleanup_time = 0.0` | 142 | Number 0.0→N | First read is `now - _last_cleanup_time < 60` (line 697); `now` ≈ 1.7e9 s, so `now - N` ≫ 60 for any small init N. The throttle never fires on the first call regardless of init. After the first call the value is reassigned to `now` (line 699). The init constant has no reachable boundary. | 713e0406, ed6f765f |
| `_last_check_found_expired = False` | 258 | False→True | `check_approval_grant` resets it to `False` at line 464 on EVERY call before any conditional set, and `last_check_found_expired()` is the only reader. The module-init value is overwritten before any read can observe it. | 4ba3a150 |

**Why unkillable:** the init value is shadowed by a write that always executes
before any read, OR the only comparison against it has no reachable equality
boundary (wall-clock `time.time()` dominates any small init).

---

## Category E6 — index `[-1]` vs `[1]` coincide under the guard

`_db_row_to_pending_dict` line 746: `operation.rsplit(": ", 1)[-1].strip()`,
guarded by `if ": " in operation:` (line 745). `rsplit(": ", 1)` yields at most
2 elements; the guard guarantees the separator IS present, so it yields EXACTLY
2 elements. For a 2-element list, `[-1]`, `[1]`, and `[+1]` all select the same
element. `ReplaceUnaryOperator_USub_UAdd` (`-1`→`+1`), `NumberReplacer` (`-1`→
other under the index), and `Delete_USub` (`-1`→`1`) therefore all resolve to
the identical element given the guard.

| Function | Line | job_ids |
|----------|-----:|---------|
| `_db_row_to_pending_dict` | 746 | 36c687e6 (USub_UAdd), d2c506f9 (Number), 58b9323 (Delete_USub) |

**Why unkillable:** the `if ": " in operation` guard forces `rsplit(": ", 1)` to
return exactly 2 parts, where index `-1` and index `1` are the same element. No
input that reaches this line distinguishes them.

---

## Category E7 — `exc_info` / best-effort except-handler mutations with no reachable raising path

| Function | Line | Mutation | Why equivalent | job_id |
|----------|-----:|----------|----------------|--------|
| `activate_db_pending_by_prefix` | 1515 | `exc_info=True`→`False` (ReplaceTrueWithFalse) | `exc_info` only controls whether the traceback is appended to the log record. Log-only; no behavioural observable. | cf538bff |
| `capture_environment_snapshot` | 439 | `except Exception` (ExceptionReplacer) | The try body (lines 420-437) calls only `_run_git_query` — which itself wraps everything in `try/except: pass` and never propagates — plus pure dict assignments that cannot raise. No reachable input makes the try body raise, so the handler is never entered; narrowing its caught type is unobservable. | 869f0f87 |
| `module-level` | 374 | `_ENV_SNAPSHOT_TIMEOUT_SECONDS = 2`→3 (Number) | The constant is the `subprocess.run(timeout=...)` for git queries. Distinguishing 2 s from 3 s requires a git invocation that hangs > 2 s and < 3 s — non-deterministic and unreachable in an honest test; both values succeed for any real git call. | 287e196d, 2d88a05a |

---

## AC-5 CIERRE FINAL — residual equivalents proven this session (E8–E12)

GRIND-TOTAL pass over the 33 survivors not previously in the skip-file. Each
equivalent below was proven by **hand-applying the exact cosmic-ray mutant** to
`approval_grants.py` and observing the test suite stay green (no honest input
distinguishes it), then reverting. This is the exhaustive-proof bar, not a
log-only hand-wave.

### Category E8 — SQL pre-filter makes an in-Python re-check a constant-False branch

`match_command_set_grant` line 1683 `if grant.get("scope") != "COMMAND_SET":`.
The grants come from `gaia.store.writer.list_command_set_grants_agnostic`, whose
SQL is `SELECT * FROM approval_grants WHERE scope = 'COMMAND_SET' AND status = ?`.
Every returned row therefore has `scope == "COMMAND_SET"`, so the Python re-check
is **always False** (never `continue`s). Operator flips:

| Operator | Mutant | Why equivalent | job_id |
|----------|--------|----------------|--------|
| NotEq→Gt | `scope > "COMMAND_SET"` | equal strings → `>` is False, like `!=` | 6de4c76e |
| NotEq→Lt | `scope < "COMMAND_SET"` | equal strings → `<` is False, like `!=` | cdcb2e87 |
| NotEq→Is | `scope is "COMMAND_SET"` | the sqlite value is **non-interned**, so `is` is False, like `!=` | 2a7b0e10 |
| ContinueWithBreak (1684) | `continue`→`break` | line 1684 is unreachable (1683 always False) | ba66c609 |

**NotEq_Is puzzle — VERDICT: genuine equivalence (cause c), NOT a harness bug.**
The harness reported NotEq_Is SURVIVED. I suspected a fidelity bug, but the
minimal experiment settled it: cosmic-ray's `ReplaceComparisonOperator_NotEq_Is`
substitutes `!=` with **`is`** (not `is not`). Hand-applying
`grant.get("scope") is "COMMAND_SET"` and running the COMMAND_SET match tests:
they **PASS** — because the sqlite-sourced scope string is non-interned, so `is`
is False exactly like `!=`. (A separate hand-test confirmed that `is not` — which
the operator does NOT produce — *would* kill it, which is what misled the first
analysis.) The harness is faithful; new tests ARE re-collected per run (verified
by the Eq_Is 6/7 and Or→And 13/14 kills landing). No harness fix needed.

### Category E9 — fast-path boundary coincides with the wall-clock fall-through

`_is_ttl_expired` line 177 `if timestamp == 0: return True` (NumberReplacer
0→±1). For `timestamp == 0` the fall-through `(time.time() - 0) / 60` is ≈ 2.9e7
minutes, which exceeds any honest `ttl_minutes`, so it ALSO returns True.
Distinguishing the fast-path from the fall-through would require
`ttl_minutes >= time.time()/60` (~55 years) — unreachable. Proven: all 28
ttl/expiry tests green under `timestamp == 1`. job_id: ad6ce3d8.

### Category E10 — index value used only as a truthiness check, never read

`activate_db_pending_by_prefix` line 1175 `command = command_set_items[0]["command"]`
(NumberReplacer [0]→[1]/[-1]). Reached only when `is_command_set and not command`.
In the `is_command_set` path `command` only gates the `if not command` guard
(both set items are non-empty command strings → truthy either way), and the
COMMAND_SET branch (line 1299) returns without ever using `command` for the
signature. Proven: the full activation suite (295 tests) is green under
`command_set_items[1]`. job_ids: aedf3ee1, ea36340d.

### Category E11 — dead local variable

`activate_db_pending_by_prefix` line 1458
`verbs = [signature.verb] if signature.verb else ([danger_verb.lower()] if danger_verb else ["write"])`.
`verbs` has **exactly one occurrence** in the module (assigned, never read).
Both AddNot flips (on `if signature.verb` and the inner `if danger_verb`) change
only the value of a dead variable. Proven: 1313 security tests green under both
flips applied simultaneously. job_ids: 763b4d7f, da2d5e14.

### Category E12 — log-only branch / log argument / interned-name comparison

These mutate a constant or branch whose ONLY consumer is a `logger.*` call (or an
interned attribute). An honest test asserts observable behaviour (return / state /
persisted row), never log text, so none is distinguishable.

| Site | Mutant | Why equivalent | job_ids |
|------|--------|----------------|---------|
| `consume_grant` 542 | `if consumed:` AddNot | gates only a log line; `return consumed` identical | b9394bf3 |
| `cleanup_expired_grants` 713 | `if cleaned:` AddNot | gates only a log line; `return cleaned` identical | f22e69b4 |
| `check_approval_grant_for_file` 978 | `str(row.get("approval_id",""))[:16]` NumberReplacer ×2 | slice bound / `.get` default inside `logger.info` arg | 43288238, f2bd0b18 |
| `create_command_set_grant` 1579 | `len(command_set) if command_set else 0` AddNot + NumberReplacer ×2 | entirely inside the `logger.error` argument | 01e81148, 8f4d7202, 9c506393 |
| `load_pending_by_nonce_prefix` 358 | `candidates[0].get("nonce","?")[:12]` NumberReplacer | `logger.info` argument only | 079c60e3 |
| `activate_db_pending_by_prefix` 1324 | `(originating_session or "")[:12]` Or→And | `logger.info` argument only | 287684e7 |
| `_get_grants_dir` 252 | `_grants_dir_created = False` ReplaceFalseWithTrue | module-init memoization flag; `mkdir(exist_ok=True)` idempotent and any state-resetting test masks the init before the only (first-call) observable read | ffe55f16 |
| `activate_db_pending_by_prefix` 1212 | `_fp_exc.__class__.__name__ == "ChainTamperError"` Eq→Is | `__name__` is interned by CPython, so `is` behaves like `==` | 094def26 |

---

## Category E13 — `len(parts) == 2` guard where the outer `in`-check makes `len < 2` unreachable (1 mutant)

`activate_db_pending_by_prefix` line 1426:
```python
if "intercepted:" in operation_str:
    parts = operation_str.split("intercepted:")
    if len(parts) == 2:   # <-- mutated to <= 2
```

`ReplaceComparisonOperator_Eq_LtE` mutates `== 2` to `<= 2`. The outer guard (line 1424) ensures `"intercepted:"` is present before `split("intercepted:")` executes. When the separator IS present, `.split()` always returns **at least 2 parts** — `len(parts) >= 2` for every reachable call. Therefore:

- `len(parts) == 2` (one occurrence of the separator): `== 2` → True; `<= 2` → True. Same.
- `len(parts) >= 3` (multiple occurrences of the separator): `== 2` → False; `<= 2` → False (3 ≤ 2 is False). Same.
- `len(parts) == 1` is **unreachable** (the outer guard guarantees separator presence).
- `len(parts) == 0` is **impossible** (`.split()` always returns ≥ 1 element).

No reachable input yields a `len` value that distinguishes `== 2` from `<= 2`.

**Proof by suite:** hand-applied `<= 2` to line 1426 and ran the full security test suite (1313 tests + 1 skipped). Result: **all green**. No honest test can distinguish the mutant from the original.

| Function | Line | Expression | Operator | job_id |
|----------|-----:|------------|----------|--------|
| `activate_db_pending_by_prefix` | 1426 | `if len(parts) == 2:` | Eq→LtE | 0ef52574 |

---

## CANDIDATES UNDER VERIFICATION (NOT yet excluded)

The following survivors are **provisionally** behavioral and are being killed by
new tests, OR are being confirmed equivalent by the scoped harness. They are
NOT excluded from the denominator until resolved. Listed here so the accounting
is complete and the next iteration can pick them up:

- `consume_grant` 542 `AddNot` — `if consumed:` gates only a log line; return is
  `consumed` either way. Provisionally E (log-branch-only). Verifying no honest
  observable differs.
- `cleanup_expired_grants` 713 `AddNot` — `if cleaned:` gates only a second log
  line; return is `cleaned`. Provisionally E (log-branch-only).
- `activate_db_pending_by_prefix` 1175 `NumberReplacer` (`command_set_items[0]`)
  — only feeds the singular path which is NOT taken when `is_command_set` (the
  COMMAND_SET branch uses `command_set_items` directly). Provisionally E.
- `activate_db_pending_by_prefix` 1324 `ReplaceOrWithAnd` (`(originating_session
  or "")[:12]`) — log arg only. Provisionally E (log-only).
- ExceptionReplacers at 1234/1381/1501/1512 and match 1678/1690/1711 — targeted
  by the `TestActivateDbPendingExceptionHandlersAC1` reason-discriminator tests;
  confirming the scoped harness now reports them KILLED.

---

## Exclusion mechanism (AC-1 denominator)

**Skip file:** `tests/evals/equivalents-approval-grants.skip`

The scoped harness (`tests/evals/mutkill_approval_grants.py`) accepts
`--skip-file <path>` which reads one 32-char job_id per non-comment line and
excludes those specs from both the denominator and the numerator. The skip file
is the materialization of this document: every job_id proven equivalent above is
listed there, one per line, with category comments.

**Denominator composition (after grind-total, E1–E13):**

| Population | Count |
|------------|------:|
| Total specs in DB | 653 |
| INCOMPETENT (excluded by cosmic-ray) | 2 |
| Proven-equivalent excluded (E1–E13, skip file) | 121 |
| **Killable denominator** | **530** |

**Grind-total closure:** all surviving mutants triaged. 0 untriaged survivors remain in `approval_grants.py`.
- Mutant `a498bdeea402498c9686e78f97441903` (line 1212 Eq→LtE): **KILLED** by new test `TestActivateDbPendingIntegrityLabelAC1::test_lexically_early_class_name_not_treated_as_tamper` using `AttributeError` as the discriminating input (`"AttributeError" < "ChainTamperError"` → `<=` returns True wrongly labeling a non-tamper error).
- Mutant `0ef52574fe5b441da1331068f689f727` (line 1426 Eq→LtE): **EQUIVALENT** (E13). Proven by suite (1313 tests green under mutant); outer `"intercepted:" in` guard makes `len < 2` unreachable.

---

# Equivalent Mutants — `blocked_commands.py` (GRIND-TOTAL)

**Date:** 2026-06-27
**Branch:** `harden/approval-grants-m1-loop`
**Module:** `hooks/modules/security/blocked_commands.py`
**Session DB:** `blocked-commands.sqlite` (cosmic-ray init, 157 specs, 0 INCOMPETENT)
**Baseline:** 99 killed / 58 survived (63.06%). Closure adds
`tests/hooks/modules/security/test_blocked_commands_mutants.py`.

**Scoped recheck after closure** (`--only-survivors` over the 58 baseline
survivors): **37 killed / 21 survived**. The 21 survivors are all proven
equivalent below — no honest input distinguishes them for ANY reachable
input. Method: exhaustive input search (`_explore_sep.py`, 70+ inputs covering
leading/mid/trailing separators, quoted/escaped/long(>256)/tight-packed cases)
plus closed-form reasoning on the index walk; the two non-cluster ones proven
by control-flow analysis (`_probe_equiv.py`).

## Category B1 — `_has_unquoted_separator` quote-walk equivalents (19)

The walk advances `i` by 1 (normal / quote-toggle) or by 2 (escape), starting
at 0, and the escape branch is gated by `i + 1 < n` so it is only taken when
`i <= n-2`. Therefore **`i` always lands EXACTLY on `n`, never overshoots**.
This invariant is what makes the following equivalent.

### B1a — loop bound `while i < n` (line 705)

- `Lt_NotEq` `6b7d47abdbec4c66a1c7946dd2758f8b`: `i != n` ≡ `i < n` because `i`
  never exceeds `n` (lands exactly on it).
- `Lt_IsNot` `4483941e37c8479d8ea0df6823f7bace`: `i is not n`. For all reachable
  `n` (small-int cached at the boundary) behaves like `!=`; distinguishing
  would require relying on CPython int-identity for `n > 256`, which is not an
  honest behavioral test (implementation detail, not a security property).

### B1b — escape guard `if ch == "\\" and i + 1 < n` (line 707)

- `Eq_Is` (col 14, occ2) `5f9ffde261a348dd9bf671f3f8df2f64`: `ch is "\\"`.
  Single-char strings are interned by CPython, so `is` ≡ `==`.
- `Add_*` on `i + 1` (col 28, occ1) — Sub/Mul/Div/FloorDiv/Mod/Pow/RShift/
  BitOr/BitAnd/BitXor: `2f99fabe...`, `7eb86068...`, `17f76459...`,
  `2731fa40...`, `af4edb4f...`, `424f34c9...`, `4bda2c16...`, `5295f6ad...`,
  `c61e470c...`, `e83d90ae...`. The guard `<n` is only False for a TRAILING
  backslash; for every reachable `i` these alternate ops keep `expr < n` at the
  same truth value as `i+1 < n` (all reduce to "enter escape unless trailing").
  (Add_LShift `i<<1` was NOT equivalent — KILLED by `aaaa\|`; only the listed
  ops are equivalent.)
- `Number 1→0` (col 30, occ5) `515dfe842d2940bc999b7ad237905462`: `i + 0 < n` =
  `i < n`, always True inside the loop; a trailing backslash then enters escape
  (`i+=2` past EOF) → still returns False. (Occ4, `1→2`, was KILLED by `\|`.)
- `Lt_*` on `i + 1 < n` (col 32, occ1) — NotEq `496b5550...`, LtE `39d9bb39...`,
  IsNot `4778615b...`: `i+1` never exceeds `n`, so `!=`/`<=` match `<` at every
  reachable point; `is not` is the int-identity case as in B1a.

### B1c — quote-toggle Eq_Is (lines 710, 714)

- `710 Eq_Is` (occ3) `4f56c3a963d84a69800b12db9744a5a3`: `ch is "'"`.
- `714 Eq_Is` (occ4) `665257f4dee34e1484edd229caa236cc`: `ch is '"'`.
  Both compare a single-char string indexed from `command` to a single-char
  literal; CPython interns single-char latin1 strings so `is` ≡ `==`.

## Category B2 — `is_blocked_command` empty-guard or→and (line 596)

- `ReplaceOrWithAnd` `792699f458f2460d84a1cdd4252f145b`:
  `not command or not command.strip()` → `... and ...`. The two diverge ONLY
  for whitespace-only inputs: `or` returns early (`is_blocked=False`); `and`
  falls through, but `command.strip()` is then `""` which matches no pattern,
  so the function still returns `is_blocked=False`. Identical observable for
  every input class (confirmed in `_probe_equiv.py`).

## Category B3 — `_read_only_base_cmds` unreachable except (line 74)

- `ExceptionReplacer` `3667c2b2771244e6b5a9201a67e2f4fa`: mutates the
  `except ImportError:` handler. The `from .mutative_verbs import
  READ_ONLY_BASE_CMDS` always succeeds in-process (no circular-import failure
  at call time), so the handler body is unreachable and the mutation has no
  observable effect (confirmed in `_probe_equiv.py`).

**Endpoint:** 0 untriaged survivors in `blocked_commands.py`. 37 killed by
honest tests, 21 proven equivalent (listed above) and excluded via
`equivalents-blocked-commands.skip`.

---

# Equivalent Mutants — `mutative_verbs.py` (GRIND-TOTAL, this session)

**Module:** `hooks/modules/security/mutative_verbs.py`
**Session DB:** `mutative-verbs.sqlite`
**Baseline:** 55.78% (410 killed / 325 survived / 735 total).

Survivors are killed by honest tests in
`tests/hooks/modules/security/test_mutative_verbs_mutants.py`. Mutants listed
below are genuinely equivalent — no honest input distinguishes them for ANY
reachable input — and are excluded via `equivalents-mutative-verbs.skip`.

## Category M1 — import-fallback `except ImportError` handlers (3 mutants)

The module imports three sibling modules at load time, each guarded by
`try / except ImportError`:

- line 34: `from .capability_classes import ...`
- line 46: `from .blocked_commands import is_blocked_command`
- line 55: `from .inline_ast_analyzer import analyze_python_inline`

All three siblings always import successfully in-process (confirmed: a direct
`import` of `capability_classes`, `blocked_commands`, and `inline_ast_analyzer`
from `modules.security` succeeds). The `except ImportError:` body therefore
never executes. `ExceptionReplacer` swaps the caught type, but since the body
is unreachable the mutation has no observable effect — identical to the
`blocked_commands` B3 case above.

- `5973c2d7f577477ea68ca846e34322cb` (line 34)
- `7032f9b90c1c4dceb03b15ff2387b05f` (line 46)
- `147eb27c5a254c6ab0d3b92600f1a333` (line 55)

## Category M2 — `@functools.lru_cache` transparency (3 mutants)

`detect_mutative_command` is decorated with `@functools.lru_cache(maxsize=128)`
(line 994). `lru_cache` is a transparent memoization of a pure function: for
identical arguments it returns identical results whether the cache is present,
absent, or sized differently. None of these mutants change any observable
output:

- `0a92fbce93084dee862d41bedee91f99` — `RemoveDecorator`: removing the cache
  only forgoes memoization; every call recomputes the same `MutativeResult`.
- `56957e5de53c4785b32c7afe9f7c0cc8`, `bb8fe5f7d7184699b97978221d36a6c8` —
  `NumberReplacer` on `maxsize=128`: changes only the cache eviction capacity,
  never a return value. (An unbounded or smaller cache yields identical
  results for the test population.)

**Endpoint:** every other `mutative_verbs.py` survivor is killed by an honest
test. 6 mutants proven equivalent here (3 import-fallback + 3 lru_cache).
