---
name: gaia-compact
description: Use when the user asks to compact the current session, including “compacta”, “compactemos”, or “compact the session”
---

# Gaia Compact

Gaia Compact preserves the small amount of transient context needed to resume
work after compaction. Durable decisions and work belong in memory, briefs,
plans, tasks, or project context; compact references those objects instead of
duplicating their bodies.

## Process

1. **Collect user extras.** Preserve any specific item the user names.
2. **Classify what would be lost, by which door you came in.** After a
   `session-reflection`, every decision, learning, and pending already has a
   home, so `UNSAVED TRANSIENT CONTEXT` is empty by construction: anything you
   are tempted to put there names an item the reflection did not finish, and it
   belongs back in that table rather than in a summary that expires. Arriving
   standalone with unsaved decisions, learnings, milestones, or Gaia
   improvements, offer the `session-reflection` → `memory` flow first; if the
   user insists on compacting now, preserve those items explicitly under that
   field, labeled as not durable rather than pretending permanence.
3. **Preflight continuity.** Resolve or clearly retain approvals in flight,
   unpersisted brief/plan changes, verification evidence, and requested git
   actions. Compaction does not make them durable.
4. **Build the handoff.** Preserve only:
   - active objective;
   - exact resume point;
   - durable references (slugs, brief/plan/task IDs), never their bodies;
   - unsaved transient facts;
   - blockers and approvals;
   - active files needed to resume;
   - user-provided preservation instructions.
5. **Invoke `/compact` with that handoff** -- the orchestrator's own action; this skill cannot invoke it.

## Preservation prompt

```text
Preserve continuity for the next turn:
- ACTIVE OBJECTIVE: <goal>
- RESUME POINT: <exact next action>
- DURABLE REFERENCES: <memory slugs and brief/plan/task IDs; no copied bodies>
- UNSAVED TRANSIENT CONTEXT: <facts that exist only in this conversation>
- BLOCKERS / APPROVALS: <current state>
- ACTIVE FILES: <only files needed to resume>
- USER EXTRAS: <request-specific instructions, if any>

Compress tool output and intermediate reasoning. Do not duplicate durable
memory or structured work; retain identifiers and the next decision.
```

## Handoffs

- `session-reflection` supplies the resume point, not the handoff; compact
  builds its own from that pointer and from the session's own state.
- `memory` owns durable knowledge and live threads; compact owns transient
  conversational continuity only.
- See `examples.md` for chained and standalone compaction.

## Anti-patterns

- **Compacting durable candidates silently:** offer reflection and curation
  before losing the context that explains them.
- **Copying durable bodies:** duplicates memory and causes later divergence;
  preserve identifiers.
- **Inventorying every file read:** retain only active files needed to resume.
- **Treating compaction as persistence:** a summary is context, not a durable
  database record.
