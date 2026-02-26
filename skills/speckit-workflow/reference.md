# Spec-Kit Workflow — Reference

## Artifact Templates

### spec.md

```markdown
# Feature Specification: [FEATURE NAME]

**Feature Branch**: `[###-feature-name]`
**Created**: [DATE]
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### Primary User Story
[Main user journey in plain language]

### Acceptance Scenarios
1. **Given** [state], **When** [action], **Then** [outcome]

### Edge Cases
- What happens when [boundary]?

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: System MUST [capability]
- **FR-002**: [NEEDS CLARIFICATION: specific question]

### Key Entities *(if data involved)*
- **[Entity]**: [What it represents]

## Review Checklist
- [ ] No implementation details
- [ ] Requirements testable and unambiguous
- [ ] All [NEEDS CLARIFICATION] resolved
```

### plan.md

```markdown
# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Spec**: [link]

## Summary
[Primary requirement + technical approach]

## Technical Context
**Language/Version**: [e.g., TypeScript 5.0]
**Primary Dependencies**: [e.g., NestJS, React]
**Storage**: [e.g., PostgreSQL]
**Testing**: [e.g., Jest, Playwright]

## Constitution Check
- [ ] GitOps patterns enforced
- [ ] HTTPS for external endpoints
- [ ] Health checks included
- [ ] No :latest image tags

## Phase 0: Research
[Unknowns to resolve]

## Phase 1: Design
[Contracts, data model, architecture]

## Phase 2: Task Planning
[Approach for task generation - DO NOT create tasks.md]
```

### tasks.md

Every task MUST include a `verify:` line.

```markdown
# Tasks: [FEATURE NAME]

## Phase 3.1: Setup
- [ ] T001 Create project structure
  - verify: `ls -la src/` shows expected directories
  <!-- 🤖 Agent: devops-developer | 👁️ T0 | ❓ 0.70 -->
  <!-- 🏷️ Tags: #setup #config -->

## Phase 3.2: Tests First (TDD)
- [ ] T004 [P] Contract test POST /api/users
  - verify: `pytest tests/contract/test_users_post.py` runs
  <!-- 🤖 Agent: devops-developer | ✅ T1 | 🔥 1.00 -->
  <!-- 🏷️ Tags: #test #api -->
```

**High-risk tasks (T2/T3):**

```markdown
- [ ] T042 Apply Terraform changes to production
  - verify: `terraform show` confirms expected resources created
  <!-- 🤖 Agent: terraform-architect | 🚫 T3 | 🔥 0.95 -->
  <!-- 🏷️ Tags: #terraform #infrastructure #production -->
  <!-- ⚠️ HIGH RISK: Analyze before execution -->
  <!-- 💡 Suggested: /speckit.analyze-task T042 -->
```
