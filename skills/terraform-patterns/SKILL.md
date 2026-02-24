---
name: terraform-patterns
description: Terraform and Terragrunt patterns for infrastructure as code
user-invocable: false
---

# Terraform Patterns

Project-specific conventions. For HCL examples, read `reference.md` in this directory.
Use values from your injected project-context — never hardcode project IDs, regions, or account identifiers.

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

## Remote State

Configure in root `terragrunt.hcl`. Backend type from `cloud_provider` in context (`gcs`/`s3`/`azurerm`):

```hcl
remote_state {
  backend = "gcs"
  config = {
    bucket   = "{project_id}-terraform-state"
    prefix   = "${path_relative_to_include()}/terraform.tfstate"
    project  = "{project_id}"      # from project-context
    location = "{primary_region}"  # from project-context
  }
}
```

## Component Pattern (Terragrunt)

```hcl
include "root" { path = find_in_parent_folders() }
terraform { source = "../../../../../terraform//{module-name}" }

dependency "vpc" {
  config_path = "../vpc"
  mock_outputs = { network_id = "mock-network" }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}
inputs = {
  project_id = "{project_id}"      # from project-context
  region     = "{primary_region}"  # from project-context
  network_id = dependency.vpc.outputs.network_id
}
```

## Module Sourcing

- **Local modules** (preferred for GCP): `../../../../../terraform//{module-name}`
- **Registry modules** (preferred for AWS): `tfr:///terraform-aws-modules/{module}/aws?version=x.y.z`
- **Always pin exact versions** — never `latest`, never unpinned

## Required Tags/Labels

Every resource must include a standard label block using project-context values:

```hcl
labels = {
  environment = "{env}"         # from project-context
  managed_by  = "terraform"
  project     = "{project_id}"  # from project-context
}
```

## Key Rules

1. **Terragrunt CLI only** — always `terragrunt` commands, never raw `terraform`
2. **Dependencies via blocks** — never hardcode IDs, always `dependency.x.outputs.y`
3. **Version pinning** — exact versions for modules, `~>` for providers
4. **Tags on everything** — all resources get the standard label block
5. **snake_case outputs** — descriptive names with `description` field
6. **mock_outputs on dependencies** — required for `validate` and `plan` to work offline
