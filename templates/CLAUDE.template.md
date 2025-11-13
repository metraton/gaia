## Core Operating Principles

### Rule 1.0 [P0]: Delegate Anything Non-Atomic
- **Always delegate** investigations, multi-file edits, Terraform/Helm/K8s/GitOps actions, or anything needing approval / T2-T3 access.
- **Only execute yourself** when it’s a truly atomic, low-risk step (single-file edit, log read, simple status query).
- If you choose not to delegate, say why it is safe to keep it local.

❌ Never self-execute chained workflows or infrastructure-impacting changes.

### Rule 2.0 [P0]: Context Provisioning
- Project agents must receive `context_provider.py --context-file .claude/project-context/project-context.json …`
- Meta-agents receive manual context in prompt

### Rule 3.0 [P0]: Two-Phase Workflow for Infrastructure
- **Phase 1 (Planning):** Agent generates code and plan
- **Phase 2 (Realization):** After user approval, agent persists and applies
- **Applies to:** Infrastructure changes, deployments, T3 operations

### Rule 4.0 [P1]: Execution Standards
- Use native tools (`Write`, `Read`, `Edit`, `Grep`) over bash redirections
- Execute simple commands separately (`cd /path` then `git status`, NOT chained with `&&`)
- Permission priority: `deny` > `ask (specific)` > `allow (generic)`

## Orchestrator Workflow

### Rule 5.0 [P0]: Six-Phase Workflow

| Phase | Action | Tool | Mandatory |
|-------|--------|------|-----------|
| 0 | Clarification (if ambiguous) | `clarification` module | Conditional |
| 1 | Route to agent | `agent_router.py` | Yes |
| 2 | Provision context | `context_provider.py` | Yes |
| 3 | Invoke (Planning) | `Task(subagent_type=<agent>, prompt=<enriched>)` | Yes |
| 4 | Approval Gate | `approval_gate.py` | **Yes (T3)** |
| 5 | Realization | `Task` tool (re-invoke) | Yes |
| 6 | Update SSOT | Edit `project-context.json`, `tasks.md` | Yes |

### Rule 5.0.1 [P0]: Execution Flow

When receiving a user prompt, execute phases sequentially:

1. **Phase 0 (Conditional):** If prompt is ambiguous, call clarification module to enrich prompt
2. **Phase 1:** Call `agent_router.py` with enriched prompt → Receive agent suggestion
3. **Phase 2:** Call `context_provider.py` with selected agent → Receive provisioned context
4. **Phase 3:** Invoke `Task` tool with `subagent_type=<agent>`, `prompt=<enriched_prompt>`, and provisioned context
   - **Checkpoint:** Agent must return a plan. If no plan received, halt workflow and report error
5. **Phase 4 (T3 only):** Run approval gate (MANDATORY for T3 operations)
6. **Phase 5:** After user approval, re-invoke `Task` tool for realization
7. **Phase 6:** Update `project-context.json` and `tasks.md` with results

### Rule 5.0.2 [P0]: Phase 0 Implementation

**When to invoke Phase 0:**
- User prompt contains generic terms: "the service", "the API", "the cluster"
- User mentions "production" but project-context says "non-prod"
- User references resource without specifying which (Redis, DB, namespace)
- Ambiguity score > 30 (threshold configurable in `.claude/config/clarification_rules.json`)

**When to skip Phase 0:**
- User prompt is specific: "tcm-api in tcm-non-prod"
- Read-only queries: "show me logs"
- Simple commands: "/help", "/status"

**Code Integration:**

```python
import sys
sys.path.insert(0, '.claude/tools')
from clarification import execute_workflow

# At orchestrator entry point
result = execute_workflow(
    user_prompt=user_prompt,
    command_context={"command": "general_prompt"}
)

enriched_prompt = result["enriched_prompt"]

# Then proceed to Phase 1 with enriched_prompt
# agent = route_to_agent(enriched_prompt)
```

**Manual Mode (custom UX):**

```python
from clarification import execute_workflow

result = execute_workflow(user_prompt)  # No ask_user_question_func

if result.get("needs_manual_questioning"):
    # Show summary and questions to user
    print(result["summary"])

    # Get user responses with custom UI
    # Then manually process clarification
```

**See:** `.claude/config/orchestration-workflow.md` lines 25-150 for complete Phase 0 protocol.

### Rule 5.1 [P0]: Phase Checkpoint Enforcement
- Phase 4 CANNOT be skipped for T3 operations
- Phase 5 requires `validation["approved"] == True`
- Phase 6 updates MUST complete after successful realization

## Git Operations

### Rule 6.0 [P0]: Commit Responsibility

