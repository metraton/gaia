# Tasks: [FEATURE NAME]

<!-- AUTO-ENRICHMENT CONFIG
  MIN_CONFIDENCE: 0.5
  When routing confidence score < MIN_CONFIDENCE, the agent MUST emit a LOW_CONFIDENCE warning:
    <!-- ⚠️ LOW_CONFIDENCE: score=0.3 — review surface classification manually -->
  Tasks with score >= MIN_CONFIDENCE proceed normally.
-->

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: plan.md (required), research.md, data-model.md, contracts/

## Execution Flow (main)
```
1. Load plan.md from feature directory
   → If not found: ERROR "No implementation plan found"
   → Extract: tech stack, libraries, structure
2. Load optional design documents:
   → data-model.md: Extract entities → model tasks
   → contracts/: Each file → contract test task
   → research.md: Extract decisions → setup tasks
3. Generate tasks by category:
   → Setup: GitOps setup, HelmRelease validation, image tag verification
   → Tests: contract tests, integration tests, health checks
   → Core: models, services, CLI commands
   → Infrastructure: Ingress-GCE, certificate management, DNS setup
   → Integration: DB, middleware, logging, observability
   → Polish: unit tests, performance, documentation, rollback procedures
4. Apply task rules:
   → Different files = mark [P] for parallel
   → Same file = sequential (no [P])
   → Tests before implementation (TDD)
5. Number tasks sequentially (T001, T002...)
6. Generate dependency graph
7. Create parallel execution examples
8. Validate task completeness:
   → All contracts have tests?
   → All entities have models?
   → All endpoints implemented?
9. Return: SUCCESS (tasks ready for execution)
```

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- **verify**: Each task MUST include a verification step (command, check, or observable outcome)

## Path Conventions
- **Single project**: `src/`, `tests/` at repository root
- **Web app**: `backend/src/`, `frontend/src/`
- **Mobile**: `api/src/`, `ios/src/` or `android/src/`
- Paths shown below assume single project - adjust based on plan.md structure

