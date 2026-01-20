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

**`pre_tool_use.py`** (~319 lines) - Main guardian (v2 modular) - validates ALL operations before execution

### Post-Execution Hooks

**`post_tool_use.py`** (~300 lines) - Audits ALL operations after execution

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
| **T3** | Execution (apply, delete) | **Yes** | pre_tool_use |

## File Structure

```
hooks/
├── pre_tool_use.py        (~319 lines) - Main guardian (v2 modular)
├── post_tool_use.py       (~300 lines) - Main auditor
└── subagent_stop.py       (~200 lines) - Workflow metrics
```

**Note:** Shell parser (`shell_parser.py`) is now in `modules/tools/shell_parser.py`

---

**Version:** 4.1.0
**Last updated:** 2026-01-16
**Total hooks:** 3 active hooks (1 pre, 1 post, 1 metrics)
**Maintained by:** Gaia (meta-agent)
