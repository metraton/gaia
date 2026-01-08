# Orchestration Workflow: Detailed Implementation

**Version:** 2.0.0
**Last Updated:** 2025-11-07
**Parent:** CLAUDE.md

This document describes the complete 6-phase workflow for the orchestrator when handling user requests that require specialized agent invocation.

---

## Overview

The orchestrator operates in a **two-phase model** with an optional clarification step:

1. **Phase 0 (Optional):** Intelligent Clarification - Detect ambiguity and gather missing context
2. **Phase 1-3:** Planning - Route, provision context, and invoke agent for plan generation
3. **Phase 4:** Approval Gate - MANDATORY user approval before realization
4. **Phase 5:** Realization - Execute and verify in live environment
5. **Phase 6:** State Update - Update SSOTs (project-context.json, tasks.md)

**Critical Rule:** Phases 4 (Approval) is MANDATORY and cannot be skipped for T3 operations.

---

## Phase 0: Intelligent Clarification (Optional)

**Trigger:** User request contains ambiguous terms, missing context, or multiple interpretations.

### Step 0.1: Detect Ambiguity

Execute `clarify_engine.py` to analyze the user's request:

```python
import sys
sys.path.insert(0, '$PROJECT_ROOT/.claude/tools')
from clarify_engine import request_clarification

# Detect ambiguity
clarification_data = request_clarification(
    user_prompt="User's original request",
    command_context={"command": "general_prompt"}  # or speckit command name
)
```

**Output:** `clarification_data` dictionary containing:
- `needs_clarification`: Boolean (True if ambiguity detected)
- `summary`: Human-readable explanation of what's ambiguous
- `question_config`: Pre-formatted payload for AskUserQuestion tool
- `clarification_context`: Internal state for enrichment
- `engine_instance`: Reference for Phase 0.4

---

### Step 0.2: Decision Point

**If `clarification_data["needs_clarification"] == False`:**
- Skip to Phase 1 (Agent Selection)
- Use original user prompt as-is

**If `clarification_data["needs_clarification"] == True`:**
- Continue to Step 0.3

---

### Step 0.3: Present Ambiguity & Gather Input

Present `clarification_data["summary"]` to the user, showing:
- What is ambiguous (detected keywords)
- Why clarification is needed (ambiguity score and reasoning)
- What information is missing (preview of options)

**MANDATORY:** Use AskUserQuestion tool:

```python
response = AskUserQuestion(**clarification_data["question_config"])
```

**Question Format:**
- 3-4 targeted questions (not just 2 binary options)
- Dynamic options from `project-context.json` (services, namespaces, resources)
- Rich descriptions with metadata (tech stack, namespace, port, status)
- Emoji for visual scanning (📦 services, 🎯 namespaces, 🔧 resources, ⚠️ warnings)
- "Other" option for custom input (automatic escape hatch)

**Example Question:**
```
📦 Servicio
¿Qué servicio quieres revisar?

Options:
  📦 tcm-api
     NestJS | Namespace: tcm-non-prod | Puerto: 3001 | Estado: ✅ Running

  📦 pg-api
     Spring Boot | Namespace: pg-non-prod | Puerto: 8086 | Estado: ✅ Running

  🌐 Todos los servicios
     Aplicar a todos los recursos (3 total)
```

---

### Step 0.4: Enrich Prompt

Process user responses to generate enriched prompt:

```python
from clarify_engine import process_clarification

result = process_clarification(
    engine_instance=clarification_data["engine_instance"],
    original_prompt="User's original request",
    user_responses=response["answers"],
    clarification_context=clarification_data["clarification_context"]
)

enriched_prompt = result["enriched_prompt"]
```

**Output:** `enriched_prompt` - Original prompt + resolved context (service names, namespaces, etc.)

---

### Step 0.5: Logging

Clarification is automatically logged to `.claude/logs/clarifications.jsonl` for audit trail.

