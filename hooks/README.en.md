# Gaia-Ops Hooks

**[Spanish version](README.md)**

Hooks are interception points that validate and audit operations before and after execution. They're like security guards that verify each action.

## Purpose

Hooks ensure operations comply with security policies and are auditable. They provide automatic protection without requiring constant manual intervention.

## How It Works

### Architecture Flow

```
Agent attempts to execute command
        |
[pre_tool_use.py] <- intercepts BEFORE
        |
    Validates operation
    +-------+-------+
    |               |
 ALLOWED        BLOCKED
    |               |
Command executes  ERROR + log
    |
[post_tool_use.py] <- intercepts AFTER
    |
Audits result
    |
Log to .claude/logs/
```

## Available Hooks

### Pre-Execution Hooks

**`pre_tool_use.py`** (~400 lines) - Main guardian - validates ALL operations before execution  
**`pre_phase_hook.py`** (~200 lines) - Validates workflow phase transitions (Phase 0-6)  
**`pre_kubectl_security.py`** (~180 lines) - Specialized validation for Kubernetes commands

### Post-Execution Hooks

**`post_tool_use.py`** (~300 lines) - Audits ALL operations after execution  
**`post_phase_hook.py`** (~150 lines) - Audits phase transitions

### Workflow Metrics Hook

**`subagent_stop.py`** (~200 lines) - Captures metrics and detects anomalies when agents finish

## How Hooks Work

### Automatic Invocation

Claude Code invokes hooks automatically - no manual call required:

```
Agent -> pre_tool_use.py -> VALIDATE -> ALLOW/BLOCK
                            |
                      If ALLOW:
                            |
                      Execute command
                            |
Agent <- post_tool_use.py <- AUDIT
```

### Permission Configuration

Hooks read `.claude/settings.json` for decisions:

```json
{
  "security_tiers": {
    "T0": {"approval_required": false},
    "T1": {"approval_required": false},
    "T2": {"approval_required": false},
    "T3": {"approval_required": true}
  }
}
```

### Security Tiers

| Tier | Operation Type | Requires Approval | Hook Validation |
|------|----------------|-------------------|-----------------|
| **T0** | Read-only (get, list) | No | pre_tool_use |
| **T1** | Validation (validate, dry-run) | No | pre_tool_use |
| **T2** | Planning (plan, simulate) | No | pre_tool_use |
| **T3** | Execution (apply, delete) | **Yes** | pre_tool_use + pre_phase |

## File Structure

```
hooks/
├── pre_tool_use.py        (~400 lines) - Main guardian
├── post_tool_use.py       (~300 lines) - Main auditor
├── pre_phase_hook.py      (~200 lines) - Phase validator
├── post_phase_hook.py     (~150 lines) - Phase auditor
├── pre_kubectl_security.py (~180 lines) - K8s security
└── subagent_stop.py       (~200 lines) - Workflow metrics
```

---

**Version:** 2.0.0  
**Last updated:** 2025-12-06  
**Total hooks:** 6 hooks (4 pre, 1 post, 1 metrics)  
**Maintained by:** Gaia (meta-agent)
