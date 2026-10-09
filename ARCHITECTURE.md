# Architecture

## What is Gaia?

Gaia is an orchestration system for Claude Code agents. It turns a single Claude Code session into a coordinated multi-agent system with security enforcement, context injection, surface-based routing, episodic memory, and deterministic response contracts.

The package is published as `@jaguilar87/gaia` on npm and installed into a project's `.claude/` directory via symlinks. Gaia ships as a **single, unified plugin** named `gaia` — one artifact carrying the full orchestrator, all agents, all skills, all hooks, all tools, and all config. Every install runs the full orchestrator surface, and the T3 mutation-safety floor for the main session is unconditional (see `skills/gaia-patterns/reference.md` → "Plugin Packaging").

## Core Concepts

| Concept | Definition |
|---------|-----------|
| **Agent** | A Markdown file in `agents/` defining identity, scope, skills, and delegation rules |
| **Skill** | Injected procedural knowledge (in `skills/`) -- the HOW for agents |
| **Hook** | Python scripts that intercept tool calls before and after execution |
| **Tool** | Python modules in `tools/` providing context assembly, memory, and validation |
| **Config** | JSON files in `config/` defining contracts, rules, surface routing, and security |
| **Orchestrator** | Agent definition in `agents/gaia-orchestrator.md`, activated via `settings.json: { "agent": "gaia-orchestrator" }`; routes requests to the correct agent via on-demand skills |

## Runtime Flow

```
User request
    |
    v
Orchestrator (agents/gaia-orchestrator.md, activated via settings.json agent config)
    |  Identity defined in agent definition file
    |  Routes from agent identity plus DB-backed surface configuration
    |  Skills loaded on-demand: agent-response
    v
Orchestrator dispatches to agent
    |  Routes by surface classification
    v
pre_tool_use.py  (PreToolUse hook)
    |  1. Validate the Task/Agent dispatch and birth the contract row its turn will claim
    |  2. Validate Bash commands (security gate)
    |  3. Validate SendMessage (agent resumption)
    v
subagent_start.py  (SubagentStart hook)
    |  Claim the born row and inject the dispatch kernel:
    |  # Your Contract, # Your CLI, # How the user works
    v
Agent executes
    |  Pulls project context and memory on demand (gaia context, gaia memory),
    |  fills its contract row with gaia contract set|add|fill, closes with finalize
    v
subagent_stop.py  (SubagentStop hook)
    |  1. Gate the turn's own agent_contract_handoffs row
    |  2. Clean up the agent's approval, process update_contracts
    |  3. Record workflow metrics, audit the workflow, store the episode
    v
Orchestrator reads the row's closing state (via agent-response skill)
    |  COMPLETE -> summarize to user
    |  APPROVAL_REQUEST (with approval_id) -> get approval -> resume via SendMessage
    |  NEEDS_INPUT -> ask user -> resume via SendMessage
    |  NEEDS_VERIFICATION -> dispatch an independent verifier
    |  BLOCKED -> report blocker
```

## Hook Pipeline: pre_tool_use.py

Entry point for all Bash and Task/Agent tool validation. With `Bash(*)` in the settings.json allow list, the hook is the sole security gate.

### Bash Command Validation (BashValidator)

Order is short-circuit -- first match wins:

```
1. blocked_commands.py    --> permanently denied patterns (exit 2)
2. Claude footer strip    --> auto-remove Co-Authored-By (transparent updatedInput)
3. Commit message check   --> conventional commits format validation
4. cloud_pipe_validator   --> block pipes/redirects/chains on cloud CLIs (exit 0, corrective)
5. mutative_verbs.py      --> scan tokens 1-5 for MUTATIVE verbs
   |                          If mutative + no active grant -> block with an approval_id
   |                          If mutative + active grant -> allow (T3)
   |                          If not mutative -> safe by elimination (T0)
```

### Task/Agent Validation

```
1. Session events digest    --> recent git commits, pushes, file mods, built first
2. TaskValidator            --> validate agent name, check available agents; a refusal ends here
3. Row birth                --> birth the agent_contract_handoffs row the turn will claim
                                (hooks/adapters/tool_policy.py::_maybe_birth_dispatched_row);
                                a failed birth degrades the row and never blocks the dispatch
4. Digest delivery          --> the digest, when not empty, is handed to the host
```

The subagent's context is not assembled here: it is rendered at SubagentStart
from the born row (see Dispatch Kernel).