**Log Entry:**
```json
{
  "timestamp": "2025-11-07T14:32:10Z",
  "original_prompt": "revisa el servicio",
  "ambiguity_score": 0.8,
  "detected_keywords": ["servicio"],
  "user_responses": {"service": "tcm-api", "namespace": "tcm-non-prod"},
  "enriched_prompt": "revisa el servicio tcm-api en el namespace tcm-non-prod"
}
```

---

### Step 0.6: Proceed with Enriched Prompt

**IMPORTANT:** The `enriched_prompt` replaces the original user request for ALL subsequent phases (Phase 1-5).

**Benefits:**
- Better routing accuracy (agent_router.py has full context)
- Reduced agent invocation failures (complete context from start)
- Audit trail of clarification decisions

---

## Phase 1: Analysis & Agent Selection

**Input:** `enriched_prompt` (from Phase 0) OR original user task (if Phase 0 skipped)

### Step 1.1: Route Request

Execute `agent_router.py` to determine the appropriate specialized agent:

```bash
python3 $PROJECT_ROOT/.claude/tools/agent_router.py --prompt "$PROMPT"
```

**Output:** Agent name (e.g., `gitops-operator`, `terraform-architect`, `cloud-troubleshooter`)

**Routing Logic:**
- Semantic matching against agent capabilities
- Keyword triggers (terraform → terraform-architect, kubernetes → gitops-operator)
- Context from enriched prompt (service names improve accuracy by ~40%)

**Fallback:** If no agent matches, use `Explore` agent for codebase investigation.

---

## Phase 2: Deterministic Context Provisioning

**Input:** Agent name (from Phase 1), user task

### Step 2.1: Execute Context Provider

**CRITICAL:** Use absolute path to ensure it works from any directory:

```bash
python3 $PROJECT_ROOT/.claude/tools/context_provider.py "$AGENT_NAME" "$USER_TASK"
```

**What context_provider.py does:**
1. Loads agent's "Context Contract" from `.claude/agents/$AGENT_NAME.md`
2. Reads `project-context.json` (SSOT for infrastructure state)
3. Performs semantic enrichment (correlates services, namespaces, resources)
4. Returns complete, structured context payload

**Output:** JSON payload containing:
```json
{
  "contract": {
    "project_details": {...},
    "gitops_configuration": {...},
    "cluster_details": {...},
    "operational_guidelines": {...}
  },
  "enrichment": {
    "related_services": [...],
    "affected_namespaces": [...],
    "recent_changes": [...]
  }
}
```

**DO NOT:** Manually construct context. `context_provider.py` is the SSOT for context generation.

---

## Phase 3: Agent Invocation (Planning)

**Input:** Context payload (from Phase 2), user task

### Step 3.1: Build Minimalist Prompt

Construct a prompt with ONLY:
1. Full structured context payload (contract + enrichment)
2. User's task, stated clearly and concisely

**DO NOT:**
- Add instructions about agent's protocol ("generate a realization package")
- Explain how the agent should work
- Provide implementation details

**Why:** The agent knows its own job. Your role is to provide context, not instructions.

**Example Prompt:**
```markdown
## Context

{context_payload_from_phase_2}

## Task

{user_task}
```

---

### Step 3.2: Invoke Agent for Plan

Execute the Task tool:

```python
Task(
    subagent_type="gitops-operator",  # from Phase 1
    description="Generate deployment plan",
    prompt=minimalist_prompt
)
```

**Agent Responsibility:**
- Analyze context
- Generate declarative code (YAML, HCL, etc.)
- Create validation plan
- Return "Realization Package"

**Realization Package Contents:**
- Files to create/modify/delete
- Git operations (commit message, branch, remote)
- Resources affected in live environment
- Commands to execute (git push, terraform apply, etc.)

---

## Phase 4: Synthesis & Approval Gate (MANDATORY)

**Input:** Realization package (from Phase 3)

**CRITICAL:** This phase is NON-NEGOTIABLE for T3 operations. Skipping it is a protocol violation.

### Step 4.1: Process Output & Halt

Receive agent's output and **HALT the workflow**. DO NOT proceed to Phase 5 automatically.

---

### Step 4.2: Generate Approval Summary

**MANDATORY:** Use `approval_gate.py` to generate structured summary:

