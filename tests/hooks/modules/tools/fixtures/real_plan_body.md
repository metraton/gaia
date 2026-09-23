## Plan

### Approach
Build the neutral approval core first, inside the existing neutral pieces (sealed-payload builder, presentation, atomic activation, reserve/settle), then move each host onto it one at a time: Claude Code lifecycle (terminal event per call, no Stop sweep), Claude Code decision surface (Gaia-built question bound by tool_use_id), and OpenCode (own adapter, no borrowed ClaudeCodeAdapter, no regex). Only after both hosts sit on the core do the readers (AC-9) and the seven approval-cycle skills (AC-5) get rewritten, so they describe the final mechanism once. The last task integrates the final PR, installs it in /home/jorge/ws/me and records the live proofs. Every task is its own worktree and squash PR with the full test suite green, so main stays releasable between milestones. All repo reads for this plan were done against origin/main bbc2f09 with git grep/show (the live checkout is 2 commits behind).

### Decisions
- PD1: The new states (no result, withdrawn, expired window) are DERIVED or mapped onto existing vocabulary, not added to the approval_events CHECK. No-result means a reserved call with no terminal event when the window closes. Withdraw uses the existing REJECTED (pending) or REVOKED (grant). Expiry stays on the grant status. -- motivated by AC-1, AC-9
  - alternatives: add NO_RESULT/WITHDRAWN/EXPIRED to the event_type CHECK. SQLite needs a table rebuild of an append-only, hash-chained, trigger-guarded table, which risks the chain verification of every old row that AC-9 must keep reading. Rejected unless the executor proves a derived state cannot be shown by the readers, and then it becomes a plan change, not a silent migration.
- PD2: Core before hosts, hosts one at a time, readers and skills last. -- motivated by AC-2, AC-5
  - alternatives: vertical slice per host (duplicates the core twice and lets the hosts drift again, which is the defect AC-2 names). Skills in parallel with code (they would describe an intermediate mechanism, which AC-5 forbids).
- PD3: Closing event per host. Claude Code: register PostToolUseFailure for Bash. PostToolUse or PostToolUseFailure by tool_use_id is the only thing that advances or freezes a set. Cancellation fires nothing and leaves the call without result. OpenCode V1: tool.execute.after with the exit code taken from the tool metadata is the terminal event. An errored tool part for a call Gaia allowed (host refusal) closes it as not-run, never FAILED. -- motivated by AC-1, AC-3, AC-7
  - alternatives: keep a Stop sweep scoped by agent_id (still guesses termination, D1 forbids it). A session.idle sweep in OpenCode (same guess). Waiting for OpenCode V2 execute.after (out of scope per brief).
- PD4: Claude Code decision binding by registration, not by text. A PreToolUse on AskUserQuestion compares the questions object with the presentation Gaia produced, records SHOWN under that tool_use_id, and denies with a reason to Claude when they differ. PostToolUse with the same tool_use_id maps the chosen label to approve, reject or details. Free text (the Other row) never activates. -- motivated by AC-4
  - alternatives: match the question text only at PostToolUse (no proof the user saw the sealed surface). Keep the id in the label (N1 forbids it).
- PD5: One PR per task, each green, the last one is the AC-8 PR. -- motivated by AC-8
  - alternatives: a single long-lived branch (one unreviewable PR, and a mid-way failure blocks everything).

