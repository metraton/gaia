---
name: context-updater
description: CONTEXT_UPDATE format for enriching project-context.json progressively
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

Place this block after all analysis and before `AGENT_STATUS`:

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

## Writable Sections Per Agent

| Agent | Writable Sections |
|-------|-------------------|
| `cloud-troubleshooter` | `cluster_details`, `infrastructure_topology`, `monitoring_observability` |
| `gitops-operator` | `gitops_configuration`, `cluster_details` |
| `terraform-architect` | `terraform_infrastructure`, `infrastructure_topology` |
| `devops-developer` | `application_services`, `application_architecture`, `development_standards` |

Writing to a section you don't own will be rejected.
`gaia` and `speckit-planner` do not write to project-context — they manage gaia-ops internals and specs respectively.

For examples, see [examples.md](examples.md).
