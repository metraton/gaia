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
You relay that token plus the operation details. Approvals are in-loop and
single-session: the orchestrator presents from your relay in the same
turn/session -- there is no per-turn or cross-session resurfacing of pendings.
Payload integrity is enforced at grant activation (`verify_fingerprint`), so the
orchestrator never dispatches to verify or derive your request.

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

## Batch / COMMAND_SET -- wired

The legacy `verb_family` multi-use grant was removed (see module docstring in
`approval_grants.py`: "The legacy verb_family path has been removed"). Its
replacement is the `COMMAND_SET` grant -- an explicit list of `{command, rationale}`
items, each matched byte-for-byte and consumed individually
(`approval_grants.create_command_set_grant()`; `approval_grants.match_command_set_grant()`).
All three sides are now wired end-to-end -- **intake**, **activation**, and
**consume** -- so one consent covers N commands.

**Intake -- at the block, from a chain you attempted.** There is no
plan-first declaration step. You attempt a **single Bash call that chains >= 2
T3 sub-commands** (e.g. `git add -A && git commit -m 'v1.2.0' && git push
origin main`); the hook's compound-command classifier
(`bash_validator._validate_compound_command`) runs a non-minting pre-pass
(`_is_ungranted_t3_component`) over the chain's components, and when **>= 2**
of them are ungranted T3, it calls `decide_t3_outcome(command_set=chain_set)`,
which builds a sealed_payload carrying the `command_set` key (mirroring the
shape `_build_sealed_payload()` emits for a single command) and calls
`gaia.approvals.store.insert_requested()` -- minting **exactly ONE** pending
`COMMAND_SET` approval with one `approval_id`, denied with the **same
`[T3_BLOCKED]` shape as a singular block**. A chain with `<= 1` ungranted-T3
component is not a batch: the per-component singular path owns it (no
COMMAND_SET is ever minted for one command).

**The COMMAND_SET `approval_id` is content-derived, not uuid4.** Like the
singular hook-block path (which mints `P-{uuid4hex}`), the chain-intake path
derives the id from the command_set content via
`gaia.approvals.store.derive_command_set_id()`:
`P-<first 32 hex of sha256(canonical(post-filter command strings))>`. It then
passes that id to `insert_requested(..., approval_id=...)` as the pending row id.
The point is reproducibility without a fragile uuid4: a retry of the identical
chain reproduces the identical id, so `insert_requested`'s fingerprint dedup
reuses the same pending instead of minting a duplicate. Because the id arrives
in the same `[T3_BLOCKED]` denial you relay for any T3 command, you never
compute, derive, or reproduce it -- you relay whatever `approval_id` the block
gives you, exactly like the singular path. The id is
**order-sensitive** (the consume side matches positionally) and **content-only**
(rationale/session/agent are not folded in, so both sides agree from the command
list alone). Idempotency follows the existing fingerprint dedup: two identical
command sets map to one id.

**Envelope shape.** The sealed_payload the chain-intake writes carries a
`command_set` key holding the chain's components (`rationale` is always `""` --
it is built by the hook from the chain, not authored by the subagent), and
`commands` listing every command string in the set:

```json
{
  "operation": "MUTATIVE command intercepted: push",
  "exact_content": "git add -A",
  "commands": ["git add -A", "git commit -m 'v1.2.0'", "git push origin main"],
  "command_set": [
    {"command": "git add -A",             "rationale": ""},
    {"command": "git commit -m 'v1.2.0'", "rationale": ""},
    {"command": "git push origin main",   "rationale": ""}
  ]
}
```

**Activation -- one consent, one grant.** When the user approves, the
ElicitationResult hook (`approval_grants.activate_db_pending_by_prefix()`)
detects the `command_set` and branches to `approval_grants.create_command_set_grant()`,
which inserts a single `COMMAND_SET` grant row into `approval_grants`
(status `PENDING`, `command_set_json` holding the whole set). The grant TTL is
**5 minutes** (`DEFAULT_COMMAND_SET_TTL_MINUTES`), aligned to the singular
active-grant TTL. Approving is the order to execute -- the orchestrator
re-dispatches the batch immediately.

**Consume -- item by item, replay-protected.** On each retry,
`bash_validator._validate_single_command()` calls `match_command_set_grant()`,
which finds the matching command's index byte-for-byte and returns it; the
validator then calls `mark_command_set_item_consumed()` **at match, before the
command executes**, appending that index to `consumed_indexes_json`. A consumed
index never matches again (replay protection), and when every index is consumed
the grant flips to `CONSUMED`. Wrapping an approved command -- adding `cd`, a
redirect, a pipe, or a flag -- produces a different string and matches nothing
in the set; it requires fresh approval.

**Consequence:** for a set of N related T3 commands that genuinely belong
together, chain them into one Bash call, let the hook block the chain, relay
the `approval_id` from that block, and the user approves once. Each command
runs on its own retry, single-use, within the 5-minute window.

## Status to emit -- with vs without approval_id

Always `agent_state: "APPROVAL_REQUEST"`. The presence of `approval_id` tells the
orchestrator which path:

- **With `approval_id`** -- the hook blocked a command, singular or a
  COMMAND_SET chain; either way the orchestrator presents from your same-turn
  relay (approvals are in-loop, single-session -- no later-turn resurfacing).
  A singular block activates a single-use semantic grant on user approval
  (fingerprint checked at activation), consumed at match. A COMMAND_SET block
  (`command_set` of >= 2 items alongside the `approval_id`) activates one
  `COMMAND_SET` grant covering the whole chain; each sub-command is consumed
  byte-for-byte at its own match. See "Batch / COMMAND_SET -- wired" above.
- **Without `approval_id` and without a `command_set`** -- you are presenting
  one T3 plan before attempting; the orchestrator gates on user consent before
  any execution. There is no state where a `command_set` appears without an
  `approval_id` -- batching is always minted at the block, never declared
  ahead of an attempt.

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