### Feasibility Findings
- AC-1: the Stop reconciler exists because the code assumes a failing Bash has no post event, and hooks.json registers no PostToolUseFailure (events today: PreToolUse, PostToolUse, SubagentStop, SessionStart, SessionEnd, PreCompact, Stop, TaskCompleted, SubagentStart, UserPromptSubmit). The host provides PostToolUseFailure with tool_use_id (design handoff 21733, CC DOC 4). Subagent hook payloads carry agent_id and agent_type (code.claude.com/docs/en/hooks, common input fields). Gap closable inside the plan.
- AC-2: OpenCode instantiates ClaudeCodeAdapter in pre and post and extracts approval_id by regex in Python and in plugin.ts. The Write/Edit protected-path request lives inside ClaudeCodeAdapter. Moving that policy out of the Claude adapter is part of T1, or the OpenCode task has nowhere to call.
- AC-3: request-set has no cwd, expect-exit or what field. TTLs are 5, 30 and 60 minutes in the writer and WINDOW is never sealed. The orchestrator guard denies reject/revoke although the tier classifier already treats them as non-T3. Claude Code activates with no binding. All closable in T1.
- AC-4: AskUserQuestion answers map question text to label and carry no option id, so the correlation must come from tool_use_id at Pre and Post (PD4). NOT documented: whether a PreToolUse can rewrite AskUserQuestion input without answering, and the exact PostToolUse tool_response shape (read empirically today). Hence validate, never rewrite.
- AC-5: seven skills. The design deletes template.md, agent-approval-protocol and claude-code-consent-adapter (the adapter registry points at the last one as skill_document, crosscheck names agent-approval-protocol in a comment, about 14 agent and skill files name them). Existing skill tests pin old wording (tests/skills: approval_skill_normalization, command_set_failed_docs, legacy_approval_skills_resolved).
- AC-7: the OpenCode V1 plugins page documents event names only. It says nothing about signatures or whether tool.execute.after fires on failure. The behaviour PD3 relies on is backed by the plugin source and a measurement recorded in it (execute.after fires only on a result, host refusals appear as an errored tool part). This is the one host rule not backed by official documentation.
- AC-9 readers found (origin/main):
  - gaia approvals list, show, stats, pending, history (bin/cli/approvals.py) through the approvals store, display and chain modules and the writer grant listing.
  - gaia doctor: checks that the approval tables and triggers exist, reads no rows.
  - gaia metrics: reads no approval table. It counts T3 from hook logs and the t3_degraded_block and approval_persist_failed event tags.
  - gaia defects: reads nothing about approvals today (zero matches). The task confirms it still renders and decides whether it should surface no-result calls.
  - OpenCode plugin: calls opencode-present and opencode-decide, keeps its own expected index and derives exit codes from tool metadata.
  - Not named in AC-9 but reading approval data: the handoff persister (maps grant status to a handoff approval decision, treats PENDING as granted and does not know FAILED or no-result), SessionStart cleanup (expired grants, stale pending count), approval_cleanup, the contract crosscheck (APPROVAL_ID_NOT_PENDING), decision_audit lanes, and the writer lapsed-grant predicate used by the expiry sweep.
- AC-8: CI runs pytest over tests/ with xdist, eslint, manifest build and pre-publish validation. Install into /home/jorge/ws/me is a local step on this side.

### Assumptions
- The brief decisions win over the design handoff where they differ: rollback stays optional (N4) and the question labels are fixed English Approve / Reject / Details (N7).
- A single blocked command follows the same cycle through the orchestrator (D9 default). The native ask-in-attempt path is not built.
- Pending approvals created before the deploy only need to display correctly. No new activation path accepts an id inside a label.
- The reservation TTL (10 min) stays as the reclaim mechanism for a call with no terminal event. Reclaiming marks no result, never FAILED, and never advances the set.
- Owner of every code and skill task is gaia-system (Gaia hooks, CLI, skills, plugin).

### Risks
- One repository, one hot set of files (claude_code.py, opencode.py, plugin.ts, writer.py, approvals CLI). Tasks are strictly serial, so a long review on one blocks the rest.
- PD3 in OpenCode rests on measured, undocumented V1 behaviour. A host update can change it. The live proof in OpenCode is the only end-to-end check.
- PD4 depends on undocumented details of AskUserQuestion at PreToolUse and PostToolUse. T3 must first capture a real payload as a test fixture.
- Removing the Stop sweep changes behaviour for calls that never report (no result instead of FAILED). Readers and skills must say so or users read it as a hang.
- Test churn: about 16 test files pin removed symbols or old skill wording.
- G3 (resumed agent without tool_use_id) is unresolved by design and can still deny a COMMAND_SET item during the live proof.

### Tasks
#### T1: Neutral approval core decides, seals, activates, consumes and withdraws for any host
- agent: gaia-system
- covers: AC-2, AC-3
- depends on: none
- blast radius: approvals store and writer (grant creation, TTL constants, reserve/settle), sealed-payload builder in the Bash validator, consent presentation, request-set CLI, orchestrator CLI guard, protected-path request logic now inside the Claude adapter
- gates: command, core contract tests red before and green after

**Context:** One builder seals every request (reactive Bash, request-set, file write) with what-it-does, a 30-minute window, cwd and expected exit codes per item, optional rollback, and the requesting session and agent. Activation takes a structured decision bound to a registered presentation (native ref) and always binds session and agent. A call matches only with the sealed cwd. An expected non-zero exit continues the set. The orchestrator may reject or revoke a pending approval, never approve. Neutral entry points follow design handoff 21733 (Call, CallEnded, Shown, Answer, RequestSet, Withdraw).

#### T2: In Claude Code a signed call closes only on its own terminal event
- agent: gaia-system
- covers: AC-1
- depends on: T1
- blast radius: hooks.json (new PostToolUseFailure registration), Claude adapter post-tool and Stop handling, hook state correlation, dispatch identity prefix
- gates: command, lifecycle tests red before and green after

