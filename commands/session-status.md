---
description: Check current session status and show recent session information
---

This command provides an intelligent overview of your current session state and recent work.

## Quick status check
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/session_startup_check.py
```

Shows:
- 🎯 Most relevant recent session based on current work
- 📚 Summary of other recent sessions (last 24 hours)
- 💡 Smart suggestions for session restoration
- 🔧 Available session management commands

## What you'll see

**Current context analysis:**
- Working directory y feature activa (si se definió `SPECIFY_FEATURE`)
- Recent commits and project activity
- Relevance scoring for available sessions

**Session recommendations:**
- Bundles with high relevance to current work
- Time-based suggestions (recent sessions)
- Project context matching (similar technologies/tasks)

**Available actions:**
- Commands to restore specific sessions
- Options to view session details
- Links to session management tools

## Intelligence features

The status check analyzes your current work context and compares it with recent sessions to:

- **Score relevance** based on project keywords, git activity, and timing
- **Suggest restoration** when previous sessions are highly relevant
- **Provide context** about what each session accomplished
- **Show continuation paths** for ongoing work

## Use cases

**Starting a work session:**
- Run `/session-status` to see if you should continue previous work
- Get oriented about recent sessions and accomplishments
- Decide whether to restore context or start fresh

**During development:**
- Check what sessions are available for reference
- Find sessions related to current task
- Get quick overview of recent work

**Before switching contexts:**
- Review current session state before saving
- Check if similar work was done in recent sessions
- Plan session organization and cleanup

This replaces the need to manually browse session directories or remember what you were working on previously.
