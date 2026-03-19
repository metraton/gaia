# Tasks: {FEATURE_NAME}

<!-- Feature: {feature-dir-absolute-path} -->
<!-- Tasks file: {this-file-absolute-path} -->

**Feature**: {feature-id}
**Date**: {date}
**Plan**: `{feature-dir}/plan.md`
**Total Tasks**: {count}

---

## Execution Contract

- Each task is self-contained: a `devops-developer` agent executes it without SpecKit knowledge
- Each task includes a verify command that proves completion
- When ALL acceptance criteria pass and verify succeeds, the executing agent MUST mark `[ ]` as `[x]` in this file
- Quality gate tasks validate the milestone before proceeding
- Tasks are grouped by milestone; milestones execute sequentially, tasks within a milestone may parallelize where marked [P]

---

## Task Format Reference

Every task MUST follow this structure exactly:

```markdown
### T{NNN}: {Title} [ ]
<!-- FR: {FR-numbers} -->
- **Description**: {What to do -- specific enough for an agent with no SpecKit context}
- **Files**: {Files to create/modify with paths relative to repo root}
- **Acceptance criteria**:
  - {Specific testable criterion}
  - {Another criterion}
- **Complexity**: {S|M|L}
- **Agent**: `{agent-name}`
- **Tier**: {T0|T1|T2|T3} ({reason})
- **Tags**: {#tag1 #tag2}
- **Verify**: `{command that proves it works}`
- **On completion**: Mark `[ ]` as `[x]` in this file
```

**Required fields**: Description, Files, Acceptance criteria, Verify, On completion.
**Optional fields**: FR comment, Complexity, Agent, Tier, Tags.

**Quality gate tasks** end each milestone:

```markdown
### T{NNN}: Milestone {N} Quality Gate [ ]
<!-- FR: {all-FR-numbers-covered-by-this-milestone} -->
- **Description**: Verify all FRs covered by Milestone {N} are satisfied.
- **Files**: N/A (validation only)
- **Acceptance criteria**:
  - All T{first}-T{last} verify commands pass
  - {Milestone-specific validation}
- **Agent**: `devops-developer`
- **Tier**: T0 (read-only validation)
- **Tags**: #validation #quality-gate
- **Verify**: `{command that validates all milestone tasks}`
- **On completion**: Mark `[ ]` as `[x]` in this file
```

---

## Task Generation Rules

When generating real tasks from plan.md and design artifacts:

1. **From plan.md** (required):
   - Extract tech stack, architecture, file structure
   - Each architectural component becomes one or more tasks
   - Setup tasks come first (project init, dependencies, config)

