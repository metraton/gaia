# Gaia-Ops Configuration Files

Central configuration for the orchestration system. Contracts are the SSOT for agent context provisioning.

## Files

| File | Purpose | Read by |
|------|---------|---------|
| `context-contracts.json` | Base cloud-agnostic contracts: `read`/`write` sections per agent, `core_sections` list, `workspace_repos` schema | `context_provider.py`, `context_writer.py`, `pre_tool_use.py` |
| `cloud/gcp.json` | GCP extensions: `gcp_services`, `workload_identity`, `static_ips` | Same trio, merged at runtime |
| `cloud/aws.json` | AWS extensions: `vpc_mapping`, `load_balancers`, `api_gateway`, `irsa_bindings`, `aws_accounts` | Same trio, merged at runtime |
| `git_standards.json` | Commit standards (Conventional Commits), allowed types, forbidden footers | `hooks/modules/validation/commit_validator.py` |
| `universal-rules.json` | Behavior rules injected into all agents | `context_provider.py` |
| `surface-routing.json` | Generic surface classification and investigation-brief rules | `surface_router.py`, `context_provider.py`, Spec-Kit |

## How the base+cloud merge works

At runtime, `tools/context/context_provider.py` executes the following logic:

```
1. Read context-contracts.json         <- cloud-agnostic sections (all clouds)
2. Detect cloud_provider from project-context.json
3. Read cloud/{provider}.json          <- cloud-specific sections
4. Merge: extend read/write lists per agent (no duplicates)
5. Result: complete contract for the agent on that cloud
```

## Structure

```
config/
├── context-contracts.json        <- agnostic base (all agents, v4)
├── cloud/
│   ├── gcp.json                  <- GCP extensions + section_schemas
│   └── aws.json                  <- AWS extensions + section_schemas
├── surface-routing.json          <- generic surface routing + investigation brief config
├── git_standards.json
├── universal-rules.json
└── README.md
```

## Adding support for a new cloud (Azure, etc.)

> **Note:** Only GCP and AWS are currently implemented.

1. Create `cloud/azure.json` with the same schema as `cloud/gcp.json`
2. Define agents and their Azure-specific sections
3. No code changes needed -- `context_provider.py` detects it automatically

## References

- [Hooks](../hooks/) - Security hooks (use contracts for validation)
- [Tools](../tools/) - Context provisioning tools
- [Tests](../tests/) - Test suite

---

**Updated:** 2026-03-25 | **Active contracts:** base v4 + 2 clouds (GCP, AWS)