```python
import sys
sys.path.insert(0, '$PROJECT_ROOT/.claude/tools')
from approval_gate import request_approval

approval_data = request_approval(
    realization_package=agent_response,
    agent_name="gitops-operator",  # or terraform-architect, etc.
    phase="Phase 3.3"  # or feature name
)
```

**Output:** `approval_data` dictionary containing:
- `summary`: Human-readable breakdown of changes
- `question_config`: Pre-formatted payload for AskUserQuestion
- `gate_instance`: Reference for Step 4.4

---

### Step 4.3: Present Summary

Present `approval_data["summary"]` to the user, showing:

**Files:**
- Files to create (with content preview)
- Files to modify (with diff)
- Files to delete (with warning)

**Git Operations:**
- Commit message (with validation status)
- Branch name (with remote tracking info)
- Remote push target (with permissions check)

**Resources Affected:**
- Kubernetes resources (Deployments, Services, Ingresses)
- Terraform resources (GCP/AWS resources)
- External dependencies (databases, storage, APIs)

**Critical Operations:**
- `git push` (irreversible after execution)
- `terraform apply` (creates/modifies live resources)
- `kubectl apply` (updates live cluster)

---

### Step 4.4: MANDATORY User Question

**CRITICAL:** Call AskUserQuestion tool. This is NON-NEGOTIABLE.

```python
response = AskUserQuestion(**approval_data["question_config"])
```

**Question Format:**

Exactly 3 options:
1. "✅ Aprobar y ejecutar" - Proceed to realization
2. "❌ Rechazar" - Cancel and halt workflow
3. "Other" - Custom response (automatic option)

**Example:**
```
🚦 Aprobación Requerida

¿Aprobar la ejecución de estos cambios?

📄 Archivos afectados: 3 creados, 1 modificado
🔧 Recursos: 2 Deployments, 1 Service, 1 Ingress
⚠️  Operaciones críticas: git push, kubectl apply

Options:
  ✅ Aprobar y ejecutar
     Proceder con la realización

  ❌ Rechazar
     Cancelar y detener el workflow

  Other
     (Provide custom response)
```

---

### Step 4.5: Validate Response

Process user's response using `approval_gate.py`:

```python
from approval_gate import process_approval_response

validation = process_approval_response(
    gate_instance=approval_data["gate_instance"],
    user_response=response["answers"]["question_1"],
    realization_package=agent_response,
    agent_name="gitops-operator",
    phase="Phase 3.3"
)
```

**Output:** `validation` dictionary containing:
- `approved`: Boolean (True if user approved)
- `action`: String ("proceed", "halt_workflow", "clarify_with_user")
- `reason`: String (explanation of decision)

---

### Step 4.6: Enforcement Rules

**If `validation["approved"] == True`:**
- Proceed to Phase 5 (Realization)

**If `validation["approved"] == False` AND `validation["action"] == "halt_workflow"`:**
- STOP. Report to user.
- DO NOT proceed to Phase 5.
- Log rejection to `.claude/logs/approvals.jsonl`

**If `validation["action"] == "clarify_with_user"`:**
- Ask for clarification
- Re-run approval gate (Steps 4.2-4.5)

**ABSOLUTE RULE:** You CANNOT proceed to Phase 5 without `validation["approved"] == True`.

---

### Step 4.7: Logging

Approval decision is automatically logged to `.claude/logs/approvals.jsonl` for audit trail.

**Log Entry:**
```json
{
  "timestamp": "2025-11-07T14:35:22Z",
  "agent": "gitops-operator",
  "phase": "Phase 3.3",
  "realization_package": {...},
  "user_response": "Aprobar y ejecutar",
  "approved": true,
  "files_affected": 4,
  "resources_affected": 3,
  "critical_operations": ["git push", "kubectl apply"]
}
```

---

## Phase 5: Realization, Verification & Closure

**PREREQUISITE:** Phase 5 can ONLY execute if `validation["approved"] == True` from Phase 4.

### Step 5.1: Invoke for Realization

Upon approval, re-invoke the **SAME agent** with a concise prompt:

