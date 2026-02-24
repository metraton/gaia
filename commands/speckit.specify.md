---
description: Create or update the feature specification from a natural language feature description.
---

The user input to you can be provided directly by the agent or as a command argument - you **MUST** consider it before proceeding with the prompt (if not empty).

User input:

$ARGUMENTS

**IMPORTANT**: This command now requires arguments in format:
```
/speckit.specify <speckit-root> <feature-description>
```

**Example usage**:
```
/speckit.specify spec-kit-tcm-plan Add dark mode toggle to settings
```

The text after `<speckit-root>` is the feature description.

Given those arguments, do this:

1. Extract `<speckit-root>` (first word) and `<feature-description>` (remaining text) from $ARGUMENTS.
   - If speckit-root is missing, ERROR and show usage example.
   - If feature-description is empty, ERROR and ask user to provide a description.

2. **CLARIFICATION CHECK (NEW)**:
   Before proceeding, analyze the feature description for ambiguity using the clarification engine:

   ```python
   import sys
   sys.path.insert(0, '.claude/tools')
   from clarification import execute_workflow

   # Detect and resolve ambiguity in feature description
   result = execute_workflow(
       user_prompt=feature_description,
       command_context={"command": "speckit.specify"},
       ask_user_question_func=AskUserQuestion  # Claude Code tool
   )

   # Use enriched description for remaining steps
   feature_description = result["enriched_prompt"]

   # Log clarification if it occurred
   if result["clarification_occurred"]:
       from clarification import get_clarification_summary
       print(get_clarification_summary(result["clarification_data"]))
   ```

   **What gets clarified**:
   - Ambiguous service references (e.g., "add API endpoint" → which API?)
   - Ambiguous namespace (e.g., "deploy to cluster" → which namespace?)
   - Infrastructure keywords without specifics (e.g., "needs Redis" → which Redis instance?)
   - Environment confusion (e.g., user says "prod" but context is "non-prod")

   **Example clarification**:
   ```
   Feature description: "Add caching to the API"

   Clarification question:
   📦 Servicio
   ¿Qué API quieres modificar?

   Options:
     📦 tcm-api (NestJS | tcm-non-prod | Port 3001 | ✅ Running)
     📦 pg-api (Spring Boot | pg-non-prod | Port 8086 | ✅ Running)

   Enriched description:
   "Add caching to the API

   [Clarification - service_ambiguity]: tcm-api"
   ```

3. **Resolve paths and auto-number the feature**:

   a) Resolve `<speckit-root>` to an absolute path:
      - If it starts with `/`, use it as-is.
      - Otherwise, resolve relative to the repository root (locate it with Glob or treat the working directory as root).

   b) Determine the next feature number:
      - Use Glob to list all directories matching `<speckit-root>/specs/*/`.
      - For each directory basename, extract the leading number (e.g., `004` → 4).
      - Set `FEATURE_NUM` to the highest number found + 1, zero-padded to 3 digits (e.g., `005`).
      - If no directories exist yet, start at `001`.

   c) Generate the feature slug from the description:
      - Lowercase the description, replace non-alphanumeric characters with `-`, collapse consecutive dashes, strip leading/trailing dashes.
      - Take the first 3 meaningful words joined with `-`.
      - Combine: `FEATURE_NAME = "<FEATURE_NUM>-<slug>"` (e.g., `005-add-dark-mode`).

   d) Derive all paths:
      ```
      SPECKIT_ROOT   = <resolved absolute path>
      SPECS_DIR      = <SPECKIT_ROOT>/specs
      FEATURE_DIR    = <SPECS_DIR>/<FEATURE_NAME>
      SPEC_FILE      = <FEATURE_DIR>/spec.md
      ```

4. **Load project-context.json (MANDATORY)**:
   Read `.claude/project-context/project-context.json` to auto-fill infrastructure placeholders.

   **If project-context.json EXISTS**:
   - Extract: `project_details.id`, `project_details.region`, `project_details.cluster_name`
   - Extract: `gitops_configuration.repository.path`
   - Extract: `terraform_infrastructure.layout.base_path`
   - Extract (optional): `databases.postgres.instance`
   - Use these values to replace placeholders in spec template

   **If project-context.json MISSING**:
   - Warn user: "⚠️ project-context.json not found"
   - Suggest: "Run /speckit.init to create project configuration"
   - **Ask interactively**:
     - "GCP Project ID?"
     - "GCP Region?"
     - "GKE Cluster Name?"
     - "GitOps repository path?"
     - "Terraform repository path?"
   - Store answers temporarily for this spec
   - Recommend running /init after to persist configuration

5. **Create the feature directory and spec file**:

   a) Use the Write tool to create `<FEATURE_DIR>/spec.md`:
      - Read `.claude/speckit/templates/spec-template.md` to get the template content.
      - Replace infrastructure placeholders with project-context.json values:
        - `[PROJECT_ID]` → `project_details.id`
        - `[REGION]` → `project_details.region`
        - `[CLUSTER]` → `project_details.cluster_name`
        - `[GITOPS_PATH]` → `gitops_configuration.repository.path`
        - `[TERRAFORM_PATH]` → `terraform_infrastructure.layout.base_path`
        - `[POSTGRES_INSTANCE]` → `databases.postgres.instance` (if applicable)
      - Replace feature placeholders with derived content from feature description:
        - `[FEATURE NAME]` → `FEATURE_NAME`
        - `[###-feature-name]` → `FEATURE_NAME`
        - `[DATE]` → today's date (YYYY-MM-DD)
        - `[FEATURE_DESCRIPTION]` → user's feature description
        - `[FUNCTIONAL_REQUIREMENTS]` → analyze description for requirements
        - `[USER_STORIES]` → generate user stories from description
        - `[TECHNICAL_CONSTRAINTS]` → infer from description (or mark TBD)
      - Preserve section order and headings from template.
      - Flag any placeholders that could not be auto-filled.

   b) `<FEATURE_DIR>/plan.md`, `<FEATURE_DIR>/tasks.md`, and `<FEATURE_DIR>/contracts/` do NOT need to be created here; they are created by later commands.

6. **Auto-context verification**:
   - List which placeholders were auto-filled from project-context.json
   - List which placeholders need manual completion
   - If >5 manual placeholders remain, suggest running /clarify after

7. Report completion with feature name, spec file path, feature directory, and auto-filled context summary:
   ```markdown
   ✅ Feature specification created: <FEATURE_NAME>

   **Files**:
   - Spec: <SPEC_FILE>
   - Directory: <FEATURE_DIR>

   **Auto-filled from project-context.json**:
   - ✅ Project ID: <value>
   - ✅ Region: <value>
   - ✅ Cluster: <value>
   - ✅ GitOps path: <value>
   - ✅ Terraform path: <value>

   **Manual placeholders** (review and complete):
   - [ ] Performance targets (spec.md:L45)
   - [ ] Security requirements (spec.md:L67)

   **Next steps**:
   1. Review and complete manual placeholders in spec.md
   2. Run: /speckit.plan <speckit-root> <feature-name>
   ```

Note: Git workflow is managed separately by the user.
