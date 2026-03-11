---
description: Execute the implementation planning workflow using the plan template to generate design artifacts.
---

The user input to you can be provided directly by the agent or as a command argument - you **MUST** consider it before proceeding with the prompt (if not empty).

User input:

$ARGUMENTS

**IMPORTANT**: This command now requires TWO arguments:
1. `<speckit-root>`: Path to spec-kit root directory (e.g., `specs` or `/absolute/path/to/specs`)
2. `<feature-name>`: Feature name (e.g., `004-project-guidance-deployment`)

**Example usage**:
```
/speckit.plan specs 004-project-guidance-deployment
```

Given the implementation details provided as an argument, do this:

1. Extract `<speckit-root>` and `<feature-name>` from $ARGUMENTS. If not provided, ERROR and show usage example.

2. **Resolve all paths manually** (no script needed):

   a) Resolve `<speckit-root>` to an absolute path:
      - If it starts with `/`, use it as-is.
      - Otherwise, treat it as relative to the repository root.

   b) Clean `<feature-name>`: strip leading/trailing whitespace.

   c) Derive all paths:
      ```
      SPECKIT_ROOT = <resolved absolute speckit-root>
      FEATURE_DIR  = <SPECKIT_ROOT>/specs/<feature-name>
      FEATURE_SPEC = <FEATURE_DIR>/spec.md
      IMPL_PLAN    = <FEATURE_DIR>/plan.md
      SPECS_DIR    = <SPECKIT_ROOT>/specs
      ```

   d) Verify that `<SPECKIT_ROOT>` exists (Read the directory). If not, ERROR:
      ```
      ❌ ERROR: Spec-Kit root not found: <SPECKIT_ROOT>
      Run /speckit.init <feature-name> to create the feature structure first.
      ```

   e) **Create feature directory and initialize plan.md if they don't exist**:
      - If `<FEATURE_DIR>` does not exist (Read will fail), create it implicitly by writing the first file.
      - Read `.claude/speckit/templates/plan-template.md` to get the template content.
      - Use the Write tool to create `<IMPL_PLAN>` with the plan-template.md content.
        If `<IMPL_PLAN>` already exists, skip the Write step (do not overwrite existing work).
      - Report: `plan.md initialized at <IMPL_PLAN>` (or `plan.md already exists, skipping copy`).

   All future file paths must be absolute.

3. **Verify spec completeness**:
   The spec.md is provided by the orchestrator (created conversationally with the user).
   Read the spec and verify:
   - No unresolved `[NEEDS CLARIFICATION]` markers remain
   - Functional requirements are present and testable
   - If critical gaps exist, report them and ask the user to resolve before proceeding

4. Read and analyze the feature specification to understand:
   - The feature requirements and user stories
   - Functional and non-functional requirements
   - Success criteria and acceptance criteria
   - Any technical constraints or dependencies mentioned

5. Read governance.md to understand architectural principles and standards:
   - Path: `<SPECKIT_ROOT>/governance.md`
   - Extract: Code-First Protocol, GitOps principles, security tiers, commit standards

6. Execute the implementation plan template:
   - The plan-template.md content is now loaded in `<IMPL_PLAN>`
   - Set Input path to FEATURE_SPEC
   - Run the Execution Flow (main) function steps 1-9
   - The template is self-contained and executable
   - Follow error handling and gate checks as specified
   - Let the template guide artifact generation in the feature directory:
     * Phase 0 generates research.md
     * Phase 1 generates plan.md, data-model.md, contracts/, quickstart.md
     * Phase 2 generates tasks.md
   - Incorporate user-provided details from arguments into Technical Context: $ARGUMENTS
   - Update Progress Tracking as you complete each phase

7. Verify execution completed:
   - Check Progress Tracking shows all phases complete
   - Ensure all required artifacts were generated
   - Confirm no ERROR states in execution

8. Report results with the generated paths and artifacts created (no need to include the git branch).

Use absolute paths with the repository root for all file operations to avoid path issues.
