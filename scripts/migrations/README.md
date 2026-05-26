# Migration Conventions

Operational rules for the `scripts/migrations/` ledger. Follow these before
adding any new migration file.

---

## 1. One feature per migration version

Each independent feature that introduces new DDL gets its own migration version.
Do NOT extend a version that has already been stamped. The migration ledger is
monotonic: once a version is written to `schema_migrations`, its content is
frozen and `bootstrap_database.sh` will not re-run it.

If you have two unrelated features ready at the same time, assign them
consecutive versions (e.g., v6 and v7). Do not bundle them into v6 to save a
number -- bundling violates atomicity and makes rollback reasoning impossible.

---

## 2. Version assertions in tests

Tests that assert the schema version must use a floor check, not a point check:

```python
# Correct -- survives future bumps without re-editing this test
assert schema_version >= 5

# Wrong -- breaks every time a new migration lands
assert schema_version == 5
```

A floor assertion preserves test intent (the feature that introduced v5 is
present) without becoming a maintenance burden as the ledger grows.

---

## 3. Migration file naming

| Pattern | When to use |
|---------|-------------|
| `vN_to_vN+1.sql` | Applied to an existing DB at version N. Contains the full DDL delta. |
| `vN_to_vN+1_fresh.sql` | Applied after a clean install where `schema.sql` already contains the target state. Usually carries only indexes and constraints that `schema.sql` cannot safely declare for older DBs. |

Both files are driven by `bootstrap_database.sh`. The `_fresh` variant runs
when the bootstrap detects that the target objects already exist (Section 3c
case logic). It must be idempotent (`CREATE INDEX IF NOT EXISTS`, etc.).

---

## 4. Lessons from Plan B closure

Plan B originally added the `evidence` table DDL into `v4_to_v5.sql` after
that version had already been stamped and executed on existing installs.
Bootstrap skipped the modified file because the ledger row for v4->v5 was
already present, so the new DDL never ran.

The fix was to extract evidence DDL into its own `v5_to_v6.sql` (and a
matching `v5_to_v6_fresh.sql` for clean installs). This is the canonical
example of the one-feature-per-version rule in practice.

See `v5_to_v6.sql` and `v5_to_v6_fresh.sql` in this directory for the
resulting migration files.
