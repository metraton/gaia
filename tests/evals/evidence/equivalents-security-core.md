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

Equivalents are identified by a stable key: `operator|location|occurrence`.
This tuple persists across `cosmic-ray init` re-runs. Line numbers are as of
the commit that lands this file; the operator + function + stable-key form the
durable anchors for equivalence proofs.

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

| Function | Line | Annotation | Operators (BitOr→X) | Stable key (`operator|location|occurrence`) |
|----------|-----:|------------|---------------------|---------|
| `create_command_set_grant` | 1547 | `session_id: str \| None` | Pow, FloorDiv, LShift, Mod, BitAnd, Sub, BitXor, RShift, Mul, Div, Add | ReplaceBinaryOperator_BitOr_*\|1547\|1-11 |
| `create_command_set_grant` | 1548 | `agent_id: str \| None` | Add, Sub, Mod, BitAnd, BitXor, FloorDiv, Pow, Div, RShift, Mul, LShift | ReplaceBinaryOperator_BitOr_*\|1548\|1-11 |
| `match_command_set_grant` | 1622 | `-> tuple \| None` (return) | Sub, Mod, BitXor, Div, Add, LShift, Pow, RShift, FloorDiv, BitAnd, Mul | ReplaceBinaryOperator_BitOr_*\|1622\|1-11 |

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

| Function | Line | Token | Operator | Stable key |
|----------|-----:|-------|----------|--------|
| `create_command_set_grant` | 1546 | `*,` (kw-only marker) | Mul→Div | ReplaceBinaryOperator_Mul_Div\|1546\|1 |
| `match_command_set_grant` | 1620 | `*,` (kw-only marker) | Mul→Div | ReplaceBinaryOperator_Mul_Div\|1620\|1 |

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

| Function | Line(s) | Slice / constant | Stable keys |
|----------|---------|------------------|---------|
| `check_approval_grant` | 506 | `command[:80]`, `(... or "?")[:16]`, `or` chain | NumberReplacer\|506\|1-5 |
| `consume_grant` | 545, 550 | `command[:80]`, `approval_id[:16]` | NumberReplacer\|545\|1, NumberReplacer\|545\|2, NumberReplacer\|550\|1-3 |
| `consume_session_grants` | 615, 620 | `approval_id[:16]` (×2 log sites) | NumberReplacer\|615\|1, NumberReplacer\|620\|1-2 |
| `confirm_grant` | 653, 662, 667 | `command[:80]`, `approval_id[:16]` | NumberReplacer\|653-667\|* |
| `find_pending_for_command` | 855 | `command[:80]` | NumberReplacer\|855\|1-2 |
| `create_command_set_grant` | 1606 | `approval_id[:12]` (logger.info args) | NumberReplacer\|1606\|1-2 |
| `match_command_set_grant` | 1707 | `retried_command[:80]`, `approval_id[:12]` | NumberReplacer\|1707\|1-3 |
| `activate_db_pending_by_prefix` | 1311, 1322, 1325, 1406, 1441, 1480 | `approval_id[:16]`, `[:12]`, `command[:80]`, `session[:12]` | NumberReplacer\|1311-1480\|* |
| `load_pending_by_nonce_prefix` | 358 | `candidates[0].get("nonce","?")[:12]` (log arg) | NumberReplacer\|358\|1-2 |

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

| Function | Line | Expression | Stable key |
|----------|-----:|------------|--------|
| `get_pending_approvals_for_session` | 810 | `d.get("timestamp", 0)` | NumberReplacer\|810\|1-2 |
| `load_pending_by_nonce_prefix` | 355 | `d.get("timestamp", 0)` | NumberReplacer\|355\|1-3 |

**Why unkillable:** `_db_row_to_pending_dict` (line 763-778) unconditionally
includes `"timestamp": ts`. No reachable row omits the key, so the mutated
default value is never the value used; the sort order is identical.

---

## Category E5 — module-init constants overwritten before first read, or with no reachable boundary

| Const | Line | Mutation | Why equivalent | Stable keys |
|-------|-----:|----------|----------------|---------|
| `_last_cleanup_time = 0.0` | 142 | Number 0.0→N | First read is `now - _last_cleanup_time < 60` (line 697); `now` ≈ 1.7e9 s, so `now - N` ≫ 60 for any small init N. The throttle never fires on the first call regardless of init. After the first call the value is reassigned to `now` (line 699). The init constant has no reachable boundary. | NumberReplacer\|142\|1-2 |
| `_last_check_found_expired = False` | 258 | False→True | `check_approval_grant` resets it to `False` at line 464 on EVERY call before any conditional set, and `last_check_found_expired()` is the only reader. The module-init value is overwritten before any read can observe it. | ReplaceFalseWithTrue\|258\|1 |

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

| Function | Line | Stable keys |
|----------|-----:|---------|
| `_db_row_to_pending_dict` | 746 | ReplaceUnaryOperator_USub_UAdd\|746\|1, NumberReplacer\|746\|1, Delete_USub\|746\|1 |

**Why unkillable:** the `if ": " in operation` guard forces `rsplit(": ", 1)` to
return exactly 2 parts, where index `-1` and index `1` are the same element. No
input that reaches this line distinguishes them.

---

## Category E7 — `exc_info` / best-effort except-handler mutations with no reachable raising path

| Function | Line | Mutation | Why equivalent | Stable key |
|----------|-----:|----------|----------------|--------|
| `activate_db_pending_by_prefix` | 1515 | `exc_info=True`→`False` (ReplaceTrueWithFalse) | `exc_info` only controls whether the traceback is appended to the log record. Log-only; no behavioural observable. | ReplaceTrueWithFalse\|1515\|1 |
| `capture_environment_snapshot` | 439 | `except Exception` (ExceptionReplacer) | The try body (lines 420-437) calls only `_run_git_query` — which itself wraps everything in `try/except: pass` and never propagates — plus pure dict assignments that cannot raise. No reachable input makes the try body raise, so the handler is never entered; narrowing its caught type is unobservable. | ExceptionReplacer\|439\|1 |
| `module-level` | 374 | `_ENV_SNAPSHOT_TIMEOUT_SECONDS = 2`→3 (Number) | The constant is the `subprocess.run(timeout=...)` for git queries. Distinguishing 2 s from 3 s requires a git invocation that hangs > 2 s and < 3 s — non-deterministic and unreachable in an honest test; both values succeed for any real git call. | NumberReplacer\|374\|1-2 |

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

| Operator | Mutant | Why equivalent | Stable key |
|----------|--------|----------------|--------|
| NotEq→Gt | `scope > "COMMAND_SET"` | equal strings → `>` is False, like `!=` | NotEq\|1683\|1 |
| NotEq→Lt | `scope < "COMMAND_SET"` | equal strings → `<` is False, like `!=` | NotEq\|1683\|2 |
| NotEq→Is | `scope is "COMMAND_SET"` | the sqlite value is **non-interned**, so `is` is False, like `!=` | NotEq\|1683\|3 |
| ContinueWithBreak (1684) | `continue`→`break` | line 1684 is unreachable (1683 always False) | ContinueWithBreak\|1684\|1 |

