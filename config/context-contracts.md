# Context Contracts

**Version:** 2.1.0
**Last Updated:** 2025-11-10
**Parent:** CLAUDE.md

This document defines the **Context Contracts** for each specialized agent. A context contract specifies the minimum structured data that MUST be provided when invoking an agent.

---

## Overview

### What is a Context Contract?

A context contract is a schema that defines:
1. **Required fields:** Data that MUST be present for the agent to operate
2. **Optional fields:** Data that SHOULD be present when available
3. **Data types:** Expected structure (string, array, object, etc.)
4. **Source:** Where the data comes from (project-context.json, .claude/config/, etc.)

### Why Context Contracts?

**Benefits:**
- **Consistency:** All invocations of an agent receive uniform context structure
- **Validation:** context_provider.py validates that required fields are present
- **Documentation:** Developers know exactly what data an agent needs
- **Evolution:** Contracts can be versioned and extended without breaking existing invocations

---

## Provider-Specific Contracts (NEW in v2.1.0)

**As of v2.1.0, contracts are defined per cloud provider:**

```
.claude/config/
├── context-contracts.gcp.json      # GCP-specific contracts
├── context-contracts.aws.json      # AWS-specific contracts
└── context-contracts.azure.json    # Azure-specific contracts (future)
```

### How It Works

1. **context_provider.py detects the cloud provider:**
   - Reads `metadata.cloud_provider` from project-context.json
   - Falls back to inferring from field presence (`project_id` → GCP, `account_id` → AWS)
   - Defaults to GCP if undetected

2. **Loads the correct contract file:**
   - GCP projects → `context-contracts.gcp.json`
   - AWS projects → `context-contracts.aws.json`
   - Azure projects → `context-contracts.azure.json`

3. **Validates against provider-specific requirements:**
   - GCP contracts expect `project_details.project_id`
   - AWS contracts expect `project_details.account_id`
   - All other fields remain provider-agnostic

### Benefits of Provider-Specific Contracts

✅ **Clarity:** Field names match cloud provider terminology
✅ **Simplicity:** No complex conditional validation logic
✅ **Extensibility:** Adding Azure = create `context-contracts.azure.json` (15 minutes)
✅ **Agents stay agnostic:** Agents use pattern discovery, don't care about provider
✅ **Single source of truth:** Orchestrator selects the right contract

### Example

```python
# context_provider.py automatically handles this:

cloud_provider = detect_cloud_provider(project_context)  # → "gcp"
contracts = load_provider_contracts(cloud_provider)      # → loads context-contracts.gcp.json
payload = get_contract_context(project_context, "terraform-architect", contracts)
```

**Result:** Orchestrator validates GCP-specific fields, agents receive clean payload.

---

## Contract Format

Each contract section below follows this structure:

```yaml
agent_name:
  contract_version: "1.0"
  required:
    - field_group_name:
        fields:
          - field_name: type (description)
        source: where_data_comes_from
  optional:
    - field_group_name:
        fields:
          - field_name: type (description)
        source: where_data_comes_from
```

---

## terraform-architect

**Purpose:** Manages Terraform/Terragrunt infrastructure lifecycle (validation, planning, realization)

### Contract v1.0

```yaml
terraform-architect:
  contract_version: "1.0"

  required:
    - project_details:
        fields:
          - id: string (GCP project ID, e.g., "aaxis-rnd-non-prod")
          - region: string (Primary region, e.g., "us-central1")
          - environment: string (Environment name, e.g., "non-prod")
        source: project-context.json

    - terraform_infrastructure:
        fields:
          - layout:
              base_path: string (Root of terraform code, e.g., "/home/.../ops/terraform")
              modules_path: string (Shared modules, e.g., "modules/")
              environments_path: string (Environment configs, e.g., "environments/")
          - provider_credentials:
              gcp_credentials_path: string (Path to GCP service account JSON)
              aws_credentials_profile: string (AWS profile name, if multi-cloud)
        source: project-context.json

    - operational_guidelines:
        fields:
          - commit_standards:
              validation_required: boolean (Always true)
              validator_path: string (Path to commit_validator.py)
              config_path: string (Path to git_standards.json)
        source: project-context.json

  optional:
    - terraform_state:
        fields:
          - backend_type: string (e.g., "gcs", "s3")
          - backend_config:
              bucket: string (GCS bucket or S3 bucket name)
              prefix: string (State file prefix)
        source: project-context.json or auto-detect from backend.tf

    - recent_changes:
        fields:
          - commits: array (Last 5 commits in terraform code)
          - deployments: array (Recent terraform applies with timestamps)
        source: enrichment (git log, terraform state metadata)
```

