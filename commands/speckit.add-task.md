---

**USAGE**: This command requires: `<speckit-root> <feature-name>`
**Example**: `/speckit.add-task spec-kit-tcm-plan 004-feature-name`
description: Add a single task to active feature's tasks.md with automatic enrichment. Use during implementation for ad-hoc tasks.
---

**USAGE**: This command requires: `<speckit-root> <feature-name>`
**Example**: `/speckit.add-task spec-kit-tcm-plan 004-feature-name`

The user input after `/speckit.add-task` MUST contain the high-level task intent (e.g., "Fix database connection pool leak"). Treat `$ARGUMENTS` below as that description and combine it with interactive prompts.

User input:

$ARGUMENTS

## Overview
Use this command to append or insert a **single** task in the currently active Spec-Kit feature (e.g., `specs/001-feature-name/tasks.md`). Every task must respect the phase ordering, ID sequencing, and formatting rules defined in `.claude/speckit/templates/tasks-template.md`.

**When to use:**
- Discovered new requirement during implementation
- Hotfix or bug discovered mid-development
- Plan changed and need to add unplanned tasks
- Ad-hoc investigation or research task needed

## Required Inputs (ask the user if any are missing)
1. **Task ID** (e.g., `T024`): MUST be unique and sequential for its section. Confirm with the user before writing.
2. **Phase**: One of the existing headers (e.g., `## Phase 3.1: Setup`). Ask where the task belongs.
3. **Parallel marker**: Whether the task can run in parallel (`[P]`). Default to sequential if unsure.
4. **Task description**: Clear action phrased as "Verb ..." and including file paths when applicable. Use the `/speckit.add-task` arguments as starting point and refine with the user.
5. **Dependencies / notes** (optional): If the task introduces new dependencies, capture them in the `## Dependencies` or checklist section.

## Procedure

1. **Resolve prerequisites** (no script needed):

   a) Extract `<speckit-root>` and `<feature-name>` from $ARGUMENTS.
      - If either is missing, ERROR and show usage example.

   b) Resolve `<speckit-root>` to an absolute path:
      - If it starts with `/`, use it as-is.
      - Otherwise, treat it as relative to the repository root.

   c) Derive paths:
      ```
      SPECKIT_ROOT = <resolved absolute speckit-root>
      FEATURE_DIR  = <SPECKIT_ROOT>/specs/<feature-name>
      TASKS        = <FEATURE_DIR>/tasks.md
      IMPL_PLAN    = <FEATURE_DIR>/plan.md
      ```

   d) Use Read to verify `<TASKS>` exists. If it does not exist:
      ```
      ❌ ERROR: tasks.md not found at <TASKS>
      Run /speckit.tasks <speckit-root> <feature-name> first to create the task list.
      ```

   e) Use Read to check which optional documents exist (for context):
      - `<FEATURE_DIR>/research.md`
      - `<FEATURE_DIR>/data-model.md`
      - `<FEATURE_DIR>/contracts/` (use Glob: `<FEATURE_DIR>/contracts/*`)
      - `<FEATURE_DIR>/quickstart.md`

2. **Load context**:
   - Read `tasks.md` to understand current phases, ordering, and existing task IDs.
   - Read `plan.md` and `<SPECKIT_ROOT>/governance.md` (from `paths.speckit_root` in project-context.json) to keep the new task aligned with architectural and governance rules.

3. **Gather missing details**:
   - If the command arguments do not include the task ID, phase, or `[P]` marker, explicitly ask the user.
   - Verify that the proposed ID is unused and fits the numeric ordering inside the chosen phase. If not, negotiate a new ID with the user.

