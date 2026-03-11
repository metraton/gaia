# SpecKit Redesign Proposal

**Date**: 2026-03-11
**Author**: gaia (meta-agent)
**Status**: IMPLEMENTED

---

## Part 1: Claude Code Plan Mode -- Research Findings

### What Plan Mode Is

Plan Mode is a first-class operating mode in Claude Code that restricts the agent to read-only tools. When active, Claude can Read, Glob, Grep, LS, WebSearch, WebFetch, Task (read-only subagents), and AskUserQuestion. It **cannot** Edit, Write, or run Bash commands.

The internal implementation uses two built-in tools:
- **`EnterPlanMode`** -- transitions Claude into plan mode (read-only research + plan creation)
- **`ExitPlanMode`** -- signals that the plan is ready for user approval

A "plan" in Claude Code is a markdown file written to a plans folder. There is no structured schema beyond text.

### How to Activate Plan Mode

| Method | When | Notes |
|--------|------|-------|
| `Shift+Tab` twice | Interactive session | Cycles through Normal -> Auto-Accept -> Plan |
| `/plan` command | Interactive session (v2.1.0+) | Types directly in prompt |
| `--mode plan` CLI flag | Session start | Forces plan mode from the start |
| `--append-system-prompt` | Session start | Can inject "use `exit_plan_mode` before executing" to auto-trigger |

### Can It Be Triggered Programmatically?

**Partially.** Key findings:

1. **CLI flag**: `claude --mode plan` starts a session in plan mode. This is the cleanest programmatic trigger.

2. **System prompt injection**: `--append-system-prompt "Before executing ANY tool, use exit_plan_mode to present your plan"` creates "Auto Plan Mode" -- Claude plans first, waits for approval, then executes.

3. **No hook-level trigger**: There is no `PreToolUse` hook that can force plan mode. Hooks can block tools (exit 2), but they cannot switch the mode. The plan mode "accept plan" prompt does not fire any hook event (there is an open GitHub issue requesting this).

4. **No `settings.json` key**: There is no `planMode: true` in settings. The mode is a session-level concept.

5. **Subagent limitation**: The `Task` tool (subagents) cannot be forced into plan mode programmatically. A subagent inherits the parent's system prompt but manages its own mode.

### Can We Integrate SpecKit with Plan Mode?

**My recommendation: No, and we do not need to.**

The reason: Plan mode solves a different problem. It prevents Claude from accidentally writing code before the user approves a plan. Our orchestrator already solves this through delegation and the approval protocol. When the user talks to the orchestrator about a spec, the orchestrator *already cannot* use tools directly -- it can only delegate. The speckit-planner agent *already produces artifacts* (spec.md, plan.md) that require user review.

What plan mode would give us is read-only research during spec creation. But our agents already do this through the investigation skill and security tiers (T0/T1 for research, T3 requiring approval for writes). Adding plan mode on top would create a second approval layer with no added safety.

**One narrow exception:** if we wanted the orchestrator itself to do spec creation (see Part 2), we could use `--append-system-prompt` to make the orchestrator draft a spec in conversational mode without tool use. But this conflicts with the orchestrator's identity as a router, not an executor.

---

## Part 2: Architectural Redesign Proposal

### Problem Statement

The current SpecKit architecture has these issues:

1. **`/speckit.implement` is the wrong abstraction.** Implementation is what agents do. SpecKit should produce plans and tasks, not execute them. Having speckit-planner "know how to implement" violates the principle that skills teach process, agents teach domain, and runtime enforces contracts.

2. **The workflow is command-driven, not conversation-driven.** The user must invoke `/speckit.specify`, then `/speckit.plan`, then `/speckit.tasks`, then `/speckit.implement` in sequence. This is rigid. The user should talk to the orchestrator, iterate on the spec conversationally, and the orchestrator decides when to delegate.

3. **speckit-planner does too much.** It currently owns specify + plan + tasks + implement. It should own plan + tasks only. Spec creation is conversational (orchestrator territory). Implementation is execution (agent territory).

4. **Tasks are not self-sufficient.** They reference the plan but live in a separate file. An executing agent must load multiple artifacts to understand one task. Tasks should be self-contained units of work.

5. **Governance connects at the wrong level.** Currently governance.md is checked during planning (Phase 2 step 5), but it should be a constraint on the plan itself, not something the planner checks mid-flight.

