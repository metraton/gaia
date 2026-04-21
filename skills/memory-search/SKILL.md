---
name: memory-search
description: Use when searching, inspecting, or diagnosing episodic memory via the `gaia memory` CLI -- queries like "what do I remember about X", "search my memory", "show episode", or memory health checks
metadata:
  user-invocable: false
  type: reference
---

# Memory Search

Inspect and query Gaia's episodic memory (Memory v2) through the `gaia memory` CLI. Memory v2 indexes past sessions in an FTS5 SQLite database with hybrid scoring (recency + retrieval count). This skill covers search, stats, show, and conflict detection. For curating/reorganizing the separate MEMORY.md index and topic files, load `memory-curation` instead.

## When to use each subcommand

| Trigger | Subcommand |
|---------|-----------|
| "what do I know about X", "search my memory" | `gaia memory search <query>` |
| "memory health", "is my memory healthy" | `gaia memory stats` |
| "show episode abc123", "open that memory" | `gaia memory show <id>` |
| "any memory conflicts", "contradictions" | `gaia memory conflicts` |

Always prefer `--json` when the output will feed a follow-up step. Human text is for final user replies.

## Output shapes

All shapes are contract -- downstream code relies on these keys.

```
gaia memory search "<query>" [--limit N] --json
  -> {"results": [{"id", "title", "score", "date", "snippet"}]}

gaia memory stats --json
  -> {"total_episodes", "indexed", "avg_score", "conflicts"}

gaia memory show <episode_id> --json
  -> {"id", "title", "content", "score", "tags", "retrieval_count", "age_days"}
  exit 1 if episode not found

gaia memory conflicts [--threshold F] --json
  -> {"conflicts": [{"file_a", "file_b", "score", "reason"}]}
```

`score` in search and show is the hybrid score from `tools.memory.scoring.score_memory(days_old, retrieval_count)` -- higher means more relevant. `score` in conflicts is semantic similarity between two memory files (0.0-1.0, higher means more similar/conflicting).

## Typical flows

**Answering "what do I remember about X"**

1. `gaia memory search "X" --limit 5 --json`
2. If `results` empty: tell the user nothing indexed yet, suggest checking `stats`
3. If hits: pick top 1-2 by `score`, call `gaia memory show <id> --json` for full content
4. Summarize `content` back to the user; cite `date` and `id`

**Diagnosing memory health**

1. `gaia memory stats --json`
2. If `indexed < total_episodes * 0.9`: FTS5 index is stale -- suggest `gaia doctor --fix`
3. If `conflicts > 0`: run `gaia memory conflicts --json` to show specifics, then hand off to `memory-curation` for resolution
4. `avg_score` near 0 means scoring module is not loading -- check `gaia doctor`

**Checking before saving a new memory**

1. `gaia memory search "<topic>" --json` to see if the topic already exists
2. If high-score hit exists: load it with `show`, extend instead of duplicating
3. If no hit: safe to create a new memory file (handoff to `memory-curation`)

## Interpreting results

- Empty `results` list is not an error -- it means nothing indexed matches. Surface this plainly; do not invent context.
- Low `score` (< 0.3) hits are likely noise -- mention them only if explicitly asked for broad results.
- `age_days` from `show` helps decide whether to trust the memory or flag it as potentially stale.
- A `conflicts` entry with `score > 0.85` is almost certainly a genuine duplicate/contradiction worth resolving.

## Handoffs

| Situation | Next step |
|-----------|-----------|
| Conflicts found, user wants cleanup | Load `memory-curation` skill |
| FTS5 index stale or missing | Run `gaia doctor --fix` |
| Want to save a new finding | Load `memory-curation` skill |
| Results enriched and user wants broader research | Delegate to `investigation` skill or WebSearch |

## Anti-patterns

- **Using `search` for verbatim recall** -- FTS5 ranks by relevance, not exact match. If the user needs an exact episode, use `show <id>`.
- **Ignoring `--json`** -- piping human output into follow-up logic breaks whenever the format tweaks. Ask for JSON at the source.
- **Calling `conflicts` on every query** -- it scans all memory files and is expensive; run it only on explicit health-check intent or after bulk imports.
- **Reporting raw `score` to the user** -- users care about what was found, not the number. Translate: high score -> "strong match", low score -> "loose match".
