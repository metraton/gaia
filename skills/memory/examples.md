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
gaia memory add --name=user_pref_weekly_summary --type=user --kind=preference \
  --description="Reports: send the summary weekly, on Monday." --body="..."
gaia memory link user_pref_weekly_summary user_pref_daily_summary --kind=supersedes
# -> user_pref_weekly_summary supersedes user_pref_daily_summary
gaia memory reclassify user_pref_daily_summary --class=log
```

The daily row leaves every injection and both stay visible in
`gaia memory show user_pref_weekly_summary --links` and `gaia memory story`.
The link works even when the old row still sits under the workspace it was
first written in, because both are user preferences; a link from a project
preference to a user one is refused, since the project's row already wins
where it applies.

## A tool built for him

The orchestrator has a specialist build `ghx`, a launcher that runs `gh` with
the account pinned per command. The same turn it lands on PATH, its row is
written on the user:

```bash
gaia memory add --name=user_tool_ghx --type=user --kind=tool \
  --binary=ghx --replaces=gh \
  --description="ghx -C <tree> pr create --repo <owner/repo> --head <branch>" \
  --body="Pins the GitHub account per command instead of switching it..."
```

Every session and every specialist now sees
`tool=ghx "ghx -C <tree> pr create ..." replaces=gh ref=memory:user/user_tool_ghx`
in Environment, and the hooks judge `ghx repo delete` exactly as
`gh repo delete`. Saved as a preference, it would have reached the
orchestrator alone and the hooks would not know what it wraps.

## A rule that is a bug

The user asks that every goal remind specialists to load `code-standards`.
Would that hold for any user? Yes: every user's specialists should apply the
standard without being told, so it is no difference of his. It becomes a
`kind=defect` thread under `gaia_system` naming the defect -- specialists do
not apply `code-standards` in their first version -- with its evidence, and no
user row.

If he wants the reminder until the fix lands, that bridge is a default he has
agreed to bend: a user preference written with
`--until memory:gaia/<defect slug>`, so it is injected only while that defect
is open and leaves the day it closes.

## Atomic milestone

A release closes a meaningful project arc and leaves two independent follow-up
concerns. The checkpoint contains one milestone record and two pendings.
`gaia memory checkpoint` writes all rows and links or none; the operator does
not decompose it into a best-effort batch.

## Ambiguous input

The orchestrator requests “save this under the project” but supplies neither a
project nor a workspace. Choosing either changes attribution, so the operator
returns `NEEDS_INPUT` with the missing scope instead of inferring from cwd.
