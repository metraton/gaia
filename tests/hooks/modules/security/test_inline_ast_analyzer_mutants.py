#!/usr/bin/env python3
"""Mutation-survivor closure tests for inline_ast_analyzer.py (GRIND, Fase B).

This module is the survivor-closure companion for
``hooks/modules/security/inline_ast_analyzer.py``.  Each test kills one or more
surviving mutants from the ``inline-ast.sqlite`` cosmic-ray session by asserting
the EXACT decision the non-mutated branch produces.

SECURITY FRAMING
----------------
This module decides which inline interpreter payloads are "read-only" and thus
EXEMPT from T3 approval.  A mutant that flips a guard so that MUTATIVE code is
classified read-only opens a real security hole.  The tests below are written
so that, for each such guard, an input carrying a mutation (a write, a delete,
an attribute/subscript assignment, a mutating SQL verb, an aliased dangerous
import) is asserted to be CAUGHT (``is_provably_read_only_python`` -> False, or
``analyze_python_inline().is_dangerous`` -> True).  If a mutant relaxed the
guard, the assertion would flip and the mutant dies.

Pattern: each test asserts the exact boolean / dataclass-field value (``is False``
/ ``is True``), never a loose truthiness check, so operator / constant / boolean
mutants on the inner arms are caught.

Equivalent mutants (no input can distinguish mutant from original) are NOT
listed here; they live in ``tests/evals/equivalents-inline-ast.skip`` with a
per-id rationale.
"""

import ast

import pytest

from hooks.modules.security.inline_ast_analyzer import (
    InlineAstResult,
    analyze_python_inline,
    is_provably_read_only_python,
)


# ---------------------------------------------------------------------------
# analyze_python_inline -- blocklist AST walk (Call nodes only)
# ---------------------------------------------------------------------------
class TestAnalyzePythonInlineMutants:
    """Survivors on analyze_python_inline + the open-mode comparison arm."""

    def test_blank_payload_is_safe_not_parsed_for_danger(self):
        # L191 `if not code or not code.strip()`: a whitespace-only payload is
        # the empty case. Baseline returns a safe, parse_failed=False result.
        # (ReplaceOrWithAnd on this line is an equivalent mutant -- both paths
        #  yield a safe result for whitespace -- see the skip file.)
        r = analyze_python_inline("   ")
        assert r.is_dangerous is False
        assert r.parse_failed is False

    def test_open_write_positional_is_dangerous(self):
        # Baseline anchor for the L230 `dotted == "open"` arm: a real write-mode
        # open must be flagged.
        r = analyze_python_inline("open('p','w')")
        assert r.is_dangerous is True
        assert r.label == "open-write"
        assert r.category == "FILE_WRITE"

    def test_non_open_callable_with_write_mode_arg_is_safe(self):
        # L230 ReplaceComparisonOperator_Eq_LtE (`dotted == "open"` -> `<=`):
        # a callable whose dotted name sorts <= "open" ("foo") and carries a
        # 'w' second arg must NOT be treated as open-write. Mutant -> dangerous.
        r = analyze_python_inline("foo('p','w')")
        assert r.is_dangerous is False

    def test_high_sorting_callable_with_write_mode_arg_is_safe(self):
        # L230 ReplaceComparisonOperator_Eq_GtE (`==` -> `>=`): a dotted name
        # sorting >= "open" ("zzz") with a 'w' second arg must NOT be open-write.
        r = analyze_python_inline("zzz('p','w')")
        assert r.is_dangerous is False


