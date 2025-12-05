# CLAUDE.md - Orchestrator Protocol

## TL;DR (Essential Loading Only)

**Two-Phase Workflow:**
1. Planning (Phase 1-3): Route → Context → Agent generates plan
2. Approval (Phase 4): MANDATORY user approval via `approval_gate.py`
3. Realization (Phase 5): Execute ONLY if `validation["approved"] == True`

**Critical Rules:**
- [P0] Delegate non-atomic ops (Rule 1.0)
- [P0] Phase 4 approval CANNOT be skipped for T3 (Rule 5.1)
- [P0] All commits use `commit_validator.py` (Rule 6.1)

**Tool Contracts:** See Rule 5.2 for agent_router.py, context_provider.py, approval_gate.py
**Failure Handling:** See Rule 5.3 for complete failure matrix

**On-Demand Loading:** Load full context from `.claude/config/*.md` only when needed.

---

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

### Rule 5.0.1 [P0]: Phase 0 Implementation

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

### Rule 5.2 [P0]: Tool Invocation Contracts

**Phase 1 - Agent Router:**
```bash
python3 .claude/tools/1-routing/agent_router.py "$USER_REQUEST" --json
```
**Output:** `{"agent": str, "confidence": int, "reason": str}`

**Phase 2 - Context Provider:**
```bash
python3 .claude/tools/2-context/context_provider.py "$AGENT_NAME" "$USER_TASK"
```
**Output:** `{"contract": {...}, "enrichment": {...}, "metadata": {...}}`

**Phase 4 - Approval Gate:**
```python
from approval_gate import request_approval, process_approval_response

# Step 1: Request
approval_data = request_approval(realization_package, agent_name, phase)
# Returns: {summary: str, question_config: dict, gate_instance: ApprovalGate}

# Step 2: Present summary + Ask question
AskUserQuestion(**approval_data["question_config"])

# Step 3: Validate
validation = process_approval_response(gate_instance, user_response, ...)
# Returns: {approved: bool, action: str, message: str}
```

**Enforcement:**
- Phase 5 REQUIRES `validation["approved"] == True`
- NO manual approval bypass allowed

### Rule 5.3 [P0]: Failure Matrix & Recovery

| Phase | Failure Mode | Detection | Action | Fallback |
|-------|-------------|-----------|--------|----------|
| **0** | Clarification timeout | No response in 60s | Use original prompt | Log warning, proceed |
| **0** | Invalid user response | Non-standard answer | Re-ask with examples | Max 2 retries |
| **1** | Router returns no agent | `confidence == 0` | Use `devops-developer` | Log fallback reason |
| **1** | Router timeout | Process > 5s | Kill, use fallback | Investigate routing complexity |
| **2** | Context file missing | `project-context.json` not found | HALT workflow | User must run `/speckit.init` |
| **2** | Invalid JSON in context | JSONDecodeError | HALT workflow | User must fix syntax |
| **2** | Provider mismatch | GCP contract for AWS project | Load correct contract | Auto-detect provider |
| **3** | Agent invocation fails | Task tool error | Retry once, then HALT | Log full error context |
| **3** | Agent timeout | Duration > 120s | Warn user, extend timeout | Max 300s for T3 ops |
| **3** | Malformed realization package | Missing required fields | Request re-generation | Provide package schema |
| **4** | Approval timeout | No response in 300s | Auto-reject | Email notification (future) |
| **4** | Approval bypassed | Phase 5 without Phase 4 | ABORT immediately | Log security violation |
| **4** | Invalid approval response | Parse error | Re-ask with simplified options | Max 2 retries |
| **5** | Git push fails | Non-zero exit code | Check remote access | Suggest `git push --force-with-lease` |
| **5** | Kubectl apply fails | ImagePullBackOff | Rollback, check registry | Suggest image tag verification |
| **5** | Terraform apply fails | Resource already exists | Suggest `terraform import` | Check state file |
| **5** | Verification fails | Resources not ready | Wait + retry (max 3x) | Manual verification instructions |
| **6** | SSOT update fails | Edit tool error | HALT, require manual fix | Log inconsistency |
| **6** | Tasks.md not found | File doesn't exist | Skip task updates | Create with `/speckit.init` |

**Critical Recovery Actions:**

**Phase 2 Context Failure (P0):**
```bash
# Auto-recovery script
if [ ! -f .claude/project-context.json ]; then
    echo "ERROR: project-context.json missing"
    echo "Run: /speckit.init"
    exit 1
fi
```

**Phase 4 Bypass Detection (P0):**
```python
# Enforcement check before Phase 5
if not validation.get("approved"):
    raise SecurityViolation("Phase 5 cannot proceed without Phase 4 approval")
    # Log to .claude/logs/security-violations.jsonl
```

**Phase 5 Verification Failure (P1):**
```bash
# Rollback sequence
kubectl rollout undo deployment/$NAME
kubectl get pods -l app=$NAME  # Verify rollback
git revert HEAD  # Revert code changes
git push origin $BRANCH
```

**Logging Requirements:**
- ALL failures logged to `.claude/logs/audit-YYYY-MM-DD.jsonl`
- Include: timestamp, phase, error_type, recovery_action, success
- Retention: 90 days minimum

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

## Workflow Enforcement & Memory Systems

### Task Tool Validation (NEW - Implemented)

**CRITICAL:** Task tool invocations are now validated by `pre_tool_use.py`:
- Agent existence verification
- T3 operations require approval indication in prompt
- Unknown agents are blocked

```python
# T3 operations must include approval in prompt:
Task(
    subagent_type='terraform-architect',
    prompt='User approval received. Run terraform apply...'  # ✅ Allowed
)

# Without approval:
Task(
    subagent_type='terraform-architect',
    prompt='Run terraform apply...'  # ❌ BLOCKED
)
```

### Episodic Memory System (NEW - Active)

**Project Memory** (`.claude/project-context/episodic-memory/`):
- Stores operational episodes (migrations, troubleshooting, configurations)
- Automatically searched during Phase 0 (clarification)
- Enriches agent context via `context_provider.py`
- No manual commands needed - fully automatic

**Workflow Memory** (`.claude/memory/workflow-episodic/`):
- Captures execution metrics (duration, exit codes)
- Detects anomalies (>120s execution, consecutive failures)
- Auto-signals Gaia when problems detected
- Check for signals: `.claude/memory/workflow-episodic/signals/needs_analysis.flag`

### Anomaly Detection & Auto-Trigger

When anomalies are detected:
1. Signal file created at `.claude/memory/workflow-episodic/signals/needs_analysis.flag`
2. Orchestrator should check for signal before starting workflow
3. If signal exists, offer to invoke Gaia for analysis

```python
# Check for analysis signal (orchestrator responsibility)
signal_file = Path(".claude/memory/workflow-episodic/signals/needs_analysis.flag")
if signal_file.exists():
    # Offer Gaia analysis to user
    # If accepted: Task(subagent_type="gaia", prompt="Analyze system anomalies...")
```

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