**NotEq_Is puzzle — VERDICT: genuine equivalence (cause c), NOT a harness bug.**
The harness reported the mutant (stable key `NotEq|1683|3`) as SURVIVED. I
suspected a fidelity bug, but the minimal experiment settled it: cosmic-ray's
`ReplaceComparisonOperator_NotEq_Is` substitutes `!=` with **`is`** (not `is not`).
Hand-applying `grant.get("scope") is "COMMAND_SET"` and running the COMMAND_SET
match tests: they **PASS** — because the sqlite-sourced scope string is
non-interned, so `is` is False exactly like `!=`. (A separate hand-test
confirmed that `is not` — which the operator does NOT produce — *would* kill it,
which is what misled the first analysis.) The harness is faithful; new tests ARE
re-collected per run. No harness fix needed.

### Category E9 — fast-path boundary coincides with the wall-clock fall-through

`_is_ttl_expired` line 177 `if timestamp == 0: return True` (NumberReplacer
0→±1). For `timestamp == 0` the fall-through `(time.time() - 0) / 60` is ≈ 2.9e7
minutes, which exceeds any honest `ttl_minutes`, so it ALSO returns True.
Distinguishing the fast-path from the fall-through would require
`ttl_minutes >= time.time()/60` (~55 years) — unreachable. Proven: all 28
ttl/expiry tests green under `timestamp == 1`. Stable key: `NumberReplacer|177|1`.

### Category E10 — index value used only as a truthiness check, never read

`activate_db_pending_by_prefix` line 1175 `command = command_set_items[0]["command"]`
(NumberReplacer [0]→[1]/[-1]). Reached only when `is_command_set and not command`.
In the `is_command_set` path `command` only gates the `if not command` guard
(both set items are non-empty command strings → truthy either way), and the
COMMAND_SET branch (line 1299) returns without ever using `command` for the
signature. Proven: the full activation suite (295 tests) is green under
`command_set_items[1]`. Stable keys: `NumberReplacer|1175|1`, `NumberReplacer|1175|2`.

### Category E11 — dead local variable

`activate_db_pending_by_prefix` line 1458
`verbs = [signature.verb] if signature.verb else ([danger_verb.lower()] if danger_verb else ["write"])`.
`verbs` has **exactly one occurrence** in the module (assigned, never read).
Both AddNot flips (on `if signature.verb` and the inner `if danger_verb`) change
only the value of a dead variable. Proven: 1313 security tests green under both
flips applied simultaneously. Stable keys: `AddNot|1458|1`, `AddNot|1458|2`.

### Category E12 — log-only branch / log argument / interned-name comparison

These mutate a constant or branch whose ONLY consumer is a `logger.*` call (or an
interned attribute). An honest test asserts observable behaviour (return / state /
persisted row), never log text, so none is distinguishable.

| Site | Mutant | Why equivalent | Stable keys |
|------|--------|----------------|---------|
| `consume_grant` 542 | `if consumed:` AddNot | gates only a log line; `return consumed` identical | AddNot\|542\|1 |
| `cleanup_expired_grants` 713 | `if cleaned:` AddNot | gates only a log line; `return cleaned` identical | AddNot\|713\|1 |
| `check_approval_grant_for_file` 978 | `str(row.get("approval_id",""))[:16]` NumberReplacer ×2 | slice bound / `.get` default inside `logger.info` arg | NumberReplacer\|978\|1-2 |
| `create_command_set_grant` 1579 | `len(command_set) if command_set else 0` AddNot + NumberReplacer ×2 | entirely inside the `logger.error` argument | AddNot\|1579\|1, NumberReplacer\|1579\|1-2 |
| `load_pending_by_nonce_prefix` 358 | `candidates[0].get("nonce","?")[:12]` NumberReplacer | `logger.info` argument only | NumberReplacer\|358\|1 |
| `activate_db_pending_by_prefix` 1324 | `(originating_session or "")[:12]` Or→And | `logger.info` argument only | ReplaceOrWithAnd\|1324\|1 |
| `_get_grants_dir` 252 | `_grants_dir_created = False` ReplaceFalseWithTrue | module-init memoization flag; `mkdir(exist_ok=True)` idempotent and any state-resetting test masks the init before the only (first-call) observable read | ReplaceFalseWithTrue\|252\|1 |
| `activate_db_pending_by_prefix` 1212 | `_fp_exc.__class__.__name__ == "ChainTamperError"` Eq→Is | `__name__` is interned by CPython, so `is` behaves like `==` | Eq\|1212\|1 |

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

| Function | Line | Expression | Operator | Stable key |
|----------|-----:|------------|----------|--------|
| `activate_db_pending_by_prefix` | 1426 | `if len(parts) == 2:` | Eq→LtE | Eq\|1426\|1 |

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
`--skip-file <path>` which reads one stable key per non-comment line
(`operator|location|occurrence`) and excludes those mutants from both the
denominator and the numerator. The skip file is the materialization of this
document: every mutant proven equivalent above is listed there, one per line,
with category comments. Stable keys persist across `cosmic-ray init` re-runs,
eliminating the silent "false 100%" exclusion-zero that occurred when re-initting
with regenerated `job_ids`.

**Denominator composition (after grind-total, E1–E13):**

| Population | Count |
|------------|------:|
| Total specs in DB | 653 |
| INCOMPETENT (excluded by cosmic-ray) | 2 |
| Proven-equivalent excluded (E1–E13, skip file) | 121 |
| **Killable denominator** | **530** |

**Grind-total closure:** all surviving mutants triaged. 0 untriaged survivors remain in `approval_grants.py`.
- Mutant `Eq|1212|2` (line 1212 Eq→LtE): **KILLED** by new test `TestActivateDbPendingIntegrityLabelAC1::test_lexically_early_class_name_not_treated_as_tamper` using `AttributeError` as the discriminating input (`"AttributeError" < "ChainTamperError"` → `<=` returns True wrongly labeling a non-tamper error).
- Mutant `Eq|1426|1` (line 1426 Eq→LtE): **EQUIVALENT** (E13). Proven by suite (1313 tests green under mutant); outer `"intercepted:" in` guard makes `len < 2` unreachable.

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

- `Lt_NotEq` `Lt|705|1`: `i != n` ≡ `i < n` because `i`
  never exceeds `n` (lands exactly on it).
- `Lt_IsNot` `Lt|705|2`: `i is not n`. For all reachable
  `n` (small-int cached at the boundary) behaves like `!=`; distinguishing
  would require relying on CPython int-identity for `n > 256`, which is not an
  honest behavioral test (implementation detail, not a security property).

### B1b — escape guard `if ch == "\\" and i + 1 < n` (line 707)

- `Eq_Is` (col 14, occ2) `Eq|707|2`: `ch is "\\"`.
  Single-char strings are interned by CPython, so `is` ≡ `==`.
- `Add_*` on `i + 1` (col 28, occ1) — Sub/Mul/Div/FloorDiv/Mod/Pow/RShift/
  BitOr/BitAnd/BitXor: `Add|707|1` (one per operator variant). The guard `<n` is
  only False for a TRAILING backslash; for every reachable `i` these alternate
  ops keep `expr < n` at the same truth value as `i+1 < n` (all reduce to "enter
  escape unless trailing"). (Add_LShift `i<<1` was NOT equivalent — KILLED by
  `aaaa\|`; only the listed ops are equivalent.)
