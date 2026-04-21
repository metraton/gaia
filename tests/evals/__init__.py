"""Agent eval framework -- context-evals layer.

This package is the acceptance layer between "infrastructure works" (unit
tests under ``tests/system`` and ``tests/integration``) and "agents behave
correctly in production".

Structure:

- :mod:`tests.evals.runner` dispatches a task to an agent and captures the
  response (stdout + optional session transcript + audit log slice).
- :mod:`tests.evals.graders` validates responses: keyword match
  (``code_grader``), ``json:contract`` block shape (``contract_grader``),
  and tool-call trace assertions (``tool_trace_grader``).
- :mod:`tests.evals.reporter` persists grading results as JSON artifacts
  under ``tests/evals/results/`` with a timestamped filename.
- :mod:`tests.evals.catalog` loads YAML catalogs of cases into
  parametrizable :class:`CaseModel` objects.

T1 (this task) provides stubs and the directory scaffold. Real logic for
session capture, the new graders, and the catalog loader lands in
subsequent tasks (T2-T5). See
``.claude/project-context/briefs/open_context-evals/plan.md`` for the full
task breakdown.
"""
