# Context Extensions for Project-Context.json

## Overview

This document describes 5 new essential (TIER 1) sections that have been added to the context system to improve agent efficiency and reduce token usage.

**Date:** 2025-11-12
**Status:** Available in context_section_reader.py v2.1+
**Impact:** ~34-44% token savings per agent invocation with new sections

---

## New Sections (TIER 1 - Essential)

### 1. `gitops_repositories`

**Purpose:** Map all GitOps repositories used by the project
**Agentes Beneficiados:** gitops-operator, terraform-architect

**Structure:**
```json
{
  "gitops_repositories": {
    "repositories": [
      {
        "name": "production-eks-gitops",
        "status": "ACTIVE|ARCHIVED|TO_BE_DELETED",
        "environment": "production|development",
        "purpose": "GitOps for X cluster",
        "managed_namespaces": ["namespace1", "namespace2"],
        "reconciliation": "Flux v2.6+ (5m interval)",
        "key_helmreleases": ["app1", "app2"],
        "deletion_phase": "Phase 1|Phase 2|Phase 3 (if applicable)"
      }
    ]
  }
}
```

**Example:**
```json
{
  "gitops_repositories": {
    "repositories": [
      {
        "name": "prod-digital-eks-cluster",
        "status": "ACTIVE",
        "environment": "production",
        "purpose": "GitOps for digital-eks-prod cluster",
        "managed_namespaces": ["ofertasvtr", "b2b", "delegate"],
        "reconciliation": "Flux v2.6+ (5m interval)",
        "key_helmreleases": ["ofertasvtr-nextjs", "b2b-services"]
      },
      {
        "name": "production-ofertas-cluster",
        "status": "ARCHIVED",
        "environment": "production",
        "purpose": "Legacy GitOps for ofertasvtr-prod cluster",
        "deletion_phase": "Phase 1 - Archive after cluster deletion"
      }
    ]
  }
}
```

---

### 2. `vpc_mapping`

**Purpose:** Document all VPCs and their current state
**Agentes Beneficiados:** terraform-architect, aws-troubleshooter

**Structure:**
```json
{
  "vpc_mapping": {
    "vpcs": [
      {
        "vpc_id": "vpc-xxxxx",
        "name": "descriptive-name",
        "environment": "production|development",
        "region": "us-east-1",
        "cidr": "10.0.0.0/16",
        "clusters": ["cluster1", "cluster2"],
        "subnets": ["subnet-xxx", "subnet-yyy"],
        "status": "ACTIVE|TO_BE_DELETED|DEPRECATED",
        "deletion_phase": "Phase 3 (optional)"
      }
    ]
  }
}
```

**Example:**
```json
{
  "vpc_mapping": {
    "vpcs": [
      {
        "vpc_id": "vpc-031bb6818f814cfd9",
        "name": "prod-vpc",
        "environment": "production",
        "region": "us-east-1",
        "cidr": "10.0.0.0/16",
        "clusters": ["digital-eks-prod"],
        "status": "ACTIVE"
      },
      {
        "vpc_id": "vpc-0311b5a54f644ec97",
        "name": "prod-vpc-ofertas-legacy",
        "environment": "production",
        "region": "us-east-1",
        "cidr": "10.1.0.0/16",
        "clusters": ["ofertasvtr-prod"],
        "status": "TO_BE_DELETED",
        "deletion_phase": "Phase 3 (optional)"
      }
    ]
  }
}
```

---

### 3. `application_deployments`

**Purpose:** List all applications deployed in the project
**Agentes Beneficiados:** gitops-operator, devops-developer

**Structure:**
```json
{
  "application_deployments": {
    "applications": [
      {
        "name": "app-name",
        "service_name": "service-name",
        "namespace": "namespace",
        "active_cluster": "cluster-name",
        "legacy_cluster": "old-cluster-name (if migrated)",
        "migration_status": "COMPLETED|IN_PROGRESS|PLANNED",
        "deployed_via": "HelmRelease (Flux CD)|Direct kubectl|Other",
        "helm_repository": "repo-name",
        "image_tag": "version-tag",
        "replicas": 1,
        "status": "ACTIVE|DEPRECATED|TO_BE_DELETED"
      }
    ]
  }
}
```

**Example:**
```json
{
  "application_deployments": {
    "applications": [
      {
        "name": "ofertasvtr-nextjs",
        "service_name": "ofertasvtr",
        "namespace": "ofertasvtr",
        "active_cluster": "digital-eks-prod",
        "legacy_cluster": "ofertasvtr-prod",
        "migration_status": "COMPLETED",
        "deployed_via": "HelmRelease (Flux CD)",
        "helm_repository": "prod-digital-eks-cluster",
        "image_tag": "321-prod",
        "replicas": 1
      }
    ]
  }
}
```

---

### 4. `terraform_configurations`

**Purpose:** Map Terraform/Terragrunt modules and their state
**Agentes Beneficiados:** terraform-architect

**Structure:**
```json
{
  "terraform_configurations": {
    "base_path": "terraform/tf_live/project/region",
    "modules": [
      {
        "name": "module-name",
        "status": "ACTIVE|TO_BE_DELETED|DEPRECATED",
        "phase": "Phase 1|Phase 2|Phase 3",
        "resources": ["aws_resource_type_1", "aws_resource_type_2"],
        "estimated_deletion_time": "10-15 minutes"
      }
    ],
    "state_management": {
      "backend": "s3|gcs|local",
      "state_bucket": "bucket-name",
      "lock_table": "table-name",
      "region": "us-east-1",
      "backup_recommended": "YES|NO"
    }
  }
}
```