### SendMessage Validation (PreToolUse matcher)

```
1. Agent ID format check    --> must match /^a[0-9a-f]{16,}$/
                                (gaia.contract.validator.AGENT_ID_PATTERN_TEXT,
                                 the single source of truth for every copy)
2. Message presence check   --> non-empty message required
```

Grant activation is not part of SendMessage validation: the user's answer to the
approval question is read by the PostToolUse `AskUserQuestion` handler
(`hooks/adapters/claude_code.py::_handle_ask_user_question_result`).

## Agent Completion Pipeline: subagent_stop.py

Fires after every agent tool completes:

```
1. Gate the turn's own agent_contract_handoffs row
   |  Found unfinalized, or no row at all -> reject the close (exit 2)
   |  A rejected turn's substantive text is preserved and relayed back
   |  (modules/agents/rejected_turn_relay.py)
2. Clean up the agent's approval --> modules/security/approval_cleanup.py
3. Process update_contracts     --> modules/context/context_writer.py
4. Record workflow metrics      --> modules/audit/workflow_recorder.py
5. Audit the workflow           --> modules/audit/workflow_auditor.py; anomalies reach the episode
6. Store the episode            --> modules/memory/episode_writer.py
```

The agent writes its contract during the turn with `gaia contract set|add|fill`
and promotes it with `gaia contract finalize`; the hook gates that row and does
not parse the final message (`hooks/adapters/subagent_stop_core.py::run_subagent_stop`).

## Surface Routing: surface_router.py

Classifies user tasks into surfaces using whole-token signal matching against the DB-backed `surface_routing` table in `~/.gaia/gaia.db`. The source of truth is each agent's `routing:` frontmatter block; `tools/scan/seed_surface_routing.py` seeds the table at install time (mirror of `seed_contract_permissions.py`). The retired `config/surface-routing.json` previously held this table.

| Surface | Primary Agent | Typical Signals |
|---------|--------------|-----------------|
| `live_runtime` | cloud-troubleshooter | pods, services, logs, kubectl, gcloud |
| `gitops_desired_state` | gitops-operator | manifests, Flux, Helm, Kustomize |
| `iac` | platform-architect | Terraform, Terragrunt, IAM, modules |
| `app_ci_tooling` | developer | CI/CD, Docker, package tooling |
| `planning_specs` | gaia-planner | briefs, plans |
| `gaia_system` | gaia-system | hooks, skills, agents/, CLAUDE.md |
| `workspace` | gaia-operator | memory, email, file transfers |

**Classification algorithm:**
1. Normalize task text
2. Score each surface by command (1.5) and artifact (1.0) matches
3. Keep surfaces with score >= 1.0 and >= 55% of top score
4. If no match and current agent maps to a surface, use agent-fallback (score 0.2)
5. If still no match, dispatch reconnaissance agent

**Investigation brief** is generated per agent from routing results. It contains role assignment (primary/cross_check/adjacent), required evidence fields, stop conditions, and whether a CONSOLIDATION_REPORT is required.

## Dispatch Kernel: kernel_builder.py

A dispatched subagent starts with a small, data-only kernel, not a preloaded
project-context snapshot. At PreToolUse the hook births the turn's
`agent_contract_handoffs` row; at SubagentStart `claim_dispatch_row`
correlates the starting subagent to that row and
`hooks/modules/context/kernel_builder.py` renders three blocks from it:

```
# Your Contract       identity, goal, role and surface, the dispatch project and the
                      workflow it declared, can_read / can_write section scope, and the
                      acceptance gates when the turn is bound to a plan task
# Your CLI            the gaia verbs the turn uses to pull context (gaia context),
                      memory (gaia memory) and its own contract (gaia contract)
# How the user works  the user's standing rows, bodies inline, minus those whose
                      audience is the orchestrator alone
```

`can_read` is the menu of `project_context_contracts` sections the turn pulls on
demand. A fourth block, `# Your skills`, is rendered only for a host that
preloads no skill body (OpenCode). Every builder is fail-safe: a failed read
shrinks the block and never fails SubagentStart.

## Approval Flow

T3 approval lifecycle, the same on Claude Code and OpenCode:

```
1. A specialist's T3 command is blocked by the Bash gate with an approval_id,
   or the specialist requests it plan-first with `gaia approvals request-set`,
   sealing its phrases (what it does, impact, rollback, verification, shared
   state) and the sha256 of every file the command runs or reads.
2. The specialist ends its turn APPROVAL_REQUEST with the approval_id; it asks
   the user nothing.
3. The orchestrator runs `gaia approvals question <approval_id> ...` and opens
   its output unchanged: AskUserQuestion on Claude Code, `question` on
   OpenCode. Each command is one one-line question opened by
   [ GAIA-SECURITY ], at most four per signature (gaia/approvals/surface.py);
   `--details` re-asks with what it does, impact, verification and rollback.
4. Gaia checks the call before it opens -- the PreToolUse hook on Claude Code,
   the plugin on OpenCode, where only the orchestrator may open it -- and ties
   each answer to its signature. Approve on every question approves; Reject on
   any one rejects the whole signature.
5. The orchestrator resumes the same specialist, which retries the
   byte-identical command; the grant is single-use and consumed at match, and
   a sealed file that changed asks for a new signature.
```

Pending approvals and grants are rows in `~/.gaia/gaia.db` (`gaia/approvals/store.py`), read with `gaia approvals pending|show`.

## Response Contract Validation

Every turn persists one contract in its `agent_contract_handoffs` row, filled during the turn with `gaia contract set|add|fill` and promoted by `gaia contract finalize`; the final message carries no envelope. SubagentStop gates the row (`hooks/adapters/subagent_stop_core.py`), and `hooks/modules/agents/contract_validator.py` checks the envelope:

- **agent_status**: `agent_state` is one of IN_PROGRESS, BLOCKED, NEEDS_INPUT, APPROVAL_REQUEST, NEEDS_VERIFICATION, COMPLETE (only COMPLETE is terminal), with `agent_id`, `pending_steps` and `next_action`
- **evidence_report**: the seven lists `patterns_checked`, `files_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `cross_layer_impacts`, `open_gaps`, plus `verification`; a COMPLETE turn needs `verification.result` of `pass`
- **consolidation_report**: carried when the task is multi-surface or a cross-check

A row that is missing or was never cleanly finalized rejects the close (exit 2); the rejected turn's substantive text is preserved and relayed back (`hooks/modules/agents/rejected_turn_relay.py`). A COMPLETE turn bound to a plan task cannot seal itself: `_blind_verification_required` makes it close NEEDS_VERIFICATION for an independent verifier.

## Adapter Layer

The adapter layer decouples business logic from CLI-specific protocols. Located at `hooks/adapters/`.

### Components
- `types.py` -- Normalized dataclasses (HookEvent, ValidationRequest, ValidationResult, etc.)
- `base.py` -- Abstract HookAdapter interface
- `claude_code.py` -- Claude Code adapter (stdin JSON <-> normalized types)
- `channel.py` -- Distribution channel detection (plugin vs npm)

### Flow
```
Claude Code stdin JSON -> ClaudeCodeAdapter.parse_event() -> normalized HookEvent
    -> Business logic (unchanged) ->
