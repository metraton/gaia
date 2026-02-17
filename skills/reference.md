# Agent Reference

Reference material for the gaia meta-agent. Load from disk when needed.

## Agent Template

```markdown
---
name: agent-name
description: One-line description of what this agent does
tools: Tool1, Tool2, Tool3
model: inherit
skills:
  - security-tiers
  - output-format
  - agent-protocol
  - context-updater
  - investigation
  - command-execution
---

## TL;DR

**Purpose:** [What this agent does]
**Input:** [What context it needs]
**Output:** [What it produces]
**Tier:** [T0-T2 or T0-T3]

For T3 approval/execution workflows, read `.claude/skills/approval/SKILL.md` and `.claude/skills/execution/SKILL.md`.

---

## Core Identity

[What makes this agent unique - 2-3 paragraphs max]

### Code-First Protocol

1. **Trust the Contract** - [Key contract field]
2. **Analyze Before Generating** - Follow `investigation` skill
3. **Pattern-Aware Generation** - [Domain-specific generation rules]
4. **Validate** - [Domain-specific validation]
5. **Output is a Realization Package** - [What the package contains]

---

## 4-Phase Workflow

### Phase 1: Investigation
Follow `investigation` skill protocol. Then: [domain-specific steps]

### Phase 2: Present
[What to show user]

### Phase 3: Confirm
[Approval requirements]

### Phase 4: Execute
[Execution steps]

---

## Scope

### CAN DO
- [List capabilities]

### CANNOT DO
- [List restrictions with delegation targets]

### DELEGATE
[When to recommend other agents]

---

## Error Handling

| Error | Detection | Recovery |
|-------|-----------|----------|
| [error] | [how detected] | [how to recover] |
```

## Release Checklist

When publishing a new version:

1. Read `package.json` for current version
2. Review changes (`git log`, CHANGELOG.md)
3. Determine version bump (patch/minor/major)
4. Update CHANGELOG.md with changes
5. Test symlinks work in consuming project:
   ```bash
   # In consuming project
   ls -la .claude/  # Should point to node_modules/@jaguilar87/gaia-ops/
   ```
6. Bump version:
   ```bash
   npm version [patch|minor|major]
   ```
7. Publish:
   ```bash
   npm publish --access public
   ```
8. Verify:
   ```bash
   npm info @jaguilar87/gaia-ops version
   ```

## Documentation Template

```markdown
# Component Name

Brief description of what this does and why it exists.
Written like you're explaining to a colleague.

## Where This Fits

```
User request → Orchestrator → **This Tool** → Agent receives context
```

## Quick Start

\`\`\`bash
python3 tool.py --help
\`\`\`

## Examples

\`\`\`bash
python3 tool.py "example input"
# Output: example output
\`\`\`
```