### Example Payload

```json
{
  "contract": {
    "project_details": {
      "id": "aaxis-rnd-non-prod",
      "region": "us-central1",
      "environment": "non-prod"
    },
    "terraform_infrastructure": {
      "layout": {
        "base_path": "$PROJECT_ROOT/ops/terraform",
        "modules_path": "$PROJECT_ROOT/ops/terraform/modules",
        "environments_path": "$PROJECT_ROOT/ops/terraform/environments"
      },
      "provider_credentials": {
        "gcp_credentials_path": "$HOME/.config/gcloud/aaxis-rnd-terraform-sa.json"
      }
    },
    "operational_guidelines": {
      "commit_standards": {
        "validation_required": true,
        "validator_path": "$PROJECT_ROOT/.claude/tools/commit_validator.py",
        "config_path": "$PROJECT_ROOT/.claude/config/git_standards.json"
      }
    }
  },
  "enrichment": {
    "recent_changes": {
      "commits": [
        {"hash": "abc123", "message": "feat(gke): add node pool for batch jobs", "timestamp": "2025-11-06T10:30:00Z"}
      ]
    }
  }
}
```

---

## gitops-operator

**Purpose:** Manages Kubernetes/Flux application lifecycle (manifests, deployments, verification)

### Contract v1.0

```yaml
gitops-operator:
  contract_version: "1.0"

  required:
    - project_details:
        fields:
          - id: string (GCP project ID)
          - region: string (Primary region)
          - cluster_name: string (GKE cluster name, e.g., "non-prod-rnd-gke")
        source: project-context.json

    - gitops_configuration:
        fields:
          - repository:
              path: string (Root of gitops repo, e.g., "/home/.../gitops/non-prod-rnd-gke-gitops")
              remote_url: string (Git remote URL)
              branch: string (Main branch, e.g., "main")
          - flux_details:
              version: string (Flux version, e.g., "2.1.0")
              namespaces: array (Flux system namespaces, e.g., ["flux-system"])
        source: project-context.json

    - cluster_details:
        fields:
          - namespaces: array (Application namespaces)
            Example: ["tcm-non-prod", "pg-non-prod", "shared-services"]
          - ingress_class: string (Ingress controller class, e.g., "nginx")
        source: project-context.json

    - operational_guidelines:
        fields:
          - commit_standards: (same as terraform-architect)
        source: project-context.json

  optional:
    - application_services:
        fields:
          - services: array (List of deployed services)
            Example: [
              {
                "name": "tcm-api",
                "namespace": "tcm-non-prod",
                "port": 3001,
                "tech_stack": "NestJS",
                "status": "running"
              }
            ]
        source: project-context.json

    - recent_changes:
        fields:
          - commits: array (Last 5 commits in gitops repo)
          - deployments: array (Recent flux reconciliations)
        source: enrichment (git log, flux get all)
```

### Example Payload

