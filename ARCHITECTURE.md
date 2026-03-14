# Architecture

## What is gaia-ops?

gaia-ops is an orchestration system for Claude Code agents. It turns a single Claude Code session into a coordinated multi-agent system with security enforcement, context injection, surface-based routing, episodic memory, and deterministic response contracts.

The package is published as `@jaguilar87/gaia-ops` on npm and installed into a project's `.claude/` directory via symlinks.

## Core Concepts

| Concept | Definition |
|---------|-----------|
| **Agent** | A Markdown file in `agents/` defining identity, scope, skills, and delegation rules |
| **Skill** | Injected procedural knowledge (in `skills/`) -- the HOW for agents |
| **Hook** | Python scripts that intercept tool calls before and after execution |
| **Tool** | Python modules in `tools/` providing context assembly, memory, and validation |
| **Config** | JSON files in `config/` defining contracts, rules, surface routing, and security |
| **Orchestrator** | The root `CLAUDE.md` that routes user requests to the correct agent |

## Runtime Flow

```
User request
    |
    v
Orchestrator (CLAUDE.md)
    |  Routes by surface classification
    v
pre_tool_use.py  (PreToolUse hook)
    |  1. Inject project-context into agent prompt
    |  2. Inject session events
    |  3. Validate Bash commands (security gate)
    |  4. Validate Task/Agent invocations
    v
Agent executes
    |  Uses tools, follows skills, emits AGENT_STATUS
    v
subagent_stop.py  (SubagentStop hook)
    |  1. Read transcript, extract task description
    |  2. Capture workflow metrics
    |  3. Validate response contract
    |  4. Detect anomalies
    |  5. Store episodic memory
    |  6. Process CONTEXT_UPDATE blocks
    v
Orchestrator processes AGENT_STATUS
    |  COMPLETE -> summarize to user
    |  PENDING_APPROVAL -> get approval -> resume
    |  NEEDS_INPUT -> ask user -> resume
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
   |                          If mutative + no active grant -> generate nonce, block
   |                          If mutative + active grant -> allow (T3)
   |                          If not mutative -> safe by elimination (T0)
6. gitops_validator       --> GitOps policy for kubectl/helm/flux
```

### Task/Agent Validation

```
1. Response contract guard  --> if pending repair exists, block new tasks until resolved
2. Context injection        --> context_provider.py assembles payload, injected into prompt
3. Session events injection --> recent git commits, pushes, file mods added to prompt
4. Resume validation        --> validate agent ID format, detect approval nonces
5. TaskValidator            --> validate agent name, check available agents
```

## Agent Completion Pipeline: subagent_stop.py

Fires after every agent tool completes:

```
1. Consume approval file    --> delete pending approval if matches agent
2. Capture workflow metrics  --> duration, exit code, plan status -> metrics.jsonl
3. Validate response contract
   |  Parse AGENT_STATUS block (plan_status, agent_id, pending_steps, next_action)
   |  Parse EVIDENCE_REPORT block (7 required fields)
   |  Parse CONSOLIDATION_REPORT if multi-surface task
   |  If invalid -> save pending-repair.json for pre_tool_use guard
   |  If valid -> clear pending repair
4. Detect anomalies          --> execution failures, consecutive failures
   |  If anomalies found -> create needs_analysis.flag for Gaia
5. Capture episodic memory   --> store episode via tools/memory/episodic.py
6. Process context updates   --> apply CONTEXT_UPDATE blocks via context_writer.py
```

## Surface Routing: surface_router.py

Classifies user tasks into surfaces using signal matching against `config/surface-routing.json`.

| Surface | Primary Agent | Typical Signals |
|---------|--------------|-----------------|
| `live_runtime` | cloud-troubleshooter | pods, services, logs, kubectl, gcloud |
| `gitops_desired_state` | gitops-operator | manifests, Flux, Helm, Kustomize |
| `terraform_iac` | terraform-architect | Terraform, Terragrunt, IAM, modules |
| `app_ci_tooling` | devops-developer | CI/CD, Docker, package tooling |
| `planning_specs` | speckit-planner | specs, plans, task breakdowns |
| `gaia_system` | gaia | hooks, skills, agents/, CLAUDE.md |

**Classification algorithm:**
1. Normalize task text
2. Score each surface by keyword (1.0), command (1.5), and artifact (1.0) matches
3. Keep surfaces with score >= 1.0 and >= 55% of top score
4. If no match and current agent maps to a surface, use agent-fallback (score 0.2)
5. If still no match, dispatch reconnaissance agent

**Investigation brief** is generated per agent from routing results. It contains role assignment (primary/cross_check/adjacent), required evidence fields, stop conditions, and whether a CONSOLIDATION_REPORT is required.

## Context Injection: context_provider.py

Assembles the context payload injected into agent prompts by pre_tool_use.py.

