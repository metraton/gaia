---
name: memory-curation
description: Use when reorganizing, deduplicating, or pruning accumulated memory files and the MEMORY.md index
metadata:
  user-invocable: false
  type: reference
---

# Memory Curation

Organize and maintain memory files that Claude Code saves natively. This skill does not cover creating or searching memory -- only curating what already exists.

## Memory Structure

Memory lives at `~/.claude/projects/{project-slug}/memory/`.

| Component | Purpose |
|-----------|---------|
| `MEMORY.md` | Index file -- table of all memory files with descriptions |
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

| Type | Purpose | Example |
|------|---------|---------|
| `project` | Repo/system knowledge | `project_gaia_v5.md` |
| `user` | Personal preferences | `user_blog_articles.md` |
| `feedback` | Corrections/learnings | `feedback_terraform_style.md` |

## Curation Operations

### Index Integrity

MEMORY.md must reflect actual files. To reconcile:

1. Scan the memory directory for all `.md` files (excluding MEMORY.md itself)
2. Compare against the index table rows
3. Add missing files to the index; remove rows for deleted files
4. Update descriptions that no longer match the file's actual content

### Deduplication

1. Read all memory files and identify overlapping topics
2. Merge content into the file with the broader scope
3. Delete the narrower file
4. Update the index

### Pruning Stale Entries

1. Identify entries that reference outdated projects, deprecated tools, or resolved decisions
2. Remove or archive the stale content
3. Update the index

### Merging Overlapping Topics

1. When two files cover adjacent concerns, merge into one with a clear scope
2. Choose the name that best represents the combined topic
3. Update frontmatter description to reflect the merged scope
4. Delete the redundant file and update the index

### Splitting Overgrown Files

When a file exceeds ~100 lines, split into focused subtopics. Create one file per subtopic, update the index for each.

## Rules

| Rule | Reason |
|------|--------|
| Always update index and files together | Prevents drift between MEMORY.md and actual files |
| One topic per file | Split if a file outgrows its scope |
| Frontmatter required | Every file needs name, description, type in YAML frontmatter |
| Conciseness | Memory files are scannable references -- tables, bullets, short sections |
| Confirm before deleting | Report what will be pruned/merged and get user confirmation |
