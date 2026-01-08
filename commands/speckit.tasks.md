---
description: Generate an actionable, dependency-ordered tasks.md for the feature based on available design artifacts.
---

The user input to you can be provided directly by the agent or as a command argument - you **MUST** consider it before proceeding with the prompt (if not empty).

User input:

$ARGUMENTS

**IMPORTANT**: This command now requires TWO arguments:
1. `<speckit-root>`: Path to spec-kit root directory (e.g., `spec-kit-tcm-plan`)
2. `<feature-name>`: Feature name (e.g., `004-project-guidance-deployment`)

**Example usage**:
```
/speckit.tasks spec-kit-tcm-plan 004-project-guidance-deployment
```

1. Extract `<speckit-root>` and `<feature-name>` from $ARGUMENTS. If not provided, ERROR and show usage example.
2. Derive paths manually (no check-prerequisites.sh needed):
   ```
   SPECKIT_ROOT = <speckit-root> (resolve to absolute if relative)
   FEATURE_DIR = $SPECKIT_ROOT/specs/<feature-name>
   IMPL_PLAN = $FEATURE_DIR/plan.md
   DATA_MODEL = $FEATURE_DIR/data-model.md
   CONTRACTS_DIR = $FEATURE_DIR/contracts/
   RESEARCH = $FEATURE_DIR/research.md
   QUICKSTART = $FEATURE_DIR/quickstart.md
   TASKS = $FEATURE_DIR/tasks.md
   ```
3. Check which design documents are available (use file existence checks):
2. Load and analyze available design documents:
   - Always read plan.md for tech stack and libraries
   - IF EXISTS: Read data-model.md for entities
   - IF EXISTS: Read contracts/ for API endpoints
   - IF EXISTS: Read research.md for technical decisions
   - IF EXISTS: Read quickstart.md for test scenarios

   Note: Not all projects have all documents. For example:
   - CLI tools might not have contracts/
   - Simple libraries might not need data-model.md
   - Generate tasks based on what's available

3. Generate tasks following the template:
   - Use `.claude/speckit/templates/tasks-template.md` as the base
   - Replace example tasks with actual tasks based on:
     * **Setup tasks**: Project init, dependencies, linting
     * **Test tasks [P]**: One per contract, one per integration scenario
     * **Core tasks**: One per entity, service, CLI command, endpoint
     * **Integration tasks**: DB connections, middleware, logging
     * **Polish tasks [P]**: Unit tests, performance, docs

4. Task generation rules:
   - Each contract file → contract test task marked [P]
   - Each entity in data-model → model creation task marked [P]
   - Each endpoint → implementation task (not parallel if shared files)
   - Each user story → integration test marked [P]
   - Different files = can be parallel [P]
   - Same file = sequential (no [P])

5. Order tasks by dependencies:
   - Setup before everything
   - Tests before implementation (TDD)
   - Models before services
   - Services before endpoints
   - Core before integration
   - Everything before polish

6. Include parallel execution examples:
   - Group [P] tasks that can run together
   - Show actual Task agent commands

7. Create FEATURE_DIR/tasks.md with enriched metadata:
   - Correct feature name from implementation plan
   - Numbered tasks (T001, T002, etc.)
   - Clear file paths for each task
   - Dependency notes
   - Parallel execution guidance
   - **INLINE metadata for EVERY task** (generated automatically, not post-processed)

