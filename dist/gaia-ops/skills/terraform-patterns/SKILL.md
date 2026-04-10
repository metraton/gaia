---
name: terraform-patterns
description: Use when creating, modifying, or reviewing Terraform or Terragrunt configuration files
metadata:
  user-invocable: false
  type: domain
---

# Terraform Patterns

Project-specific conventions. Use values from your injected project-context — never hardcode project IDs, regions, or account identifiers.

For HCL examples (remote state, component structure, labels, outputs), read `reference.md` in this directory.

## Discover the Project's Organization

Every project organizes Terraform differently. Before creating any
file, discover how THIS project does it.

1. **Find the modules directory.** Look for `tf_modules/`, `modules/`,
   `terraform/`, or similar. The name varies — what matters is whether
   reusable modules exist and where they live.
2. **Read 2-3 existing terragrunt.hcl files.** Look at the `source =`
   lines. Do they reference local modules? Registry modules? A mix?
3. **Follow the majority pattern.** If 8 out of 10 components use
   local module references, yours should too. Consistency with the
   project matters more than what you'd choose on a greenfield.

### Module vs Inline

If the project has reusable modules for similar resource compositions
(e.g., a cloud-sql module that composes instance + database + user +
secrets), and your new resource follows a similar composition pattern,
create a reusable module. If it's truly one-off glue with no reuse
potential, inline is acceptable — but check first, because most
projects lean one way.

## Directory Structure (Reference)

The structure below is a common starting point, not a prescription.
If the codebase uses a different layout, follow the codebase.

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

1. **Prefer Terragrunt** — prefer `terragrunt` commands for all environment operations; raw `terraform` is acceptable for module development and testing only
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