2. **From contracts/** (if exists):
   - Each contract file produces a contract test task [P]
   - Each endpoint produces an implementation task

3. **From data-model.md** (if exists):
   - Each entity produces a model creation task [P]
   - Relationships produce service layer tasks

4. **From research.md** (if exists):
   - Technical decisions inform setup and integration tasks

5. **Ordering**:
   - Setup before tests, tests before implementation (TDD), core before integration
   - Dependencies block parallel execution
   - Different files = can be parallel [P]; same file = sequential

6. **Milestones**:
   - Group related tasks into milestones (3-8 tasks per milestone)
   - Each milestone ends with a quality gate task
   - Milestone N+1 depends on Milestone N quality gate passing

7. **Cross-spec dependencies**:
   - If the feature shares contracts or schemas with other features, note the dependency at the top of the file and on each affected task

---

## Example Tasks

The following examples show the format for common task types. Replace with real tasks generated from your plan.md.

### Example: Milestone 1 -- Foundation

### T001: Create package structure [ ]
<!-- FR: FR-001 -->
- **Description**: Create the `src/myfeature/` package directory with `__init__.py`, establish the module hierarchy for core modules and tests.
- **Files**: `src/myfeature/__init__.py`, `src/myfeature/core/__init__.py`, `tests/myfeature/__init__.py`, `tests/myfeature/conftest.py`
- **Acceptance criteria**:
  - `python3 -c "import src.myfeature"` succeeds without error
  - Directory structure: `src/myfeature/`, `src/myfeature/core/`, `tests/myfeature/`
  - `src/myfeature/__init__.py` exports `__version__` string
- **Complexity**: S
- **Agent**: `devops-developer`
- **Tier**: T3 (creates files)
- **Tags**: #python #setup #foundation
- **Verify**: `python3 -c "from src.myfeature import __version__; print(__version__)"`
- **On completion**: Mark `[ ]` as `[x]` in this file

---

### T002: Implement base interface [ ]
<!-- FR: FR-001, FR-002 -->
- **Description**: Create `src/myfeature/core/base.py` with the abstract base class. Define the contract methods and type hints per `contracts/interface.md`.
- **Files**: `src/myfeature/core/base.py`
- **Acceptance criteria**:
  - Abstract base class importable via `from src.myfeature.core.base import BaseHandler`
  - Subclass that does not implement `handle()` raises `TypeError` on instantiation
  - Docstring documents the interface contract
- **Complexity**: S
- **Agent**: `devops-developer`
- **Tier**: T3
- **Tags**: #python #architecture #interface
- **Verify**: `python3 -c "from src.myfeature.core.base import BaseHandler; print('OK')"`
- **On completion**: Mark `[ ]` as `[x]` in this file

---

### T003: Milestone 1 Quality Gate [ ]
<!-- FR: FR-001, FR-002 -->
- **Description**: Verify all FRs covered by Milestone 1 are satisfied. Run import checks for all foundation modules.
- **Files**: N/A (validation only)
- **Acceptance criteria**:
  - All T001-T002 verify commands pass
  - Package structure is correct and importable
  - Base interface enforces its contract
- **Agent**: `devops-developer`
- **Tier**: T0 (read-only validation)
- **Tags**: #validation #quality-gate
- **Verify**: `python3 -c "from src.myfeature.core.base import BaseHandler; print('M1 GATE PASS')"`
- **On completion**: Mark `[ ]` as `[x]` in this file

---

### Example: Parallel implementation tasks

### T004: Implement handler A [P] [ ]
<!-- FR: FR-003 -->
- **Description**: Create `src/myfeature/handlers/handler_a.py` implementing `BaseHandler` for the A workflow. Include detection logic from plan.md section 3.2.
- **Files**: `src/myfeature/handlers/handler_a.py`
- **Acceptance criteria**:
  - Class `HandlerA` extends `BaseHandler` and implements all abstract methods
  - Detection logic returns correct results for known test fixtures
  - Includes `_source` metadata
- **Complexity**: M
- **Agent**: `devops-developer`
- **Tier**: T3
- **Tags**: #python #handler #implementation
- **Verify**: `python3 -c "from src.myfeature.handlers.handler_a import HandlerA; print('OK')"`
- **On completion**: Mark `[ ]` as `[x]` in this file

---

### T005: Implement handler B [P] [ ]
<!-- FR: FR-004 -->
- **Description**: Create `src/myfeature/handlers/handler_b.py` implementing `BaseHandler` for the B workflow. This task is independent of T004 and can run in parallel.
- **Files**: `src/myfeature/handlers/handler_b.py`
- **Acceptance criteria**:
  - Class `HandlerB` extends `BaseHandler` and implements all abstract methods
  - Returns empty result when no B indicators found
  - Includes `_source` metadata
- **Complexity**: M
- **Agent**: `devops-developer`
- **Tier**: T3
- **Tags**: #python #handler #implementation
- **Verify**: `python3 -c "from src.myfeature.handlers.handler_b import HandlerB; print('OK')"`
- **On completion**: Mark `[ ]` as `[x]` in this file

---

### Example: High-risk task

### T006: Wire integration with external system [ ]
<!-- FR: FR-005 -->
- **Description**: Modify `src/myfeature/orchestrator.py` to call the external API. Use atomic write pattern. Preserve existing behavior for offline mode.
- **Files**: `src/myfeature/orchestrator.py`
- **Acceptance criteria**:
  - External API called when available, graceful fallback when not
  - Atomic write: output file is either fully written or not present
  - Existing offline tests still pass
- **Complexity**: L
- **Agent**: `devops-developer`
- **Tier**: T3
- **Tags**: #python #integration #external-api
- **Verify**: `python3 -m pytest tests/myfeature/test_orchestrator.py -v --tb=short`
- **On completion**: Mark `[ ]` as `[x]` in this file
- **HIGH RISK**: Modifies integration point. Verify offline fallback still works.

---

## Dependency Graph

<!-- AGENT INSTRUCTION: Replace with actual dependencies when generating tasks.
     - dependencies: maps each task to the list of tasks it requires first
     - parallel_groups: lists sets of tasks that can run simultaneously
     Omit a task from 'dependencies' if it has no prerequisites.
     Keep valid YAML inside the fenced block.
-->
```yaml
dependencies:
  T002: [T001]
  T003: [T001, T002]
  T004: [T003]
  T005: [T003]
  T006: [T004, T005]
parallel_groups:
  - [T004, T005]
```

---

## Summary

<!-- AGENT INSTRUCTION: Replace with actual milestone summary table. -->

| Milestone | Tasks | Parallel | Complexity |
|---|---|---|---|
| M1: Foundation | T001-T003 | No (sequential) | 2S + 1QG |
| M2: Implementation | T004-T006 | Partial (T004, T005 parallel) | 2M + 1L |
| **Total** | **6** | | |

---

## FR Traceability Matrix

<!-- AGENT INSTRUCTION: Replace with actual FR traceability.
     Every FR from spec.md MUST appear with at least one primary task and quality gate.
-->

| FR | Description | Primary Task(s) | Quality Gate |
|---|---|---|---|
| FR-001 | {requirement} | T001 | T003 |
| FR-002 | {requirement} | T002 | T003 |
| FR-003 | {requirement} | T004 | T006 |
| FR-004 | {requirement} | T005 | T006 |
| FR-005 | {requirement} | T006 | T006 |
