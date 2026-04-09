---
name: memory-management
description: Use when creating, updating, searching, or managing the user's persistent memory files and MEMORY.md index
metadata:
  user-invocable: false
  type: reference
---

# Memory Management

## Memory Structure

Memory lives at `~/.claude/projects/{project-slug}/memory/`.

| Component | Purpose |
|-----------|---------|
| `MEMORY.md` | Index file — table of all memory files with descriptions |
| `{type}_{topic}.md` | Individual memory files, one per topic |

## File Format

Every memory file has YAML frontmatter and markdown body:

```yaml
---
name: project_gaia_v5
description: Gaia v5 architecture decisions
type: project
---
```

Types: `project` (repo/system knowledge), `user` (personal preferences), `feedback` (corrections/learnings).

## Operations

### Create
1. Search existing files for the topic (deduplication)
2. Choose type and name: `{type}_{topic}.md`
3. Write file with frontmatter + content
4. Add row to MEMORY.md index table

### Update
1. Read existing file
2. Modify content (append or replace sections)
3. Update MEMORY.md description if scope changed

### Search
1. Grep across memory files for the query
2. Read MEMORY.md index to find relevant files by description
3. Report findings with source file references

## Rules

- **Index integrity**: MEMORY.md must always reflect actual files. Never create/delete without updating the index.
- **Conciseness**: Memory files are scannable references, not prose. Use tables, bullets, short sections.
- **One topic per file**: Don't merge unrelated topics. Split if a file outgrows its scope.
- **Deduplication**: Before creating, search existing files. Update rather than duplicate.
- **Frontmatter required**: Every file needs name, description, type in YAML frontmatter.

## Anti-Patterns

- **Prose dumps** — storing paragraphs of narrative instead of scannable tables and bullets.
- **Orphaned files** — creating memory files without adding them to MEMORY.md index.
- **Topic creep** — adding unrelated content to an existing file instead of creating a new one.
- **Duplicate creation** — creating a new file without checking if one for the topic already exists.
