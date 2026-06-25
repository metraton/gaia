# Agent Approval Protocol -- Reference

Deep mechanics of the approval handoff. Read on-demand. Anchors are to symbols
in `gaia/approvals/` and `hooks/modules/security/approval_grants.py`.

## Fingerprint canonicalization

`chain.canonical_payload()` serializes with
`json.dumps(payload, sort_keys=True, separators=(',', ':'))`.
`chain.fingerprint_payload()` takes `SHA-256` of the UTF-8 bytes of that
canonical string. `store.insert_requested()` stores both the canonical JSON
(`payload_json`) and the hex fingerprint on the `approvals` row and on the
`REQUESTED` event.

The fingerprint is verified at grant **activation**, not at presentation.
`chain.verify_fingerprint(approval_id, payload_json, con)` re-parses and
re-canonicalizes the payload, recomputes the fingerprint, and compares it
against the fingerprint stored on the `REQUESTED` event; a mismatch raises
`ChainTamperError` and the grant never forms -- a security boundary, not a
recoverable UX issue. The per-turn `[PENDING-APPROVALS-VERIFIED]` builder
(`build_verified_pending_approvals`) applies the same check when assembling the
injected block, so only fingerprint-clean pendings reach the orchestrator marked
`verified: true`. The orchestrator therefore presents from that already-verified
block (or a same-turn relayed `approval_request`) and never dispatches to verify
the payload itself.

## Hash chain

Each event links to the previous via `prev_hash` -> `this_hash`
(`chain.insert_event()`). `chain.validate_chain()` re-walks the chain;
`verify_fingerprint()` checks the relayed payload against the `REQUESTED` row.
Because `approval_events` is append-only (UPDATE/DELETE blocked by the
`bu_approval_events_immutable` and `bd_approval_events_immutable` triggers),
`this_hash` is computed in the application layer before INSERT, inside
`chain.insert_event()` -- not by a DB trigger. `EXECUTED` / `FAILED` events,
appended by the PostToolUse adapter through `store.record_event()` after an
approved T3 command runs, extend the same chain. `REVERTED` remains a valid
CHECK value but is **inert** -- the revert feature was removed, so no code
writes it.

## Grant activation walk-through

When the user selects the `[P-xxxxxxxx]` Approve label, the ElicitationResult
hook calls `approval_grants.activate_db_pending_by_prefix()`, which:

1. flips the `approvals` row to `status='approved'` and writes `SHOWN` +
   `APPROVED` events;
2. inserts a row into `approval_grants` with `scope='SCOPE_SEMANTIC_SIGNATURE'`,
   `status='PENDING'`, keyed by the same `approval_id`.

On the retry, `writer.check_db_semantic_grant()` finds that grant (scope +
status PENDING + not expired + session match), and `bash_validator` immediately
calls `writer.consume_db_semantic_grant()` to flip it to `status='CONSUMED'`
(single-use, replay protection). A second attempt within the TTL will not match.

No nonce or `approval_id` is relayed through SendMessage; activation is entirely
hook-driven by the label the user selected.

## Reading a granted approval

After approval and a resume, the subagent re-attempts the original command using
the exact `exact_content` string -- no modifications. The hook reads the grant
from the DB and allows the command through.

If the retry is blocked again (a new `approval_id` is issued), one of these is
true:

1. The grant was consumed by a prior attempt -- emit a new `APPROVAL_REQUEST`.
2. The command drifted from the approved `exact_content` -- use the literal.
3. The approval was rejected or revoked -- stop and report to the orchestrator.

To list pending approvals directly:

```python
from gaia.approvals.store import list_pending

# list_pending() returns ONLY status='pending' approvals (with
# age_seconds/stale enrichment); it does NOT return 'approved' rows.
# get_by_id(approval_id) reads an approved row.
pending = list_pending(all_sessions=False)
```