8. **Automatic Task Enrichment (MANDATORY)**:
   For EACH task you generate, automatically add metadata comments IMMEDIATELY after the task line:

   **Task Format**:
   ```markdown
   - [ ] T001 Create GKE cluster configuration
     <!-- 🤖 Agent: terraform-architect | 👁️ T0 | ⚡ 0.85 -->
     <!-- 🏷️ Tags: #terraform #infrastructure #gke -->
     <!-- 🎯 skill: terraform_infrastructure (8.0) -->
   ```

   **Agent Routing Rules** (analyze task description):

   | Keywords in Task | Agent | Tier |
   |-----------------|-------|------|
   | terraform, terragrunt, .tf, infrastructure, vpc, gke, cloud-sql | terraform-architect | T0 (read), T2 (plan), T3 (apply) |
   | kubectl, helm, flux, kubernetes, k8s, deployment, service, ingress | gitops-operator | T0 (read), T2 (dry-run), T3 (push) |
   | gcloud, GCP, cloud logging, IAM, service account | cloud-troubleshooter | T0 (diagnostics) |
   | docker, npm, build, test, CI, pipeline, Dockerfile | devops-developer | T0-T1 |

   **Security Tier Detection**:
   - T0: describe, get, show, list, logs, read operations
   - T1: validate, lint, template, format operations
   - T2: plan, dry-run, diff operations
   - T3: apply, push, create, delete, deploy operations (⚠️ HIGH RISK)

   **Tag Generation** (add ALL matching):
   - Tech: #terraform #kubernetes #helm #docker #gcp #aws
   - Domain: #database #security #networking #api #monitoring
   - Work: #setup #test #deploy #config #docs #debug

   **Priority Signal**:
   - ❓ Low complexity (setup, config, docs)
   - ⚡ Medium complexity (implementation, integration)
   - 🔥 High impact (security, database, production)

   **High-Risk Warning** (for T2/T3 tasks):
   ```markdown
   - [ ] T042 Apply Terraform changes to production VPC
     <!-- 🤖 Agent: terraform-architect | 🚫 T3 | 🔥 0.95 -->
     <!-- 🏷️ Tags: #terraform #infrastructure #networking #production -->
     <!-- ⚠️ HIGH RISK: Analyze before execution -->
     <!-- 💡 Suggested: /speckit.analyze-task T042 -->
     <!-- 🎯 skill: terraform_infrastructure (12.0) -->
   ```

   **CRITICAL**: Generate ALL tasks with metadata inline. NO post-processing step required.

9. **Automatic Cross-Artifact Validation (Integrated)**:
   After generating tasks.md, IMMEDIATELY perform consistency analysis:

   a) **Load artifacts for validation**:
      - spec.md: Functional/Non-functional requirements, User Stories, Edge Cases
      - plan.md: Architecture, Data Model, Technical constraints
      - tasks.md: All generated tasks with metadata
      - governance.md: Architectural principles and standards

   b) **Detection passes** (automated):
      - **Duplication**: Flag near-duplicate requirements or tasks
      - **Ambiguity**: Detect vague adjectives ("fast", "robust") without measurable criteria, unresolved TODOs/placeholders
      - **Underspecification**: Requirements missing acceptance criteria, tasks referencing undefined components
      - **Governance alignment**: Check against principles (GitOps, Code-First Protocol, Security Tiers, etc.)
      - **Coverage gaps**: Requirements with zero tasks, tasks with no mapped requirement, missing non-functional coverage
      - **Inconsistency**: Terminology drift, data entities mismatch, task ordering contradictions

   c) **Severity assignment**:
      - CRITICAL: Violates governance MUST principle, requirement with zero coverage
      - HIGH: Duplicate/conflicting requirements, ambiguous security/performance
      - MEDIUM: Terminology drift, missing non-functional coverage
      - LOW: Style/wording improvements

   d) **Validation report** (inline output):
      ```markdown
      ### Validation Report

      **Metrics**:
      - Total Requirements: X
      - Total Tasks: Y
      - Coverage: Z% (requirements with ≥1 task)
      - Issues: A critical, B high, C medium, D low

      **Issues Found**:
      | ID | Severity | Category | Location | Summary | Recommendation |
      |----|----------|----------|----------|---------|----------------|
      | A1 | CRITICAL | Coverage | spec.md:L45 | "Performance monitoring" has zero tasks | Add task for metrics/logging setup |
      | A2 | HIGH | Ambiguity | spec.md:L89 | "Fast response time" lacks metric | Clarify: <200ms p95 latency? |

      **Next Actions**:
      - If CRITICAL issues: Resolve before proceeding to /implement
      - If only MEDIUM/LOW: Proceed with caution, address during implementation
      ```

   e) **Automatic remediation** (if no CRITICAL issues):
      - For LOW/MEDIUM issues: Add notes to tasks.md Dependencies section
      - For ambiguities: Flag for clarification during implementation
      - For coverage gaps: Generate missing tasks inline

   f) **Gate decision**:
      - If CRITICAL issues found: Pause and require user approval to proceed
      - If only HIGH/MEDIUM/LOW: Report and continue
      - If zero issues: Report success and proceed

   **CRITICAL**: Validation runs automatically. NO separate /analyze-plan command needed.

Context for task generation: $ARGUMENTS

The tasks.md should be immediately executable - each task must be specific enough that an LLM can complete it without additional context.