```
context_provider.py <agent_name> <user_task>
    |
    +--> Load project-context.json
    +--> Detect cloud provider (GCP/AWS)
    +--> Load base contracts (config/context-contracts.json)
    +--> Merge cloud overrides (config/cloud/{provider}.json)
    +--> Extract contracted sections for this agent (read permissions)
    +--> Load universal rules (config/universal-rules.json)
    +--> Load relevant episodic memory (similarity match)
    +--> Classify surfaces (surface_router.py)
    +--> Build investigation brief (surface_router.py)
    |
    v
    JSON payload:
      project_knowledge:      {sections the agent may read}
      write_permissions:      {readable/writable section lists}
      rules:                  {universal + agent-specific rules}
      surface_routing:        {active surfaces, dispatch mode, confidence}
      investigation_brief:    {role, required checks, stop conditions}
      historical_context:     {relevant episodes if any}
      metadata:               {provider, version, counts}
```

## Approval Flow

Nonce-based T3 approval lifecycle:

```
1. Agent attempts dangerous command (e.g., terraform apply)
2. mutative_verbs.py detects MUTATIVE verb
3. BashValidator generates 128-bit nonce via generate_nonce()
4. write_pending_approval() saves pending-{nonce}.json to .claude/cache/approvals/
5. Hook returns corrective deny (exit 0) with NONCE:{hex} in message
6. Agent includes NONCE:{hex} in PENDING_APPROVAL status to orchestrator
7. Orchestrator presents plan to user, asks for approval
8. User approves -> orchestrator resumes agent with "APPROVE:{nonce}"
9. pre_tool_use.py detects APPROVE: prefix, calls activate_pending_approval()
10. Pending grant converted to active grant (TTL 10 min, verb-matched)
11. Agent retries command -> check_approval_grant() finds active grant -> allowed
```

## Response Contract Validation

Every agent response must end with a `json:contract` block containing `agent_status`. The contract validator (`hooks/modules/agents/contract_validator.py`) enforces:

- **AGENT_STATUS**: PLAN_STATUS (from 8 valid states), PENDING_STEPS, NEXT_ACTION, AGENT_ID
- **EVIDENCE_REPORT**: required for all states except APPROVED_EXECUTING. Seven fields: PATTERNS_CHECKED, FILES_CHECKED, COMMANDS_RUN, KEY_OUTPUTS, VERBATIM_OUTPUTS, CROSS_LAYER_IMPACTS, OPEN_GAPS
- **CONSOLIDATION_REPORT**: required when multi-surface or cross-check. Fields: OWNERSHIP_ASSESSMENT (enum), CONFIRMED_FINDINGS, SUSPECTED_FINDINGS, CONFLICTS, OPEN_GAPS, NEXT_BEST_AGENT

Invalid responses trigger a repair loop: save pending-repair.json, pre_tool_use guard blocks new tasks, orchestrator must resume the same agent for repair (max 2 attempts before escalation).

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
gaia-ops is distributable as a Claude Code plugin via `.claude-plugin/plugin.json`.
The plugin is auto-discovered by Claude Code -- agents, skills, commands, and hooks
are loaded from their respective directories.

See `.claude-plugin/marketplace.json` for the self-hosted marketplace with sub-plugins.

## Adapter Coupling Points

The adapter layer connects Claude Code's hook protocol to gaia-ops business logic through 5 coupling points. Each coupling point is a thin entry point that delegates to the adapter for JSON parsing/formatting and to business logic modules for decisions.

### CP-1: `hooks/pre_tool_use.py` -- Command Validation Entry Point

| Attribute | Value |
|-----------|-------|
| **File** | `hooks/pre_tool_use.py` |
| **Hook event** | PreToolUse |
| **What it does** | Security gate for all Bash, Task, and Agent tool invocations. Validates commands (blocked patterns, mutative verbs, nonce-based approval), injects project-context into agent prompts, guards pending contract repairs. |
| **Adapter methods called** | `ClaudeCodeAdapter.parse_event()`, `ClaudeCodeAdapter.parse_pre_tool_use()`, `ClaudeCodeAdapter.format_validation_response()` |
| **Business logic modules** | `security/blocked_commands.py`, `security/mutative_verbs.py`, `security/approval_grants.py`, `tools/bash_validator.py`, `tools/task_validator.py`, `agents/response_contract.py`, `context/context_provider.py` |

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
| **What it does** | Fires after every agent completes. Consumes approval files, captures workflow metrics, validates the response contract (AGENT_STATUS, EVIDENCE_REPORT, CONSOLIDATION_REPORT), detects anomalies, stores episodic memory, and processes CONTEXT_UPDATE blocks. |
| **Adapter methods called** | `ClaudeCodeAdapter.parse_event()`, `ClaudeCodeAdapter.parse_agent_completion()` |
| **Business logic modules** | `agents/response_contract.py` (`validate_response_contract`, `save_pending_repair`, `clear_pending_repair`), `tools/memory/episodic.py` (`EpisodicMemory.store_episode`), `context/context_writer.py` (`process_agent_output`) |

### CP-4: `hooks/modules/tools/hook_response.py` -- Response Formatting