### Proposed Architecture

```
User <-> Orchestrator (conversational spec creation + iteration)
              | (when spec is ready, user approves)
         speckit-planner (creates plan from spec + governance constraints)
              | (plan presented for approval)
         speckit-planner (creates tasks from approved plan)
              | (tasks ready, returned to orchestrator)
         Orchestrator (executes tasks by routing each to the right agent)
              | (for each task)
         devops-developer / terraform-architect / etc. (executes task)
              | (agent reports completion with evidence)
         Orchestrator (marks [x], tracks progress, routes next task)
```

### Design Decisions (Opinionated)

#### 1. Spec creation: Orchestrator skill, not speckit-planner

**Recommendation: The orchestrator drives spec creation conversationally.**

The spec is a conversational artifact. The user describes what they want, the orchestrator asks clarifying questions, they iterate. This is routing + coordination, which is the orchestrator's core job.

Implementation: Add a `speckit-specify` skill to the orchestrator (not an agent). The orchestrator stays in its role (AskUserQuestion + conversational iteration), and when the spec is "done," it delegates to speckit-planner for planning.

This means `/speckit.specify` either becomes an orchestrator-level command or disappears entirely (the user just says "I want to build X" and the orchestrator knows to start the spec workflow).

**What changes:**
- Remove Phase 1 (Specify) from speckit-workflow SKILL.md
- Remove `/speckit.specify` command
- Add spec creation logic to CLAUDE.md as an orchestrator workflow (or a lightweight skill)
- speckit-planner receives a completed spec.md as input

#### 2. Task completion marking: Orchestrator marks [x], not the executing agent

**Recommendation: The orchestrator marks task completion.**

Reason: The orchestrator is the only entity with full visibility across tasks. An executing agent (devops-developer) should not need to know about tasks.md at all. It receives a work item, executes it, reports evidence of completion via AGENT_STATUS + EVIDENCE_REPORT, and the orchestrator decides whether the exit criteria are met.

This also solves a practical problem: the executing agent would need Write access to tasks.md, which creates file contention when tasks run in parallel.

**What changes:**
- Remove "When complete, mark [ ] as [x] in this tasks.md file" from task templates
- Add task tracking logic to the orchestrator (read AGENT_STATUS, verify evidence, mark [x])
- Tasks include exit criteria that the orchestrator can verify from the agent's report

#### 3. Task routing: Orchestrator routes dynamically, tasks suggest agents

**Recommendation: Tasks include an agent suggestion (metadata), but the orchestrator makes the final routing decision.**

This is already partially implemented (tasks have `<!-- Agent: devops-developer -->` metadata). The change is that this metadata becomes a *suggestion*, not a binding assignment. The orchestrator's surface routing table is the authority.

This means the orchestrator can re-route a task if the suggested agent is wrong, busy, or if the task's real scope crosses surfaces.

**What changes:**
- Task metadata `Agent:` becomes `Suggested-Agent:` (cosmetic but clarifies semantics)
- Orchestrator's task execution loop uses surface routing to confirm or override
- No change to task generation logic in speckit-planner

#### 4. `/speckit.implement` elimination

**Recommendation: Delete `/speckit.implement`. Replace with orchestrator task execution logic.**

`/speckit.implement` currently:
1. Loads tasks.md and all artifacts
2. Parses phases and dependencies
3. Executes tasks phase by phase
4. Marks completion

All of this is orchestrator work. The orchestrator reads tasks.md, routes each task to the right agent, processes the agent's AGENT_STATUS, marks completion, and moves to the next task.

**What changes:**
- Delete `commands/speckit.implement.md`
- Remove Phase 4 (Implement) from speckit-workflow SKILL.md
- Add task execution protocol to CLAUDE.md (a new section under Agent Invocation or a separate skill)
- The orchestrator manages: task ordering, dependency checking, parallel dispatch, progress tracking

#### 5. Governance boundary: Plan vs spec

**Decision: Governance constrains the plan architecturally. Governance informs the spec conversationally (for pushback and conflict detection).**

The plan is "how to build it" -- governance rules (use GitOps, no `:latest` tags, health checks required) are hard constraints at this level. The spec is "what to build" -- governance is loaded during spec creation not as a hard constraint but as conversational context, enabling the orchestrator to push back on ideas that conflict with governance and to detect architectural conflicts early.

