---
description: Restore a previous Claude session with intelligent context loading
---

This command provides intelligent session restoration with context loading for both main Claude and specialized sub-agents on-demand.

## Quick command (restore latest relevant)
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/session_startup_check.py
```

Shows the most relevant recent session with restoration suggestions.

## Restore specific session
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/restore_session.py <bundle-id>
```

Restores complete session context from the specified bundle.

## List available sessions
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/restore_session.py --list
```

Shows all available session bundles with descriptions and timestamps.

## View session details (without restoring)
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/restore_session.py <bundle-id> --show
```

Display detailed information about a session bundle without loading it.

## Agent-specific context extraction
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/restore_session.py <bundle-id> --agent terraform-architect
python3 $PROJECT_ROOT/.claude/session/scripts/restore_session.py <bundle-id> --agent gitops-operator
python3 $PROJECT_ROOT/.claude/session/scripts/restore_session.py <bundle-id> --agent devops-developer
```

Extract context specific to a particular agent type for on-demand loading.

## Examples

**View recent sessions:**
```bash
# Check what sessions are available
/restore-session --list

# See startup recommendations
python3 .claude/session/scripts/session_startup_check.py
```

**Restore a session:**
```bash
# Full session restore
/restore-session 2025-09-26-session-164535-384ea531

# Just view details first
/restore-session 2025-09-26-session-164535-384ea531 --show
```

**Agent context loading:**
```bash
# For terraform work
/restore-session latest --agent terraform-architect

# For kubernetes/GitOps work
/restore-session latest --agent gitops-operator
```

## What gets restored

- ✅ **Active context** - Current session state, primers, and context files
- ✅ **Project state** - Git status, working directory, recent changes
- ✅ **Session metadata** - Task information, timestamps, descriptions
- ✅ **Conversation summary** - Key accomplishments and next steps

## Session restoration is smart

- **Non-destructive**: Backs up current context before restoring
- **Selective**: Can extract context for specific agents without full restore
- **Informative**: Shows session details before committing to restore
- **Flexible**: Supports partial bundle IDs for convenience

Use this when you want to continue work from a previous session or need context from past work for current tasks.