**Example:**
```json
{
  "terraform_configurations": {
    "base_path": "terraform/tf_live/ofertasvtr-prod/us-east-1",
    "modules": [
      {
        "name": "ofertasvtr-eks",
        "status": "TO_BE_DELETED",
        "phase": "Phase 1",
        "resources": ["aws_eks_cluster", "aws_eks_addon", "aws_eks_node_group"],
        "estimated_deletion_time": "10-15 minutes"
      },
      {
        "name": "ofertasvtr-iam",
        "status": "TO_BE_DELETED",
        "phase": "Phase 2",
        "resources": ["aws_iam_role", "aws_iam_role_policy_attachment"],
        "estimated_deletion_time": "2-5 minutes"
      }
    ],
    "state_management": {
      "backend": "s3",
      "state_bucket": "vtr-terraform-state",
      "lock_table": "terraform-locks",
      "region": "us-east-1",
      "backup_recommended": "YES (before Phase 1 execution)"
    }
  }
}
```

---

### 5. `dynamic_queries`

**Purpose:** Store dynamic query commands for operational validations
**Agentes Beneficiados:** aws-troubleshooter

**Structure:**
```json
{
  "dynamic_queries": {
    "query_name": "command description or command template",
    "another_query": "command or description"
  }
}
```

**Example:**
```json
{
  "dynamic_queries": {
    "dns_resolution": "dig www.ofertasvtr.cl +short (should show only prod IPs)",
    "cluster_status": "aws eks list-clusters --region us-east-1",
    "alb_traffic": "aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name RequestCount",
    "vpc_resources": "aws ec2 describe-network-interfaces --filters Name=vpc-id,Values=vpc-0311b5a54f644ec97",
    "terraform_state": "terragrunt show -json (from terraform base path)"
  }
}
```

---

## Agent Context Mapping

Each agent now automatically receives specific sections based on their role:

### gitops-operator (5 sections)
- `infrastructure_topology`
- `gitops_configuration`
- **`gitops_repositories`** ✨ NEW
- **`application_deployments`** ✨ NEW
- `operational_guidelines`

### terraform-architect (5 sections)
- `infrastructure_topology`
- `terraform_infrastructure`
- **`terraform_configurations`** ✨ NEW
- **`vpc_mapping`** ✨ NEW
- `operational_guidelines`

### aws-troubleshooter (4 sections)
- `infrastructure_topology`
- **`vpc_mapping`** ✨ NEW
- **`dynamic_queries`** ✨ NEW
- `operational_guidelines`

### devops-developer (4 sections)
- `application_architecture`
- **`application_deployments`** ✨ NEW
- `development_standards`
- `operational_guidelines`

### gcp-troubleshooter (3 sections - unchanged)
- `infrastructure_topology`
- `operational_guidelines`
- `monitoring_observability`

---

## Migration Guide

### For Existing Projects

Add the 5 new sections to your `.claude/project-context/project-context.json`:

```bash
# 1. Open your project's project-context.json
vi .claude/project-context/project-context.json

# 2. Add the new sections in the "sections" block
# Use the examples above as templates

# 3. Verify syntax
python3 .claude/tools/2-context/context_section_reader.py list

# 4. Test agent context loading
python3 .claude/tools/2-context/context_section_reader.py agent terraform-architect
python3 .claude/tools/2-context/context_section_reader.py agent gitops-operator
python3 .claude/tools/2-context/context_section_reader.py agent aws-troubleshooter
```

### For New Projects

When setting up a new project:

1. Create `.claude/project-context/project-context.json`
2. Include all 5 new sections from the beginning
3. Use this document as reference for structure and examples
4. Validate with `context_section_reader.py list`

---

## Benefits

### Token Savings
- **terraform-architect:** 33.8% reduction vs full context
- **gitops-operator:** 36.0% reduction vs full context
- **aws-troubleshooter:** 43.8% reduction vs full context

### Operational Efficiency
- **Reduced file reads:** Agents receive pre-loaded information
- **Better decisions:** Agents have complete infrastructure picture
- **Faster execution:** No need for agents to manually explore directories
- **Consistency:** All projects follow same structure

### Future-Proof
- System automatically loads correct sections per agent
- New agents can benefit by adding entries to AGENT_SECTIONS
- Adding more sections doesn't impact existing agents

---

## Implementation Status

| Component | Status |
|-----------|--------|
| context_section_reader.py | ✅ Updated (v2.1+) |
| AGENT_SECTIONS dict | ✅ Updated with new sections |
| Path resolution | ✅ Supports project-context/ subdirectory |
| VTR project-context.json | ✅ Updated with all 5 sections |
| Documentation | ✅ This file (CONTEXT_EXTENSIONS.md) |

---

## References

- **context_section_reader.py:** `tools/2-context/context_section_reader.py`
- **Project Context Template:** `tests/fixtures/project-context.aws.json` (basic example)
- **Specification:** `/home/jaguilar/aaxis/vtr/repositories/spec-kit-vtr-plan/specs/001-safe-deletion-of/` (VTR example)

---

**Last Updated:** 2025-11-12
**Maintainer:** Infrastructure Team
**Questions?** Review the examples in this document or check the VTR project's project-context.json