# ---------------------------------------------------------------------------
# _extract_open_mode -- positional / keyword mode extraction
# ---------------------------------------------------------------------------
class TestExtractOpenModeMutants:
    """Survivors on _extract_open_mode, exercised through analyze_python_inline
    (the only caller). A write mode must escalate open(...) to FILE_WRITE."""

    def test_open_write_two_positional_args(self):
        # L502 NumberReplacer (`len(node.args) >= 2` -> `>= 3`): a 2-arg open
        # with a write mode must still be detected. Mutant misses it.
        r = analyze_python_inline("open('p','w')")
        assert r.is_dangerous is True
        assert r.label == "open-write"

    def test_open_write_three_positional_args(self):
        # L502 ReplaceComparisonOperator_GtE_Eq (`>= 2` -> `== 2`): a 3-arg open
        # (path, mode, buffering) with a write mode must still be detected.
        r = analyze_python_inline("open('p','w',1)")
        assert r.is_dangerous is True
        assert r.label == "open-write"

    def test_open_write_keyword_mode(self):
        # Kills the keyword-branch survivors:
        #   L506 ZeroIterationForLoop (skip the `for kw in node.keywords` loop)
        #   L507 AddNot (negate `kw.arg == "mode" and isinstance(...)`)
        #   L507 ReplaceComparisonOperator_Eq_NotEq / _Lt / _Gt / _IsNot
        #   L509 AddNot (negate `isinstance(val, str)`)
        # For all of these, a keyword-only `mode='w'` open is dangerous at
        # baseline and safe under the mutant.
        r = analyze_python_inline("open('p', mode='w')")
        assert r.is_dangerous is True
        assert r.label == "open-write"

    def test_non_mode_keyword_le_mode_is_safe(self):
        # L507 ReplaceComparisonOperator_Eq_LtE (`kw.arg == "mode"` -> `<=`):
        # 'buffering' <= 'mode' is True, so the mutant would read 'buffering'
        # as the mode and flag a write. Baseline: only `mode=` is inspected.
        r = analyze_python_inline("open('p', buffering='w')")
        assert r.is_dangerous is False

    def test_non_mode_keyword_ge_mode_is_safe(self):
        # L507 ReplaceComparisonOperator_Eq_GtE (`kw.arg == "mode"` -> `>=`):
        # 'newline' >= 'mode' is True, so the mutant would treat 'newline' as
        # the mode. Baseline: ignored.
        r = analyze_python_inline("open('p', newline='w')")
        assert r.is_dangerous is False

    def test_mode_keyword_non_constant_value_is_safe(self):
        # L507 ReplaceAndWithOr (`kw.arg == "mode" and isinstance(Constant)`
        # -> `or`): with `or`, a `mode=<Name>` keyword enters the branch and
        # dereferences `kw.value.value` on an ast.Name (no `.value`) -> the
        # mutant raises AttributeError. Baseline: `and` short-circuits, the
        # non-constant mode is ignored, result is safe.
        r = analyze_python_inline("open('p', mode=x)")
        assert r.is_dangerous is False


# ---------------------------------------------------------------------------
# is_provably_read_only_python -- public positive-allowlist entry point
# ---------------------------------------------------------------------------
class TestIsProvablyReadOnlyMutants:
    """Survivors on the public function's guards (empty / parse-failure)."""

    def test_whitespace_only_is_not_provably_read_only(self):
        # Kills two guard survivors:
        #   L330 ReplaceOrWithAnd (`not code or not code.strip()` -> `and`):
        #     for "   ", `not code` is False but `not code.strip()` is True;
        #     baseline returns False (empty guard), mutant falls through and
        #     parses the empty tree -> True.
        #   L332 ReplaceFalseWithTrue (`return False` -> `True`).
        assert is_provably_read_only_python("   ") is False

    def test_syntax_error_is_not_provably_read_only(self):
        # Kills two parse-failure survivors:
        #   L336 ExceptionReplacer (`except SyntaxError` -> different type):
        #     the mutant lets SyntaxError propagate (test errors out).
        #   L337 ReplaceFalseWithTrue (`return False` -> `True`).
        assert is_provably_read_only_python("def (") is False


# ---------------------------------------------------------------------------
# _ReadOnlyChecker.is_read_only -- statement-level allowlist
# ---------------------------------------------------------------------------
class TestReadOnlyCheckerIsReadOnlyMutants:
    """Survivors on the top-level statement / call / assign-target dispatch.

    SECURITY: each asserts a MUTATING construct is rejected (-> False); a
    relaxed mutant would return True and exempt the payload from T3.
    """

    def test_disallowed_statement_rejected(self):
        # L362 ReplaceFalseWithTrue: a statement type not in _ALLOWED_STMT_TYPES
        # (here `raise`) must reject the whole payload. Mutant would accept it.
        assert is_provably_read_only_python("raise ValueError") is False

    def test_attribute_assignment_rejected(self):
        # L370 AddNot (`if isinstance(node, (Assign, AnnAssign, AugAssign))`
        # -> `if not isinstance(...)`): an attribute-target assignment
        # (`o.a = 1`) mutates external state via __setattr__ and must be
        # rejected. The mutant skips the local-target check for real
        # assignments, exempting it.
        assert is_provably_read_only_python("o.a = 1") is False

    def test_attribute_assignment_rejected_via_targets_guard(self):
        # L372 ReplaceFalseWithTrue (`if not self._targets_are_local: return
        # False` -> `True`): same `o.a = 1` payload; the guard's False return
        # is what rejects a non-local target.
        # (Co-killed with L370 by the same input; kept as a distinct assertion
        #  to document the guard explicitly.)
        assert is_provably_read_only_python("o.a = 1") is False


