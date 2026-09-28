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
| `gaia/store/schema.sql` | Produces the **latest** (EXPECTED) shape directly -- fresh installs land here, not at the floor. |
| `scripts/bootstrap_database.py` (`SCHEMA_FLOOR = 18`) | Stamps the fresh ledger at the floor; rejects DBs below it. |
| `bin/cli/doctor.py` (`EXPECTED_SCHEMA_VERSION`) | The version the CLI expects; equals the floor when no forward migration exists, and the highest migration target once they do. |

How bootstrap treats each case:

* **Fresh install** (no tables at all): `schema.sql` produces the EXPECTED
  shape, the ledger is stamped at the **floor** (not EXPECTED), and every
  forward migration from `floor+1` to EXPECTED replays on top -- all in one
  transaction. It does **not** seed v1 and walk the historical chain. Because
  the migrations run against objects `schema.sql` already created, they **must
  be idempotent** (see section 1). The writer's own first-write
  materialization (`gaia/store/writer.py`) seals the version `schema.sql`
  builds instead of replaying.
* **DB at or above the floor** (the common case, e.g. `~/.gaia/gaia.db`):
  only the forward migrations it is still behind on run, up to EXPECTED.
  `schema.sql` is never applied to it. A copy is written to
  `<db dir>/backups/` with SQLite's backup API before the chain opens.
* **Tables but an empty ledger** (built by an older writer that did not seal):
  handled like a fresh build over existing data -- backed up and gated.
* **DB below the floor** (`1 <= version < 18`): **no longer supported** for
  in-place upgrade. Bootstrap aborts with a clear message asking you to
  recreate the DB (back up, delete `~/.gaia/gaia.db`, re-run `gaia install`).

There are no `_fresh` / `_merge` variants under the floor model. Those existed
only because the old baseline was v1 and the whole chain was walked on every
fresh install. Under the floor model the forward-migration loop is still
replayed on every fresh install (from `floor+1` to EXPECTED) -- so the single
forward migration file per bump must be idempotent rather than split into
`_fresh` / `_merge` variants.

---

## 1. Adding a future migration (one file per bump, forward-only)

Going forward the convention is **forward-only, one migration file per
version bump**. To raise the schema from the current floor (or any later
version) to `N`:

1. Add the new DDL to `gaia/store/schema.sql` so fresh installs land in the
   target shape.
2. Create exactly one `scripts/migrations/v{N-1}_to_v{N}.sql` containing the
   full DDL delta applied to a DB at version `N-1`. It **must be idempotent**
   (`CREATE ... IF NOT EXISTS`; for `ADD COLUMN`, rely on the runner's
   existence guard -- SQLite has no `ADD COLUMN IF NOT EXISTS`), because it is
   replayed on fresh installs against a DB that already has those objects.
3. Bump `EXPECTED_SCHEMA_VERSION` to `N` in `bin/cli/doctor.py` **in the same
   commit**.

`gaia migrate apply` (engine `scripts/bootstrap_database.py`) then applies
`v{N-1}_to_v{N}.sql` to any DB behind `N`. The whole pending chain -- every
migration and the `schema_version` row that seals it -- runs statement by
statement inside ONE transaction, so an interruption anywhere, even between a
migration and its seal, leaves objects and ledger as they were. A fresh install
stamps the ledger at the floor and replays `floor+1 .. N` in that same
transaction -- since `schema.sql` already produced the `N` shape, the migration
runs against objects that already exist, which is exactly why it must be
idempotent (no `_fresh` variant is used). An existing DB never gets
`schema.sql`: its own migrations are the only thing that changes it, so a file
that silently relied on `schema.sql` having created an object first breaks the
upgrade from older bases -- `tests/cli/test_migration_upgrade_from_published_bases.py`
catches that against every published base.

`tests/cli/test_schema_version_lockstep.py` enforces that
`EXPECTED_SCHEMA_VERSION` equals the floor when no forward migrations exist,
and equals the highest migration target once they do.

Each independent feature that introduces new DDL gets its own migration
version. Do NOT extend a version that has already been stamped: the ledger is
monotonic, and the engine will not re-run a frozen version. Two
unrelated features ready at once get consecutive versions (e.g. v19 and v20),
never bundled.

---

## 1b. A chain that reaches DATA is not applied unattended

The engine applies whatever it finds in this directory on any install, update
or dev run, so committing a file is what schedules it. That is safe for
structure and unsafe for data: `v49_to_v50.sql` was the first file here that
also rewrote rows, and the next arbitrary CLI call erased a counter on 1359
live curated rows before anyone was asked.

