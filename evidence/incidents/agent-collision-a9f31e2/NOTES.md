# Incident: agent identity collision on draft handle `a9f31e2` (7-hex, pre-16-floor)

**Date preserved:** 2026-07-26
**Preserved by:** gaia-system, at the request of jaguilar@aaxis.io, before GC quiescence-window deletion.

## What this file is

`a9f31e2.a8dae04f0cee.json` in this directory is an **exact copy** of
`/home/jorge/.gaia/contract_drafts/a9f31e2.a8dae04f0cee.json` (original left
untouched at that path). It is the one contract draft, out of the 377 files
that were in that directory at time of preservation, whose envelope
contradicts its own database row.

## The discrepancy

- The draft file's envelope declares `agent_status.agent_state:
  "NEEDS_VERIFICATION"` and carries a full `evidence_report.verification`
  block (`result: "pass"`) describing a git rename/commit/push against the
  `century-inc/branchkinect-aaxis-iac` repo (renaming `installer/evidence/AC-82`
  to `AC-1`, etc., commit `b4d59ce`).
- The corresponding row in `gaia.db`, table `agent_contract_handoffs`, is:

  | id   | agent_id  | plan_task_id | agent_state | created_at           |
  |------|-----------|--------------|-------------|----------------------|
  | 6803 | a9f31e2   | (null)       | COMPLETE    | 2026-07-26T07:55:04Z |

  Row 6803 is already terminal (`COMPLETE`) for handle `a9f31e2`. Terminal
  rows are immutable by design (`gaia contract finalize` refuses any further
  UPDATE once `agent_state` is already terminal). So row 6803's finalized
  content is NOT the content sitting in this draft file -- the draft was
  written to (or over) by a call that resolved to handle `a9f31e2` for
  `--agent-id` lookup, landed a `NEEDS_VERIFICATION`-shaped write with the
  branchkinect verification narrative, and that write happened AFTER (or
  around) the point at which the real owner of `a9f31e2` had already reached
  `COMPLETE` and closed its row.

## Why this happened

`a9f31e2` is a 7-hex-digit agent handle, minted before the 16-hex-digit
collision floor was enforced (`^a[0-9a-f]{16,}$`, see `agent-protocol` /
`agent-contract-handoff`). At 7 hex digits the measured collision rate across
distinct handles is 11.7% (12/103) -- far outside the zero-collision regime
that only starts at 16 hex digits (0/2658 measured). `gaia contract`
resolution by `--agent-id` narrows to a draft only when the handle names
exactly one; with two live agents both using the short handle `a9f31e2` in
overlapping windows, resolution-by-recency picked whichever draft was most
recently touched at call time, and the winner differed between two calls --
one agent's write landed on the other agent's draft.

## Why it cannot be fixed at the source

- The DB row (id 6803) is immutable once `COMPLETE` -- there is no correcting
  it after the fact, by design (no fix-it-after; verify before you finalize).
- The draft file's rightful owner cannot reclaim or rewrite
  `contract_drafts/a9f31e2.a8dae04f0cee.json` under its own (7-hex) handle,
  because handles that short are now below the enforced 16-hex floor and the
  CLI mints only conforming (16+) handles going forward -- there is no live
  path back to a 7-hex handle to disambiguate or repair this specific draft.
- The GC classifies this draft as spent (its DB-side contract is terminal)
  and would delete the file after 24h of quietness, which would erase the
  only remaining artifact showing the envelope/row mismatch.

## What this proves, and what it does not

This file proves that a draft's on-disk envelope and its DB row can diverge
under short-handle collision, and it pins one concrete instance of it
(handle `a9f31e2`, row 6803). It does NOT tell us what row 6803's own
(legitimate) content actually was -- that content is whatever the real
`a9f31e2` owner finalized, and it is not recoverable from this file, since
this file is the OTHER agent's write, not the owner's.

## Provenance of this copy

Copied byte-for-byte from
`/home/jorge/.gaia/contract_drafts/a9f31e2.a8dae04f0cee.json` on 2026-07-26 by
gaia-system, via a direct read of the original followed by a write of the
identical content here -- no shell `cp`, no move. The original was left in
place at the `contract_drafts` path; this is a copy, not a relocation.
