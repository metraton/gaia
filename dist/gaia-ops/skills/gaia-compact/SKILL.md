---
name: gaia-compact
description: Use when the user asks to compact the current session -- "compacta", "compact", "oye Gaia compacta", "orquestador compacta", "haz un compact", "compactemos la sesión". Runs /compact with a structured prompt that preserves decisions, components, gaps, file map, and next steps.
metadata:
  user-invocable: true
  type: technique
---

# Gaia Compact

A raw `/compact` tells the model "summarize everything you remember." What survives that summary is what the model happened to find salient -- which is rarely the same as what the user needs to resume work. This skill replaces that default with a preservation contract: the six categories below are what matter for continuity, and the compact prompt forces the model to retain them with high fidelity.

## When this skill fires

Load this skill when the user asks the orchestrator to compact the session. Spanish and English both trigger: "compacta", "orquestador compacta", "oye Gaia compacta", "haz un compact", "compact the session", "compactemos".

## What the orchestrator does

The Skill tool cannot invoke `/compact` directly -- built-in slash commands are not reachable through Skill. The orchestrator reads this skill, builds the combined prompt below, and then invokes `/compact <combined_prompt>` itself as its next action.

## Process

1. **Extract any extra preservation instructions** the user gave alongside the compact request. Examples:
   - "compacta pero preserva el estado del DB schema"
   - "compacta y no olvides la decisión sobre Tailscale"
   - "compact keeping the failing test output"

   If the user gave no extra instructions, `$ARGUMENTS` is empty and the base prompt is used as-is.

2. **Build the combined prompt** by concatenating the base preservation prompt with any extra instructions:

   ```
   <BASE_PROMPT>

   Additional preservation instructions from this request:
   $ARGUMENTS
   ```

   If `$ARGUMENTS` is empty, omit the "Additional preservation instructions" block entirely -- do not leave a dangling header.

3. **Before invoking /compact, verify persistence-critical work**. If any of the following are in-flight and NOT yet written to disk, surface them to the user and ask whether to persist first:
   - Unsaved changes to `MEMORY.md` or memory documents under `.claude/projects/*/memory/`
   - Brief or plan files under `.claude/project-context/briefs/` that were drafted but not written
   - Evidence files (`T{N}.txt`, `AC-N.*`) from a dispatch whose verification has not been persisted
   - Uncommitted git changes the user asked to commit

   Compaction is lossy by design. Anything only held in the model's context window is gone after `/compact`.

4. **Invoke /compact with the combined prompt**. The orchestrator runs this as its own action -- the skill does not and cannot execute it.

5. **After /compact returns**, briefly confirm to the user what was preserved (the six categories plus any extra instructions they gave).

## Base preservation prompt

This is the literal text the orchestrator prepends:

```
Preserve the following with high fidelity:
1. DECISIONS: Every architectural decision made, including the rationale and alternatives rejected.
2. COMPONENTS: Agent roster with responsibilities, skill assignments, and known gaps identified.
3. OPEN ITEMS: All pending briefs, open questions, and identified gaps -- with their current status.
4. FILE MAP: Absolute paths of every file read, created, or modified, with one-line description.
5. KEY FINDINGS: Bugs, security issues, or design problems surfaced during investigation.
6. NEXT STEPS: The exact next action agreed upon before this compact.
Compress tool outputs, file contents, and intermediate reasoning. Retain conclusions, not process.
```

## Anti-patterns

- **Compacting without the preservation prompt** -- defaults to a generic summary that drops file paths, approval_ids, and nonces; resuming becomes guesswork.
- **Compacting while a T3 approval is in flight** -- the approval_id and nonce live in context; after `/compact` the grant activation can lose its anchor. Resolve approvals first, then compact.
- **Ignoring user-provided preservation hints** -- if the user says "preserva el DB schema", appending that to `$ARGUMENTS` is the whole point; dropping it makes the skill a fancy wrapper for the default.
- **Compacting with unsaved memory or brief drafts in context** -- these are not recoverable from `/compact` output; warn the user before running.
- **Summarizing what was preserved in vague terms** -- after compacting, name the six categories explicitly so the user can spot a missing one immediately.