## Phase 3.1: Setup
- [ ] T001 Create project structure per implementation plan
  - verify: `ls -la src/` shows expected directories
  <!-- 🤖 Agent: terraform-architect | ✅ T1 | ❓ 0.70 -->
  <!-- 🏷️ Tags: #code #setup -->
  <!-- 🧠 Reasoning: Skill 'terraform_operations' matched (score: 2.0), Routed to terraform-architect, Security tier: T1 -->
  <!-- 🎯 skill: terraform_operations (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T002 Initialize [language] project with [framework] dependencies
  - verify: `[package-manager] list` shows dependencies installed
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags: #setup -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->
  <!-- ⚠️ LOW_CONFIDENCE: score=0.00 — review surface classification manually -->

- [ ] T003 [P] Configure linting and formatting tools
  - verify: `npm run lint` exits with code 0
  <!-- 🤖 Agent: gitops-operator | 👁️ T0 | ❓ 0.50 -->
  <!-- 🏷️ Tags: #config #setup -->
  <!-- 🧠 Reasoning: Skill 'configuration_management' matched (score: 2.0), Routed to gitops-operator, Security tier: T0 -->
  <!-- 🎯 skill: configuration_management (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->


## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3
**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**
- [ ] T004 [P] Contract test POST /api/users in tests/contract/test_users_post.py
  - verify: `pytest tests/contract/test_users_post.py` runs (fails before implementation)
  <!-- 🤖 Agent: devops-developer | ✅ T1 | 🔥 1.00 -->
  <!-- 🏷️ Tags: #api #hr #integration #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 10.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (10.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T005 [P] Contract test GET /api/users/{id} in tests/contract/test_users_get.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | 🔥 1.00 -->
  <!-- 🏷️ Tags: #api #hr #integration #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 10.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (10.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T006 [P] Integration test user registration in tests/integration/test_registration.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #hr #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 10.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (10.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T007 [P] Integration test auth flow in tests/integration/test_auth.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | 🔥 1.00 -->
  <!-- 🏷️ Tags: #security #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 10.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (10.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->


## Phase 3.3: Core Implementation (ONLY after tests are failing)
- [ ] T008 [P] User model in src/models/user.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ❓ 0.90 -->
  <!-- 🏷️ Tags: #hr -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 6.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: application_development (6.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T009 [P] UserService CRUD in src/services/user_service.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | 🔥 1.00 -->
  <!-- 🏷️ Tags: #api #hr #kubernetes -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 8.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: application_development (8.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T010 [P] CLI --create-user in src/cli/user_commands.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ❓ 0.90 -->
  <!-- 🏷️ Tags: #hr #setup -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 6.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: application_development (6.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T011 POST /api/users endpoint
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.50 -->
  <!-- 🏷️ Tags: #api #hr #integration -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 2.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: application_development (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T012 GET /api/users/{id} endpoint
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.50 -->
  <!-- 🏷️ Tags: #api #hr #integration -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 2.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: application_development (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T013 Input validation
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.70 -->
  <!-- 🏷️ Tags: #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 2.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T014 Error handling and logging
  <!-- 🤖 Agent: cloud-troubleshooter | 👁️ T0 | ❓ 0.50 -->
  <!-- 🏷️ Tags: #debug -->
  <!-- 🧠 Reasoning: Skill 'monitoring_observability' matched (score: 2.0), Routed to cloud-troubleshooter, Security tier: T0 -->
  <!-- 🎯 skill: monitoring_observability (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->


## Phase 3.4: Integration
- [ ] T015 Connect UserService to DB
  <!-- 🤖 Agent: gitops-operator | 👁️ T0 | ⚡ 0.60 -->
  <!-- 🏷️ Tags: #api #database #hr #kubernetes -->
  <!-- 🧠 Reasoning: Skill 'kubernetes_deployment' matched (score: 2.0), Routed to gitops-operator, Security tier: T0 -->
  <!-- 🎯 skill: kubernetes_deployment (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T016 Auth middleware
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags: #security -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T017 Request/response logging
  <!-- 🤖 Agent: cloud-troubleshooter | 👁️ T0 | ❓ 0.50 -->
  <!-- 🏷️ Tags:  -->
  <!-- 🧠 Reasoning: Skill 'monitoring_observability' matched (score: 2.0), Routed to cloud-troubleshooter, Security tier: T0 -->
  <!-- 🎯 skill: monitoring_observability (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T018 CORS and security headers
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.70 -->
  <!-- 🏷️ Tags: #infrastructure #security #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 2.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->


## Phase 3.5: Polish
- [ ] T019 [P] Unit tests for validation in tests/unit/test_validation.py
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 12.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (12.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T020 Performance tests (<200ms)
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #performance #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 5.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (5.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T021 [P] Update docs/api.md
  <!-- 🤖 Agent: devops-developer | ✅ T1 | 🔥 1.00 -->
  <!-- 🏷️ Tags: #api #docs #integration -->
  <!-- 🧠 Reasoning: Skill 'documentation_creation' matched (score: 8.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: documentation_creation (8.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T022 Remove duplication
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags:  -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T023 Run manual-testing.md
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #docs #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 7.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (7.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->


## Dependencies
- Tests (T004-T007) before implementation (T008-T014)
- T008 blocks T009, T015
- T016 blocks T018
- Implementation before polish (T019-T023)

## Parallel Example
```
# Launch T004-T007 together:
Task: "Contract test POST /api/users in tests/contract/test_users_post.py"
Task: "Contract test GET /api/users/{id} in tests/contract/test_users_get.py"
Task: "Integration test registration in tests/integration/test_registration.py"
Task: "Integration test auth in tests/integration/test_auth.py"
```

## Notes
- [P] tasks = different files, no dependencies
- Verify tests fail before implementing
- Commit after each task
- Avoid: vague tasks, same file conflicts

## Task Generation Rules
*Applied during main() execution*

1. **From Contracts**:
   - Each contract file → contract test task [P]
   - Each endpoint → implementation task
   
2. **From Data Model**:
   - Each entity → model creation task [P]
   - Relationships → service layer tasks
   
3. **From User Stories**:
   - Each story → integration test [P]
   - Quickstart scenarios → validation tasks

4. **Ordering**:
   - Setup → Tests → Models → Services → Endpoints → Polish
   - Dependencies block parallel execution

## Validation Checklist
*GATE: Checked by main() before returning*

- [ ] T024 All contracts have corresponding tests
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 7.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (7.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T025 All entities have model tasks
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags:  -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T026 All tests come before implementation
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #code #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 5.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (5.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T027 Parallel tasks truly independent
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags:  -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T028 Each task specifies exact file path
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.80 -->
  <!-- 🏷️ Tags: #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 3.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (3.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T029 No task modifies same file as another [P] task
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags:  -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->


**TCM Constitution Compliance**:
- [ ] T030 GitOps patterns enforced (no manual kubectl apply tasks)
  <!-- 🤖 Agent: terraform-architect | 🚫 T3 | ❓ 0.70 -->
  <!-- 🏷️ Tags: #docs #kubernetes -->
  <!-- 🧠 Reasoning: Skill 'terraform_operations' matched (score: 2.0), Routed to terraform-architect, Security tier: T3 -->
  <!-- 🎯 skill: terraform_operations (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T031 Concrete image tags specified (no :latest references)
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 1.00 -->
  <!-- 🏷️ Tags: #docker #test -->
  <!-- 🧠 Reasoning: Skill 'testing_validation' matched (score: 8.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: testing_validation (8.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T032 HTTPS endpoints required for external exposure
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.60 -->
  <!-- 🏷️ Tags: #api #web -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 3.0), Routed to devops-developer, Security tier: T1 -->
  <!-- 🎯 skill: application_development (3.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T033 Health checks included before DNS exposure
  <!-- 🤖 Agent: cloud-troubleshooter | 👁️ T0 | ❓ 0.50 -->
  <!-- 🏷️ Tags: #monitoring #networking #test -->
  <!-- 🧠 Reasoning: Skill 'monitoring_observability' matched (score: 2.0), Routed to cloud-troubleshooter, Security tier: T0 -->
  <!-- 🎯 skill: monitoring_observability (2.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T034 Certificate management strategy documented
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.00 -->
  <!-- 🏷️ Tags: #security #tcm -->
  <!-- 🧠 Reasoning: Dominant surface unresolved; use devops-developer only for reconnaissance and re-classify after evidence -->
  <!-- 🎯 default: devops-developer -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

- [ ] T035 Rollback procedures defined for deployments
  <!-- 🤖 Agent: devops-developer | 🚫 T3 | ❓ 0.60 -->
  <!-- 🏷️ Tags: #deploy #kubernetes -->
  <!-- 🧠 Reasoning: Skill 'application_development' matched (score: 3.0), Routed to devops-developer, Security tier: T3 -->
  <!-- 🎯 skill: application_development (3.0) -->
  <!-- 🧭 Adjacent surfaces: note any cross-layer checks or follow-up agents -->

## Dependency Graph
<!-- AGENT INSTRUCTION: Fill this section when generating tasks.md.
     Replace the example entries below with the actual task dependencies for this feature.
     - dependencies: maps each task to the list of tasks it requires to be complete first
     - parallel_groups: lists sets of tasks that can run simultaneously (all share no file conflicts)
     Omit a task from 'dependencies' if it has no prerequisites.
     This section is machine-readable — keep it valid YAML inside the fenced block.
-->
```yaml
dependencies:
  T002: [T001]
  T003: [T001]
  T008: [T004, T005, T006, T007]
  T009: [T008]
  T010: [T008]
  T011: [T009]
  T012: [T009]
  T013: [T011, T012]
  T014: [T011, T012]
  T015: [T008]
  T016: [T015]
  T018: [T016]
  T019: [T013]
  T020: [T013]
  T021: [T011, T012]
  T022: [T019, T020]
  T023: [T022]
parallel_groups:
  - [T004, T005, T006, T007]
  - [T008, T010]
  - [T019, T021]
```
