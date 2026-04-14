# Gaia Planner -- Reference

## Phase 1: Brief Creation

### Step 1: Load Context

Read `project-context.json` -- extract constraints relevant to the feature
(cloud, stack, git, services). These feed your thinking, not the user.
Missing -> BLOCKED, suggest `/scan-project`.

### Step 2: Size the Work

| Size | Signal | Questions | Brief? |
|------|--------|-----------|--------|
| S | Bug fix, config tweak, single-file change | 0-1 | No -- go directly to Task dispatch |
| M | Feature, new endpoint, integration | 2-3 | Yes |
| L | Project, multi-agent, cross-surface | 4-6 | Yes |

When uncertain, ask: "This feels like a [size] -- does that match?"

### Step 3: Ask Questions (M/L only)

Target gaps, not completeness:

- What problem does this solve? (skip if already clear)
- What constraints matter? (cloud, performance, security, timeline)
- How will we know it works? (acceptance criteria)
- What is explicitly NOT in scope?

Use AskUserQuestion. One question per round. Stop when you can write a
problem statement without guessing and list 2+ acceptance scenarios.

### Step 4: Write brief.md

Use `templates/brief-template.md` as structure. Write to:
`.claude/project-context/briefs/{feature-name}/brief.md`

Create the directory if it does not exist. The `{feature-name}` should be
a kebab-case slug derived from the feature title (e.g., `oauth2-auth`,
`planner-cleanup`).

**Acceptance criteria rules:**
- Every AC has a human description AND a verify command
- Verify commands are binary: they pass or fail (exit code 0 or non-zero)
- Examples: `curl -s http://localhost:3000/health | jq -e '.status == "ok"'`
- Examples: `terraform plan -detailed-exitcode` (exit 2 = changes pending)
- Examples: `pytest tests/test_auth.py -x` (exit 0 = pass)

If the user provides vague ACs ("it should be fast"), push back:
"Fast means what? Under 200ms p95? Under 500ms average?"

### Step 5: Present and Iterate

Show the full brief. Ask: "Does this capture what you want?" Apply
feedback, show updated sections. When confirmed, suggest: "Ready to
break this into tasks?"

## Phase 2: Task Dispatch

### Step 1: Read the Brief

Read brief.md. For S-sized work (no brief), use the user's original
request as the brief.

### Step 2: Decompose into Tasks

Each task MUST:
- **Fit in one context window.** If you need to say "see also", the task is too big. Split it.
- **Name its agent target.** Use signals from the brief to route: terraform keywords -> terraform-architect, k8s/helm -> gitops-operator, code/test/build -> developer.
- **Carry its own context slice.** The agent receives the task description, not the brief. Inline the relevant constraints, file paths, and tech stack.
- **Have ACs with verify commands.** Same rules as brief ACs -- binary pass/fail.

Task sizing: aim for 2-5 minutes of agent work. A task that takes 15 minutes
is three tasks that should have been split.

### Step 3: Dispatch via TaskCreate

```
TaskCreate:
  subject: "Imperative title (e.g., Create auth middleware)"
  description: |
    ## Objective
    [1-2 sentences]

    ## Context
    [Stack, paths, constraints -- everything the agent needs]

    ## Acceptance Criteria
    - AC-1: [description] | `verify command`
    - AC-2: [description] | `verify command`
```

Use `addBlockedBy` for dependencies. Independent tasks can run in parallel.

### Step 4: Verify Gate

When an agent completes a task, check the `json:contract` verification:

| Result | Action |
|--------|--------|
| `verification.result == "pass"` | TaskUpdate(completed) |
| `verification.result == "fail"` | Retry with failure output (max 2 retries) |
| 2 consecutive failures | TaskUpdate(blocked), AskUserQuestion with failure details |

For graded or optimization tasks where binary pass/fail does not apply,
route through the agentic-loop skill instead.

## Task List Checkpoint

Before dispatching any tasks, present the complete task list to the user
and wait for confirmation. The checkpoint must show:

- Task number, title, and target agent
- Dependencies (blocked-by relationships)
- Estimated size (S/M/L)

Ask: "Here are the tasks I plan to dispatch. Confirm to proceed, or
suggest changes." Do not dispatch until the user confirms.

## Completion Rollup

When all tasks complete successfully, update the brief frontmatter
`status` from `draft` (or `in-progress`) to `completed`. If any tasks
are blocked or failed, set status to `blocked` and report which tasks
need attention.

## TaskCreate Routing Limitation

Tasks created via TaskCreate dispatch to generic Claude Code subagents,
not to named Gaia agents. The `subject` field describes intent, and the
task description should include enough context for any capable subagent
to execute. Do not assume the receiving agent has Gaia skills or
project-context injection -- inline all necessary context in the task
description.

## Progress Tracking

Use TaskList/TaskGet for session status. For cross-session persistence,
optionally maintain a `progress.md` with task/agent/status rows.
