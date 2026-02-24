# Gaia-Ops Configuration Files

**[Version en espanol](README.md)**

Central configuration for the orchestration system.

## Files

| File | Purpose | Read by |
|------|---------|---------|
| `context-contracts.json` | Base cloud-agnostic contracts: `read`/`write` sections per agent | `context_provider.py`, `context_writer.py`, `pre_tool_use.py` |
| `cloud/gcp.json` | GCP extensions: `gcp_services`, `workload_identity`, `static_ips` | Same trio, merged at runtime |
| `cloud/aws.json` | AWS extensions: `vpc_mapping`, `load_balancers`, `api_gateway`, `irsa_bindings`, `aws_accounts` | Same trio, merged at runtime |
| `context-contracts.gcp.json` | **Legacy** — kept for backward compatibility | Fallback if `context-contracts.json` not found |
| `context-contracts.aws.json` | **Legacy** — kept for backward compatibility | Fallback if `context-contracts.json` not found |
| `git_standards.json` | Commit standards (Conventional Commits), allowed types, forbidden footers | `commit_validator.py` |
| `universal-rules.json` | Behavior rules injected into all agents | `context_provider.py` |

## How the base+cloud merge works

At runtime, `context_provider.py` executes the following logic:

```
1. Read context-contracts.json         <- cloud-agnostic sections (all clouds)
2. Detect cloud_provider from project-context.json
3. Read cloud/{provider}.json          <- cloud-specific sections
4. Merge: extend read/write lists per agent (no duplicates)
5. Result: complete contract for the agent on that cloud
```

Fallback if `context-contracts.json` not found: uses `context-contracts.{provider}.json` (legacy).

## Structure

```
config/
├── context-contracts.json        <- agnostic base (all agents)
├── cloud/
│   ├── gcp.json                  <- GCP extensions + section_schemas
│   └── aws.json                  <- AWS extensions + section_schemas
├── context-contracts.gcp.json    <- legacy (fallback)
├── context-contracts.aws.json    <- legacy (fallback)
├── git_standards.json
├── universal-rules.json
├── README.md
└── README.en.md
```

## Adding support for a new cloud (Azure, etc.)

1. Create `cloud/azure.json` with the same schema as `cloud/gcp.json`
2. Define agents and their Azure-specific sections
3. No code changes needed — `context_provider.py` detects it automatically

## References

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Tests](../tests/README.md)

---

**Updated:** 2026-02-24 | **Active contracts:** base + 2 clouds (GCP, AWS)
