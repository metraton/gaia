---
name: context-updater
description: Use when investigation reveals data that is missing from or differs from project-context.json
metadata:
  user-invocable: false
---

# Context Updater Protocol

## When to Emit CONTEXT_UPDATE

Emit a `CONTEXT_UPDATE` block when ANY of these are true:

1. **Empty section** — A section you own exists but has no data
2. **Drift detected** — Discovered data differs from current section
3. **New resources found** — Resources not currently listed
4. **Pattern discovered** — Investigation revealed a pattern, structure, or config not yet captured (see `investigation` skill DOCUMENT rule)

Do NOT emit if findings match existing data exactly.

## Format

Place this block after analysis and before the `json:contract` block:

```
CONTEXT_UPDATE:
{
  "section_name": {
    "key": "value"
  }
}
```

**Rules:**
- Must be valid JSON
- Section names must match your writable sections
- One block per response (combine all updates)
- Include only keys to add or update

## Merge Rules

| Operation | Behavior |
|-----------|----------|
| **ADD** | New keys inserted into the section |
| **MERGE** | Existing dicts recursively merged |
| **UNION** | Lists merged, no duplicates |
| **OVERWRITE** | Scalar values replaced |
| **NO-DELETE** | Keys you don't mention are preserved |

## Writable Sections Source of Truth

Do **not** memorize a static table from this skill.
Use the injected `context_update_contract` as the source of truth:

- `readable_sections` — the context sections available to you
- `writable_sections` — the only sections you may update

If `context_update_contract` is absent, fall back to your agent contract in
`config/context-contracts.json`. Do not invent section names.

Writing to a section you do not own will be rejected by the hook.
`gaia` and `speckit-planner` do not write to project-context — they manage
gaia-ops internals and specs respectively.

## Progressive Enrichment Targets

When a section you own is empty or sparse, prioritize populating it with high-value keys first.

| Priority | What to capture | Why |
|----------|----------------|-----|
| **P0** | Resource identifiers (names, IDs, paths) | Enables direct targeting in future searches |
| **P1** | Structural relationships (what connects to what) | Enables cross-agent reasoning |
| **P2** | Configuration values (versions, replicas, limits) | Enables drift detection |
| **P3** | Behavioral patterns (conventions, naming schemes) | Enables consistency enforcement |

Capture P0 keys on every investigation. P1-P3 when naturally encountered -- do not investigate solely to populate context.

For concrete examples, read `examples.md` in this directory.
