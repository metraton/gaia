# Migration Conventions

Operational rules for the `scripts/migrations/` ledger. Follow these before
adding any new migration file.

---

## 0. The schema floor (baseline = current version)

Gaia is a single-user personal tool. Nobody upgrades a database older than the
current version, and fresh installs build the schema directly from
`gaia/store/schema.sql`. The full historical `v1 -> v17` migration chain was
therefore collapsed into a **schema floor**: the lowest schema version that is
supported for in-place use.

The floor is **v18**. It is declared in three places that must agree:

| Location | What it holds |
|----------|---------------|
| `gaia/store/schema.sql` | Produces the v18 shape directly (fresh installs land here). |
| `scripts/bootstrap_database.sh` Section 3b (`SCHEMA_FLOOR=18`) | Seeds/stamps the ledger at the floor; rejects DBs below it. |
| `bin/cli/doctor.py` (`EXPECTED_SCHEMA_VERSION`) | The version the CLI expects; equals the floor until a forward migration is added. |

How bootstrap treats each case:

* **Fresh install** (no `schema_version` rows): `schema.sql` already produced
  the floor shape, so bootstrap stamps `(version=18, ...)` directly. It does
  **not** seed v1 and walk the chain.
* **DB at or above the floor** (the common case, e.g. `~/.gaia/gaia.db`): no
  migration needed. Section 3c only runs if a forward migration exists.
* **DB below the floor** (`1 <= version < 18`): **no longer supported** for
  in-place upgrade. Bootstrap aborts with a clear message asking you to
  recreate the DB (back up, delete `~/.gaia/gaia.db`, re-run `gaia install`).

There are no `_fresh` / `_merge` variants under the floor model. Those existed
only because the old baseline was v1 and the whole chain was walked on every
fresh install. With the floor, a fresh install is already at the expected
version after `schema.sql`, so the migration loop is skipped entirely.

---

## 1. Adding a future migration (one file per bump, forward-only)

Going forward the convention is **forward-only, one migration file per
version bump**. To raise the schema from the current floor (or any later
version) to `N`:

1. Add the new DDL to `gaia/store/schema.sql` so fresh installs land in the
   target shape.
2. Create exactly one `scripts/migrations/v{N-1}_to_v{N}.sql` containing the
   full DDL delta applied to a DB at version `N-1`.
3. Bump `EXPECTED_SCHEMA_VERSION` to `N` in `bin/cli/doctor.py` **in the same
   commit**.

`bootstrap_database.sh` Section 3c then applies `v{N-1}_to_v{N}.sql` inside a
single `BEGIN/COMMIT` transaction for any DB behind `N`, and stamps the ledger
only on success. A fresh install is already at `N` after `schema.sql`, so it
never enters the loop -- no `_fresh` variant is required.

`tests/cli/test_schema_version_lockstep.py` enforces that
`EXPECTED_SCHEMA_VERSION` equals the floor when no forward migrations exist,
and equals the highest migration target once they do.

Each independent feature that introduces new DDL gets its own migration
version. Do NOT extend a version that has already been stamped: the ledger is
monotonic, and `bootstrap_database.sh` will not re-run a frozen version. Two
unrelated features ready at once get consecutive versions (e.g. v19 and v20),
never bundled.

---

## 2. Version assertions in tests

Tests that assert the schema version must use a floor check, not a point check:

```python
# Correct -- survives future bumps without re-editing this test
assert schema_version >= 18

# Wrong -- breaks every time a new migration lands
assert schema_version == 18
```

A floor assertion preserves test intent without becoming a maintenance burden
as the ledger grows.

---

## 3. Migration file naming

| Pattern | When to use |
|---------|-------------|
| `vN_to_vN+1.sql` | Applied to an existing DB at version N. Contains the full DDL delta, applied inside a `BEGIN/COMMIT` transaction by bootstrap Section 3c. |

The historical `_fresh` and `_merge` variants are no longer used: under the
floor model a fresh install is already at the expected version after
`schema.sql`, so it never runs a migration script.

---

## 4. Why the floor (lesson from the collapsed chain)

The pre-floor design seeded `(version=1)` then walked `v1 -> v2 -> ... -> v18`
on every fresh install, each step guarded by a per-version "is the live DDL
already at target?" probe in `bootstrap_database.sh`. That machinery existed
solely to make a fresh install (which `schema.sql` had already built to the
latest shape) walk the chain without re-running destructive DDL.

Since fresh installs build straight from `schema.sql` and no one runs a DB
older than the current version, the entire chain plus its guard probes were
dead weight. Collapsing to a floor removes ~35 migration files and the
per-version `case` block, leaving a single forward-only loop for genuine future
bumps.
