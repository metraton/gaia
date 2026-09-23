# Gaia Planner -- Reference

Lookup material for the process in `SKILL.md`: the plan markdown, the
questionnaire shape, gate examples, the states a plan moves through, and agent
routing.

## Plan markdown

The body written to `~/.gaia/scratch/<contract_id>.md` and saved with
`gaia plan save --content-file`. Sections scale with the plan: a small fix
keeps Approach, Tasks and whatever of the rest actually has content.

```markdown
## Plan

### Approach
{Technical strategy, 3-5 sentences, areas referenced loosely.}

### Decisions
{3-5 choices that shape the plan. For each:}
- D1: {the choice} -- motivated by AC-n
  - alternatives: {what else was possible, and why not}

### Feasibility Findings
{Per AC: what the end-state needs vs what exists, and how the plan closes the
gap (prerequisite task, existing capability) or why it stays open.}

### Assumptions
{Judgments the brief did not settle and you did not ask about.}

### Risks
{Execution risk: shared blast radius, an untested integration, an unknown still
being investigated.}

### Tasks
#### T1: {the outcome}
- agent: {owning specialist}
- covers: AC-1, AC-2        # mirrors `gaia task cover`
- depends on: none          # mirrors `gaia task depend`
- blast radius: {what it touches beyond its outcome}
- gates: {claim -> check, per gate}

**Context:** {the slice the executor needs, loosely referenced}

### Execution Order
{Dependency graph; tasks that share no dependency or blast radius run in
parallel.}

### Ordering Rationale
{Which dependency or overlap forces each edge.}

### Third-party checklist
{Actions that depend on someone other than the user. Never tasks.}
- [ ] {action} -- who: {person/team} -- validates: {what it confirms}
```

The markdown is for reading; the rows are what Gaia computes from. `covers` and
`depends on` in the markdown must match the `task cover` / `task depend` rows,
which are the ones `gaia brief verify` and the derived states read.

## Blocking questionnaire

When a divergence between brief and system changes the plan's structure and
only the user can resolve it, return `NEEDS_INPUT` with:

```
Decision needed: <one-line framing of the divergence>
Options:
  A) <concrete option, with what it implies for the plan>
  B) <concrete option, with what it implies for the plan>
Default if unspecified: <the safest option, or "none -- blocking">
```

`FALTA ACLARAR:` marks in the brief are returned the same way, one question per
mark.

## Gate examples

| Task outcome | type | evidence-type (what) | evidence-shape (how) |
|---|---|---|---|
| The list reader returns archived briefs | `command` | archived briefs are listed | `python3 -m pytest tests/cli/test_brief_list.py -q -k archived` |
| The lint rule is clean on the module | `code` | no lint findings in the module | `ruff check gaia/briefs` |
| The skill teaches the change flow | `semantic` | a reader applies the change flow unaided | one criterion per line: names who requests, who proposes, who approves; states that untouched verified tasks stay frozen |
| The executor checked its migration by hand | `self_review` | the migration was dry-run | the statement must name the command run and what it showed |

For `command` / `code` the shape is executed verbatim (tokenized, never through
a shell) and passes on exit 0. A check whose success is a non-zero exit has to
be phrased so success exits 0; the persisted gate has no field for another
expected code.

## States

Set by hand, with a verb:

| Object | States | Verb |
|---|---|---|
| Plan | `draft -> active -> closed` | `gaia plan set-status` |
| Plan pause | an `active` plan paused with a reason: still approved, but its tasks are not dispatched | `gaia plan pause --reason` / `gaia plan resume` |
| Task | `skipped` (set aside deliberately) | `gaia task set-status` |
| AC | `descoped` (terminal) | `gaia ac set-status` |
| Gate verdict | `pass`, or `fail` with `--cause` (`product`, `environment`, `broken_test`, `requirement_changed`) | `gaia task gate set-status` (verifier) |

Derived, never set (`gaia/briefs/store.py::derive_brief_state`, exposed by
`gaia brief show --json`):

- **stale** -- a verdict recorded before its gate, task goal or a covered AC
  changed, or sent back with `gaia task gate reverify`. The verdict is kept but
  proves nothing until a verifier records a new one.
- **task done** -- every gate passes and none is stale (or an audited override).
- **task blocked** -- waiting on a dependency that is neither done nor skipped.
- **AC done** -- not descoped, every covering task done, and at least one
  positive evidence row.
- **ready to close** -- every AC done or descoped.

A plan-change proposal moves `requested -> proposed -> approved -> applied`;
`gaia plan change list` shows where each one is, and `gaia plan history` lists
every replaced version with its reason.

## Agent routing

| Domain signal | Agent |
|---|---|
| Terraform, IaC, cloud resources | `platform-architect` |
| Kubernetes, Helm, Flux, manifests | `gitops-operator` |
| Live cluster, pods, logs, diagnostics | `cloud-troubleshooter` |
| App code, tests, CI/CD, Docker | `developer` |
| Gaia hooks, skills, agents, routing | `gaia-system` |
| Workspace, memory, email, automation | `gaia-operator` |