- `Number 1→0` (col 30, occ5) `NumberReplacer|707|5`: `i + 0 < n` =
  `i < n`, always True inside the loop; a trailing backslash then enters escape
  (`i+=2` past EOF) → still returns False. (occ4, `1→2`, was KILLED by `\|`.)
- `Lt_*` on `i + 1 < n` (col 32, occ1) — NotEq `Lt|707|1` (NotEq variant), LtE
  `Lt|707|1` (LtE variant), IsNot `Lt|707|1` (IsNot variant): `i+1` never exceeds
  `n`, so `!=`/`<=` match `<` at every reachable point; `is not` is the
  int-identity case as in B1a.

### B1c — quote-toggle Eq_Is (lines 710, 714)

- `710 Eq_Is` (occ3) `Eq|710|3`: `ch is "'"`.
- `714 Eq_Is` (occ4) `Eq|714|4`: `ch is '"'`.
  Both compare a single-char string indexed from `command` to a single-char
  literal; CPython interns single-char latin1 strings so `is` ≡ `==`.

## Category B2 — `is_blocked_command` empty-guard or→and (line 596)

- `ReplaceOrWithAnd` `ReplaceOrWithAnd|596|1`:
  `not command or not command.strip()` → `... and ...`. The two diverge ONLY
  for whitespace-only inputs: `or` returns early (`is_blocked=False`); `and`
  falls through, but `command.strip()` is then `""` which matches no pattern,
  so the function still returns `is_blocked=False`. Identical observable for
  every input class (confirmed in `_probe_equiv.py`).

## Category B3 — `_read_only_base_cmds` unreachable except (line 74)

- `ExceptionReplacer` `ExceptionReplacer|74|1`: mutates the
  `except ImportError:` handler. The `from .mutative_verbs import
  READ_ONLY_BASE_CMDS` always succeeds in-process (no circular-import failure
  at call time), so the handler body is unreachable and the mutation has no
  observable effect (confirmed in `_probe_equiv.py`).

**Denominator (blocked_commands.py, after grind-total):**

| Population | Count |
|------------|------:|
| Total specs in DB | 157 |
| INCOMPETENT (excluded by cosmic-ray) | 0 |
| Proven-equivalent excluded (B1–B3, skip file) | 21 |
| **Killable denominator** | **136** |

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

- `ExceptionReplacer|34|1` (line 34)
- `ExceptionReplacer|46|1` (line 46)
- `ExceptionReplacer|55|1` (line 55)

## Category M2 — `@functools.lru_cache` transparency (3 mutants)

`detect_mutative_command` is decorated with `@functools.lru_cache(maxsize=128)`
(line 994). `lru_cache` is a transparent memoization of a pure function: for
identical arguments it returns identical results whether the cache is present,
absent, or sized differently. None of these mutants change any observable
output:

- `RemoveDecorator|994|1` — `RemoveDecorator`: removing the cache
  only forgoes memoization; every call recomputes the same `MutativeResult`.
- `NumberReplacer|994|1`, `NumberReplacer|994|2` —
  `NumberReplacer` on `maxsize=128`: changes only the cache eviction capacity,
  never a return value. (An unbounded or smaller cache yields identical
  results for the test population.)

## Category M3 — `split_camel_case` len-guard boundary (2 mutants)

`split_camel_case` (line 932) ends in:

```python
return [p.lower() for p in parts] if len(parts) > 1 else [token.lower()]
```

`parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", token).split()`. Two survivors on
the `len(parts) > 1` guard are equivalent because the two arms produce the
SAME value for every `len(parts)` the original guard could route differently:

- `Gt|932|1` — `Gt_GtE` (`len(parts) >= 1`): the only
  lengths that change branch are `len == 1` and `len == 0`. At `len == 1`,
  `[p.lower() for p in parts]` is a one-element list of the same lowercased
  word that `[token.lower()]` produces (the single part *is* the token,
  lowercased). At `len == 0` (empty token `""`), `parts == []` so the
  comprehension is `[]` — but `>= 1` is also False for `len 0`, so that arm is
  never reached; both operators take the `else` branch → `[""]`. No input
  distinguishes `>=` from `>`.
- `NumberReplacer|932|25` — `NumberReplacer` occ25 = `1 -> 0`
  (`len(parts) > 0`; cosmic-ray `OFFSETS = [+1, -1]`, occurrence index 1 →
  `-1`). Identical argument to Gt_GtE: only `len == 1` and `len == 0` change
  branch, and at both the two arms coincide as above. (The sibling `1 -> 2`
  mutant `> 2` IS killable and is killed by `test_camel_split_two_parts`
  asserting `["batch","delete"]`; it is NOT listed here.)

## Category M4 — `_scan_dangerous_flags` elif-chain + compound guard (17 mutants)

`_scan_dangerous_flags` (lines 867-915) walks tokens. For a token that is a
DANGEROUS_FLAGS key it runs an `if flag_type == "ALWAYS": ... elif token ==
"-f": ... elif token in ("-r","-R"): ... elif token == "-D": ... elif "-M" ...
elif "--delete" ... elif "--recursive" ... elif "--hard"`. The context
flag-sets are: `F_FLAG_MEANS_FORCE` (rm/cp/mv/...), `R_FLAG_MEANS_RECURSIVE_
DELETE` (rm/cp/chmod/find/gsutil/...), and `D_FLAG_MEANS_FORCE_DELETE ==
M_FLAG_MEANS_FORCE_MOVE == HARD_FLAG_IS_DESTRUCTIVE == {git}`, `DELETE_FLAG_IS_
DESTRUCTIVE == {git, rsync}`. The KILLABLE survivors (L882 `==-f` Eq_GtE; L879
ReplaceContinueWithBreak; L906 `len>2` Gt_NotEq) are killed by honest tests;
the 17 below cannot be distinguished by any reachable input:

**L877 `flag_type == "ALWAYS"` — values ∈ {"ALWAYS","CONTEXT"} only:**
- `Eq|877|1` — Eq_LtE: `"CONTEXT" <= "ALWAYS"` is False
  ("C" > "A"), `"ALWAYS" <= "ALWAYS"` True → identical truth table to `==`.
- `Eq|877|2` — Eq_Is: both values are module-level
  identifier-like string literals (interned); `is` reproduces `==`.

**L888/L891/L897/L894 — later elif branches only ever see their own flag:**
A single `==` is mutated in isolation; the earlier branches still match their
own tokens, so the mutated branch is reached ONLY by the flag it tests for.
- `Eq|888|1` — L888 `==-D` Eq_GtE: tokens reaching L888
  that are `>= "-D"` are `-D` (match) and `-M`; `-M` enters the `-D` branch but
  `D_FLAG_MEANS_FORCE_DELETE == M_FLAG_MEANS_FORCE_MOVE == {git}`, so the append
  decision is identical for every cli.
- `Eq|891|1` — L891 `==-M` Eq_GtE: only `-M` reaches
  L891 with `>= "-M"` (`--*` flags sort below `-M`; `-D` matched earlier).
