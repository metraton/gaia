---
name: context-updater
description: Teaches agents the CONTEXT_UPDATE format for enriching project-context.json
triggers: [terraform-architect, gitops-operator, cloud-troubleshooter, devops-developer]
phase: end
---

# Context Updater Protocol

## When to Emit CONTEXT_UPDATE

Emit a `CONTEXT_UPDATE` block when ANY of these are true:

1. **Empty section** -- A section you own exists but has no data
2. **Drift detected** -- Discovered data differs from what the section currently contains
3. **New resources found** -- You found resources not currently listed in the section

Do NOT emit `CONTEXT_UPDATE` if your findings match the existing data exactly.

---

## CONTEXT_UPDATE Block Format

Place this block on its own line, **after** all agent analysis and **before** the `AGENT_STATUS` block:

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
- Section names must match your contract's writable sections (see below)
- One block per response (combine all section updates into a single JSON object)
- Include only the keys you want to add or update

---

## Merge Rules

The hook processes your update using these merge strategies:

| Operation | Behavior | Example |
|-----------|----------|---------|
| **ADD** | New keys are inserted into the section | `"node_pools": [...]` added to empty section |
| **MERGE** | Existing dicts are recursively merged | `"namespaces"` merges into existing `"cluster_details"` |
| **UNION** | Lists are merged, no duplicates | `["adm", "dev"]` + `["dev", "test"]` = `["adm", "dev", "test"]` |
| **OVERWRITE** | Scalar values are replaced | `"version": "1.29"` replaces `"version": "1.28"` |
| **NO-DELETE** | Keys you don't mention are preserved | Omitting `"region"` keeps the existing value intact |

---

## Writable Sections Per Agent

Each agent can only write to sections it owns:

| Agent | Writable Sections |
|-------|-------------------|
| `cloud-troubleshooter` | `cluster_details`, `infrastructure_topology` |
| `gitops-operator` | `gitops_configuration`, `cluster_details` |
| `terraform-architect` | `terraform_infrastructure`, `infrastructure_topology` |
| `devops-developer` | `application_services` |

Writing to a section you don't own will be rejected by the hook.

---

## Per-Agent Examples

### cloud-troubleshooter

```
CONTEXT_UPDATE:
{
  "cluster_details": {
    "kubernetes_version": "1.29",
    "node_pools": [
      {"name": "default-pool", "machine_type": "e2-standard-4", "node_count": 3}
    ]
  }
}
```

### gitops-operator

```
CONTEXT_UPDATE:
{
  "gitops_configuration": {
    "flux_version": "v2.6.1",
    "reconciliation_interval": "1m"
  }
}
```

### terraform-architect

```
CONTEXT_UPDATE:
{
  "terraform_infrastructure": {
    "modules": ["vpc", "eks", "rds"],
    "backend": "s3"
  }
}
```

### devops-developer

```
CONTEXT_UPDATE:
{
  "application_services": {
    "services": [
      {"name": "graphql-server", "port": 3000, "namespace": "common"}
    ]
  }
}
```

---

## Complete Example: Fresh Install Enrichment

After investigating a new cluster, the `gitops-operator` discovers namespace structure not yet in context:

```
CONTEXT_UPDATE:
{
  "cluster_details": {
    "namespaces": {
      "application": ["adm", "dev", "test"],
      "infrastructure": ["flux-system", "ingress-nginx"],
      "system": ["kube-system", "kube-public"]
    }
  }
}
```

This merges into the existing `cluster_details` section. Any keys already present (like `kubernetes_version`) are preserved. The `namespaces` dict is added as a new key.