**What changes:**
- Governance is loaded during spec creation for critical thinking and conflict detection (conversational, not constraining)
- Governance.md remains a mandatory input to plan creation (Phase 2) as a hard architectural constraint
- speckit-planner reads governance.md before generating plan.md
- Plan validation includes governance compliance check
- Tasks inherit governance constraints from the plan

#### 6. Task self-sufficiency

**Recommendation: Each task includes enough context to execute without loading multiple files.**

Currently tasks are terse: "T001 Create project structure." An agent receiving this must also load plan.md, data-model.md, contracts/, and research.md to understand what "project structure" means.

The fix: task generation (Phase 3 in speckit-planner) enriches each task with inline context. Not the full plan, but the relevant slice.

```markdown
- [ ] T001 Create project structure for user-auth service
  - context: NestJS project, TypeScript 5.x, PostgreSQL
  - files: src/modules/auth/{controller,service,module}.ts, src/entities/user.entity.ts
  - depends-on: none
  - exit-criteria: `ls src/modules/auth/` shows controller.ts, service.ts, module.ts
  - suggested-agent: devops-developer
  - tier: T3
```

**What changes:**
- Update task template to include inline context, file lists, and exit criteria
- speckit-planner extracts relevant plan slices per task during generation
- Remove the requirement for executing agents to load plan.md (tasks are self-contained)

### What Stays the Same

- **speckit-planner agent** -- still exists, still owns plan + tasks
- **Agent definitions** -- devops-developer, terraform-architect, etc. unchanged
- **Security tiers** -- T0-T3 classification unchanged
- **Agent protocol** -- AGENT_STATUS, EVIDENCE_REPORT unchanged
- **Approval protocol** -- PENDING_APPROVAL flow unchanged
- **`/speckit.plan`** -- still triggers plan creation (but receives spec as input from orchestrator)
- **`/speckit.tasks`** -- still triggers task generation
- **`/speckit.init`** -- still bootstraps governance.md
- **`/speckit.add-task`** -- still works for ad-hoc tasks
- **`/speckit.analyze-task`** -- still works for pre-execution analysis
- **governance.md** -- still exists, connects at plan level

### What Changes (Summary)

| Component | Current | Proposed |
|-----------|---------|----------|
| Spec creation | speckit-planner Phase 1 + `/speckit.specify` | Orchestrator conversational workflow |
| Plan creation | speckit-planner Phase 2 + `/speckit.plan` | Same (speckit-planner), but receives completed spec |
| Task creation | speckit-planner Phase 3 + `/speckit.tasks` | Same, but tasks are self-contained with inline context |
| Implementation | speckit-planner Phase 4 + `/speckit.implement` | **Deleted**. Orchestrator routes tasks to agents |
| Task completion | Executing agent marks [x] | Orchestrator marks [x] based on agent evidence |
| Agent routing | Static metadata in tasks | Dynamic routing by orchestrator (metadata is suggestion) |
| Governance | Checked at spec + plan level | Constrains plan architecturally; informs spec conversationally (pushback + conflict detection) |
| speckit-planner scope | specify + plan + tasks + implement | **plan + tasks only** |

### Files That Change

| File | Action | Reason |
|------|--------|--------|
| `commands/speckit.implement.md` | **DELETE** | Replaced by orchestrator task execution |
| `commands/speckit.specify.md` | **DELETE** | Replaced by orchestrator conversational spec |
| `skills/speckit-workflow/SKILL.md` | **REWRITE** | Remove Phase 1 (Specify) and Phase 4 (Implement), update flow diagram |
| `skills/speckit-workflow/reference.md` | **UPDATE** | Update artifact summaries, task format |
| `agents/speckit-planner.md` | **REWRITE** | Narrow scope to plan + tasks, remove "specify" and "implement" from identity |
| `CLAUDE.md` | **UPDATE** | Add spec creation workflow, add task execution protocol, update surface routing |
| `commands/speckit.plan.md` | **UPDATE** | Minor -- remove clarification step (already done by orchestrator) |
| `commands/speckit.tasks.md` | **UPDATE** | Update task format to include inline context + exit criteria |
| `commands/speckit.add-task.md` | **UPDATE** | Update task format to match new schema |
| `commands/speckit.init.md` | **KEEP** | No changes needed |
| `commands/speckit.analyze-task.md` | **KEEP** | No changes needed |

