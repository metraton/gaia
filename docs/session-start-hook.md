# SessionStart Hook Evaluation

## Purpose

Auto-refresh project-context.json when a new session starts, if the context is stale or missing.
This ensures agents always have reasonably fresh environment data without requiring manual intervention.

## Design

- **Trigger:** SessionStart event (Claude Code fires this when a session begins)
- **Check:** Is project-context.json older than 24 hours or missing?
- **Action:** Run lightweight scan (tools + runtime detection only, not full project scan)
- **Output:** Update environment.tools and environment.runtimes sections

### Scope of auto-refresh

The SessionStart hook performs a **lightweight scan only**:

| Detected | Method | Duration |
|----------|--------|----------|
| CLI tools | command -v tool | ~2s |
| Runtime versions | node --version, python3 --version, etc. | ~1s |
| OS info | uname -s -r | <1s |

Full project scanning (languages, frameworks, infrastructure, git setup) is **NOT** performed
at session start. Use the /scan-project command for a comprehensive scan.

### Staleness check

```python
import os, time
STALE_THRESHOLD = 86400  # 24 hours in seconds

def is_stale(context_path):
    if not os.path.exists(context_path):
        return True
    age = time.time() - os.path.getmtime(context_path)
    return age > STALE_THRESHOLD
```

## Implementation (Future)

Add to hooks/hooks.json:

```json
{
  "SessionStart": [{
    "matcher": "startup",
    "hooks": [{
      "type": "command",
      "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py"
    }]
  }]
}
```

### session_start.py outline

The script would:

1. Check if project-context.json exists and is younger than 24 hours
2. If fresh, emit allow and exit immediately (~0s overhead)
3. If stale or missing, run lightweight detection:
   - Detect installed CLI tools via command -v
   - Detect runtime versions (Node, Python, Go)
   - Detect OS information
4. Merge detected data into existing context (preserving user and agent edits)
5. Write updated context and emit allow

## Interaction with existing hooks

- **PreToolUse:** Security validation does not depend on project-context (confirmed by T027 degraded mode tests). SessionStart refresh is additive, not a dependency.
- **PostToolUse:** No interaction.
- **SubagentStop:** No interaction.

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Slow startup on first session | Lightweight scan only (~3s); full scan deferred to /scan-project |
| Race condition with concurrent sessions | File-level atomicity via write-then-rename (future) |
| Stale detection on NFS/remote fs | Use mtime comparison; accept ~1s accuracy |

## Status: IMPLEMENTED

The SessionStart hook and the /scan-project command are both implemented.
The session_start.py hook performs lightweight environment refresh on session start.
The /scan-project command runs the full modular project scanner.