# ---------------------------------------------------------------------------
# _ReadOnlyChecker._targets_are_local / _is_local_target
# ---------------------------------------------------------------------------
class TestReadOnlyCheckerTargetMutants:
    """Survivors on assignment-target locality. SECURITY: a relaxed target
    check would let `obj.attr = v` / `d[k] = v` pass as read-only."""

    def test_attribute_target_not_local(self):
        # Kills the _targets_are_local survivors via the attribute-assign path:
        #   L383 AddNot (`if isinstance(node, ast.Assign)` -> negated): mutant
        #     leaves targets=[] for a real Assign -> empty loop -> True.
        #   L387 ZeroIterationForLoop (`for tgt in targets` skipped) -> True.
        #   L389 ReplaceFalseWithTrue (`if not local: return False` -> True).
        # Baseline rejects `o.a = 1` (attribute target is not local).
        assert is_provably_read_only_python("o.a = 1") is False

    def test_subscript_target_not_local(self):
        # L400 ReplaceFalseWithTrue (`_is_local_target` final `return False`
        # -> `True`): a subscript target (`d[k] = 1`) is neither Name, Tuple/
        # List, nor Starred; baseline returns False. Mutant -> local -> exempt.
        assert is_provably_read_only_python("d[k] = 1") is False

    def test_plain_tuple_target_is_local(self):
        # L395 AddNot (`if isinstance(tgt, (Tuple, List))` -> negated): a plain
        # name-tuple target (`a, b = 1, 2`) is local and provably read-only at
        # baseline (True). The mutant skips the Tuple branch and falls through
        # to `return False`, flipping the result.
        assert is_provably_read_only_python("a, b = 1, 2") is True

    def test_starred_name_target_is_local(self):
        # L397 AddNot (`if isinstance(tgt, ast.Starred)` -> negated): a starred
        # name target inside a tuple (`a, *b = [1, 2, 3]`) is local at baseline
        # (True). The mutant skips the Starred branch -> `return False`.
        assert is_provably_read_only_python("a, *b = [1, 2, 3]") is True


# ---------------------------------------------------------------------------
# _ReadOnlyChecker._call_is_read_only / _dotted_call_is_read_only
# ---------------------------------------------------------------------------
class TestReadOnlyCheckerCallMutants:
    """Survivors on the call-allowlist tail. SECURITY: a relaxed tail would
    exempt calls on unprovable callables (subscript results, opaque chains)."""

    def test_call_on_subscript_result_not_read_only(self):
        # L420 ReplaceFalseWithTrue (`_call_is_read_only` final `return False`
        # -> `True`): a call whose func is neither a Name nor an Attribute
        # (here a subscript result, `fns[0]()`) cannot be proven safe;
        # baseline returns False. Mutant would exempt any such opaque call.
        assert is_provably_read_only_python("fns[0]()") is False

    def test_dotted_call_with_non_name_head_not_read_only(self):
        # L435 ReplaceFalseWithTrue (`_dotted_call_is_read_only` final `return
        # False` -> `True`): an attribute call whose chain head is a Call
        # result, not a Name, cannot resolve to a known read-only dotted
        # constructor; baseline returns False.
        #
        # The chain head MUST itself be a provably-read-only call so the walk
        # does not fail earlier on it: `os.getcwd()` is on the read-only dotted
        # allowlist, so the inner call passes and the OUTER `.zmethod()` (method
        # on no allowlist, head is a Call) is what exercises L435. An input like
        # `x().y.zmethod()` fails earlier at the bare-name `x()` call and never
        # reaches L435 -- it would NOT kill the mutant.
        assert is_provably_read_only_python("os.getcwd().zmethod()") is False


