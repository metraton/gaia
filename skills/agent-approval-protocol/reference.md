# Approval data internals

## Fingerprints

`gaia/approvals/chain.py` canonicalizes sealed JSON and records its SHA-256 on
the REQUESTED event. Activation re-reads that event and runs
`verify_fingerprint`; mismatch or missing provenance fails closed before a
grant forms. Agents relay the returned id/fingerprint and never recompute them.

COMMAND_SET additionally stores a SHA-256 fingerprint for every exact command
and an order-sensitive `request_fingerprint` for the whole list. Reservation
matches the exact next command and its fingerprint.

## Store model

- `approvals`: user decision state (lowercase).
- `approval_events`: immutable REQUESTED/SHOWN/APPROVED and execution audit
  chain.
- `approval_grants`: active scope, exact command set, expiry, ordered progress,
  reservations, and failure fields (uppercase state).

Plan-first activation calls `insert_plan_command_set`. Execution reserves one
index with `reserve_plan_command`; `settle_plan_command(success=True)` appends
the index and advances `next_index`, while failure records `failed_index` and
`failure_reason` and freezes the grant as `FAILED`. Completed indexes stay
consumed; the failed index and unexecuted remainder stay visible, but neither a
retry nor the remainder can use that grant. A fresh investigation and a new
request-set/approval are required. This reservation/settlement model replaces
any documentation that described COMMAND_SET as a compound command consumed at
intake or at a text match.

## Cross-session lookup

Pending discovery is explicit and DB-first. Full approval-id lookup may cross
sessions; nothing automatically resurfaces pending requests into a later prompt.
Legacy filesystem fallback applies only where the approvals CLI still supports
historical singular records.
