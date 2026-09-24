# Pending approvals reference

The store is `approvals`, `approval_events` and `approval_grants` in Gaia's
database. Read and change it only through `gaia approvals`; never edit the
database directly.

## States

Every reader derives one state from the stored rows
(`gaia/approvals/reading.py`); none is a stored value.

| State | Meaning |
|-------|---------|
| `pending` | Undecided, waiting for the user. |
| `orphaned` | Undecided, but the session that requested it shows no sign of life. Withdraw it, or let it expire. |
| `expired` | Undecided past the 24-hour pending lifetime. |
| `replaced` | Withdrawn because the same requester asked again for the same command with its phrases. Not a user rejection. |
| `rejected` | The user, or the orchestrator withdrawing it, rejected it. |
| `revoked` | Its grant was revoked; it can no longer be used. |
| `approved` | The user approved it; a grant exists. |

An approved request also has an outcome:

| Outcome | Meaning |
|---------|---------|
| `unused` | Nothing has run yet and the window is still open. |
| `in_flight` | A command started and has not reported its end; the window is still open. |
| `executed` | Its commands reported their end, none of them a failure. |
| `failed` | A command failed outside its declared exits; the set is frozen. |
| `no_result` | A command started and the window closed without its end being reported. Not a failure. |
| `legacy_executed` / `legacy_failed` | Recorded by the mechanism before this one; not counted with the current outcomes. |

`gaia approvals history --status <pending|approved|rejected|revoked>` filters
by the stored status, not by the state: a `replaced` row, and an `expired` one
recorded by the older sweep, are listed under `revoked`, and no filter selects `orphaned` or `no_result`. Read the
STATE column, or use `list --orphans-only` for orphans.
