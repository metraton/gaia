# Memory — Examples

## Existing structured home

A session ends with task 42 still active. Reflection emits:

```text
SKIP “finish migration”: already owned by task 42 in plan 7.
```

Compact preserves `plan 7 / task 42` as a durable reference, not a copied
memory body.

## Exact best-effort batch

All three fall on the autonomous side of the exception boundary (`append`,
`reclassify`, and `add` on a `feedback` row), so the orchestrator adjudicates
and executes three independent operations directly, then reports:

1. append evidence to `decision_router_source`;
2. close `feedback_old_warning`;
3. create `feedback_compact_duplication` under `gaia_system`.

`gaia-operator` executes them in order and returns one observed result for
each. Failure of operation 2 does not erase operation 1 or prevent independent
operation 3. It never changes a slug, scope, lifecycle, or body to make a
failing operation succeed.

## Atomic milestone

A release closes a meaningful project arc and leaves two independent follow-up
concerns. The checkpoint contains one milestone record and two pendings.
`gaia memory checkpoint` writes all rows and links or none; the operator does
not decompose it into a best-effort batch.

## Ambiguous input

The orchestrator requests “save this under the project” but supplies neither a
project nor a workspace. Choosing either changes attribution, so the operator
returns `NEEDS_INPUT` with the missing scope instead of inferring from cwd.
