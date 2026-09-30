# Memory — Examples

## Existing structured home

A session ends with task 42 still active. Reflection emits:

```text
SKIP “finish migration”: already owned by task 42 in plan 7.
```

Compact preserves `plan 7 / task 42` as a durable reference, not a copied
memory body.

## Exact best-effort batch

All three fall on the autonomous side of the exception boundary, so the
orchestrator adjudicates and executes three independent operations directly,
then reports one observed result for each:

1. append the week's run to the log `feedback_nightly_runs`;
2. close `feedback_old_warning`;
3. create `feedback_compact_duplication` under `gaia_system`.

Failure of operation 2 does not erase operation 1 or prevent independent
operation 3. No slug, scope, lifecycle, or body changes to make a failing
operation succeed.

## A preference changes

The user used to want a daily summary and now wants it weekly. The old row is
not edited: a new row states what holds now, and the link retires the old one.

```bash
gaia memory add --name=user_pref_weekly_summary --type=user \
  --description="Preferencia: resumen semanal, no diario." --body="..."
gaia memory link user_pref_weekly_summary user_pref_daily_summary --kind=supersedes
# -> user_pref_weekly_summary reemplaza a user_pref_daily_summary
gaia memory reclassify user_pref_daily_summary --class=log
```

The daily row leaves every injection and both stay visible in
`gaia memory show user_pref_weekly_summary --links` and `gaia memory story`.
The link works even when the old row still sits under the workspace it was
first written in.

## A rule that is a bug

The user asks that every goal remind specialists to load `code-standards`.
Would that make sense if Gaia worked perfectly? No: a specialist should apply
the standard without being told. So it becomes two rows:

1. a `feedback` thread under `gaia_system` naming the defect -- specialists do
   not apply `code-standards` in their first version -- with its evidence;
2. a user row, `Preferencia temporal: ...`, whose body names that bug and says
   it retires when the bug closes.

When the bug thread closes, the preference is closed with it.

## Atomic milestone

A release closes a meaningful project arc and leaves two independent follow-up
concerns. The checkpoint contains one milestone record and two pendings.
`gaia memory checkpoint` writes all rows and links or none; the operator does
not decompose it into a best-effort batch.

## Ambiguous input

The orchestrator requests “save this under the project” but supplies neither a
project nor a workspace. Choosing either changes attribution, so the operator
returns `NEEDS_INPUT` with the missing scope instead of inferring from cwd.
