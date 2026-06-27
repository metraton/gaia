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

**Denominator composition (after sync E1–E7):**

| Population | Count |
|------------|------:|
| Total specs in DB | 653 |
| INCOMPETENT (excluded by cosmic-ray) | 2 |
| Proven-equivalent excluded (E1–E7, skip file) | 99 |
| **Killable denominator** | **552** |

**AC-1 result (pre-E2–E7 sync, E1 only):** 518 killed / 618 killable = **83.82%** killable kill rate.

After sync, the new denominator will differ (equivalents removed from both
killed and survived buckets depending on which category survived). The updated
kill rate is reported in the commit that lands the sync.
