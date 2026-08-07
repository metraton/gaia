---
name: fast-queries
description: Use when diagnosing an issue, checking system health, or validating state before deeper investigation
---

# Fast Queries

Start with supplied evidence, not a fixed diagnostic script.

1. Read the injected dispatch kernel (`# Your Contract`: goal, `project`,
   your `can_read` menu). Project context is NOT preloaded.
2. Pull what the goal needs from the persistent substrate: a narrowly scoped
   `gaia context get --section <s>` (within `can_read`), then
   `gaia context show` or `gaia context query` for wider reads.
3. Run the smallest authoritative read-only CLI query for the surfaced domain.
   Use native output/filter flags and one command per call.
4. Record the query, result, and uncertainty in the contract checkpoint.
5. Deep-dive only where the evidence identifies a gap or inconsistency.

Do not invoke helpers from `.claude/`; that tree is an installed/protected
surface, not Gaia source. Do not treat a broad health command's exit code as
proof of a specific desired state. If investigation forecasts mutation, switch
to `investigation` and collect predictable exact T3 commands before requesting
consent.