- `Eq|897|1` — L897 `==--recursive` Eq_GtE: only
  `--recursive` reaches it `>= "--recursive"` (`--hard` < `--recursive`).
- `Eq|894|1` — L894 `==--delete` Eq_LtE: only
  `--delete` is `<= "--delete"` among tokens reaching L894 (`--hard`,
  `--recursive` both sort above `--delete`).

**L900 `token == "--hard"` — last branch, only `--hard` reaches:**
- `Eq|900|1` — Eq_GtE: `"--hard" >= "--hard"` True.
- `Eq|900|2` — Eq_LtE: `"--hard" <= "--hard"` True.
- `Eq|900|3` — Eq_IsNot: `"--hard"` (runtime token from
  the list) is a different object from the non-interned literal `"--hard"` (it
  contains `-`, so it is not auto-interned), so `token is not "--hard"` is True
  exactly when the branch should be entered — same observable as `==` here, and
  no other token reaches L900.

**L906 compound-flag guard `len(token) > 2 and token[0] == "-" and token[1] != "-"`:**
- `Eq|906|1`, `Eq|906|2`,
  `Eq|906|3` — `token[0] == "-"` Eq_GtE/Eq_LtE/Eq_Is:
  control reaches L906 only after L870 confirmed `token.startswith("-")`, so
  `token[0]` is ALWAYS `"-"`; `>=`, `<=`, and `is` (single-char interned) all
  reproduce `== "-"`.
- `Gt|906|1` — `len(token) > 2` Gt_GtE (`>= 2`): a
  2-char token reaching L906 cannot be `-f`/`-r`/`-R`/`-D`/`-M` (those are
  DANGEROUS_FLAGS keys, matched at L874) — so its single flag char is never
  `r`/`f`, and both `> 2` and `>= 2` leave it uncollected.
- `NumberReplacer|906|17` — `len > 2` NumberReplacer occ17 = `2->1`
  (`> 1`): same argument; the only extra token it would admit (len 2) carries
  no `r`/`f` flag char, so the result is unchanged. (The sibling `2->3` IS
  killable and is killed by `test_compound_f_only_force_cli`.)
- `Gt|906|2` is NOT here — Gt_NotEq is killed by
  `test_bare_dash_token_not_compound`.
- `NotEq|906|1`, `NotEq|906|2` —
  `token[1] != "-"` NotEq_Gt / NotEq_IsNot: `token[1]` is a single char; every
  flag letter sorts above `"-"` (0x2D) so `> "-"` matches `!= "-"`, and single
  chars are interned so `is not "-"` matches `!= "-"`.

**L907 `flag_chars = token[1:]` NumberReplacer occ23 = `1->0` (`token[0:]`):**
- `NumberReplacer|907|23` — `token[0:]` is the whole token (leading
  `"-"` included); `"r" in flag_chars` / `"f" in flag_chars` are unaffected
  because `"-"` is neither `r` nor `f`, so every membership test is unchanged.
  (The sibling `1->2` IS killable and is killed by `test_compound_rf_always`.)

## Category M5 — `_mkdir_targets_sensitive_path` opt-handling residuals (9 mutants)

`_mkdir_targets_sensitive_path` (lines 656-691) walks `tokens[1:]`, skipping
flags and `-m`/`--mode` values, and returns True iff any absolute path argument
falls under a `MKDIR_SENSITIVE_PATH_PREFIXES` prefix (all of which begin with
`/`). The KILLABLE survivors (L659 `i < len` Lt_IsNot/Lt_NotEq via a bare
trailing `-m` that overshoots into an IndexError; L677 ReplaceContinueWithBreak
via `~/foo /etc/bar`) are killed by honest tests. The 9 below cannot be
distinguished — every one of them only affects how a token *starting with*
`"-"` or `"~"` is routed, and such a token can never be an absolute sensitive
path (those start with `/`), so the boolean is unchanged:

- `NumberReplacer|658|11` — L658 `i = 1` NumberReplacer occ11 =
  `1 -> 0` (`OFFSETS = [+1,-1]`, index 1). Starting at `i = 0` re-includes
  `tokens[0]` (the base command, e.g. `mkdir`), which is a relative token →
  `not os.path.isabs` → `continue`. No sensitive path is ever at index 0, so
  the result is identical. (The sibling `1 -> 2` IS killable — it skips the
  first real path — and is killed by `test_sensitive_etc_subpath`.)
- `Eq|663|1`, `Eq|663|2`,
  `Eq|663|3` — L663 `token == "--"` Eq_Is/Eq_Lt/Eq_LtE.
  The only effect of matching `"--"` is to set `seen_end_of_opts`, which only
  changes whether a *later* `"-"`-prefixed token is treated as a path. A
  `"-"`-prefixed token is never absolute-sensitive (sensitive prefixes start
  with `/`), so the boolean is unchanged. (`is` never matches the non-interned
  `"--"` literal; `< "--"` / `<= "--"` match no real path token since paths
  start with `/` (0x2F) > `"-"` (0x2D).)
- `ReplaceTrueWithFalse|664|1` — L664 `seen_end_of_opts = True`
  ReplaceTrueWithFalse. Same argument: with it False, a post-`--` `"-x"` token
  is treated as a flag and `continue`d; with it True it is treated as a path,
  `isabs("-x")` is False → relative → `continue`. Both reach `continue`; the
  only tokens affected start with `"-"` and are never sensitive.
- `Eq|675|1`, `Eq|675|2`,
  `Eq|675|3`, `ReplaceOrWithAnd|675|1` —
  L675 `token.startswith("~/") or token == "~"` Eq_Gt/Eq_GtE/Eq_Is + the
  ReplaceOrWithAnd. The `~` guard is redundant for the boolean: a token that
  fails it falls through to `os.path.isabs(token)`, and `~`/`~/...` are not
  absolute → relative → `continue` (safe) — the same outcome the guard
  produces. No `~`-token is ever an absolute sensitive path, so weakening or
  inverting the guard changes nothing observable.

## Category M6 — `detect_mutative_command` Step-3e/3f maxsplit/len residuals (6 mutants)

Step 3e (command+subcommand tier exception, L1282-1337) extracts
`group_verb = non_flag_tokens[1] if len(non_flag_tokens) > 1 else ""` and tests
destructiveness via `group_verb.split("-", 1)[0]`. Step 3f (consent-reducing
operations, L1350-1372) has an identical extraction pattern:
`consent_verb = non_flag_tokens[1] if len(non_flag_tokens) > 1 else ""`.
Five survivors are equivalent; the KILLABLE siblings (the `or`->`and` chain,
the maxsplit `1->0`, the verb/reason index mutants, and the L1286/L1368
NumberReplacers) are killed by `TestSubcommandTierException` and
`TestStep4VerbArmsAndGuards`.

- `Gt|1286|1` — L1286 `len(non_flag_tokens) > 1`
  Gt_NotEq (`!= 1`): control is inside `if semantics.non_flag_tokens`, so
  `len >= 1` always. At `len == 1`, both `> 1` and `!= 1` are False →
  `group_verb = ""`. At `len >= 2`, both are True → `group_verb = nft[1]`. No
  reachable length distinguishes them.
