---
name: context-updater
description: Use when investigation reveals data that is missing from or differs from project-context.json
metadata:
  user-invocable: false
  type: technique
---

# Context Updater

project-context.json is shared memory across agents. When you discover something
about the project that other agents would need, you are the only one who saw it.
If you do not write it, the next agent starts from zero on that question.

## When to Emit CONTEXT_UPDATE

Emit a `CONTEXT_UPDATE` block when ANY of these are true:

1. **Empty section** — A section you own exists but has no data
2. **Drift detected** — Discovered data differs from current section
3. **New resources found** — Resources not currently listed
4. **Pattern discovered** — Investigation revealed a pattern, structure, or config not yet captured

Skip when findings match existing data exactly -- redundant writes
create noise in the audit trail without adding information.

## How to Emit

**Step 1: Check permissions**

Do **not** memorize a static table from this skill. Your write permissions are
shown in the injected context under **Your Write Permissions**. The
`writable_sections` list there is the source of truth.

If `write_permissions` is absent, fall back to your agent contract in
`config/context-contracts.json`. Do not invent section names. Writing to a
section you do not own will be rejected by the hook. `gaia-system` and `gaia-planner` do not write to project-context -- they
manage gaia-ops internals and planning respectively.

**Step 2: Build the CONTEXT_UPDATE block**

Place this block after analysis and before the `json:contract` block:

```
CONTEXT_UPDATE:
{
  "section_name": {
    "key": "value"
  }
}
```

Rules: valid JSON, section names must match writable sections, one block per
response (combine all updates), include only keys to add or update.

**Step 3: Apply merge semantics**

| Operation | Behavior |
|-----------|----------|
| **ADD** | New keys inserted into the section |
| **MERGE** | Existing dicts recursively merged |
| **UNION** | Lists merged, no duplicates |
| **OVERWRITE** | Scalar values replaced |
| **NO-DELETE** | Keys you don't mention are preserved |

## Prioritization

When a section you own is empty or sparse, prioritize high-value keys first.

| Priority | What to capture | Why |
|----------|----------------|-----|
| **P0** | Resource identifiers (names, IDs, paths) | Enables direct targeting in future searches |
| **P1** | Structural relationships (what connects to what) | Enables cross-agent reasoning |
| **P2** | Configuration values (versions, replicas, limits) | Enables drift detection |
| **P3** | Behavioral patterns (conventions, naming schemes) | Enables consistency enforcement |

Capture P0 keys on every investigation. P1-P3 when naturally encountered -- do
not investigate solely to populate context.

For concrete examples, read `examples.md` in this directory.

## Index, Not Snapshot

Principle: **project-context is index, not snapshot** of cloud state. It captures what statically exists in the project — identifiers, names, relationships, and semi-stable metadata — not real-time runtime values.

**What counts as live-state (do not write to context):**
- Cloud resource runtime status (pod counts, instance status, VPC IDs)
- API-discovered facts that change without a rescan (load balancer DNS, IP addresses, IAM bindings derived from OIDC, pubsub topic lists)
- Any field whose scanner requires a live cloud API call (gcloud, aws, kubectl) to produce

**How to obtain live state when you need it:** run the appropriate cloud CLI at the moment the question arises — `gcloud`, `aws`, `kubectl`, `terraform show`. Do not cache the result in project-context.

**Why this matters:** stale live-state in context gives agents false confidence. A field written during last week's scan silently misrepresents today's infrastructure. Fields that never change (project IDs, account aliases, cluster names declared in code) are fine to index; ephemeral runtime facts are not.

## Mutative Triggers

Some events change the workspace state and require an immediate context capture — not just investigation-time updates. Mutative triggers differ from investigative triggers: an investigative trigger fires when you *discover* something that already existed; a mutative trigger fires when an action *creates or changes* workspace state.

When you detect any of the following patterns in tool output or agent output, emit a `CONTEXT_UPDATE` block to record the new state before the session ends:

| Pattern | Example output | What to capture |
|---------|----------------|-----------------|
| `npm install` | `added 1 package, audited 42 packages` | Package name, version, scope (global vs local) |
| `pip install` | `Successfully installed acli-1.2.3` | Package name, version |
| `gaia install` | `gaia installed at /usr/local/bin/gaia` | Package name, install path |
| `auth configure` | `Credentials saved to ~/.config/gcloud/credentials.json` | Service name, credential path |

Additional patterns that are mutative (not exhaustive):
- `kubectl apply` — cluster state changed, capture workload/namespace
- `helm install` / `helm upgrade` — chart name and version
- `brew install` — formula name and version

**Contrast with investigative triggers** (do NOT emit CONTEXT_UPDATE for these):
- `kubectl get`, `helm list`, `npm list` — read-only queries; discovery goes through scanners
- `terraform plan` — simulation, no state change
- `gcloud describe`, `aws describe-*` — read-only; use scanner APIs for indexing

**Rule:** if the tool output contains language like *"installed"*, *"added"*, *"configured"*, *"applied"*, or *"upgraded"* referencing a named package or service, treat it as mutative and capture the integration row.

## Anti-Patterns

- Emitting updates without checking writable sections
- Overwriting user-curated fields with generic values
- Waiting until task completion to emit (emit as you discover)
- Skipping P0 fields while enriching lower-priority ones
- Writing live-state fields (cloud resource status, API-discovered runtime facts) to project-context — these belong in scanner output, not the index
