---
description: Execute the implementation planning workflow using the plan template to generate design artifacts.
---

The user input to you can be provided directly by the agent or as a command argument - you **MUST** consider it before proceeding with the prompt (if not empty).

User input:

$ARGUMENTS

**IMPORTANT**: This command now requires TWO arguments:
1. `<speckit-root>`: Path to spec-kit root directory (e.g., `spec-kit-tcm-plan` or `/absolute/path/to/spec-kit-tcm-plan`)
2. `<feature-name>`: Feature name (e.g., `004-project-guidance-deployment`)

**Example usage**:
```
/speckit.plan spec-kit-tcm-plan 004-project-guidance-deployment
```

Given the implementation details provided as an argument, do this:

1. Extract `<speckit-root>` and `<feature-name>` from $ARGUMENTS. If not provided, ERROR and show usage example.
2. Run `.claude/speckit/scripts/setup-plan.sh --json <speckit-root> <feature-name>` from the repo root and parse JSON for FEATURE_SPEC, IMPL_PLAN, FEATURE_DIR, FEATURE_NAME, SPECKIT_ROOT. All future file paths must be absolute.

3. **Automatic Clarification (Integrated)**:
   BEFORE proceeding with planning, perform ambiguity detection and clarification:

   a) **Scan for ambiguities** across these categories:
      - Functional Scope (user goals, out-of-scope, roles)
      - Data Model (entities, relationships, lifecycle)
      - Non-Functional (performance, security, reliability targets)
      - Integration (external APIs, failure modes)
      - Edge Cases (error handling, conflicts)
      - Terminology (vague adjectives like "robust", "intuitive")

   b) **Generate clarification questions** (max 5 total):
      - Only ask if answer materially impacts architecture, data model, or testing
      - Prioritize by Impact × Uncertainty heuristic
      - Use multiple-choice (2-5 options) or short-answer (≤5 words)
      - Skip if already answered in spec or low-impact

   c) **Interactive questioning** (if needed):
      - Present ONE question at a time
      - For multiple-choice, use markdown table with options
      - After answer, validate and integrate IMMEDIATELY into spec
      - Create `## Clarifications / ### Session YYYY-MM-DD` section
      - Update appropriate sections (Functional Requirements, Data Model, etc.)
      - Save spec after EACH answer (atomic updates)
      - Stop when: all critical ambiguities resolved, user says "done", or 5 questions reached

   d) **Clarification Results**:
      - If NO ambiguities found: Report "No critical ambiguities detected" and continue
      - If clarifications made: Report count, sections updated, suggest proceeding
      - If high-impact areas still unresolved: Warn user about rework risk

   **CRITICAL**: Clarification is automatic and integrated. No separate command invocation required.

4. Read and analyze the feature specification to understand:
   - The feature requirements and user stories
   - Functional and non-functional requirements
   - Success criteria and acceptance criteria
   - Any technical constraints or dependencies mentioned

5. Read governance.md to understand architectural principles and standards:
   - Path: `<speckit-root>/governance.md` (resolved from `paths.speckit_root` in project-context.json)
   - Extract: Code-First Protocol, GitOps principles, security tiers, commit standards

4. Execute the implementation plan template:
   - Load `.claude/speckit/templates/plan-template.md` (already copied to IMPL_PLAN path as plan.md)
   - Set Input path to FEATURE_SPEC
   - Run the Execution Flow (main) function steps 1-9
   - The template is self-contained and executable
   - Follow error handling and gate checks as specified
   - Let the template guide artifact generation in $SPECS_DIR:
     * Phase 0 generates research.md
     * Phase 1 generates plan.md, data-model.md, contracts/, quickstart.md
     * Phase 2 generates tasks.md
   - Incorporate user-provided details from arguments into Technical Context: $ARGUMENTS
   - Update Progress Tracking as you complete each phase

5. Verify execution completed:
   - Check Progress Tracking shows all phases complete
   - Ensure all required artifacts were generated
   - Confirm no ERROR states in execution

6. Report results con las rutas generadas y los artefactos creados (no es necesario incluir la rama git).

Use absolute paths with the repository root for all file operations to avoid path issues.
