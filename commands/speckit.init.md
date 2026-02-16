---
description: Bootstrap Spec-Kit structure and validate/create project-context.json
---

**USAGE**: `/speckit.init [feature-name]`

**Examples**:
```
/speckit.init 005-new-feature
/speckit.init  # Without feature name - just validates project-context.json
```

## Purpose

Initialize Spec-Kit for a new feature or bootstrap the project configuration. This command:
1. Validates that `project-context.json` exists and is properly configured
2. If missing, interactively creates `project-context.json` from user input
3. Optionally creates feature directory structure with templates

## Execution Steps

### 1. Validate project-context.json

Check if `.claude/project-context/project-context.json` exists:

**If EXISTS**:
- Read and validate structure
- Check required fields:
  - `project_details.id`
  - `project_details.region`
  - `project_details.cluster_name`
  - `gitops_configuration.repository.path`
  - `terraform_infrastructure.layout.base_path`
- Report validation results
- If missing critical fields, offer to fix interactively

**If NOT EXISTS**:
- Inform user that project-context.json is missing
- Offer to create it interactively
- Ask the following questions:

#### Interactive Project Context Creation

**Question 1: GCP Project ID**
```
What is your GCP project ID?
Example: aaxis-rnd-general-project
```

**Question 2: GCP Region**
```
What is your primary GCP region?
Options:
- us-central1
- us-east1
- us-west1
- europe-west1
- Other (specify)
```

**Question 3: GKE Cluster Name**
```
What is your GKE cluster name?
Example: tcm-gke-non-prod
```

**Question 4: GitOps Repository Path**
```
What is the ABSOLUTE path to your GitOps repository?
Example: /path/to/your/gitops-repo
```

**Question 5: Terraform Repository Path**
```
What is the ABSOLUTE path to your Terraform repository?
Example: /path/to/your/terraform-repo
```

**Question 6: PostgreSQL Instance (Optional)**
```
PostgreSQL Cloud SQL instance name (or skip):
Example: tcm-postgres-non-prod
[Press Enter to skip]
```

After collecting answers, generate `project-context.json`:

```json
{
  "version": "2.0.0",
  "last_updated": "YYYY-MM-DD",
  "project_details": {
    "id": "<answer1>",
    "region": "<answer2>",
    "environment": "non-prod",
    "cluster_name": "<answer3>"
  },
  "gitops_configuration": {
    "repository": {
      "path": "<answer4>",
      "structure": "flux-standard"
    },
    "flux_details": {
      "version": "v2",
      "reconciliation_interval": "5m"
    }
  },
  "terraform_infrastructure": {
    "layout": {
      "base_path": "<answer5>",
      "structure": "terragrunt-hierarchical"
    },
    "provider_credentials": {
      "auth_method": "gcloud-default"
    }
  },
  "databases": {
    "postgres": {
      "instance": "<answer6>",
      "type": "cloud-sql"
    }
  },
  "operational_guidelines": {
    "commit_standards": "conventional-commits",
    "gitops_principles": "flux-cd",
    "security_tiers": ["T0", "T1", "T2", "T3"]
  }
}
```

Write file to `.claude/project-context/project-context.json` and report success.

### 2. Feature Directory Bootstrap (Optional)

If `[feature-name]` argument provided:

a) **Determine Spec-Kit root**:
   - Check if directory `spec-kit-tcm-plan` exists in current directory
   - If not, ask user: "Spec-Kit root directory name? (default: spec-kit-tcm-plan)"

b) **Create feature directory structure**:
   ```
   <speckit-root>/specs/<feature-name>/
   ├── spec.md          (from .claude/speckit/templates/spec-template.md)
   ├── plan.md          (empty placeholder)
   ├── tasks.md         (empty placeholder)
   └── contracts/       (empty directory)
   ```

c) **Initialize spec.md with project context**:
   - Copy spec-template.md to feature directory
   - **AUTO-FILL** placeholders using project-context.json:
     - `[PROJECT_ID]` → `project_details.id`
     - `[REGION]` → `project_details.region`
     - `[CLUSTER]` → `project_details.cluster_name`
     - `[GITOPS_PATH]` → `gitops_configuration.repository.path`
     - `[TERRAFORM_PATH]` → `terraform_infrastructure.layout.base_path`

d) **Report feature initialization**:
   ```markdown
   ✅ Feature initialized: <feature-name>

   **Directory**: <speckit-root>/specs/<feature-name>/
   **Files created**:
   - spec.md (auto-filled with project context)
   - plan.md (placeholder)
   - tasks.md (placeholder)
   - contracts/ (directory)

   **Next steps**:
   1. Edit spec.md to define your feature
   2. Run: /speckit.plan <speckit-root> <feature-name>
   ```

### 3. Validation Summary

Always output validation summary:

```markdown
## Project Context Validation

✅ project-context.json exists
✅ All required fields present
✅ GitOps path exists: <path>
✅ Terraform path exists: <path>

**Configuration**:
- Project ID: <id>
- Region: <region>
- Cluster: <cluster-name>
- GitOps: <path>
- Terraform: <path>

**Spec-Kit Status**:
- Governance: .claude/speckit/governance.md
- ADR Template: .claude/speckit/templates/adr-template.md
- Commands: /specify, /plan, /tasks, /implement, /init

**You're ready to use Spec-Kit!**
```

## Error Handling

**Missing paths**:
```
⚠️ WARNING: GitOps path does not exist: <path>
This may cause issues with gitops-operator agent.
Recommendation: Create directory or update project-context.json
```

**Invalid JSON**:
```
❌ ERROR: project-context.json is malformed
<JSON parse error>

Fix the JSON syntax or delete the file to regenerate.
```

**Permission issues**:
```
❌ ERROR: Cannot write to .claude/project-context/project-context.json
Check directory permissions for .claude/
```

## Notes

- **Run once per project** to create project-context.json
- **Run per feature** to bootstrap new feature directory
- **Idempotent**: Safe to run multiple times (won't overwrite existing files)
- **No config.json**: Spec-Kit 2.0 uses project-context.json + explicit arguments
- **No constitution.md**: Replaced with governance.md (project-wide, not versioned per feature)

## See Also

- `/speckit.specify` - Create feature specification (uses project-context.json)
- `/speckit.plan` - Generate implementation plan
- `.claude/speckit/governance.md` - Project governance principles
