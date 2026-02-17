# Terraform Patterns - Reference Examples

Full HCL examples for common operations. Read on-demand, not injected.

## Standard terragrunt.hcl Structure

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "tfr:///terraform-aws-modules/[module]/aws?version=x.y.z"
}

dependency "vpc" {
  config_path = "../vpc"
}

inputs = {
  name = "${local.env}-${local.resource_name}"
}

locals {
  env = "prod"
  resource_name = "vpc"
}
```

## Dependency with Mock Outputs

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

## VPC Module Example

```hcl
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"

  name = "${local.env}-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["us-east-1a", "us-east-1b", "us-east-1c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = false
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = local.common_tags
}
```

## EKS Module Example

```hcl
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "19.15.0"

  cluster_name    = "digital-eks-${local.env}"
  cluster_version = "1.28"

  vpc_id     = dependency.vpc.outputs.vpc_id
  subnet_ids = dependency.vpc.outputs.private_subnets

  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access = true
  enable_irsa = true

  eks_managed_node_groups = {
    default = {
      min_size       = 2
      max_size       = 5
      desired_size   = 3
      instance_types = ["t3.medium"]
      capacity_type  = "ON_DEMAND"
    }
  }

  tags = local.common_tags
}
```

## Security Group Pattern

```hcl
name = "${local.env}-${local.service}-sg"

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

## Outputs Pattern

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

## State Operations

```bash
terragrunt state list
terragrunt state show module.vpc.aws_vpc.this
terragrunt import aws_vpc.this vpc-abc123
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| State lock | Check DynamoDB `terraform-locks` table |
| Module not found | Run `terragrunt init` |
| Dependency cycle | Review dependency declarations |
| Invalid CIDR | Verify no overlaps with existing VPCs |
