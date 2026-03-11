# Gaia-Ops Hooks

Hooks are interception points that validate and audit operations before and after execution. They act as security guards that verify each action.

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

### Pre-Execution Hook

**`pre_tool_use.py`** (~1125 lines) - Main guardian (v2 modular) - validates ALL operations before execution.

Uses the modular architecture in `modules/`:
- **Bash validation**: tier classification, blocked commands, read-only auto-approval
- **Task validation**: agent existence, tier enforcement, T3 approval gates
- **Resume validation**: agentId format, prompt presence, skip heavy validations
- **Performance**: LRU cache for tier classification, fast-paths for common commands

### Post-Execution Hook

**`post_tool_use.py`** (~270 lines) - Audits ALL operations after execution. Logs timestamp, command, exit code, duration, and approval metadata.

### Workflow Metrics Hook

**`subagent_stop.py`** (~1010 lines) - Captures metrics and detects anomalies when agents finish. Detects slow execution (>120s), failures, and consecutive failures.

## Automatic Invocation

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

## Permission Configuration

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

## Security Tiers

| Tier | Operation Type | Requires Approval | Hook Validation |
|------|----------------|-------------------|-----------------|
| **T0** | Read-only (get, list) | No | pre_tool_use |
| **T1** | Local validation (validate, lint, fmt, check) | No | pre_tool_use |
| **T2** | Simulation (plan, template, diff, --dry-run) | No | pre_tool_use |
| **T3** | Execution (apply, delete) | **Yes** | pre_tool_use |

## File Structure

```
hooks/
├── pre_tool_use.py        (~1125 lines) - Main guardian (v2 modular)
├── post_tool_use.py       (~270 lines) - Main auditor
├── subagent_stop.py       (~1010 lines) - Workflow metrics + anomaly detection
├── session_start.py       - Session start event handler
├── stop_hook.py           - Stop event handler
├── subagent_start.py      - Subagent start event handler
├── task_completed.py      - Task completed event handler
└── modules/               - Modular architecture (see modules/README.md)
    ├── core/              - Shared utilities (paths, state)
    ├── security/          - Tier classification, safe/blocked commands
    ├── tools/             - Bash/Task validators, shell parser
    ├── context/           - Context writer
    ├── validation/        - Commit validator
    ├── audit/             - Logger, metrics, event detection
    ├── workflow/          - Workflow support
    └── agents/            - Subagent support
```

**Note:** Shell parser (`shell_parser.py`) is in `modules/tools/shell_parser.py`

---

**Version:** 4.2.0
**Last updated:** 2026-03-11
**Total hooks:** 7 hook scripts (3 primary + 4 event handlers)
**Maintained by:** Gaia (meta-agent)
