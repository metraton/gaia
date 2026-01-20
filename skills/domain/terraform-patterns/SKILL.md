---
name: terraform-patterns
description: Terraform and Terragrunt patterns specific to this project
triggers: [terraform, terragrunt, hcl, module, vpc, eks, rds]
---

# Terraform Patterns for This Project

## Project Structure

```
terraform/
├── vpc/
│   ├── terragrunt.hcl
│   └── main.tf
├── eks/
│   ├── terragrunt.hcl
│   └── main.tf
└── [other resources]/
```

## Terragrunt Patterns

### Standard terragrunt.hcl Structure

```hcl
# Include root configuration
include "root" {
  path = find_in_parent_folders()
}

# Terraform source (module)
terraform {
  source = "tfr:///terraform-aws-modules/[module]/aws?version=x.y.z"
}

# Dependencies (if any)
dependency "vpc" {
  config_path = "../vpc"
}

# Inputs
inputs = {
  name = "${local.env}-${local.resource_name}"
  # ... other inputs
}

# Locals
locals {
  env = "prod"
  resource_name = "vpc"
}
```

## Naming Conventions

| Resource Type | Pattern | Example |
|---------------|---------|---------|
| **VPC** | `{env}-vpc` | `prod-vpc`, `dev-vpc` |
| **EKS Cluster** | `digital-eks-{env}` | `digital-eks-prod`, `digital-eks-dev` |
| **RDS Instance** | `{env}-{service}-{db_type}` | `prod-graphql-postgres` |
| **S3 Bucket** | `vtr-{purpose}-{env}` | `vtr-terraform-state` |
| **IAM Role** | `{service}-{env}-role` | `graphql-prod-role` |

## AWS-Specific Conventions

### Region Selection
- **Primary region:** us-east-1
- **Multi-region:** Not used currently

### Account Structure
- **Production account:** 929914624686
- **Shared/Dev account:** 059588584554

### VPC CIDR Blocks
- **Production VPC:** 10.0.0.0/16
- **Dev VPC:** [Verify in existing code]

## State Management

### Backend Configuration

```hcl
# In root terragrunt.hcl
remote_state {
  backend = "s3"
  config = {
    bucket         = "vtr-terraform-state"
    key            = "${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}
```

## Module Usage Patterns

### Prefer Official AWS Modules

```hcl
terraform {
  source = "tfr:///terraform-aws-modules/[module]/aws?version=x.y.z"
}
```

**Common modules:**
- `terraform-aws-modules/vpc/aws`
- `terraform-aws-modules/eks/aws`
- `terraform-aws-modules/rds/aws`
- `terraform-aws-modules/security-group/aws`

### Module Versioning
- Always pin module versions
- Use semantic versioning constraints: `version=5.2.0` (exact)
- Never use `latest` or unpinned versions

## Dependency Management

### Declaring Dependencies

```hcl
dependency "vpc" {
  config_path = "../vpc"

  mock_outputs = {
    vpc_id = "vpc-mock123"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  vpc_id = dependency.vpc.outputs.vpc_id
}
```

**Dependency naming:**
- Use descriptive names matching the resource type
- Examples: `dependency "vpc"`, `dependency "eks"`, `dependency "db_subnet_group"`

## Tag Standards

### Required Tags

```hcl
tags = {
  Environment = local.env
  ManagedBy   = "Terraform"
  Project     = "VTR Digital"
  Owner       = "Platform Team"
}
```

### Tag Propagation
- Enable `enable_dns_hostnames = true` for VPCs
- Use `tags` block in all resources
- Propagate tags to child resources where applicable

## Security Groups

### Naming Pattern
```hcl
name = "${local.env}-${local.service}-sg"
```

### Ingress/Egress Rules
```hcl
ingress_rules = [
  {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
    description = "HTTPS from VPC"
  }
]
```

## EKS-Specific Patterns

### Cluster Configuration

```hcl
inputs = {
  cluster_name    = "digital-eks-${local.env}"
  cluster_version = "1.28"

  vpc_id     = dependency.vpc.outputs.vpc_id
  subnet_ids = dependency.vpc.outputs.private_subnets

  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access = true

  enable_irsa = true

  eks_managed_node_groups = {
    default = {
      desired_size = 3
      min_size     = 2
      max_size     = 5

      instance_types = ["t3.medium"]
      capacity_type  = "ON_DEMAND"
    }
  }
}
```