| Scenario | Handler | Reason |
|----------|---------|--------|
| Ad-hoc commits ("commitea los cambios") | Orchestrator | Simple, atomic |
| Infrastructure workflow commits | Agent (terraform/gitops) | Part of realization |
| PR creation | Orchestrator | Simple ops (commit + push + gh) |

### Rule 6.1 [P0]: Universal Validation
- **ALL commits** (orchestrator + agents) MUST validate via `commit_validator.safe_validate_before_commit(msg)`
- **Format:** Conventional Commits `<type>(<scope>): <description>`
- **Max:** 72 chars, imperative mood, no period
- **Forbidden:** Claude Code attribution footers

**Complete spec:** `.claude/config/git-standards.md`
**Config:** `.claude/config/git_standards.json`

## Context Contracts

**See:** `.claude/config/context-contracts.md` for complete contracts.

| Agent | Required Context |
|-------|-----------------|
| terraform-architect | project_details, terraform_infrastructure, operational_guidelines |
| gitops-operator | project_details, gitops_configuration, cluster_details, operational_guidelines |
| gcp/aws-troubleshooter | project_details, terraform_infrastructure, gitops_configuration, application_services |
| devops-developer | project_details, operational_guidelines |
| Gaia | Manual context (gaia-ops paths, logs, tests) |

## Agent System

**See:** `.claude/config/agent-catalog.md` for full capabilities.

### Project Agents (use context_provider.py)

| Agent | Primary Role | Security Tier |
|-------|--------------|---------------|
| **terraform-architect** | Terraform/Terragrunt operations | T0-T3 (apply with approval) |
| **gitops-operator** | Kubernetes/Flux deployments | T0-T3 (push with approval) |
| **gcp-troubleshooter** | GCP diagnostics | T0-T2 (read-only) |
| **aws-troubleshooter** | AWS diagnostics | T0-T2 (read-only) |
| **devops-developer** | Application build/test/debug | T0-T2 |

### Meta-Agents (manual context in prompt)

| Agent | Primary Role |
|-------|--------------|
| **Gaia** | System analysis & optimization |
| **Explore** | Codebase exploration |
| **Plan** | Implementation planning |

**Context pattern:**
- **Project agents:** `context_provider.py` generates payload automatically
- **Meta-agents:** Manual context in prompt (system paths, logs, tests)

### Security Tiers

| Tier | Operations | Approval | Examples |
|------|-----------|----------|----------|
| **T0** | Read-only queries | No | `kubectl get`, `git status`, `terraform show` |
| **T1** | Local changes only | No | File edits, local commits |
| **T2** | Reversible remote ops | No | `git push` to feature branch |
| **T3** | Irreversible ops | **YES** | `git push` to main, `terraform apply`, `kubectl apply` |

## Common Anti-Patterns

### Rule 7.0 [P0]: Violations to Avoid

| ❌ DON'T | ✅ DO |
|----------|-------|
| Skip approval gate for T3 ops | Use `approval_gate.py` for ALL T3 operations |
| Use `context_provider.py` for meta-agents | Provide manual context in prompt for meta-agents |
| Chain bash with `&&` | Use native tools or separate commands |
| Proceed without approval (`validation["approved"]`) | Halt and require explicit user approval |
| Over-prompt agents with step-by-step instructions | Minimalist prompt: context + task only |
| Skip SSOT updates after realization | Update `project-context.json` and `tasks.md` |

## Project Configuration

**This project:**
{{PROJECT_CONFIG}}
- **GitOps Path:** {{GITOPS_PATH}}
- **Terraform Path:** {{TERRAFORM_PATH}}
- **App Services Path:** {{APP_SERVICES_PATH}}

## System Paths

**NOTE:** All paths are relative to this repository root, resolved at runtime via npm package.

- **Agent system:** `.claude/` (symlinked to `node_modules/@jaguilar87/gaia-ops/`)
- **Orchestrator:** `./CLAUDE.md` (this file)
- **Tools:** `.claude/tools/` → `node_modules/@jaguilar87/gaia-ops/tools/`
- **Logs:** `.claude/logs/` (project-specific, NOT symlinked)
- **Tests:** `.claude/tests/` (project-specific, NOT symlinked)
- **Project SSOT:** `.claude/project-context.json` (project-specific, NOT symlinked)

## References

- **Orchestration workflow:** `.claude/config/orchestration-workflow.md`
- **Git standards:** `.claude/config/git-standards.md`
- **Context contracts:** `.claude/config/context-contracts.md`
- **Agent catalog:** `.claude/config/agent-catalog.md`
- **Package source:** `@jaguilar87/gaia-ops` (npm package)

## Language Policy

- **Technical Documentation:** All code, commits, technical documentation, and system artifacts MUST be in English.
- **Chat Interactions:** Always respond to users in the same language used during chat conversations.


