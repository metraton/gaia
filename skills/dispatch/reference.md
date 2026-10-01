# Dispatch -- Reference

The commands, sequences and numbers behind `SKILL.md`, keyed by principle. The
principles say why; this file says how it is spelled today.

## Goal tokens (principle 1)

A hook extracts four literal tokens from the dispatch prompt --
`project=`, `task_id=`, `plan_id=` and `parent_handoff_id=`
(`hooks/modules/agents/dispatch_binding.py::extract_dispatch_binding`); prose
around them is not parsed.

| Token | Effect |
|---|---|
| `project=<name>` | The born row's `dispatch_project`, resolved from the token first and the cwd only as fallback. It is what renders `project:` and the project's declared `workflow:` line in `# Your Contract`. On every goal. |
| `task_id=<tasks.id>` | Binds the turn to a plan task: its gates render as `acceptance:` and the turn cannot self-`COMPLETE`. The id is `tasks.id`, not the plan position; `gaia task show <brief> <order>` prints both. |
| `plan_id=<N>` | Stamps the born row's `plan_id`. Like `task_id=` and `parent_handoff_id=`, it marks the dispatch as task execution rather than a free investigation or memory turn; it does not bind a task's gates, which `task_id=` alone does. |
| `parent_handoff_id=<N>` | Required on a verifier dispatch: `N` is the producer's contract row id. A dispatch without it runs with no binding to what it verifies. |

## Acceptance forms (principle 2)

| Gate type | How the goal words it | Who settles it |
|---|---|---|
| `command` / `code` | "Run `<command>` directly; it passes when it exits `<n>` with `<output>`." One atomic command, no pipe, no `&&`, no `$?`, no redirect. | The specialist loops on it; `verification-oracle` re-runs it. |
| `semantic` / `self_review` | A numbered rubric: what a reader opens and what each item must show. | `gaia-verifier` with `verification-rubric`; never the producer. |

A command gate whose pass condition is "no matches" is proven red before the
change: run it on the untouched tree and record the failing output as refuting
evidence, `gaia evidence add --brief <b> --ac <AC> --type command_output --gate
<gate_id> --negative --text '<output>'`. Green after, red before, same command.

## Worktree lifecycle (principle 6)

The specialist runs both verbs itself, inside its turn.

- Create: `gaia worktree create --repo <abs repo> --project <name>
  --contract-id <contract_id> --agent-id <agent_id> --branch <name> --base
  <commit-ish>`. `--contract-id` and `--agent-id` are required, which is why
  only the born turn can run it. Without `--base` the branch starts from the
  remote's default branch, fetched first; for work that accumulates on a
  feature branch, fetch that branch and pass `--base origin/<branch>`.
- Location: `<workspace>/.project-worktrees/<project>/<id>`, the workspace
  being the one that registered the repository.
- Release: `gaia worktree release <path>`. A clean tree is unlocked and
  removed; a dirty one has its diff deposited as evidence (`--brief --ac
  --workspace`, or `--contract-id` for a turn with no brief) and stays in place.
- Inspect: `gaia worktree list --repo <path>`, `gaia worktree show <path>`.

The orchestrator's frontmatter withholds `EnterWorktree` and `ExitWorktree`.

## The integration sequence (principle 3)

One ordered set per integrating turn, run from the turn's worktree:

1. `git -C <wt> fetch origin <branch>`
2. `git -C <wt> rebase origin/<branch>`
3. every command gate of the task, re-run on the rebased tree
4. the tests that guard the touched area
5. `git -C <wt> push origin HEAD:<branch>` -- never `--force`

Only the push leaves the machine, so it is the step that takes the user's
signature, named by branch (`subagent-request-approval`). A rejected push means
a sibling landed first: fetch, rebase and re-run 3 and 4, then ask for a new
push. A failed step freezes the rest of an approved set (`execution`).

## Waves (principle 4)

- Cap: the project's rule. The current plan's rule is at most two writing
  turns in flight at once.
- Composition: list the files each ready task touches; tasks that share a file
  go in one turn or in consecutive waves.
- Order: the change others build on integrates first; otherwise the smaller
  one, so the larger rebases over less.

## Verification binding (principle 5)

- A plan-bound producer closes `NEEDS_VERIFICATION`; both seams refuse its
  self-`COMPLETE` (`hooks/adapters/claude_code.py::_blind_verification_required`,
  `bin/cli/contract.py::cmd_finalize`).
- A failing verdict carries its cause -- `product`, `environment`,
  `broken_test` or `requirement_changed` (`agents/gaia-verifier.md`) -- and the
  orchestrator routes by it.
- CI: a push or PR turn waits on CI inside its own turn, with the forge's own
  tool (for GitHub, `pr checks <n> --watch`), and returns the final check table
  verbatim in `verbatim_outputs`, the commit SHA it covers, and the time it was
  observed. One dispatch covers push and green; the orchestrator never queries
  the forge itself. The task is done when that table is green for the PR head
  (`agent-response`). Measured: task 725 closed green while the PR's CI was red.
- Any other external state -- a deploy, a reconcile, a pipeline run -- follows
  the same rule: the owning specialist observes it in its turn and brings the
  literal output with what it covers and when it was seen.

## Sizing and steering (principles 7 and 8)

- The ceiling is a number of tool calls written in the goal, beside explicit
  permission to close partially. For reference, a nine-file identity-and-skills
  change with one integration was dispatched with a ceiling of about 110.
- Split: when a turn was cut near its close, the next dispatch implements and
  commits, and a separate one integrates.
- Steering: `SendMessage` is queued for the turn's next tool round; a resume
  names the contract row the turn owns; a fresh dispatch starts from zero.