```json
{
  "contract": {
    "project_details": {
      "id": "aaxis-rnd-non-prod",
      "region": "us-central1",
      "cluster_name": "non-prod-rnd-gke"
    },
    "gitops_configuration": {
      "repository": {
        "path": "$PROJECT_ROOT/gitops/non-prod-rnd-gke-gitops",
        "remote_url": "git@github.com:aaxis/non-prod-rnd-gke-gitops.git",
        "branch": "main"
      },
      "flux_details": {
        "version": "2.1.0",
        "namespaces": ["flux-system"]
      }
    },
    "cluster_details": {
      "namespaces": ["tcm-non-prod", "pg-non-prod", "shared-services"],
      "ingress_class": "nginx"
    },
    "operational_guidelines": {
      "commit_standards": {
        "validation_required": true,
        "validator_path": "$PROJECT_ROOT/.claude/tools/commit_validator.py",
        "config_path": "$PROJECT_ROOT/.claude/config/git_standards.json"
      }
    }
  },
  "enrichment": {
    "application_services": [
      {
        "name": "tcm-api",
        "namespace": "tcm-non-prod",
        "port": 3001,
        "tech_stack": "NestJS",
        "status": "running",
        "replicas": 2,
        "image": "gcr.io/aaxis-rnd/tcm-api:v1.2.3"
      },
      {
        "name": "pg-api",
        "namespace": "pg-non-prod",
        "port": 8086,
        "tech_stack": "Spring Boot",
        "status": "running",
        "replicas": 3,
        "image": "gcr.io/aaxis-rnd/pg-api:v2.1.0"
      }
    ],
    "recent_changes": {
      "commits": [
        {"hash": "def456", "message": "feat(helmrelease): add tcm-api Phase 3.3", "timestamp": "2025-11-07T09:15:00Z"}
      ]
    }
  }
}
```

---

## gcp-troubleshooter

**Purpose:** Diagnoses issues by comparing intended state (IaC) with actual state (live GCP resources)

### Contract v1.0

```yaml
gcp-troubleshooter:
  contract_version: "1.0"

  required:
    - project_details:
        fields:
          - id: string (GCP project ID)
          - region: string (Primary region)
          - cluster_name: string (GKE cluster name, if applicable)
        source: project-context.json

    - terraform_infrastructure:
        fields:
          - layout:
              base_path: string (Root of terraform code)
        source: project-context.json

    - gitops_configuration:
        fields:
          - repository:
              path: string (Root of gitops repo)
        source: project-context.json

  optional:
    - application_services:
        fields:
          - services: array (Complete list for correlation)
        source: project-context.json

    - issue_context:
        fields:
          - symptoms: array (Error messages, logs, metrics)
          - affected_resources: array (Resource names/IDs)
          - timeline: string (When issue started)
        source: enrichment (user description, logs analysis)
```

### Example Payload

```json
{
  "contract": {
    "project_details": {
      "id": "aaxis-rnd-non-prod",
      "region": "us-central1",
      "cluster_name": "non-prod-rnd-gke"
    },
    "terraform_infrastructure": {
      "layout": {
        "base_path": "$PROJECT_ROOT/ops/terraform"
      }
    },
    "gitops_configuration": {
      "repository": {
        "path": "$PROJECT_ROOT/gitops/non-prod-rnd-gke-gitops"
      }
    }
  },
  "enrichment": {
    "application_services": [
      {"name": "tcm-api", "namespace": "tcm-non-prod"},
      {"name": "pg-api", "namespace": "pg-non-prod"}
    ],
    "issue_context": {
      "symptoms": [
        "Pod tcm-api-5f7d8c9b-x7k2m in CrashLoopBackOff",
        "Error: Unable to connect to CloudSQL instance"
      ],
      "affected_resources": ["tcm-api", "cloudsql-tcm-instance"],
      "timeline": "Started 2025-11-07 08:30 UTC after deployment"
    }
  }
}
```

---

## aws-troubleshooter

**Purpose:** Diagnoses issues in AWS environments (similar to gcp-troubleshooter but for AWS)

### Contract v1.0

```yaml
aws-troubleshooter:
  contract_version: "1.0"

  required:
    - project_details:
        fields:
          - account_id: string (AWS account ID)
          - region: string (Primary region, e.g., "us-east-1")
          - cluster_name: string (EKS cluster name, if applicable)
        source: project-context.json

    - terraform_infrastructure:
        fields:
          - layout:
              base_path: string (Root of terraform code)
        source: project-context.json

    - gitops_configuration:
        fields:
          - repository:
              path: string (Root of gitops repo)
        source: project-context.json

  optional:
    - application_services:
        fields:
          - services: array (Complete list for correlation)
        source: project-context.json

    - issue_context:
        fields:
          - symptoms: array (Error messages, logs, metrics)
          - affected_resources: array (Resource names/IDs)
          - timeline: string (When issue started)
        source: enrichment (user description, logs analysis)
```

