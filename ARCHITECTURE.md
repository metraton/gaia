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
      contract:               {sections the agent may read}
      context_update_contract: {readable/writable section lists}
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

Every agent response must end with an AGENT_STATUS block. The contract validator (`hooks/modules/agents/response_contract.py`) enforces:

- **AGENT_STATUS**: PLAN_STATUS (from 8 valid states), PENDING_STEPS, NEXT_ACTION, AGENT_ID
- **EVIDENCE_REPORT**: required for all states except APPROVED_EXECUTING. Seven fields: PATTERNS_CHECKED, FILES_CHECKED, COMMANDS_RUN, KEY_OUTPUTS, VERBATIM_OUTPUTS, CROSS_LAYER_IMPACTS, OPEN_GAPS
- **CONSOLIDATION_REPORT**: required when multi-surface or cross-check. Fields: OWNERSHIP_ASSESSMENT (enum), CONFIRMED_FINDINGS, SUSPECTED_FINDINGS, CONFLICTS, OPEN_GAPS, NEXT_BEST_AGENT

Invalid responses trigger a repair loop: save pending-repair.json, pre_tool_use guard blocks new tasks, orchestrator must resume the same agent for repair (max 2 attempts before escalation).

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
| `bin/*.js` | CLI tools (gaia-init, gaia-doctor, gaia-status, etc.) |