ClaudeCodeAdapter.format_validation_response() -> Claude Code stdout JSON
```

### Plugin Distribution
Gaia ships as the single unified `gaia` plugin. There is **no `dist/` bundle** --
the npm package root (`@jaguilar87/gaia`) IS the plugin. The root
`.claude-plugin/plugin.json` (with hooks embedded inline) and `hooks/hooks.json`
are generated from `build/gaia.manifest.json` at pack time
(`prepack` -> `generate:plugin-root`) and tracked in git. Claude Code
auto-discovers agents, skills, commands, and hooks from their respective
directories at the package root.

See `.claude-plugin/marketplace.json` for the self-hosted marketplace, which
advertises the one `gaia` plugin with a `source: npm` object
(`{"source": "npm", "package": "@jaguilar87/gaia"}`) -- Claude Code installs the
package into its plugin cache and reads the inline-hooks `plugin.json` from the
package root.

## Adapter Coupling Points

The adapter layer connects Claude Code's hook protocol to Gaia business logic through 5 coupling points. Each coupling point is a thin entry point that delegates to the adapter for JSON parsing/formatting and to business logic modules for decisions.

### CP-1: `hooks/pre_tool_use.py` -- Command Validation Entry Point

| Attribute | Value |
|-----------|-------|
| **File** | `hooks/pre_tool_use.py` |
| **Hook event** | PreToolUse |
| **What it does** | Security gate for all Bash, Task, and Agent tool invocations. Validates commands (blocked patterns, mutative verbs, approval grants), validates Task/Agent dispatches and births the contract row each dispatched turn claims. |
| **Adapter methods called** | `ClaudeCodeAdapter.parse_event()`, `ClaudeCodeAdapter.parse_pre_tool_use()`, `ClaudeCodeAdapter.format_validation_response()` |
| **Business logic modules** | `security/blocked_commands.py`, `security/mutative_verbs.py`, `security/approval_grants.py`, `tools/bash_validator.py`, `tools/task_validator.py`, `agents/dispatch_identity.py` (row birth) |

### CP-2: `hooks/post_tool_use.py` -- Audit Logging Entry Point

| Attribute | Value |
|-----------|-------|
| **File** | `hooks/post_tool_use.py` |
| **Hook event** | PostToolUse |
| **What it does** | Records execution audit logs, detects critical events (git commits, pushes, file modifications), updates active session context. Reads pre-hook state for timing and tier classification. |
| **Adapter methods called** | `ClaudeCodeAdapter.parse_event()`, `ClaudeCodeAdapter.parse_post_tool_use()` |
| **Business logic modules** | `audit/logger.py` (`log_execution`), `audit/event_detector.py` (`detect_critical_event`), `core/state.py` (`get_hook_state`, `clear_hook_state`) |

### CP-3: `hooks/subagent_stop.py` -- Contract Validation + Memory Entry Point

| Attribute | Value |
|-----------|-------|
| **File** | `hooks/subagent_stop.py` |
| **Hook event** | SubagentStop |
| **What it does** | Fires after every agent completes. Gates the turn's own agent_contract_handoffs row, records workflow metrics, audits the workflow, stores the episode, processes `update_contracts`, and cleans up the agent's approval. |
| **Adapter methods called** | `ClaudeCodeAdapter.adapt_subagent_stop()` (delegates to `adapters/subagent_stop_core.py::run_subagent_stop`) |
| **Business logic modules** | `audit/workflow_recorder.py`, `audit/workflow_auditor.py`, `memory/episode_writer.py`, `context/context_writer.py` (`process_update_contracts`), `security/approval_cleanup.py`, `agents/rejected_turn_relay.py` |

### CP-4: `hooks/modules/tools/hook_response.py` -- Response Formatting

| Attribute | Value |
|-----------|-------|
| **File** | `hooks/modules/tools/hook_response.py` |
| **Hook event** | (shared utility, used by PreToolUse callers) |
| **What it does** | Provides `build_hook_permission_response()` -- a shared builder for hookSpecificOutput JSON. Delegates to the adapter's `format_validation_response()` so all permission responses share a single code path. |
| **Adapter methods called** | `ClaudeCodeAdapter.format_validation_response()` |
| **Business logic modules** | None (pure formatting bridge) |

### CP-5: `hooks/hooks.json` -- Hook Configuration

Besides the `hooks` event table, `hooks/hooks.json` carries a top-level `modules` key naming the Claude Code mods under `hooks/mods/` (generated from the build manifest's `host_mods` list and checked by `scripts/check_hooks_drift.py`).

| Attribute | Value |
|-----------|-------|
| **File (plugin channel)** | `hooks/hooks.json` -- paths use `${CLAUDE_PLUGIN_ROOT}/hooks/` prefix |
| **File (npm channel)** | `hooks/hooks.json` (symlinked into `.claude/hooks/`) |
| **What it does** | Maps Claude Code hook events to handler scripts. Defines which events fire which entry points, the tool matchers (Bash, Task, Agent, `*`), and permissions (allow/deny lists). |
| **Events configured** | PreToolUse (Bash, Task, Agent, SendMessage, AskUserQuestion, and the file and web tools), PostToolUse (Bash, Task, AskUserQuestion), PostToolUseFailure (Bash), SubagentStop, SubagentStart, SessionStart (`startup\|resume\|clear\|compact\|fork`), SessionEnd, PreCompact, PostCompact, Stop, TaskCompleted, UserPromptSubmit (sparse notices) |

### HookAdapter ABC Contract

The abstract interface in `hooks/adapters/base.py` defines the adapter contract. Each CLI backend provides a concrete implementation.

| Method | Signature | Description |
|--------|-----------|-------------|
| `parse_event` | `(stdin_data: str) -> HookEvent` | Parse raw stdin JSON into a normalized, CLI-agnostic event |
| `format_validation_response` | `(result: ValidationResult) -> HookResponse` | Format a validation result for the CLI's permission protocol |
| `format_completion_response` | `(result: CompletionResult) -> HookResponse` | Format a completion result for SubagentStop |
| `format_context_response` | `(result: ContextResult) -> HookResponse` | Format a context injection result |
| `detect_channel` | `() -> DistributionChannel` | Detect whether Gaia is running as NPM or PLUGIN |

Additional abstract methods for P1/P2 events: `adapt_session_start`, `format_bootstrap_response`, `adapt_subagent_start`. `adapt_stop`, `adapt_task_completed`, `format_quality_response` and `format_verification_response` belong to the Claude Code adapter alone, the only host whose Stop and TaskCompleted hooks call them.

**Invariants:**
1. Business logic modules NEVER see `HookResponse`. They produce `ValidationResult`, `CompletionResult`, etc.
2. The adapter NEVER modifies business logic results -- it only translates format.
3. Adding a new hook event requires ONLY a new adapter method. Zero changes to business logic modules.

### Adding a New Hook Event

To add support for a new Claude Code hook event (one not yet in `HookEventType`):

1. **Add enum value** to `HookEventType` in `hooks/adapters/types.py` (already present for all 19 known events).
2. **Add adapter method** to `ClaudeCodeAdapter` in `hooks/adapters/claude_code.py` -- implement `adapt_<event_name>(raw: dict) -> <ResultType>` and the corresponding `format_<result>_response()` if a new result type is needed.
3. **Add extract/format methods** for the event type -- the extract method pulls typed data from the raw payload, the format method builds the CLI response JSON.
4. **Create hook script entry point** -- a new `hooks/<event_name>.py` file that reads stdin, calls `adapter.parse_event()`, delegates to business logic, and writes the response to stdout.
5. **Add entry to `hooks/hooks.json`** mapping the event name to the new script.

**Zero changes to business logic modules required.** The adapter is the only layer that touches CLI-specific JSON.

### Adding a New CLI Backend

To support a CLI other than Claude Code (e.g., a hypothetical Cursor or Windsurf integration):

1. **Subclass `HookAdapter`** from `hooks/adapters/base.py`.
2. **Implement `parse_event()`** and all `format_*()` methods to translate between the new CLI's JSON protocol and the normalized types in `hooks/adapters/types.py`.
3. **No changes to business logic or adapter interface.** The same `ValidationResult`, `CompletionResult`, `ContextResult`, etc. flow through unchanged.

**Business logic modules remain untouched.** They consume and produce normalized types; only the adapter layer changes.

## Key Files Reference

| File | Purpose |
|------|---------|
| `agents/gaia-orchestrator.md` | Orchestrator identity and routing (activated via settings.json agent config) |
| `surface_routing` table (gaia.db) | Surface routing (agent table, signals, dispatch); seeded from agent `routing:` frontmatter by `tools/scan/seed_surface_routing.py` |
| `skills/agent-response/SKILL.md` | Contract status handling protocol (on-demand) |
| `hooks/pre_tool_use.py` | PreToolUse hook entry point |
| `hooks/subagent_stop.py` | SubagentStop hook entry point |
| `hooks/modules/tools/bash_validator.py` | Bash command security gate |
| `hooks/modules/tools/task_validator.py` | Task/Agent invocation validator |
| `hooks/modules/security/blocked_commands.py` | Permanently denied command patterns |
| `hooks/modules/security/mutative_verbs.py` | CLI-agnostic mutative verb detector |
| `hooks/modules/security/approval_grants.py` | Approval grant lifecycle management |
| `hooks/adapters/subagent_stop_core.py` | SubagentStop contract gate and close |
| `hooks/modules/agents/contract_validator.py` | Agent contract envelope validator |
| `hooks/modules/context/kernel_builder.py` | Dispatch kernel rendered at SubagentStart |
| `hooks/modules/context/context_writer.py` | Progressive context enrichment |
| `tools/context/surface_router.py` | Surface classification and investigation briefs (reads DB-backed `surface_routing`) |
| `tools/scan/seed_surface_routing.py` | Install-time seeder: agent `routing:` frontmatter -> `surface_routing` table |
| `tools/memory/episodic.py` | Episodic memory storage |
| `agents/*.md` | Agent identity definitions |
| `skills/*/SKILL.md` | Injected procedural knowledge |
| `bin/gaia` + `bin/cli/*.py` | Unified `gaia` CLI; subcommands auto-discovered from `bin/cli/` |