---

## devops-developer

**Purpose:** Application-level operations (build, test, debug, git operations)

### Contract v1.0

```yaml
devops-developer:
  contract_version: "1.0"

  required:
    - project_details:
        fields:
          - id: string (Project ID)
          - environment: string (Environment name)
        source: project-context.json

    - operational_guidelines:
        fields:
          - commit_standards: (same as terraform-architect)
        source: project-context.json

  optional:
    - application_services:
        fields:
          - services: array (List of services with tech stack info)
        source: project-context.json

    - development_context:
        fields:
          - language: string (Primary language, e.g., "TypeScript", "Python")
          - framework: string (Framework, e.g., "NestJS", "Django")
          - package_manager: string (e.g., "npm", "pip")
          - test_command: string (e.g., "npm test", "pytest")
          - build_command: string (e.g., "npm run build")
        source: enrichment (detect from package.json, requirements.txt, etc.)
```

### Example Payload

```json
{
  "contract": {
    "project_details": {
      "id": "aaxis-rnd-non-prod",
      "environment": "non-prod"
    },
    "operational_guidelines": {
      "commit_standards": {
        "validation_required": true,
        "validator_path": "$PROJECT_ROOT/.claude/tools/commit_validator.py",
        "config_path": "$PROJECT_ROOT/.claude/config/git_standards.json"
      }
    }
  },
  "enrichment": {
    "application_services": [
      {
        "name": "tcm-api",
        "tech_stack": "NestJS",
        "language": "TypeScript"
      }
    ],
    "development_context": {
      "language": "TypeScript",
      "framework": "NestJS",
      "package_manager": "npm",
      "test_command": "npm test",
      "build_command": "npm run build"
    }
  }
}
```

---

## Gaia (Meta-Agent)

**Purpose:** Analyzes, diagnoses, and optimizes the agent orchestration system itself

**Note:** This is a META-AGENT. It does NOT use context_provider.py. Context is provided manually in the prompt.

### Manual Context Structure

```yaml
gaia:
  context_type: "manual"

  provided_in_prompt:
    - system_paths:
        - agent_system_path: string (e.g., "/home/.../repositories/.claude/")
        - logs_path: string (e.g., "/home/.../repositories/.claude/logs/")
        - tools_path: string (e.g., "/home/.../repositories/.claude/tools/")
        - tests_path: string (e.g., "/home/.../repositories/.claude/tests/")
        - orchestrator_logic: string (e.g., "/home/.../repositories/CLAUDE.md")

    - system_metadata:
        - spec_kit_commands: array (List of /speckit.* commands)
        - session_commands: array (List of /save-session, /restore-session, etc.)
        - agents: array (List of specialist agents)

    - operational_context:
        - recent_changes: string (Description of recent system changes)
        - current_issues: string (Known issues or degradations)
        - user_request: string (Specific question/analysis request)
```

### Example Invocation

```python
Task(
    subagent_type="gaia",
    description="Analyze routing accuracy",
    prompt="""
## System Context

**System Paths:**
- Agent system: $PROJECT_ROOT/.claude/
- Orchestrator: $PROJECT_ROOT/CLAUDE.md
- Logs: $PROJECT_ROOT/.claude/logs/
- Tools: $PROJECT_ROOT/.claude/tools/
- Tests: $PROJECT_ROOT/.claude/tests/

**System Knowledge:**
- 5 specialist agents: terraform-architect, gitops-operator, gcp-troubleshooter, aws-troubleshooter, devops-developer
- Routing: agent_router.py with semantic matching
- Context: context_provider.py with contracts
- Logs: JSONL format in .claude/logs/

## User's Request

Analyze routing accuracy over the last 100 invocations. Identify patterns in misrouted requests and propose improvements.

## Your Mission

1. Read routing logs from .claude/logs/agent-routing.jsonl
2. Analyze accuracy, misroutes, patterns
3. Research best practices for semantic routing
4. Propose concrete improvements to agent_router.py
"""
)
```

