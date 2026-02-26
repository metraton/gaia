---
name: terraform-patterns
description: Use when creating, modifying, or reviewing Terraform or Terragrunt configuration files
user-invocable: false
---

# Terraform Patterns

Project-specific conventions. Use values from your injected project-context — never hardcode project IDs, regions, or account identifiers.

For HCL examples (remote state, component structure, labels, outputs), read `reference.md` in this directory.

## Directory Structure

```
terraform/
└── [module-name]/
    ├── main.tf        # Resource definitions
    ├── variables.tf   # Input variables
    ├── outputs.tf     # Output values (snake_case, with descriptions)
    └── provider.tf    # Provider config (if module-level)

features/infra/[env]/
├── terragrunt.hcl           # Root: remote state config
└── [component]/
    └── terragrunt.hcl       # Component: inputs + dependency references
```

## Naming Convention

| Resource | Pattern | Notes |
|----------|---------|-------|
| Network/VPC | `{app}-{env}-vpc` | From context: project + env |
| Cluster | `{app}-{env}-cluster-{n}` | Match context cluster_name |
| Database | `{app}-{env}-{engine}-instance` | Engine: postgres, mysql |
| Secret | `{service}-secret` | Matches app service name |
| Service Account | `{resource}-sa` | Scope: resource it serves |

## Module Sourcing

- **Local modules** (preferred for GCP): `../../../../../terraform//{module-name}`
- **Registry modules** (preferred for AWS): `tfr:///terraform-aws-modules/{module}/aws?version=x.y.z`
- **Always pin exact versions** — never `latest`, never unpinned

## Key Rules

1. **Terragrunt CLI only** — always `terragrunt` commands, never raw `terraform`
2. **Dependencies via blocks** — never hardcode IDs, always `dependency.x.outputs.y`
3. **Version pinning** — exact versions for modules, `~>` for providers
4. **Tags on everything** — all resources get the standard label block
5. **snake_case outputs** — descriptive names with `description` field
6. **mock_outputs on dependencies** — required for `validate` and `plan` to work offline

## Reference Docs

Use `WebFetch` when a resource or attribute is unknown or ambiguous. Do not use WebFetch to discover patterns — the codebase always wins over external docs.

| Need | URL |
|------|-----|
| Google provider resources | `https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/{resource}` |
| Terragrunt config blocks | `https://terragrunt.gruntwork.io/docs/reference/config-blocks-and-attributes` |
