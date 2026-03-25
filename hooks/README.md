# Gaia-Ops Hooks

Hooks are interception points that validate and audit operations before and after execution.

## Architecture Flow

```
User sends prompt
        |
[user_prompt_submit.py] <- **injects orchestrator identity**
        |  Builds identity via ops_identity.py
        |  Inject surface routing recommendation (deterministic)
        |  Skills loaded on-demand: agent-response
        v
Agent attempts to execute command
        |
[pre_tool_use.py] <- intercepts BEFORE
        |  Validates Bash commands (security gate)
        |  Validates Task/Agent (context injection)
        |  Validates SendMessage (agent resumption, nonce approval)
        v
    ALLOWED / BLOCKED
        |
Command executes
        |
[post_tool_use.py] <- intercepts AFTER
        |  Audits result, logs to .claude/logs/
        v
[subagent_stop.py] <- fires when agent completes
        |  Contract validation, metrics, episodic memory
```

## Available Hooks

| Hook | Lines | Purpose |
|------|-------|---------|
| `user_prompt_submit.py` | ~50 | Identity injection via UserPromptSubmit event |
| `pre_tool_use.py` | ~1125 | Security gate: Bash validation, Task/Agent context, SendMessage |
| `post_tool_use.py` | ~270 | Audit logging, session context updates |
| `subagent_stop.py` | ~1010 | Contract validation, workflow metrics, episodic memory |
| `session_start.py` | - | Session start event handler |
| `stop_hook.py` | - | Stop event handler |
| `subagent_start.py` | - | Subagent start event handler |
| `task_completed.py` | - | Task completed event handler |

## Security Tiers

| Tier | Operation Type | Approval | Hook Validation |
|------|----------------|----------|-----------------|
| **T0** | Read-only (get, list) | No | pre_tool_use |
| **T1** | Local validation (validate, lint) | No | pre_tool_use |
| **T2** | Simulation (plan, diff) | No | pre_tool_use |
| **T3** | Execution (apply, delete) | **Yes** (native `ask` dialog) | pre_tool_use |
| **T3-blocked** | Irreversible (delete-vpc, drop db) | **Permanently blocked** | pre_tool_use (exit 2) |

## File Structure

```
hooks/
├── user_prompt_submit.py  - Identity injection (UserPromptSubmit)
├── pre_tool_use.py        - Security gate (PreToolUse)
├── post_tool_use.py       - Audit logging (PostToolUse)
├── subagent_stop.py       - Contract validation + memory (SubagentStop)
├── session_start.py       - Session start event handler
├── stop_hook.py           - Stop event handler
├── subagent_start.py      - Subagent start event handler
├── task_completed.py      - Task completed event handler
├── hooks.json             - Plugin-channel hook configuration
├── adapters/              - Adapter layer (see ARCHITECTURE.md)
└── modules/               - Modular architecture (see modules/README.md)
```

---

**Version:** 4.5.0
**Last updated:** 2026-03-24
**Total hooks:** 9 hook scripts (4 primary + 4 event handlers + post_compact)