---

## Contract Validation

### How context_provider.py Uses Contracts

1. **Load Contract:**
   - Read agent definition from `.claude/agents/$AGENT_NAME.md`
   - Extract contract schema from frontmatter or dedicated section

2. **Validate Required Fields:**
   - Check that all required fields are present in `project-context.json`
   - Fail fast if required fields are missing

3. **Enrich Optional Fields:**
   - Attempt to populate optional fields from available sources
   - Perform semantic enrichment (correlate services, recent changes, etc.)

4. **Return Structured Payload:**
   - Return JSON with `contract` (required fields) and `enrichment` (optional fields)

### Example Validation Error

```python
# context_provider.py execution
{
  "status": "error",
  "agent": "gitops-operator",
  "missing_fields": [
    "cluster_details.namespaces",
    "gitops_configuration.repository.path"
  ],
  "message": "Cannot invoke agent: required fields missing from project-context.json"
}
```

**Orchestrator response:** Report to user, ask to update `project-context.json`

---

## Extending Contracts

### When to Add Fields

**Add required fields when:**
- Agent CANNOT operate without the field (e.g., cluster_name for gitops-operator)
- Field is always available in project-context.json

**Add optional fields when:**
- Agent CAN operate without the field but performs better with it
- Field may not always be available (e.g., recent_changes requires git log)
- Field is derived/enriched (not directly in project-context.json)

### How to Extend

1. **Update agent definition:**
   - Edit `.claude/agents/$AGENT_NAME.md`
   - Add field to contract schema

2. **Update context_provider.py:**
   - Add logic to extract/enrich new field
   - Add validation if required field

3. **Update project-context.json:**
   - Add field to SSOT (if required field)

4. **Update this document:**
   - Add field to contract definition
   - Increment contract version (e.g., 1.0 → 1.1)
   - Update example payload

5. **Test:**
   - Run `pytest .claude/tests/test_context_provider.py`
   - Verify agent receives new field

---

## Contract Versioning

### Version Format

Contracts use semantic versioning: `MAJOR.MINOR`

**MAJOR:** Breaking change (removed field, changed type)
**MINOR:** Additive change (new optional field)

**Examples:**
- Add optional field: 1.0 → 1.1
- Add required field: 1.0 → 2.0 (breaking)
- Remove field: 1.0 → 2.0 (breaking)
- Change field type: 1.0 → 2.0 (breaking)

### Backward Compatibility

**Rule:** context_provider.py MUST support all contract versions for 6 months after deprecation.

**How:**
- Maintain version adapters in context_provider.py
- Convert old contract format to new format internally
- Emit deprecation warnings

**Example:**
```python
# context_provider.py
if agent_contract_version == "1.0":
    # Adapt to new format
    contract_data["new_field"] = derive_from_old_fields(contract_data)
    warnings.warn(f"Contract v1.0 is deprecated, use v2.0")
```

---

## Quick Reference Table

| Agent | Required Fields | Optional Fields | Primary Use Case |
|-------|----------------|-----------------|------------------|
| terraform-architect | project_details, terraform_infrastructure, operational_guidelines | terraform_state, recent_changes | Terraform/Terragrunt operations |
| gitops-operator | project_details, gitops_configuration, cluster_details, operational_guidelines | application_services, recent_changes | Kubernetes/Flux deployments |
| gcp-troubleshooter | project_details, terraform_infrastructure, gitops_configuration | application_services, issue_context | GCP diagnostics |
| aws-troubleshooter | project_details, terraform_infrastructure, gitops_configuration | application_services, issue_context | AWS diagnostics |
| devops-developer | project_details, operational_guidelines | application_services, development_context | App build/test/debug |
| Gaia | (manual context in prompt) | N/A | System analysis & optimization |

---

## Version History

### 2.0.0 (2025-11-07)
- Extracted from CLAUDE.md monolith
- Added complete contracts for all 6 agents
- Added validation, extension, versioning sections
- Added example payloads for each agent
- Distinguished project agents from meta-agents

### 1.x (Historical)
- Embedded in CLAUDE.md
- Minimal contract definitions (field lists only)