### Node Group Pattern
- Use EKS managed node groups (not self-managed)
- Capacity type: ON_DEMAND for prod, SPOT for dev
- Instance types: t3.medium minimum for workloads

## Validation Commands

Before any terraform operation:

```bash
# Format check
terragrunt hclfmt --check

# Lint
tflint

# Validate
terraform validate

# Plan (review changes)
terragrunt plan -out=plan.out
```

## Apply Workflow

T3 operation - requires approval:

```bash
# 1. Review plan
terragrunt plan

# 2. After approval, apply with plan file
terragrunt apply plan.out

# 3. Verify outputs
terragrunt output
```

## Outputs Pattern

### Standard Outputs

```hcl
output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "private_subnets" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnets
}
```

**Output naming:**
- Use snake_case
- Be descriptive: `eks_cluster_id` not `cluster_id`
- Include descriptions for all outputs

## Common Patterns to Replicate

### VPC Module
```hcl
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"

  name = "${local.env}-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["us-east-1a", "us-east-1b", "us-east-1c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway = true
  single_nat_gateway = false  # Use 3 NAT gateways for HA

  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = local.common_tags
}
```

### EKS Module
```hcl
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "19.15.0"

  cluster_name    = "digital-eks-${local.env}"
  cluster_version = "1.28"

  vpc_id     = dependency.vpc.outputs.vpc_id
  subnet_ids = dependency.vpc.outputs.private_subnets

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    default = {
      min_size     = 2
      max_size     = 5
      desired_size = 3

      instance_types = ["t3.medium"]
    }
  }

  tags = local.common_tags
}
```

## Troubleshooting Patterns

### Common Issues

| Issue | Pattern | Solution |
|-------|---------|----------|
| State lock | `Error: Error acquiring the state lock` | Check DynamoDB `terraform-locks` table |
| Module not found | `Error: Module not installed` | Run `terraform init` or `terragrunt init` |
| Dependency cycle | `Error: Cycle: ...` | Review dependency declarations, remove cycles |
| Invalid CIDR | `Error: invalid CIDR block` | Verify CIDR doesn't overlap with existing VPCs |

### State Operations

```bash
# List state
terragrunt state list

# Show specific resource
terragrunt state show module.vpc.aws_vpc.this

# Import existing resource (if needed)
terragrunt import aws_vpc.this vpc-abc123
```

## Anti-Patterns (NEVER Do This)

❌ **Hardcoded values instead of variables**
```hcl
# Bad
vpc_id = "vpc-abc123"

# Good
vpc_id = dependency.vpc.outputs.vpc_id
```

❌ **No version pinning**
```hcl
# Bad
source = "terraform-aws-modules/vpc/aws"

# Good
source = "terraform-aws-modules/vpc/aws?version=5.0.0"
```

❌ **Missing tags**
```hcl
# Bad
resource "aws_vpc" "this" {
  cidr_block = "10.0.0.0/16"
}

# Good
resource "aws_vpc" "this" {
  cidr_block = "10.0.0.0/16"
  tags = merge(local.common_tags, {
    Name = "${local.env}-vpc"
  })
}
```

❌ **Direct terraform commands in Terragrunt projects**
```bash
# Bad
terraform apply

# Good
terragrunt apply
```

## Version Constraints

### Terraform Version
```hcl
terraform {
  required_version = ">= 1.5.0"
}
```

### Provider Versions
```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
```

## Example: Creating New Resource

When asked to create a new Terraform resource:

1. **Find similar resources:**
   ```bash
   find terraform/ -name "terragrunt.hcl" | grep -E "(vpc|eks|rds)"
   ```

2. **Read 2-3 examples:**
   ```bash
   cat terraform/vpc/terragrunt.hcl
   cat terraform/eks/terragrunt.hcl
   ```

3. **Extract patterns:**
   - Directory structure: `terraform/{resource_type}/`
   - Naming: `{env}-{resource_name}`
   - Dependencies: declared via `dependency` blocks
   - Module source: Official AWS modules with version pinning

4. **Generate new resource following patterns:**
   - Use same directory structure
   - Follow naming convention
   - Include all required tags
   - Pin module versions
   - Declare dependencies explicitly

5. **Validate:**
   ```bash
   terragrunt validate
   tflint
   terragrunt plan
   ```
