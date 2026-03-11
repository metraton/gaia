# Spec-Kit Workflow -- Reference

For full templates, read from `{project-root}/speckit/templates/`. The templates below are summaries only.

## Template Paths

| Artifact | Template path |
|----------|---------------|
| spec.md | `speckit/templates/spec-template.md` |
| plan.md | `speckit/templates/plan-template.md` |
| tasks.md | `speckit/templates/tasks-template.md` |

## Artifact Summary: spec.md

Spec creation is handled conversationally by the orchestrator. The speckit-planner receives a completed spec.md as input.

```markdown
# Feature Specification: [FEATURE NAME]

**Feature Branch**: `[###-feature-name]`
**Created**: [DATE]
**Status**: Draft

## User Scenarios & Testing *(mandatory)*
### Primary User Story
### Acceptance Scenarios (Given/When/Then)
### Edge Cases

## Requirements *(mandatory)*
### Functional Requirements (FR-001, FR-002...)
### Key Entities *(if data involved)*

## Review Checklist
- [ ] No implementation details
- [ ] Requirements testable and unambiguous
- [ ] All [NEEDS CLARIFICATION] resolved
```

## Artifact Summary: plan.md

```markdown
# Implementation Plan: [FEATURE]

## Summary
## Technical Context (Language, Dependencies, Storage, Testing)
## Constitution Check (GitOps, Security, Scope)
## Project Structure (docs + source code layout)
## Phase 0: Research -> research.md
## Phase 1: Design -> data-model.md, contracts/, quickstart.md
## Phase 2: Task Planning Approach (describe only, do NOT create tasks.md)
## Progress Tracking
```

## Artifact Summary: tasks.md

Every task MUST include exit criteria and enough inline context to be self-contained.

```markdown
# Tasks: [FEATURE NAME]

## Phase 1: Setup
- [ ] T001 Create project structure
  - context: NestJS project, TypeScript 5.x, PostgreSQL
  - files: src/modules/auth/{controller,service,module}.ts
  - depends-on: none
  - exit-criteria: `ls src/modules/auth/` shows controller.ts, service.ts, module.ts
  - suggested-agent: devops-developer
  - tier: T0
  <!-- Tags: #setup #config -->

## Phase 2: Tests First (TDD)
- [ ] T004 [P] Contract test POST /api/users
  - context: REST API, Jest testing framework
  - files: tests/contract/test_users_post.py
  - depends-on: T001
  - exit-criteria: `pytest tests/contract/test_users_post.py` runs (fails before impl)
  - suggested-agent: devops-developer
  - tier: T1
  <!-- Tags: #test #api -->

## Phase 3: Core Implementation
## Phase 4: Integration
## Phase 5: Polish
## Dependencies (YAML dependency graph)
```

**High-risk tasks (T2/T3):**

```markdown
- [ ] T042 Apply Terraform changes to production
  - context: VPC module, shared networking layer
  - files: terraform/modules/vpc/main.tf
  - depends-on: T041
  - exit-criteria: `terraform show` confirms expected resources
  - suggested-agent: terraform-architect
  - tier: T3
  <!-- Tags: #terraform #infrastructure #production -->
  <!-- HIGH RISK: Analyze before execution -->
```

## Agent Routing Signals

| Signals in task | Agent |
|-----------------|-------|
| terraform, .tf, vpc, gke, iam | `terraform-architect` |
| kubectl, helm, flux, k8s, deployment | `gitops-operator` |
| gcloud, cloud logging, runtime drift | `cloud-troubleshooter` |
| docker, npm, build, test, CI, code | `devops-developer` |

## Security Tier Detection

| Tier | Verbs |
|------|-------|
| T0 | describe, get, show, list, logs, read |
| T1 | validate, lint, template, format |
| T2 | plan, dry-run, diff |
| T3 | apply, push, create, delete, deploy |