- `NumberReplacer|1294|46` — L1294 `split("-", 1)[0]` NumberReplacer
  occ46 = `1 -> 2` (`split("-", 2)[0]`). `str.split(sep, maxsplit)[0]` is the
  text before the FIRST separator for any `maxsplit >= 1`, so `maxsplit = 2`
  yields the same `[0]` as `maxsplit = 1`. (The `1 -> 0` sibling splits nothing
  and IS killed by `test_plan_hyphenated_destroy_verb_stays_t3`.)
- `NumberReplacer|1296|50` — L1296 `split("-", 1)[0]` (arm3, the
  EXTRA_DENY check) NumberReplacer occ50 = `1 -> 2`: identical argument.
- `NumberReplacer|1313|54` — L1313 `verb = group_verb.split("-", 1)[0]`
  NumberReplacer occ54 = `1 -> 2`: identical argument; the returned `verb` is
  the first segment for any `maxsplit >= 1`.
- `NumberReplacer|1417|78` — L1417 `candidate = stripped_token.split("-", 1)[0]`
  (Step-4 hyphen-split) NumberReplacer occ78 = `1 -> 2`: same argument as the
  three above — `[0]` is the text before the FIRST separator for any
  `maxsplit >= 1`. Proven by elimination: the idx-2 split test
  (`test_hyphen_split_at_index_two_high_confidence`, input `gh repo
  delete-thing`) kills the `1 -> 0` sibling (which would leave `candidate =
  "delete-thing"`, not a verb) and the `[0] -> [1]` sibling (`candidate =
  "thing"`); occ78 survives both, so it is the `1 -> 2` no-op.
- `Gt|1354|1` — L1354 `len(non_flag_tokens) > 1`
  Gt_NotEq (`!= 1`) in Step 3f (consent-reducing operations): exactly the same
  structural argument as the L1286 Gt_NotEq above. Control is inside
  `if semantics.non_flag_tokens` so `len >= 1` always. At `len == 1`, both
  `> 1` and `!= 1` are False → `consent_verb = ""`. At `len >= 2`, both are
  True → `consent_verb = nft[1]`. No reachable length distinguishes them.
  The KILLABLE sibling is the L1286 NumberReplacer `NumberReplacer|1286|1` (changes `1` to
  `2` or `0`), killed by `test_plan_delete_nft_len2_stays_t3` in
  `TestStep4VerbArmsAndGuards`. The analogous L1354 NumberReplacer would also
  be killed by `test_approvals_revoke_reason_exact` (same class), but the DB
  shows only the Gt_NotEq variant survived; it is proven equivalent here.

---

## Category M7 — script-file / inline / layer3 comparison & loop residuals

GRIND-TOTAL second pass. Survivors in the script-resolution and inline-code
helpers whose mutated form cannot be distinguished from the original by any
reachable input. The KILLABLE siblings in each helper are killed by honest
tests in `test_mutative_verbs_mutants.py` (`TestLayer3LengthCheck`,
`TestCheckInlineCode`, `TestCheckScriptFilePythonLane`).

`_layer3_length_check` (L1988-1990):
- `NotEq|1988|1` — L1988 `idx != -1` NotEq_Gt (`idx > -1`).
  `str.find` returns `-1` (absent) or an index `>= 0`; it never returns
  `< -1`. So `idx > -1` is True iff `idx >= 0` iff `idx != -1`. Identical
  truth table over the entire range of `find`. (The `<= -1`/`< -1`/`>= -1`/`==`
  siblings ARE killed.)
- `NotEq|1988|2` — L1988 `idx != -1` NotEq_IsNot
  (`idx is not -1`). CPython interns the small int `-1`, and every value `find`
  produces (`-1` or `>= 0`) is an interned small int for the reachable test
  inputs, so `is not -1` coincides with `!= -1`. Identity-vs-equality cannot be
  distinguished for the interned `-1` sentinel.
- `ReplaceBreakWithContinue|1990|1` — L1990 `break` ReplaceBreakWithContinue.
  For a single-flag interpreter (the python `-c` case, `_INLINE_CODE_MAP`
  value is a one-element frozenset) the loop has at most one matching
  iteration, so `continue` and `break` are identical. The only inputs that
  could differ supply TWO inline flags of one interpreter (e.g. node
  `-e`+`--eval`); under `continue` the textually-last matching flag wins, but
  which flag the frozenset yields last depends on str-hash iteration order,
  which is randomized per interpreter run (no `PYTHONHASHSEED` pin in the
  harness). No deterministic honest input distinguishes break from continue.

`_resolve_script_argument` (L1695):
- `Eq|1695|1` — L1695 `token == "-"` Eq_Is
  (`token is "-"`). The stdin sentinel `"-"` is a one-char interned string, and
  the compared literal `"-"` is the same interned object, so `is` coincides
  with `==` for the only value that makes either branch True.
- `Eq|1695|2` — L1695 `token == "-"` Eq_LtE
  (`token <= "-"`). Reached only after the loop has already `continue`d every
  token starting with `-` (L1699) and returned on the first true positional
  (L1701); the sole token whose comparison to `"-"` is evaluated and matters is
  `"-"` itself. `"-" <= "-"` is True exactly when `"-" == "-"`, and no shorter
  string precedes it here, so the arms coincide.

`_read_script_content` (L1729):
- `ExceptionReplacer|1729|1` / `ExceptionReplacer|1729|2` —
  L1729 `except (OSError, ValueError)` ExceptionReplacer (each tuple member
  swapped for `CosmicRayTestingException`). The `try` body is `os.path.isfile`
  + `open(...)` + `fh.read(...)`, which on the reachable inputs raise only
  `OSError` (missing/unreadable path) or `ValueError` (bad mode/encoding) —
  both already named. No reachable input raises a different exception type, so
  narrowing one tuple member to the never-raised testing sentinel changes
  nothing observable. (Same unreachable-handler reasoning as M1.)

`_check_script_file` (L1770):
- `Eq|1770|1` — L1770 `lane == "python"` Eq_Is
  (`lane is "python"`). `lane` is assigned the literal `"python"` or `"shell"`
  in `_resolve_script_argument`; both are interned compile-time constants, so
  `is "python"` coincides with `== "python"` for both possible values. (The
  Eq_GtE sibling IS killed.)
- `Eq|1770|2` — L1770 `lane == "python"` Eq_LtE
  (`lane <= "python"`). `lane` ranges over the two-element domain
  `{"python", "shell"}`. `"python" <= "python"` is True; `"shell" <= "python"`
  is False (`'s' > 'p'`). Same truth table as `== "python"` across the whole
  reachable domain.

**Endpoint (M1–M7, partial):** 50 mutants proven equivalent across the first
seven categories (3 import-fallback + 3 lru_cache + 2 split_camel_case +
17 _scan_dangerous_flags + 9 _mkdir_targets_sensitive_path + 6
detect_mutative_command Step-3e/3f + 10 M7 script/inline/layer3 residuals).
Categories M8–M10 (below) add 24 more for a **grand total of 74 equivalents**,
matching the `equivalents-mutative-verbs.skip` count.

**Denominator (mutative_verbs.py, after grind-total):**

