---
name: universal-protocol
description: Universal protocol for agent responses (AGENT_STATUS, Security Tiers, Local-First)
triggers: [terraform-architect, gitops-operator, cloud-troubleshooter, devops-developer]
phase: start
---

# Universal Protocol

## TL;DR

Every agent response MUST follow:
1. LOCAL FIRST → Check repository before cloud APIs
2. AGENT_STATUS block → Track plan state (6 states)
3. T3 approval → State-changing ops require user approval

---

## 1. Local-First Investigation

**Priority Order (MANDATORY):**

```
1. LOCAL FIRST   → Repository files, git, grep, read
2. DECISION      → Can I answer from local data?
   - YES → Respond with findings
   - NO  → Continue to cloud
3. CLOUD SECOND  → Query cloud APIs only if local insufficient
4. STATUS REPORT → End with AGENT_STATUS block
```

### Why Local First?

| Benefit | Impact |
|---------|--------|
| Faster | No API latency |
| No rate limits | Cloud APIs have quotas |
| Offline capable | Works without connectivity |
| Cost reduction | Cloud API calls cost money |
| Source of truth | Repo reflects desired state |

### Local Sources (Check First)

```
.claude/project-context/     → Project configuration
gitops/                      → K8s manifests, Flux configs
terraform/                   → Infrastructure as code
*.md, *.yaml, *.json         → Documentation and config
```

### Cloud Sources (Check Second)

```
gcloud/kubectl/aws           → Only when local is stale or insufficient
```

---

## 2. AGENT_STATUS Format (MANDATORY)

Every response MUST end with this block:

```html
<!-- AGENT_STATUS -->
PLAN_STATUS: [INVESTIGATING|PENDING_APPROVAL|APPROVED_EXECUTING|COMPLETE|BLOCKED|NEEDS_INPUT]
CURRENT_PHASE: [Investigation|Planning|Execution|Complete]
PENDING_STEPS: [List of remaining steps]
NEXT_ACTION: [Specific next step]
AGENT_ID: [Your agent ID from Claude Code]
<!-- /AGENT_STATUS -->
```

### 6 Valid States

| Status | Meaning | Orchestrator Action |
|--------|---------|---------------------|
| `INVESTIGATING` | Gathering information | Wait |
| `PENDING_APPROVAL` | T3 plan ready, needs approval | AskUserQuestion |
| `APPROVED_EXECUTING` | Running approved T3 actions | Wait |
| `COMPLETE` | Task finished | Respond to user |
| `BLOCKED` | Cannot proceed | Report blocker |
| `NEEDS_INPUT` | Missing information | Ask user |

### State Flow

```
INVESTIGATING → (Plan ready?) → PENDING_APPROVAL
                                      ↓
                                (User approved)
                                      ↓
                              APPROVED_EXECUTING
                                      ↓
                                  COMPLETE

INVESTIGATING → (Issue found?) → BLOCKED
INVESTIGATING → (Need info?) → NEEDS_INPUT
INVESTIGATING → (T0/T1 task?) → COMPLETE
```

---

## 3. Security Tiers

All operations are classified into tiers:

| Tier | Type | Approval | Examples |
|------|------|----------|----------|
| T0 | Read-only | NO | get, list, describe, cat, read |
| T1 | Validation | NO | validate, plan, check, lint |
| T2 | Dry-run | NO | --dry-run, --plan-only |
| T3 | **State-changing** | **YES** | apply, deploy, create, delete |

### T3 Detection Keywords

```
apply, deploy, create, delete, destroy, push, 
update (state), modify, remove, scale
```

**Exception:** Dry-run flags downgrade to T2:
```
--dry-run, --plan-only, -n, --dry-run=client
```

### T3 Two-Phase Workflow

```
Phase 1: PLAN
  1. Agent creates detailed plan
  2. Returns plan + agentId
  3. Status: PENDING_APPROVAL
  4. Wait for orchestrator

Phase 2: EXECUTE (after approval)
  1. Orchestrator resumes agent with "User approved..."
  2. Agent executes plan
  3. Status: APPROVED_EXECUTING → COMPLETE
```

---

## 4. Error Handling

### Error Classification

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry | Continue |
| Blocker | Cannot proceed | `BLOCKED` |
| Ambiguous | Need clarification | `NEEDS_INPUT` |

### Recovery Patterns

**BLOCKED errors:**
```
1. Log error details
2. Explain what's blocked
3. List possible solutions
4. Set status: BLOCKED
5. Wait for user guidance
```

**NEEDS_INPUT situations:**
```
1. Explain the ambiguity
2. List options (A, B, C)
3. Set status: NEEDS_INPUT
4. Wait for user decision
```

---

## 5. Quick Reference

```
START:
  1. Load investigation-skill
  2. Check LOCAL sources
  3. Emit AGENT_STATUS

T3 DETECTED:
  1. Load approval-skill
  2. Create plan
  3. Status: PENDING_APPROVAL
  4. Wait for approval

AFTER APPROVAL:
  1. Load execution-skill
  2. Execute plan
  3. Status: APPROVED_EXECUTING → COMPLETE

ERROR:
  1. Classify: recoverable/blocker/ambiguous
  2. Apply recovery pattern
  3. Update AGENT_STATUS
```

---

## Integration with Other Skills

This protocol applies to ALL agents. When working:

- **investigation-skill** → Use during INVESTIGATING state
- **approval-skill** → Use during PENDING_APPROVAL state
- **execution-skill** → Use during APPROVED_EXECUTING state
- **domain skills** → Apply protocol while using domain patterns

---

**See Also:**
- Full protocol: `.claude/docs/standards/universal-protocol.md`
- Skills system: `.claude/skills/README.md`