```python
Task(
    subagent_type="gitops-operator",  # SAME as Phase 3
    description="Execute realization",
    prompt=f"""
## Realization Order

Execute the following realization package:

{realization_package_from_phase_3}

## User Approval

Approved by user at {timestamp}.

## Instructions

1. Persist all files (Write tool)
2. Execute git operations (commit, push)
3. Execute live operations (kubectl apply, terraform apply)
4. Verify in live environment
5. Report verification status
"""
)
```

**Agent Responsibility:**
- Write files to disk
- Execute git operations (add, commit, push)
- Execute live operations (kubectl apply, terraform apply, etc.)
- Verify resources in live environment (kubectl get, gcloud describe, etc.)
- Return verification status

---

### Step 5.2: Agent Executes & Verifies

The agent performs:

1. **File Persistence:**
   - Write files using Write tool
   - Validate file contents
   - Check file permissions

2. **Git Operations:**
   - `git add .`
   - `git commit -m "..."` (after validation with commit_validator.py)
   - `git push origin $BRANCH`

3. **Live Operations:**
   - `kubectl apply -f ...` (for gitops-operator)
   - `terragrunt apply` (for terraform-architect)
   - Wait for resources to be ready

4. **Verification:**
   - `kubectl get deployment $NAME -o yaml`
   - `kubectl get pods -l app=$NAME`
   - Check status, readiness, errors
   - Correlate with expected state

5. **Report:**
   - Success: "✅ Deployed successfully, 2/2 pods ready"
   - Partial: "⚠️ Deployed but 1/2 pods CrashLoopBackOff"
   - Failure: "❌ Deployment failed: ImagePullBackOff"

---

## Phase 6: System State Update (MANDATORY)

**PREREQUISITE:** Phase 5 completed successfully (agent reported success/partial success)

### Step 6.1: Update Infrastructure SSOT

Modify `.claude/project-context.json` to reflect new or changed resources.

**For new services:**
```json
{
  "application_services": [
    {
      "name": "new-service",
      "namespace": "namespace",
      "port": 8080,
      "tech_stack": "Spring Boot",
      "status": "running"
    }
  ]
}
```

**For modified resources:**
Update relevant fields (port, replicas, image_tag, etc.)

**For deleted resources:**
Remove from `application_services` or mark with `"status": "deleted"`

---

### Step 6.2: Update Plan SSOT

**If working within Spec-Kit workflow:**

Use `TaskManager` to mark completed tasks in `tasks.md`:

```python
import sys
sys.path.insert(0, '$PROJECT_ROOT/.claude/tools')
from task_manager import TaskManager

# Initialize with path to tasks.md
tm = TaskManager('/path/to/spec-kit-project/tasks.md')

# Mark task(s) as complete
completed_task_ids = ["T045", "T046"]  # Extract from agent response or realization package
for task_id in completed_task_ids:
    if tm.mark_task_complete(task_id):
        print(f"✅ Task {task_id} marked as complete")
    else:
        print(f"⚠️ Task {task_id} not found or already complete")

# Verify next pending tasks
pending = tm.get_pending_tasks(limit=5)
print(f"Next pending tasks: {[t['task_id'] for t in pending]}")
```

**Why TaskManager:**
- Efficient operations on large files (>25K tokens)
- Uses Grep+Edit instead of Read (avoids token limits)
- Atomic updates (no partial state)
- Validation (task_id exists, status transition valid)

**If NOT in Spec-Kit workflow:**
- Skip this step (no tasks.md to update)

---

### Step 6.3: Report and Transition

Report to the user:

```
✅ Realization completada exitosamente

📄 Archivos actualizados:
- infrastructure/deployments/new-service.yaml (creado)
- infrastructure/services/new-service.yaml (creado)

🔧 Recursos desplegados:
- Deployment/new-service: 2/2 pods ready
- Service/new-service: ClusterIP 10.20.30.40

📊 Estado del sistema actualizado:
- project-context.json: Agregado new-service
- tasks.md: Marcadas tareas T045, T046 como completadas

🎯 Próximas tareas pendientes:
- T047: Configurar Ingress para new-service
- T048: Agregar health checks

¿Proceder con T047?
```