| Population | Count |
|------------|------:|
| Total specs in DB | 735 |
| INCOMPETENT (excluded by cosmic-ray) | 0 |
| Proven-equivalent excluded (M1–M10, skip file) | 74 |
| **Killable denominator** | **661** |

**Grind-total closure:** 0 untriaged survivors remain in `mutative_verbs.py`.

## Category M8 — detect_mutative_command fast-path / early-branch residuals

GRIND-TOTAL final pass closing the last `detect_mutative_command` survivors.
The KILLABLE siblings in each branch are killed by honest tests in
`TestDetectMutativeEarlyBranches` (and the existing `TestDetectMutativeCommand`
cases). The mutants below cannot be distinguished from the original by any
reachable input.

mkdir path-token filter (L1048 `not t.startswith("-") and t != "--"`):
- `NotEq|1048|1` — L1048 NotEq_IsNot (`t is not "--"`).
  This clause is only evaluated for a token where `not t.startswith("-")` is
  True, i.e. `t` does NOT start with `-`; such a token can never equal `"--"`,
  so `t != "--"` is unconditionally True at every reachable evaluation.
  `t is not "--"` is likewise True for every such token (none is the interned
  `"--"` object), so identity coincides with inequality across the entire
  reachable domain. (The NotEq_Gt / NotEq_GtE siblings, which compare ORDERING
  rather than identity, ARE killed by `test_mkdir_single_path_sorting_before_
  dashdash` via the arg `"!dir"` that sorts before `"--"`.)

alias fast-path family ternary (L1069 `family if family != "unknown"
else "system"`) and read-only base-cmd family ternary (L1102, identical
expression):
- L1069: `NotEq|1069|1` (NotEq_Lt),
  `NotEq|1069|2` (NotEq_Gt),
  `NotEq|1069|3` (NotEq_IsNot).