### New Components

| Component | Type | Purpose |
|-----------|------|---------|
| Orchestrator spec workflow | Section in CLAUDE.md or skill | Conversational spec creation protocol |
| Orchestrator task execution | Section in CLAUDE.md or skill | Task dispatch, tracking, completion marking |
| Task format v2 | Template update | Self-contained tasks with inline context |

---

## Part 3: Migration Path

### Phase 1: Narrow speckit-planner (low risk)

1. Rewrite `agents/speckit-planner.md` to scope = plan + tasks
2. Rewrite `skills/speckit-workflow/SKILL.md` to remove Phase 1 and Phase 4
3. Update `commands/speckit.plan.md` to remove inline clarification (orchestrator handles this)
4. Test: speckit-planner can still generate plan.md and tasks.md from a completed spec

### Phase 2: Move spec creation to orchestrator (medium risk)

1. Add spec creation workflow to CLAUDE.md
2. Delete `commands/speckit.specify.md`
3. Test: user can say "I want to build X" and orchestrator produces spec.md through conversation
4. Test: orchestrator delegates to speckit-planner with completed spec

### Phase 3: Move task execution to orchestrator (medium risk)

1. Add task execution protocol to CLAUDE.md
2. Delete `commands/speckit.implement.md`
3. Update task template to include self-contained context + exit criteria
4. Update `commands/speckit.tasks.md` for new task format
5. Test: orchestrator reads tasks.md, routes tasks, marks completion

### Phase 4: Polish (low risk)

1. Update `commands/speckit.add-task.md` for new format
2. Update `skills/speckit-workflow/reference.md`
3. Update surface routing table if needed
4. End-to-end test: full workflow from "I want to build X" to completed tasks

---

## Part 4: Risks and Trade-offs

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Orchestrator CLAUDE.md grows too large | Medium | Keep spec workflow and task execution as compact protocol sections, not verbose procedures. Consider splitting into on-demand skills. |
| Orchestrator context window fills with spec conversation | Medium | Spec conversation is inherently bounded (5-10 exchanges). The orchestrator already manages longer multi-agent conversations. |
| Loss of `/speckit.specify` as explicit entry point | Low | The user can still say "create a spec for X" and the orchestrator routes it. The explicit command was friction, not value. |
| Task self-sufficiency increases tasks.md size | Low | Inline context is a few lines per task. The alternative (loading 5 files per task) is worse. |
| Breaking existing specs/ directories | None | Migration is additive. Existing spec.md, plan.md, tasks.md files remain valid. Only the generation and execution flow changes. |

### Trade-offs

| Trade-off | Upside | Downside |
|-----------|--------|----------|
| Orchestrator owns spec | Natural conversational flow, no artificial command boundaries | Orchestrator CLAUDE.md gets more complex |
| Orchestrator owns task execution | Single point of visibility, dynamic routing, clean agent interfaces | Orchestrator does more work per session |
| Delete `/speckit.implement` | Agents are pure executors, no speckit knowledge needed | Loses the ability to "run all tasks" as a single command |
| Tasks self-contained | Agents execute faster, no multi-file loading | Task generation takes slightly longer, tasks.md is larger |
| Governance at plan level only | Specs are unconstrained creative artifacts | User might spec something that governance rejects at plan time (but this is actually good -- they learn early) |

### What About Plan Mode?

Plan mode is not needed for this redesign. The orchestrator's conversational spec creation is already read-only by design (the orchestrator cannot use tools). The speckit-planner's plan creation is already gated by approval. Adding plan mode would create redundant safety with no user benefit.

If in the future we want a "research mode" where the orchestrator explores the codebase before spec creation, that could use `--append-system-prompt` to trigger auto plan mode. But that is a separate optimization, not a requirement for this redesign.

---

## Recommendation

Execute Phase 1 (narrow speckit-planner) and Phase 3 (move task execution to orchestrator) first. These deliver the highest value: removing `/speckit.implement` eliminates the biggest architectural smell, and narrowing speckit-planner clarifies ownership.

Phase 2 (move spec creation to orchestrator) is the most impactful change but also the most subtle. The orchestrator needs a lightweight protocol for "conversational artifact creation" that does not exist yet. I recommend prototyping this with a single feature before committing to the full migration.

Phase 4 is cleanup that follows naturally.
