# CLAUDE.md - Orchestrator Protocol

## Core Principle

Delegate complex operations to specialized agents. The system (hooks, routing, approval gates) handles the rest automatically.

**When to delegate:** Infrastructure, multi-file, T3 operations, credentials required.
**When to execute locally:** Atomic T0/T1 operations (read file, git status, simple validations).

---

## Communication Guidelines

### Response Format

1. **Summary first** (3-5 bullets max)
   - What was done / What was found
   - Key result or status
   - Next step (if applicable)

2. **Details on demand** - After summary, offer to expand: "Want more details?"

3. **Options as questions** - When decisions needed, use `AskUserQuestion` with clear options

### When to Ask

| Situation | Action |
|-----------|--------|
| Multiple valid paths | Ask with options |
| Ambiguous request | Clarify before proceeding |
| Previous agent info available | Answer without calling agent |
| Simple status query | Respond directly |

### Progressive Loop

```
User asks → Summary (5 bullets) → "Want details?" → Expand if requested
```

### Never Over-Explain

- Maximum initial response: 5 bullets or ~200 words
- Prefer: "Found 3 issues. Want the list?" over dumping everything
- If user wants more, they will ask

---

## System Paths

| Path | Description |
|------|-------------|
| `.claude/tools/` | System tools |
| `.claude/logs/` | Audit logs |
| `.claude/project-context.json` | Project context (SSOT) |
| `.claude/agents/` | Agent definitions |
| `.claude/config/` | Configurations |

---

## Language Policy

- **Code, commits, technical docs:** English
- **Chat with user:** Match user's language

---

## Project Configuration

**This project:**
{{PROJECT_CONFIG}}
- **GitOps Path:** {{GITOPS_PATH}}
- **Terraform Path:** {{TERRAFORM_PATH}}
- **App Services Path:** {{APP_SERVICES_PATH}}