`scripts/migration_guard.py` now classifies every pending file before the
chain opens.
**This gate needs nothing declared** (the compatibility mark in section 1c is a
separate rule) -- the classification reads the SQL itself, and the number of rows at risk is read from the target
database. A file is refused only where those meet: a statement that rewrites or
removes rows (`UPDATE`, `DELETE`, `DROP TABLE`, `ALTER TABLE ... DROP/RENAME`,
`INSERT OR REPLACE`, or any statement the guard does not recognise) whose table
already held rows before this run started.

What this means when you author one:

* **Structure-only chains are unaffected.** `CREATE` (a `CREATE TRIGGER`
  whole, whatever its body runs later), `ADD COLUMN`, `DROP INDEX/TRIGGER/VIEW`
  and a plain `INSERT` reach no existing row; a chain made only of them applies
  alone, after the backup.
* **Fresh installs and test databases are never gated**, because their census
  is empty. Do not add a flag to skip the guard for them; there is nothing to
  skip.
* **A data-reaching file stops the whole chain once (D68).** The refusal lists
  every reaching statement of every file in the chain, with table and row
  count, and names the one command that continues --
  `gaia migrate apply --consent-chain vA..vB`. The consent names the chain, so
  it approves that span and nothing that ships after it. `gaia migrate plan`
  shows the same chain before anything is attempted.
* **A test that applies such a file must name its consent**, the way
  `tests/cli/test_migration_v49_to_v50.py` does.

The guard's behaviour is pinned by `tests/cli/test_migration_consent_gate.py`,
including a test asserting that no file currently in this directory produces an
unrecognised statement.

---

## 1c. Every migration declares its compatibility: `-- gaia-compat:`

Several Gaia installations of different versions can share one `gaia.db`. An
installation whose code is older than the database keeps reading and writing
as long as no migration since its version broke what it writes. Only the author
knows that, so every `v{N-1}_to_v{N}.sql` carries exactly one header line:

```
-- gaia-compat: backward     code older than v{N} can still write after it
-- gaia-compat: breaking     code older than v{N} must stop writing
```

A migration is **breaking** when code written before it would fail or write
wrong rows afterwards: it removes or renames a column or table, rebuilds or
rewrites rows (anything `scripts/migration_guard.py` classifies as reaching rows
-- `UPDATE`, `DELETE`, `DROP TABLE`, `ALTER TABLE ... DROP/RENAME`,
`INSERT OR REPLACE`), adds a `NOT NULL` column without a default, or adds a
`CHECK`, unique index or trigger that rejects a write the older code makes.
Adding a table, a nullable or defaulted column, an index, or a trigger that
only records is **backward**.

The mark is mandatory. `tests/cli/test_migration_compat_header.py` fails on a
file without it, and on a file declared backward that the guard classifies as
reaching rows. The engine treats an unmarked file as breaking.

**Where the minimum lives.** `schema_version.min_code_version` (added by
`v58_to_v59.sql`, nullable) holds, on each seal, the oldest code version that may
still write. `scripts/bootstrap_database.py` (`_stamp`) writes it inside the same
transaction as the migration it seals: it carries the previous seal's value, and
only a breaking migration raises it to its own version. A ledger sealed before
v59 has no value to carry, so the first seal falls back to the last breaking
migration in this directory. The writer's own first-write seal
(`gaia/store/writer.py`) records the same value.

**What older code does with it** (`gaia/store/writer.py::writes_refused`): a
database newer than the code whose newest `min_code_version` is at or below the
code's version is read and written, with one stderr notice per process asking
for `gaia install` in that installation; a larger minimum, or none recorded,
refuses every write (reads keep working) with a message naming the fix --
install a Gaia whose schema version is at least the minimum. `gaia migrate apply`
on such a database changes nothing: it returns 0 inside the window and refuses
outside it.

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
| `vN_to_vN+1.sql` | Applied to an existing DB at version N. Contains the full DDL delta, applied with its ledger seal inside the chain's single transaction by `gaia migrate apply`. It carries no `BEGIN`/`COMMIT` of its own -- the engine owns the transaction. |

The historical `_fresh` and `_merge` variants are no longer used: under the
floor model a single idempotent `vN_to_vN+1.sql` covers both an in-place
upgrade and the fresh-install replay (the engine walks `floor+1 .. EXPECTED`
on every fresh install), so one idempotent file replaces the old split.

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
