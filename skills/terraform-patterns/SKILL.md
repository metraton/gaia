---
name: terraform-patterns
description: Terraform and Terragrunt patterns specific to this project
user-invocable: false
---

# Terraform Patterns

Project-specific conventions. For HCL examples, read `reference.md` in this directory.

## Project Structure

```
terraform/
├── vpc/           (terragrunt.hcl + main.tf)
├── eks/           (terragrunt.hcl + main.tf)
└── [resource]/    (same pattern)
```

## Naming Conventions

| Resource | Pattern | Example |
|----------|---------|---------|
| VPC | `{env}-vpc` | `prod-vpc` |
| EKS | `digital-eks-{env}` | `digital-eks-prod` |
| RDS | `{env}-{service}-{db}` | `prod-graphql-postgres` |
| S3 | `vtr-{purpose}-{env}` | `vtr-terraform-state` |
| IAM | `{service}-{env}-role` | `graphql-prod-role` |

## AWS Conventions

- **Primary region:** us-east-1
- **Production account:** 929914624686
- **Dev account:** 059588584554
- **Production VPC CIDR:** 10.0.0.0/16

## State Backend

S3 bucket `vtr-terraform-state`, DynamoDB `terraform-locks`, encryption enabled.
Key pattern: `{path_relative_to_include()}/terraform.tfstate`

## Module Usage

- Always use official AWS modules: `terraform-aws-modules/{module}/aws`
- Always pin exact versions: `version=5.0.0`
- Never use `latest` or unpinned versions

## Required Tags

```hcl
tags = {
  Environment = local.env
  ManagedBy   = "Terraform"
  Project     = "VTR Digital"
  Owner       = "Platform Team"
}
```

## EKS Patterns

- Managed node groups (not self-managed)
- ON_DEMAND for prod, SPOT for dev
- Instance type: t3.medium minimum
- IRSA enabled for service accounts

## Key Rules

1. **Terragrunt over Terraform** — always `terragrunt` commands, never raw `terraform`
2. **Dependencies via `dependency` blocks** — never hardcode IDs
3. **Version pinning** — exact versions for modules, `~>` for providers
4. **Tags on everything** — all resources get the standard tag block
5. **snake_case outputs** — descriptive names with descriptions

## Provider Versions

```hcl
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
```
