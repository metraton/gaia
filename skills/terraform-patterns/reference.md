# Terraform Patterns — HCL Reference

Structural patterns for Terraform and Terragrunt. Cloud-agnostic — use values from project-context, never hardcode.

For cloud-specific resource examples (VPCs, clusters, databases), discover patterns from the existing codebase using the `investigation` skill.

---

## Remote State (root terragrunt.hcl)

```hcl
remote_state {
  backend = "gcs"                          # gcs | s3 | azurerm — from cloud_provider in context
  config = {
    bucket   = "{project_id}-terraform-state"
    prefix   = "${path_relative_to_include()}/terraform.tfstate"
    project  = "{project_id}"              # from project-context
    location = "{primary_region}"          # from project-context
  }
}
```

## Component (terragrunt.hcl)

```hcl
include "root" { path = find_in_parent_folders() }
terraform { source = "../../../../../terraform//{module-name}" }

dependency "vpc" {
  config_path = "../vpc"
  mock_outputs = { network_id = "mock-network" }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  project_id = "{project_id}"              # from project-context
  region     = "{primary_region}"          # from project-context
  network_id = dependency.vpc.outputs.network_id
}
```

## Required Labels

Every resource must include:

```hcl
labels = {
  environment = "{env}"                    # from project-context
  managed_by  = "terraform"
  project     = "{project_id}"            # from project-context
}
```

## Outputs Pattern

```hcl
output "resource_id" {
  description = "Description of what this output represents"
  value       = resource_type.name.id
}
```

Always: snake_case name, non-empty description, no sensitive values unless `sensitive = true`.

## Module Sourcing

```hcl
# Local module (GCP preferred)
terraform { source = "../../../../../terraform//{module-name}" }

# Registry module (AWS preferred)
terraform { source = "tfr:///terraform-aws-modules/{module}/aws?version=x.y.z" }
```

Always pin exact versions — never `latest`, never unpinned.

## State Operations

```bash
terragrunt state list
terragrunt state show {resource_type}.{name}
terragrunt import {resource_type}.{name} {live_id}
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| State lock | Check state backend lock table, wait or force-unlock with caution |
| Module not found | Run `terragrunt init` |
| Dependency cycle | Review dependency `config_path` declarations |
| Mock outputs mismatch | Update `mock_outputs` to match actual output types |
| Plan shows unexpected destroy | Check for naming drift between code and live state |