**Context:** Register PostToolUseFailure for Bash. The core owns the pre/post correlation by tool_use_id. Delete the Stop reconciler. A subagent command still running when the orchestrator Stop fires stays in flight and completes as EXECUTED, and the next index needs no new signature. A real failure is FAILED. An expected exit continues. EXECUTED records the sealed bytes, not the command with the injected identity prefix.

#### T3: In Claude Code the user signs on a Gaia-built question bound by tool_use_id
- agent: gaia-system
- covers: AC-4
- depends on: T2
- blast radius: approvals present CLI output, consent presentation rendering, Claude adapter AskUserQuestion handling, hooks.json (PreToolUse AskUserQuestion), label-id helpers retired
- gates: command, decision-surface tests red before and green after

**Context:** gaia approvals present prints the short signature of N1 (who asks, window, what it does, commands with cwd and expected exits, rollback if any, how to verify) and returns the questions object with Approve / Reject / Details, fingerprints only behind Details. PD4 binding. Approve activates with no id in the label. Free text activates nothing. Capture a real AskUserQuestion Pre/Post payload as a fixture first.

#### T4: OpenCode runs the same core through its own adapter
- agent: gaia-system
- covers: AC-2, AC-3, AC-4
- depends on: T3
- blast radius: OpenCode adapter, bridge, plugin.ts (request, present, decide, after, errored part), opencode-present and opencode-decide CLI
- gates: command, OpenCode tests red before and green after

**Context:** The OpenCode adapter stops instantiating ClaudeCodeAdapter and stops extracting any id by regex (Python and TypeScript). It translates native events to the core entries and transports identity by shell env outside the sealed bytes. The question in the specialist session uses the same text and Approve / Reject / Details, bound by requestID. PD3 terminal events. Same 30-minute window, cwd match, expected exit and session/agent binding as Claude Code.

#### T5: Every reader shows the new states and still reads old rows correctly
- agent: gaia-system
- covers: AC-9
- depends on: T4
- blast radius: approvals list/show/stats/history/pending, doctor, metrics, defects, plugin reads, handoff persister, SessionStart cleanup, crosscheck, decision audit
- gates: command, reader tests over a fixture of old and new rows red before and green after

**Context:** Reader list in Feasibility Findings (AC-9). New states: no result, sealed window, sealed cwd per item, binding. Old rows (label-bound, FAILED from the Stop sweep, missing window/cwd/binding, orphan pendings) render without crash and without being counted as new-state failures or successes.

#### T6: The approval-cycle skills describe only the new mechanism
- agent: gaia-system
- covers: AC-5
- depends on: T5
- blast radius: seven skills, agents and skills that name the deleted ones, adapter registry skill_document, crosscheck comment, skill tests
- gates: command, skill content test red before and green after. semantic, skill rubric

**Context:** Rewrite orchestrator-present-approval (short), subagent-request-approval (cwd, expect-exit, what), execution (expected exit continues, no result), pending-approvals (approve does create a grant, D5), security-tiers (drop grant internals). Delete template.md, agent-approval-protocol and claude-code-consent-adapter and repoint their references. No prose that duplicates what code guarantees, no id in a label, no Stop sweep, no 5 or 60 minute windows.

#### T7: The final PR is green, installed, and both live proofs are recorded
- agent: gaia-system
- covers: AC-6, AC-7, AC-8
- depends on: T6
- blast radius: release/install into /home/jorge/ws/me, live approvals in both hosts
- gates: semantic, live event chains. semantic, CI and install

**Context:** Merge the last PR with CI green, install that build in /home/jorge/ws/me, run in Claude Code a 2-command COMMAND_SET signed once while the orchestrator ends its turn, and read gaia approvals show for it. After the user runs the OpenCode proof (checklist), read its chain the same way.

### Execution Order
T1, then T2, then T3, then T4, then T5, then T6, then T7. Strictly serial.

### Ordering Rationale
- T2, T3 and T4 need the core entry points from T1.
- T2 before T3: both edit the Claude adapter and hooks.json. The terminal-event contract must exist before the surface that produces the calls it closes.
- T3 before T4: the OpenCode question reuses the presentation T3 produces.
- T5 after T4: readers must show states produced by both hosts.
- T6 after T5: skills describe the final CLI and reader output.
- No pair runs in parallel: every task moves the same git repository (metraton/gaia), and T5 and T6 both edit the shared test tree.

### Third-party checklist
- [ ] Run the OpenCode live proof: a 2-command COMMAND_SET signed on the question in the specialist session -- who: the user (jorge), from an OpenCode session on the installed build -- validates: AC-7 (EXECUTED x2, no FAILED, session and agent binding present, no second signature)
- [ ] Review and merge each squash PR on metraton/gaia -- who: the user -- validates: AC-8 and the per-milestone integration