4. **Insert the task with inline metadata**:
   - Add a markdown checklist line under the selected phase, keeping alphabetical order by ID.
   - Format: `- [ ] T0XX [P] Description` (omit `[P]` when sequential).
   - Include precise file paths or scripts where applicable.
   - **IMMEDIATELY add metadata comments** using the enrichment rules:

   **Enrichment Rules** (analyze task description):

   | Keywords | Agent | Base Tier |
   |----------|-------|-----------|
   | terraform, terragrunt, .tf, infrastructure, vpc, gke | terraform-architect | T0/T2/T3 |
   | kubectl, helm, flux, kubernetes, deployment, service | gitops-operator | T0/T2/T3 |
   | gcloud, GCP, cloud logging, IAM | cloud-troubleshooter | T0 |
   | docker, npm, build, test, CI, Dockerfile | devops-developer | T0-T1 |

   **Security Tier**:
   - T0: get, describe, show, list, logs, read
   - T1: validate, lint, template, format
   - T2: plan, dry-run, diff
   - T3: apply, push, create, delete, deploy (⚠️ HIGH RISK)

   **Tags**: Add ALL matching: #terraform #kubernetes #helm #docker #gcp #database #security #api #monitoring #setup #test #deploy #config #docs

   **Priority**: ❓ (low) | ⚡ (medium) | 🔥 (high impact)

   **Example**:
   ```markdown
   - [ ] T042 Configure CORS headers for API endpoints
     <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.75 -->
     <!-- 🏷️ Tags: #api #config #security -->
     <!-- 🎯 skill: api_configuration (6.0) -->
   ```

   For T2/T3 tasks, add high-risk warning:
   ```markdown
   - [ ] T055 Apply Terraform VPC changes
     <!-- 🤖 Agent: terraform-architect | 🚫 T3 | 🔥 0.95 -->
     <!-- 🏷️ Tags: #terraform #infrastructure #networking -->
     <!-- ⚠️ HIGH RISK: Analyze before execution -->
     <!-- 💡 Suggested: /speckit.analyze-task T055 -->
     <!-- 🎯 skill: terraform_infrastructure (12.0) -->
   ```

   Use the Edit tool to insert the new task into `tasks.md` at the correct position within the chosen phase.

5. **Update supporting sections** (if required):
   - `## Dependencies`: reflect any new blocking relationships.
   - `## Validation Checklist` / Governance Compliance: add or adjust items to keep requirements accurate.

6. **Validate task metadata**:
   - Verify metadata comments are present and accurate
   - Check agent assignment matches task type
   - Confirm security tier is appropriate
   - If T2/T3, ensure `⚠️ HIGH RISK` marker is present

7. **Auto-analyze task** (Quality gate):
   - After task is added and enriched, automatically execute `/speckit.analyze-task <Task-ID>`
   - This validates the task before continuing implementation
   - Display analysis output to user
   - User reviews to ensure task is well-defined and properly placed

8. **Report & next steps**:
   - Run `git diff` to show the inserted task WITH inline metadata
   - Summarize the new task details:
     * Task ID and phase
     * Description and file paths
     * Suggested agent and tier
     * High-risk status (if applicable)
     * Dependencies added
   - Mention that analysis was completed (step 7)
   - If task is T2/T3, remind user it will trigger analysis again during `/speckit.implement`

## Notes
- Never guess the task ID: always confirm with the user
- Avoid creating multiple tasks in one run; invoke `/speckit.add-task` per task
- Metadata is added INLINE during task creation (step 4)
- Honor security tiers: auto-detect T2/T3 operations from task keywords
- If the user request conflicts with governance.md principles, surface the concern and seek clarification before writing

## Example Usage
```bash
# During implementation, you discover a missing config:
/speckit.add-task "Configure CORS headers for API endpoints"

# System will:
# 1. Ask for task ID (e.g., T042)
# 2. Ask for phase (e.g., Integration)
# 3. Ask if parallel (e.g., yes, different file)
# 4. Insert task with inline metadata using Edit tool
# 5. Auto-analyze task (quality gate)
# 6. Show enriched result
```

## Inline Metadata Benefits
- ✅ No post-processing step required
- ✅ Consistent metadata format
- ✅ Automatic agent routing
- ✅ Security tier classification
- ✅ High-risk detection for T2/T3