- L1102: `NotEq|1102|1` (NotEq_Lt),
  `NotEq|1102|2` (NotEq_Gt),
  `NotEq|1102|3` (NotEq_IsNot).
  Both ternaries gate on `family != "unknown"`. `family =
  CLI_FAMILY_LOOKUP.get(base_cmd, "unknown")`, and the intersection of
  COMMAND_ALIASES (L1069's reachable base_cmds) with CLI_FAMILY_LOOKUP is
  empty, as is the intersection of READ_ONLY_BASE_CMDS (L1102's) with it —
  verified by enumerating both sets. So at BOTH branches `family` is always the
  literal `"unknown"`, the condition `family != "unknown"` is always False, and
  the ternary always yields `"system"`. The three surviving operators all
  preserve that False when LHS == RHS == `"unknown"`: `"unknown" < "unknown"`
  False, `"unknown" > "unknown"` False, `"unknown" is not "unknown"` False
  (interned literal). The observable cli_family stays `"system"`.
  (The AddNot siblings — which would flip the always-False condition to True
  and yield `"unknown"` — and the GtE / Eq siblings — `"unknown" >= "unknown"`
  is True — ARE killed; they were already killed by the existing alias/
  read-only result-field assertions.)

capability fast-path import guard (L1115 `_classify_capability is not None and
_is_capability_verb is not None`):
- `AndWithOr|1115|1` — AndWithOr. Both names are bound at module
  import from `capability_classes`, which always imports in-process (the
  `except ImportError` fallback is unreachable, per M1), so both operands are
  always non-None / True. `True and True` and `True or True` coincide; no
  reachable input makes either operand None. (Same unreachable-fallback
  reasoning as M1.)

capability intent branch (L1119 `cap.intent == _CAP_READ_ONLY`):
- `Eq|1119|1` (Eq_GtE) and
  `Eq|1119|2` (Eq_Is). `cap.intent` ranges over the
  two-element interned-literal domain `{"MUTATIVE", "READ_ONLY"}`
  (`CATEGORY_MUTATIVE` / `CATEGORY_READ_ONLY` in capability_classes).
  `_CAP_READ_ONLY == "READ_ONLY"`. Eq_Is: identity coincides with equality for
  interned literals. Eq_GtE: `"READ_ONLY" >= "READ_ONLY"` True (`==`);
  `"MUTATIVE" >= "READ_ONLY"` False (`'M' < 'R'`, `==` also False). Same truth
  table as `==` across the whole reachable domain.

single-token guard (L1154 `len(tokens) == 1`):
- `Eq|1154|1` — Eq_LtE (`len(tokens) <= 1`). The empty /
  whitespace-only command (`len(tokens) == 0`) is intercepted at the top of the
  function (L1013, `not command or not command.strip()`), so every input that
  reaches L1154 has `len(tokens) >= 1`. Over that range `<= 1` coincides with
  `== 1`. (The Lt / NumberReplacer siblings ARE killed.)

heredoc-guard positional comparison (L1240 `non_flag_tokens[0] == "-"`):
- `Eq|1240|1` (Eq_IsNot), `Eq|1240|2`
  (Eq_LtE), `Eq|1240|3` (Eq_GtE). The heredoc branch
  (Step 3c) is only reached when `_check_script_file` (Step 1d) returned None.
  An interpreter invocation with a positional first token that is NOT `"-"`
  (e.g. `python3 deploy.py <<EOF`) IS recognized as a script-file shape and
  returns at Step 1d, never reaching L1240. So on every input that DOES reach
  L1240, `non_flag_tokens[0]` is `"-"` (the stdin sentinel) — verified: a
  non-dash positional is intercepted upstream. With the operand fixed at `"-"`:
  Eq_LtE `"-" <= "-"` True, Eq_GtE `"-" >= "-"` True, both coincide with `==`.
  Eq_IsNot `"-" is not "-"` — the shlex-produced `"-"` is NOT the same object as
  the literal (confirmed `nft[0] is "-"` is False), so `is not "-"` is True,
  which still keeps the guard True exactly as `== "-"` does. All three preserve
  the always-True branch. (The three AndWithOr siblings on the `and` chain ARE
  killed by `test_stdin_dash_without_heredoc_not_inline_analyzed`, which drives
  a `"-"`-positional input with no `"<<"` so the chain re-association is
  observable.)

## Category M9 — detect_mutative_command Step-4 camelCase guard residuals (7)

Step 4's camelCase split (L1506-1511) reads
`raw_token = semantic_head_tokens_raw[semantic_index] if semantic_index <
len(semantic_head_tokens_raw) else token`, then gates the split on
`semantic_index == 1 and len(camel_parts) > 1 and
_is_subcommand_identifier(raw_token)`. The KILLABLE siblings (L1509 Eq_GtE,
L1510 first AndWithOr + NumberReplacer `1 -> 2`, L1511 second AndWithOr) are
killed by `TestCamelCaseSplitGuard`. The 7 below cannot be distinguished by any
reachable input.

raw-token index guard (L1506 `semantic_index < len(semantic_head_tokens_raw)`):
- `Lt|1506|1` (Lt_NotEq), `Lt|1506|2`
  (Lt_LtE), `Lt|1506|3` (Lt_IsNot). `semantic_head_tokens`
  and `semantic_head_tokens_raw` are built in lockstep in `analyze_command`
  (`non_flag_tokens` / `non_flag_tokens_raw` appended together, both prefixed
  with the base token, both sliced `[:head_size]`), so they ALWAYS have equal
  length. The loop variable `semantic_index` ranges over
  `semantic_head_tokens[1:]` indices, i.e. `1 .. len-1`, so `semantic_index <
  len(semantic_head_tokens_raw)` is unconditionally True and the `else token`
  fallback is dead code. `< len`, `!= len`, `<= len`, and `is not len` all
  coincide over the reachable range `idx in [1, len-1]` (they differ only at
  `idx == len`, never reached).

position comparison (L1509 `semantic_index == 1`):
- `Eq|1509|1` (Eq_LtE, `<= 1`). The loop starts at
  `start=1`, so `semantic_index >= 1` always. Over that range `== 1` and `<= 1`
  coincide (both True only at idx 1, both False at idx >= 2). (Eq_GtE, which
  would also fire at idx >= 2, IS killed by `test_camelcase_at_index_two_not_
  split`.)

len boundary (L1510 `len(camel_parts) > 1`):
- `Gt|1510|1` (Gt_NotEq, `!= 1`), `Gt|1510|2`
  (Gt_GtE, `>= 1`), `NumberReplacer|1510|1` (NumberReplacer `1 -> 0`,
  `> 0`). `split_camel_case` returns at least one element, so `len(camel_parts)
  >= 1` always; the three mutants differ from `> 1` only at `len == 1` (they all
  enter the loop where `> 1` skips it) and `!= 1` additionally at `len == 0`
  (impossible). At `len == 1` the single camel part equals the whole token
  lowercased; were that part a MUTATIVE verb it would already have matched at
  Step 4's primary check (L1439) BEFORE reaching the camelCase block, so the
  loop entered on a 1-part token never finds a mutative fragment — the result is
  identical whether the loop runs or is skipped. (The NumberReplacer `1 -> 2`
  sibling, `> 2`, DOES change a 2-part token's outcome and IS killed by
  `test_camelcase_two_part_at_index_one_split`.)

## Category M10 — detect_mutative_command Step-4b api-arm residuals (4)

Step 4b (L1581-1587) classifies an `api` subcommand with no explicit mutative
HTTP verb as an implicit-GET read-only call:
`not any(t in MUTATIVE_VERBS for t in semantic_head_tokens[1:]) and
len(semantic_head_tokens) > 1 and semantic_head_tokens[1] == "api"`. The
KILLABLE siblings (L1584 `[1:] -> [0:]`, L1586 NumberReplacer `1 -> 2`, L1587
Eq_LtE) are killed by `TestApiImplicitGetArm` + the existing
`test_gh_api_implicit_get`. The 4 below cannot be distinguished by any reachable
input.

membership-scan slice (L1584 `for t in semantic_head_tokens[1:]`):
- `NumberReplacer|1584|88` — NumberReplacer occ88 = `1 -> 2`
  (`head[2:]`). Step 4b is reached only AFTER the Step-4 verb loop (which scans
  `head[1:]`) finds no MUTATIVE verb; otherwise it returns at L1439. So at L1584
  NO token in `head[1:]` is in MUTATIVE_VERBS, making `not any(...)`
  unconditionally True over `[1:]`. `[2:]` is a subset of `[1:]`, so its scan is
  also empty of mutative verbs and `not any` stays True — the result is
  identical. (The `1 -> 0` sibling, `head[0:]`, ADDS the base token, which CAN
  be a mutative verb such as `post`, and IS killed by
  `test_mutative_base_cmd_before_api_blocks_arm`.)

head-length boundary (L1586 `len(semantic_head_tokens) > 1`):
- `Gt|1586|1` (Gt_NotEq, `!= 1`),
  `Gt|1586|2` (Gt_GtE, `>= 1`),
  `NumberReplacer|1586|91` (NumberReplacer occ91 = `1 -> 0`, `> 0`).
  This arm is reached only when `semantic_head_tokens[1]` exists (the third
  conjunct indexes `[1]`), and the single-token case (`len == 1`) returns far
  earlier at Step 2 (L1154). So every input reaching L1586 has `len >= 2`, where
  `> 1`, `!= 1`, `>= 1`, and `> 0` all coincide (all True). (The `1 -> 2`
  sibling, `> 2`, is False for a length-2 head and IS killed by
  `test_gh_api_bare_length_two_head`.)

## Category M11 — `_check_python_module_runner` index-walk residuals (3)

**NEW CODE** (Brief 91 AC-7). `_check_python_module_runner` recognizes
`<python> [interp-flags] -m <pkg-mgr> <args...>`, reads the module token by
walking `raw_tokens` from index 1, and re-dispatches the rewrite through
`detect_mutative_command`. After the re-init the helper has 71 mutants; 68 are
killed by `TestCheckPythonModuleRunner` in `test_mutative_verbs_mutants.py`
(reason-pinned: each test asserts the exact re-dispatch reason, which encodes
`base_cmd`, `module`, and — via `rest` — `module_idx`). The 3 below are
genuinely equivalent.

bounds guard `if i + 1 < len(raw_tokens):` (the `-m`-consumes-next check):
- `Lt|_check_python_module_runner:bounds|1` — Lt_NotEq (`i + 1 != len`).
- `Lt|_check_python_module_runner:bounds|2` — Lt_IsNot (`i + 1 is not len`).
  The loop is `for i in range(1, len(raw_tokens))`, so `i ∈ [1, len-1]` and
  `i + 1 ∈ [2, len]`. Over that range `i + 1 < len` is True exactly when
  `i + 1 != len` and (small ints interned) when `i + 1 is not len`; `i + 1`
  can never EXCEED `len`, so no reachable input separates the three. The
  killable siblings — Lt_LtE (`<= len` → reads `raw_tokens[len]` → IndexError),
  Lt_Gt, Lt_GtE, Lt_Eq, Lt_Is — flip the boundary at `i + 1 == len` (the `-m`
  is the last token) and are killed by
  `test_m_as_last_token_does_not_redispatch`.

None-guard `if module is None or module_idx is None:`:
- `ReplaceOrWithAnd|_check_python_module_runner:none-guard|1` — ReplaceOrWithAnd. `module` and
  `module_idx` are initialized `None` together and assigned together in the
  same `if` body (`module = raw_tokens[i + 1]; module_idx = i + 1`); there is
  no path where exactly one is `None`. So `A or B` and `A and B` coincide
  whenever `A == B`, which is always. The Is_IsNot siblings on each operand
  (`module is not None`, `module_idx is not None`) ARE killed by
  `test_non_package_module_returns_none_not_redispatched`.

(Arithmetic residuals: the LShift on the bounds `i + 1`, and the BitOr/BitXor
on the `module_idx + 1` slice start, coincide with `+1` only at `i == 1` /
`module_idx == 2`; they are killed by inputs that place `-m` / the module at
index 2 — `test_bounds_lshift_with_m_at_index_two_no_args`,
`test_rest_slice_bitop_with_module_idx_three`.)

---

## RE-INIT note (822-spec session)

The denominator table above (735 specs / 74 equivalents) is from the original
session. After re-init for the AC-7/AC-9 new code the session holds **822
specs**; baseline **82.60% (679 killed / 143 survived)**. The 74 old
equivalents retained their stable keys (matching operator + location + occurrence
pattern), ensuring they remain excluded across re-inits. One change: M10 L1624
`len(head) > 1` Gt_GtE is now **KILLED** by the re-init'd suite, so it left the
skip file; M11 added 3. Net: **75 equivalents** in `equivalents-mutative-verbs.skip`.
Grind-total closure holds: 0 untriaged survivors remain in `mutative_verbs.py`.

| Population (re-init) | Count |
|----------------------|------:|
| Total specs in DB | 822 |
| INCOMPETENT | 0 |
| Proven-equivalent excluded (M1–M11, skip file) | 75 |
| **Killable denominator** | **747** |

---

# Equivalent Mutants — `tiers.py` (GRIND-TOTAL, last module of the mutation network)

**Date:** 2026-06-27
**Branch:** `harden/approval-grants-m1-loop`
**Module:** `hooks/modules/security/tiers.py`
**Session DB:** `tiers-spike.sqlite` (87 specs)
**Baseline:** 91.95% (80 killed / 7 survived / 87 total).

**Closure:** of the 7 survivors, **1 killed** by an honest test in
`tests/hooks/modules/security/test_tiers_mutants.py`; **6 proven equivalent**
below and excluded via `equivalents-tiers.skip`.

**Killed survivor:**
- `ReplaceFalseWithTrue|82|1` — L82 `ReplaceFalseWithTrue` on the
  default argument `has_blocked_patterns=False` of
  `_classify_command_tier_cached`. **KILLED** by
  `TestTiersMutantClosure::test_cached_default_no_blocked_is_safe_by_elimination`:
  calling the cached classifier with the DEFAULT argument on an unknown,
  non-mutative, non-keyword command (`"some_unknown_command --flag"`) must
  return T0 (safe by elimination). With the mutated default `True`, the
  `if has_blocked_patterns: return T3` branch (L105) fires and the command is
  mis-classified T3. The public API `classify_command_tier` always passes
  `has_blocked` explicitly (L196), so the mutant is observable only via the
  cached function's documented internal contract — which the test exercises
  directly. Verified KILLED with `_probe_tiers_mutants.py 'ReplaceFalseWithTrue|82|1'`.

**Verification method:** every equivalent below was probed against the full
security test suite (with the closure test in place) via
`tests/evals/_probe_tiers_mutants.py <stable-key>` (per-mutant, deterministic — each
mutant applied alone in an isolated clone) and confirmed SURVIVED. The harness
`pytest -x` test-command is non-deterministic under sharding, so the per-mutant
probe is the authority, not a full run.

## Category T1 — `SecurityTier` / cached-classifier residuals (6 mutants)

### T1a — `requires_approval` comparison over the fixed enum domain (L40)

`requires_approval` (L40) is `return self == SecurityTier.T3_BLOCKED`. The
domain of `self` is exactly the 4 `SecurityTier` enum singletons
(`T0_READ_ONLY`, `T1_VALIDATION`, `T2_DRY_RUN`, `T3_BLOCKED`); the type system
guarantees no other value reaches it.

| Operator | Mutant | Why equivalent | Stable key |
|----------|--------|----------------|--------|
| Eq_Is | `self is T3_BLOCKED` | Enum members are singletons, so `is` reproduces `==` exactly. | `Eq\|40\|1` |
| Eq_GtE | `self >= T3_BLOCKED` | `SecurityTier(str, Enum)`; comparison is on the string values `"T0".."T3"`. `"T3"` is the maximum, so `self >= "T3"` is True iff `self == "T3"`. Identical truth table over all 4 members. | `Eq\|40\|2` |

**Verified:** for all 4 members, `==`, `is`, and `>=` against `T3_BLOCKED`
produce the identical truth table (False, False, False, True).

### T1b — `@lru_cache(maxsize=512)` transparency (L79, 2 mutants)

`_classify_command_tier_cached` is a pure function of
`(command, has_blocked_patterns)`. `lru_cache` is transparent memoization:
for identical arguments it returns identical results whether the cache is
present, absent, or sized differently. (Same as `mutative_verbs.py` M2.)

| Operator | Mutant | Why equivalent | Stable key |
|----------|--------|----------------|--------|
| RemoveDecorator | drop `@lru_cache` | Only forgoes memoization; every call recomputes the same `SecurityTier`. | `RemoveDecorator\|79\|1` |
| NumberReplacer | `maxsize=512`→N | Changes only the cache eviction capacity, never a return value. | `NumberReplacer\|79\|1` |

### T1c — public empty/whitespace guard masked by the cached guard (L178)

`classify_command_tier` L178: `if not command or not command.strip(): return T3`.
`ReplaceOrWithAnd` diverges from `or` only for whitespace-only input. With
`and`, whitespace (`"   "`) makes `not command` False → short-circuits to
False → NOT returned early → falls through to `command = command.strip()` →
`""`. The blocked-pattern loop then matches nothing, and
`_classify_command_tier_cached("")` hits its OWN (unmutated) L89 guard
`not command or not command.strip()` → returns T3. The observable result is
identical: whitespace is still T3. The cached guard masks the public guard.

| Function | Line | Operator | Stable key |
|----------|-----:|----------|--------|
| `classify_command_tier` | 178 | ReplaceOrWithAnd | `ReplaceOrWithAnd\|178\|1` |

(The L89 cached-guard `ReplaceOrWithAnd` IS killable and is killed by
`test_tiers.py::TestMutationBaselineSurvivors::test_cached_classifier_whitespace_is_t3`,
which calls the cached function directly with whitespace.)

### T1d — blocked-pattern loop `break`→`continue` no-op (L193)

`classify_command_tier` blocked-pattern loop (L190-193):
```python
for pattern in blocked_patterns:
    if pattern.search(command):
        has_blocked = True
        break   # <-- mutated to continue
```
Once a pattern matches, `has_blocked = True` is set. `ReplaceBreakWithContinue`
only changes whether the loop scans the remaining patterns; `has_blocked` stays
True regardless, and nothing else in the loop body depends on continuing. The
return value `_classify_command_tier_cached(command, has_blocked)` is identical
whether the loop breaks early or runs to completion.

| Function | Line | Operator | Stable key |
|----------|-----:|----------|--------|
| `classify_command_tier` | 193 | ReplaceBreakWithContinue | `ReplaceBreakWithContinue\|193\|1` |

---

## Exclusion mechanism — tiers.py (AC-1 denominator)

**Skip file:** `tests/evals/equivalents-tiers.skip` (6 stable keys, Category T1).

| Population | Count |
|------------|------:|
| Total specs in DB | 87 |
| Killed by honest tests (baseline + closure) | 81 |
| Proven-equivalent excluded (T1, skip file) | 6 |
| **Untriaged survivors** | **0** |

**Endpoint:** 0 untriaged survivors in `tiers.py`. 1 of the 7 spike survivors
killed by `test_tiers_mutants.py`; the other 6 proven equivalent (T1a–T1d) and
excluded via `equivalents-tiers.skip`. This closes the last module of the
security-core mutation network.