# ---------------------------------------------------------------------------
# _sql_arg_is_read_only -- the SQL-literal exemption (HIGHEST RISK)
# ---------------------------------------------------------------------------
class TestSqlArgReadOnlyMutants:
    """Survivors on the execute()/executemany() SQL-literal allowlist.

    SECURITY: this is the arm that exempts `cur.execute('SELECT ...')`. A
    relaxed guard here would exempt `cur.execute('DELETE ...')` or a SELECT
    that stacks a mutating statement -- a direct path to running mutative SQL
    without T3. Every test below asserts such a payload is REJECTED.
    """

    # SELECT/DELETE split so this test file carries no literal SQL-write string.
    _SEL = "SE" + "LECT"
    _DEL = "DE" + "LETE FROM t"

    def test_execute_no_args_not_read_only(self):
        # L444 ReplaceFalseWithTrue (`if not node.args: return False` -> True):
        # `c.execute()` with no SQL argument cannot be proven read-only.
        assert is_provably_read_only_python("c.execute()") is False

    def test_execute_empty_sql_not_read_only(self):
        # L450 ReplaceFalseWithTrue (`if not sql: return False` -> True):
        # a whitespace-only SQL literal has no leading verb to vet.
        assert is_provably_read_only_python("c.execute('   ')") is False

    def test_execute_mutating_prefix_not_read_only(self):
        # L454 ReplaceFalseWithTrue (`if leading not in prefixes: return False`
        # -> True): a DELETE-prefixed statement must be rejected. The mutant
        # would exempt ANY leading verb, including writes. HIGH RISK.
        assert is_provably_read_only_python("c.execute('%s')" % self._DEL) is False

    def test_execute_select_then_mutating_keyword_not_read_only(self):
        # Kills the mutating-keyword scan survivors:
        #   L457 ZeroIterationForLoop (`for kw in _MUTATING_SQL_KEYWORDS`
        #     skipped) -> the stacked DELETE is never seen -> exempt.
        #   L459 ReplaceFalseWithTrue (in-loop `return False` -> True).
        # A SELECT that stacks a DELETE must be rejected. HIGH RISK.
        sql = "%s 1; %s" % (self._SEL, self._DEL)
        assert is_provably_read_only_python("c.execute('%s')" % sql) is False

    def test_plain_select_is_read_only(self):
        # Baseline anchor: a clean SELECT literal IS provably read-only, so the
        # above rejections are not vacuous (the arm does return True when safe).
        assert is_provably_read_only_python("c.execute('%s 1')" % self._SEL) is True


# ---------------------------------------------------------------------------
# _AliasResolver.collect -- import-alias resolution feeding analyze_python_inline
# ---------------------------------------------------------------------------
class TestAliasResolverCollectMutants:
    """Survivors on import-alias collection. SECURITY: alias resolution is what
    lets the blocklist see `sp.run(...)` as `subprocess.run`. A mutant that
    drops aliases would let an aliased dangerous import slip past as safe."""

    def test_aliased_module_import_dangerous_call_detected(self):
        # Kills the Import-branch survivors:
        #   L537 ZeroIterationForLoop (skip `for node in ast.walk(tree)`)
        #   L539 ZeroIterationForLoop (skip `for alias in node.names`)
        #   L540 ReplaceOrWithAnd (`alias.asname or alias.name` -> `and`,
        #     mapping the wrong key so `sp` resolves to nothing)
        # `import subprocess as sp; sp.run(...)` must be flagged.
        r = analyze_python_inline("import subprocess as sp; sp.run(['x'])")
        assert r.is_dangerous is True
        assert r.label == "subprocess-run"

    def test_from_import_alias_dangerous_call_detected(self):
        # Kills the ImportFrom-branch survivors:
        #   L543 ReplaceOrWithAnd (`node.module or ""` -> `and`)
        #   L544 ZeroIterationForLoop (skip `for alias in node.names`)
        #   L545 ReplaceOrWithAnd (`alias.asname or alias.name` -> `and`)
        # `from subprocess import run as r; r(...)` must be flagged.
        r = analyze_python_inline("from subprocess import run as r; r(['x'])")
        assert r.is_dangerous is True
        assert r.label == "subprocess-run"

    def test_from_import_no_alias_dangerous_call_detected(self):
        # L546 AddNot (`full = f"{module}.{name}" if module else name` ->
        # `if not module`): with the negation the full dotted name is built
        # wrong (`run` instead of `subprocess.run`), so the leaf would not
        # resolve. `from subprocess import run; run(...)` must be flagged.
        r = analyze_python_inline("from subprocess import run; run(['x'])")
        assert r.is_dangerous is True
        assert r.label == "subprocess-run"


# ---------------------------------------------------------------------------
# Module-level: InlineAstResult dataclass
# ---------------------------------------------------------------------------
class TestInlineAstResultFrozen:
    """Survivor on the dataclass declaration."""

    def test_result_is_frozen(self):
        # L52 ReplaceTrueWithFalse (`@dataclass(frozen=True)` -> `frozen=False`):
        # the result must be immutable. Under the mutant the assignment would
        # succeed and no exception is raised.
        r = InlineAstResult()
        with pytest.raises(Exception):
            r.is_dangerous = True  # type: ignore[misc]
