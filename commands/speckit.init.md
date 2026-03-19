---
description: Bootstrap Spec-Kit structure — syncs governance.md and verifies project-context.json.
---

**USAGE**: `/speckit.init [feature-name]`

**Examples**:
```
/speckit.init 005-new-feature
/speckit.init  # Without feature name - just validates and syncs governance
```

## Purpose

Step 0 of the Spec-Kit workflow. Executed automatically by the `speckit-planner` agent before any action. This command:
1. Syncs `governance.md` from `project-context.json` (always)
2. Verifies `project-context.json` exists and has required fields (never creates it)
3. Optionally creates feature directory structure with templates

**If `project-context.json` is missing**: report the error and stop — the user must run `gaia-scan` first.

## Execution Steps

### 0. Governance Sync (ejecutar siempre, antes de cualquier otra accion)

1. Leer `.claude/project-context/project-context.json`
2. Obtener `paths.speckit_root` del JSON — si no existe, usar default `.claude/project-context/speckit-project-specs`
3. Determinar destino: `<speckit-root>/governance.md`
4. Ejecutar `updateGovernance(projectContext)`:
   - Leer template `.claude/templates/governance.template.md`
   - Interpolar placeholders con valores de project-context.json:
     - `[CLOUD_PROVIDER]` → `metadata.cloud_provider` (uppercase)
     - `[PRIMARY_REGION]` → `metadata.primary_region`
     - `[PROJECT_ID]` → `metadata.project_id`
     - `[CLUSTER_NAME]` → `sections.cluster_details.cluster_name`
     - `[GITOPS_PATH]` → `paths.gitops`
     - `[TERRAFORM_PATH]` → `paths.terraform`
     - `[POSTGRES_INSTANCE]` → `sections.databases.postgres.instance` o `N/A`
     - `[CONTAINER_REGISTRY]` → `N/A` si no definido
     - `[K8S_PLATFORM]` → derivar del cloud provider (GCP→GKE, AWS→EKS, otro→Kubernetes)
     - `[DATE]` → fecha actual ISO (YYYY-MM-DD)
   - **Si governance.md NO EXISTE**: crear el directorio `<speckit-root>/` si falta, escribir el archivo completo interpolado
   - **Si governance.md EXISTE**: actualizar SOLO la seccion `## Stack Definition`, preservar el resto (principios y ADRs pueden tener ediciones del usuario)
5. Reportar: `governance.md sincronizado en <speckit-root>/governance.md`

### 1. Verify project-context.json

Check if `.claude/project-context/project-context.json` exists:

**If NOT EXISTS**:
```
❌ BLOCKED: project-context.json not found.

```

**If EXISTS**:
- Read and validate structure
- Check required fields:
  - `metadata.project_id` or `sections.infrastructure.cloud_providers[0].project_id`
  - `metadata.primary_region` or `sections.infrastructure.cloud_providers[0].region`
  - `sections.cluster_details.cluster_name`
  - `paths.gitops` or `sections.gitops_configuration.repository.path`
  - `paths.terraform` or `sections.terraform_infrastructure.layout.base_path`
- If a required field is missing: warn but continue (gaia-scan may have generated a partial context)
- Report validation results

### 2. Feature Directory Bootstrap (Optional)

If `[feature-name]` argument provided:

a) **Determine Spec-Kit root**:
   - Read `paths.speckit_root` from project-context.json
   - If not set, use default `.claude/project-context/speckit-project-specs`
   - If that directory doesn't exist yet, it will be created automatically

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
     - `[PROJECT_ID]` → `metadata.project_id` or `sections.infrastructure.cloud_providers[0].project_id`
     - `[REGION]` → `metadata.primary_region` or `sections.infrastructure.cloud_providers[0].region`
     - `[CLUSTER]` → `sections.cluster_details.cluster_name`
     - `[GITOPS_PATH]` → `paths.gitops`
     - `[TERRAFORM_PATH]` → `paths.terraform`

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
## Spec-Kit Status

✅ governance.md synced: <speckit-root>/governance.md
✅ project-context.json valid
✅ GitOps path: <path>
✅ Terraform path: <path>

**Configuration**:
- Project ID: <id>
- Region: <region>
- Cluster: <cluster-name>
- Speckit Root: <speckit-root>

**You're ready to use Spec-Kit!**
```

## Error Handling

**project-context.json missing**:
```
❌ BLOCKED: project-context.json not found.
Run `npx gaia-scan` to initialize the project, then retry.
```

**Missing paths in project-context**:
```
⚠️ WARNING: GitOps path not set in project-context.json
This may cause issues with gitops-operator agent.
Recommendation: Run `npx gaia-scan` to regenerate project-context.json
```

**Invalid JSON**:
```
❌ ERROR: project-context.json is malformed
<JSON parse error>

Fix the JSON syntax or delete the file and run `npx gaia-scan` to regenerate.
```

## Notes

- **Automatic**: `speckit-planner` runs this as Step 0 before every action — the user rarely needs to invoke it directly
- **Read-only for project-context**: this command NEVER creates or modifies `project-context.json`
- **Always syncs governance**: governance.md is always updated to reflect the current project-context values
- **Idempotent**: safe to run multiple times
- **No config.json**: Spec-Kit 2.0 uses project-context.json + explicit arguments

## See Also

- `npx gaia-scan` - Create and configure project-context.json (run once per project)
- `/speckit.plan` - Generate implementation plan
- `/speckit.tasks` - Generate enriched task list
- `<speckit-root>/governance.md` - Project governance principles