Explicitly confirm:
1. Infrastructure state updated (project-context.json)
2. Plan state updated (tasks.md, if applicable)
3. Next steps identified

---

## Workflow Diagram

```
User Request
     ├─ Ambiguous?
     │   ├─ Yes → Phase 0: Clarification → enriched_prompt
     │   └─ No → original_prompt
     │
     ↓
Phase 1: Agent Selection (agent_router.py)
     ↓
Phase 2: Context Provision (context_provider.py)
     ↓
Phase 3: Invoke Agent (Planning)
     ↓ (returns Realization Package)
     │
Phase 4: Approval Gate (MANDATORY)
     ├─ Generate summary (approval_gate.py)
     ├─ Present to user
     ├─ Ask question (AskUserQuestion)
     ├─ Validate response
     │   ├─ Approved? → Continue
     │   └─ Rejected? → HALT
     ↓
Phase 5: Realization
     ├─ Re-invoke agent
     ├─ Persist files
     ├─ Git operations
     ├─ Live operations
     └─ Verify
     ↓
Phase 6: State Update
     ├─ Update project-context.json
     └─ Update tasks.md (if applicable)
     ↓
Report & Transition
```

---

## Common Pitfalls

### Pitfall 1: Skipping Phase 0 when needed

**Symptom:** Agent invocation fails with "insufficient context" or "ambiguous target"

**Cause:** User request was ambiguous (e.g., "revisa el servicio") but orchestrator skipped clarification

**Fix:** Lower threshold for ambiguity detection, invoke clarify_engine.py more proactively

---

### Pitfall 2: Proceeding to Phase 5 without approval

**Symptom:** User complaints about unauthorized changes, audit trail violations

**Cause:** Orchestrator skipped Phase 4 or proceeded despite `validation["approved"] == False`

**Fix:** Enforce Phase 4 with code validation (cannot invoke Phase 5 without approval token)

---

### Pitfall 3: Not updating SSOT after realization

**Symptom:** project-context.json is stale, next agent invocations fail or operate on wrong state

**Cause:** Phase 6 was skipped or only partially executed

**Fix:** Make Phase 6 part of agent's realization protocol, not orchestrator's post-processing

---

### Pitfall 4: Using stale context in Phase 2

**Symptom:** Agent operates on outdated state (e.g., tries to create service that already exists)

**Cause:** project-context.json wasn't updated in previous workflow run

**Fix:** Validate project-context.json freshness before invoking context_provider.py

---

### Pitfall 5: Over-prompting agents in Phase 3

**Symptom:** Agents ignore their own protocols, follow orchestrator's ad-hoc instructions

**Cause:** Orchestrator added too many instructions in Phase 3 prompt

**Fix:** Minimalist prompts only (context + task). Trust agent to follow its own protocol.

---

## Metrics & Observability

### Key Metrics

Track in `.claude/logs/workflow-metrics.jsonl`:

- **Clarification Rate:** % of requests that trigger Phase 0
- **Approval Rate:** % of realization packages approved in Phase 4
- **Routing Accuracy:** % of correct agent selections in Phase 1
- **Realization Success Rate:** % of Phase 5 executions that verify successfully
- **SSOT Sync Rate:** % of workflows that complete Phase 6 successfully

### Target Thresholds

- Clarification Rate: 20-30% (too high = bad UX, too low = ambiguous requests proceeding)
- Approval Rate: 80-90% (too low = poor planning, too high = rubber-stamping)
- Routing Accuracy: >95%
- Realization Success Rate: >90%
- SSOT Sync Rate: 100% (no exceptions)

---

## Version History

### 2.0.0 (2025-11-07)
- Extracted from CLAUDE.md monolith
- Added detailed Phase 0 (Clarification) workflow
- Clarified Phase 4 (Approval Gate) as MANDATORY
- Added Phase 6 (State Update) with TaskManager
- Added pitfalls, metrics, version history

### 1.x (Historical)
- Embedded in CLAUDE.md
- Basic 2-phase workflow
- Manual approval (no approval_gate.py)
