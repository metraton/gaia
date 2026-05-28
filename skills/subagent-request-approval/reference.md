# Subagent Request Approval -- Reference

Deep mechanics behind emitting an approval request. Read on-demand. All anchors
are to the DB-backed flow (`hooks/modules/tools/bash_validator.py`,
`hooks/modules/security/approval_grants.py`, `gaia/approvals/`).

## Where the payload comes from

You never construct the `sealed_payload` or compute its fingerprint. When the
hook blocks a T3 Bash command in a subagent context, `bash_validator` builds the
payload from the intercepted command and calls
`gaia.approvals.store.insert_requested()`, which:

1. generates the `approval_id` (`P-{uuid4hex}`),
2. computes the fingerprint, and
3. writes the `REQUESTED` event to the DB.

The block message you receive (`[T3_BLOCKED] ...`) ends with `approval_id: P-{...}`.
You relay that token plus the operation details; the orchestrator re-derives the
fingerprint from the DB row.

Source: `bash_validator._build_sealed_payload()`, the subagent block path in
`bash_validator._validate_single_command()`; `gaia/approvals/store.py`
`insert_requested()`.

## sealed_payload -- the 7 fields the hook stores

These are exactly what `_build_sealed_payload()` emits. Your `approval_request`
mirrors them so the orchestrator presents without re-authoring.

```json
{
  "operation":     "string -- e.g. 'MUTATIVE command intercepted: push'",
  "exact_content": "string -- the verbatim blocked command",
  "scope":         "string -- command.split()[0]: the leading CLI/resource token",
  "risk_level":    "string -- 'high' when DESTRUCTIVE, else 'medium'",
  "rollback_hint": "string | null -- null when the hook computed no inverse",
  "rationale":     "string -- why this T3 requires approval",
  "commands":      ["array of strings -- [exact_content] for a single command"]
}
```

Precision notes:
- The field is `rollback_hint` (not `rollback`). In `approval_request` you expose
  it under the key `rollback`, but the stored field is `rollback_hint`.
- `commands` is an **array of strings**, not a single string. For a single
  intercepted command the hook sets `commands = [exact_content]`.
- `risk_level` is only ever `high` or `medium` -- the hook never emits `low` or
  `critical`.

## Hook block flow detail

When the hook blocks, the deny message ends with an `approval_id` tied to exactly
this command in the DB. `bash_validator` reuses the same `approval_id` when an
identical pending already exists (`bash_validator._find_pending_in_db()`) -- but
do not rely on that as a retry strategy. Each fresh attempt of a not-yet-pending
command generates a new token. Emit `APPROVAL_REQUEST`, stop, wait.

## Grant lifecycle after approval

The grant that activates is **single-use, scoped to a semantic signature** for
this command and session. The ElicitationResult hook
(`approval_grants.activate_db_pending_by_prefix()`) writes
`SHOWN` + `APPROVED` events, flips the approval to `approved`, and inserts a
`SCOPE_SEMANTIC_SIGNATURE` grant row into `approval_grants` (status `PENDING`).

On your retry, `check_approval_grant()` matches it and immediately consumes it
(`gaia/store/writer.py` `consume_db_semantic_grant()`, status -> `CONSUMED`). A second attempt within the
TTL will NOT match -- the grant is gone. This is replay protection by design;
re-approve if you need to run the command again.

## Batch / COMMAND_SET -- designed, not wired

The legacy `verb_family` multi-use grant was removed (see module docstring in
`approval_grants.py`: "The legacy verb_family path has been removed"). Its intended
replacement is the `COMMAND_SET` grant -- an explicit list of `{command, rationale}`
items, each matched byte-for-byte and consumed individually
(`approval_grants.create_command_set_grant()`; `approval_grants.match_command_set_grant()`).

**Current state:** only the CHECK side is wired. `bash_validator._validate_single_command()`
calls `match_command_set_grant()` and consumes a matched item, but **no
production path calls `approval_grants.create_command_set_grant()`** -- it exists only in the
module and its tests. The activation paths only call
`approval_grants.activate_db_pending_by_prefix()`, which creates a single-use
`SCOPE_SEMANTIC_SIGNATURE` grant.

**Consequence:** a `batch_scope` field in `approval_request`, or the word "batch"
in a label, does nothing. Each blocked command produces its own single-use
semantic grant and its own approval. For a sweep of N commands, expect N
approvals until the COMMAND_SET create path is wired.

## Status to emit -- with vs without approval_id

Always `plan_status: "APPROVAL_REQUEST"`. The presence of `approval_id` tells the
orchestrator which path:

- **With `approval_id`** -- the hook blocked; orchestrator validates the
  fingerprint and activates the grant on user approval.
- **Without `approval_id`** -- plan-first (you are presenting a T3 plan before
  attempting); the orchestrator gates on user consent before any execution.

## Examples

### Blocked git push

Hook block message:
```
[T3_BLOCKED] MUTATIVE command intercepted: push ... approval_id: P-a1b2c3d4e5f6...
```

Emitted contract fragment:
```json
"approval_request": {
  "operation":     "MUTATIVE command intercepted: push",
  "exact_content": "git push origin main",
  "scope":         "git",
  "risk_level":    "medium",
  "rollback":      "git revert a1b2c3..d4e5f6",
  "verification":  "git log origin/main shows the 2 new commits",
  "approval_id":   "P-a1b2c3d4e5f6..."
}
```

### Drift trap

Approved `exact_content`: `rm /path/to/file-A`. On retry you run
`rm /path/to/file-B` (different path) -> different semantic signature -> re-blocked
with a fresh `approval_id`. Fix: retry the literal approved command; if the target
genuinely changed, attempt the new command and relay the new `approval_id`.