| Attribute | Value |
|-----------|-------|
| **File** | `hooks/modules/tools/hook_response.py` |
| **Hook event** | (shared utility, used by PreToolUse callers) |
| **What it does** | Provides `build_hook_permission_response()` -- a shared builder for hookSpecificOutput JSON. Delegates to the adapter's `format_validation_response()` so all permission responses share a single code path. |
| **Adapter methods called** | `ClaudeCodeAdapter.format_validation_response()` |
| **Business logic modules** | None (pure formatting bridge) |

### CP-5: `templates/settings.template.json` / `hooks/hooks.json` -- Hook Configuration

| Attribute | Value |
|-----------|-------|
| **File (npm channel)** | `templates/settings.template.json` -- paths use `.claude/hooks/` prefix |
| **File (plugin channel)** | `hooks/hooks.json` -- paths use `${CLAUDE_PLUGIN_ROOT}/hooks/` prefix |
| **What it does** | Maps Claude Code hook events to handler scripts. Defines which events fire which entry points, the tool matchers (Bash, Task, Agent, `*`), and permissions (allow/deny lists). |
| **Events configured** | PreToolUse, PostToolUse, SubagentStop, SessionStart, Stop, TaskCompleted, SubagentStart (UserPromptSubmit is a static echo in settings.json only) |

### HookAdapter ABC Contract

The abstract interface in `hooks/adapters/base.py` defines the adapter contract. Each CLI backend provides a concrete implementation.

| Method | Signature | Description |
|--------|-----------|-------------|
| `parse_event` | `(stdin_data: str) -> HookEvent` | Parse raw stdin JSON into a normalized, CLI-agnostic event |
| `format_validation_response` | `(result: ValidationResult) -> HookResponse` | Format a validation result for the CLI's permission protocol |
| `format_completion_response` | `(result: CompletionResult) -> HookResponse` | Format a completion result for SubagentStop |
| `format_context_response` | `(result: ContextResult) -> HookResponse` | Format a context injection result |
| `detect_channel` | `() -> DistributionChannel` | Detect whether gaia-ops is running as NPM or PLUGIN |

Additional abstract methods for P1/P2 events: `adapt_session_start`, `format_bootstrap_response`, `adapt_stop`, `adapt_task_completed`, `adapt_subagent_start`, `format_quality_response`, `format_verification_response`.

**Invariants:**
1. Business logic modules NEVER see `HookResponse`. They produce `ValidationResult`, `CompletionResult`, etc.
2. The adapter NEVER modifies business logic results -- it only translates format.
3. Adding a new hook event requires ONLY a new adapter method. Zero changes to business logic modules.

### Adding a New Hook Event

To add support for a new Claude Code hook event (e.g., a future `PreCompact` event):

1. **Add enum value** to `HookEventType` in `hooks/adapters/types.py` (already present for all 19 known events).
2. **Add adapter method** to `ClaudeCodeAdapter` in `hooks/adapters/claude_code.py` -- implement `adapt_<event_name>(raw: dict) -> <ResultType>` and the corresponding `format_<result>_response()` if a new result type is needed.
3. **Add extract/format methods** for the event type -- the extract method pulls typed data from the raw payload, the format method builds the CLI response JSON.
4. **Create hook script entry point** -- a new `hooks/<event_name>.py` file that reads stdin, calls `adapter.parse_event()`, delegates to business logic, and writes the response to stdout.
5. **Add entry to `hooks/hooks.json`** (plugin channel) and `templates/settings.template.json` (npm channel) mapping the event name to the new script.

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
| `CLAUDE.md` | Orchestrator identity, routing table, tool restrictions |
| `hooks/pre_tool_use.py` | PreToolUse hook entry point |
| `hooks/subagent_stop.py` | SubagentStop hook entry point |
| `hooks/modules/tools/bash_validator.py` | Bash command security gate |
| `hooks/modules/tools/task_validator.py` | Task/Agent invocation validator |
| `hooks/modules/security/blocked_commands.py` | Permanently denied command patterns |
| `hooks/modules/security/mutative_verbs.py` | CLI-agnostic mutative verb detector |
| `hooks/modules/security/approval_grants.py` | Nonce grant lifecycle management |
| `hooks/modules/agents/response_contract.py` | Agent response contract validator |
| `hooks/modules/context/context_writer.py` | Progressive context enrichment |
| `tools/context/context_provider.py` | Context payload assembly |
| `tools/context/surface_router.py` | Surface classification and investigation briefs |
| `tools/memory/episodic.py` | Episodic memory storage |
| `config/context-contracts.json` | Agent read/write section permissions |
| `config/universal-rules.json` | Universal and agent-specific rules |
| `config/surface-routing.json` | Surface signals and routing config |
| `agents/*.md` | Agent identity definitions |
| `skills/*/SKILL.md` | Injected procedural knowledge |
| `bin/*.js` | CLI tools (gaia-scan, gaia-doctor, gaia-status, etc.) |
