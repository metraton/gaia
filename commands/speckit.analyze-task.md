---

**USAGE**: This command requires: `<speckit-root> <feature-name>`
**Example**: `/speckit.analyze-task spec-kit-tcm-plan 004-feature-name`
description: Deep-dive analysis of a specific task before execution. Auto-triggered for high-risk tasks (T2/T3).
---

**USAGE**: This command requires: `<speckit-root> <feature-name>`
**Example**: `/speckit.analyze-task spec-kit-tcm-plan 004-feature-name`

The text typed after `/speckit.analyze-task` is the task identifier or description you must inspect. Treat `$ARGUMENTS` literally as that input and reference it throughout the analysis.

User input:

$ARGUMENTS

## Objective
Provide a thorough explanation of **one** task without executing it. Clarify what must be done, why it matters, expected side-effects, and how to verify completion using read-only/dry-run steps.

## Workflow
1. **Locate the task**
   - Find active feature in `specs/` directory
   - Open the task list (`specs/<feature>/tasks.md`)
   - If the user supplied a description instead of an ID, search the file and confirm the exact task with them before proceeding

2. **Collect context**
   - Record the phase, dependencies, `[P]` parallel marker, and any notes adjacent to the task entry
   - Review the implementation plan (`plan.md`) and `<speckit-root>/governance.md` (from `paths.speckit_root` in project-context.json) for constraints that apply to the task
   - Inspect referenced artifacts (contracts/, data-model.md, quickstart.md) so the explanation is accurate

3. **Extract routing metadata**
   - Read the task's inline metadata comments to understand routing information
   - Extract: agent assignment, security tier, tags, and confidence scores
   - Example metadata format:
     ```markdown
     - [ ] T055 Apply Terraform VPC changes
       <!-- 🤖 Agent: terraform-architect | 🚫 T3 | 🔥 0.95 -->
       <!-- 🏷️ Tags: #terraform #infrastructure #networking -->
     ```
   - Use this metadata for consultation only. Do NOT execute the task

4. **Produce the explanation**
   Structure the response in four sections:
   - **What it does** – plain-language summary of the task’s action and scope.
   - **Why it matters** – link to plan/constitution requirements and note risks of skipping it.
   - **Impact on system** – dependencies, affected services/repos, expected state after completion (call out T3 or manual approvals).
   - **How to validate** – explicit read-only or dry-run commands someone can run to confirm success.

## Rules
- Never mark the task as completed or modify `tasks.md`.
- Ask for clarification if multiple tasks match the input.
- Highlight manual approvals or cross-team coordination when applicable.
- Reference file paths and commands precisely; prefer absolute or repo-relative routes.

## Example prompts
```bash
/speckit.analyze-task T011
/speckit.analyze-task "Update ManagedCertificate for bot service"
/speckit.analyze-task "Verify health-check BackendConfig"
```

**Note:** This command is automatically triggered when executing high-risk tasks (T2/T3) via `/speckit.implement`. You can also call it manually to analyze any task before execution.

## Output expectations
Deliver a concise, well-structured answer (sections or bullet points) so the user can immediately understand intent, impact, and verification steps without running the task.
