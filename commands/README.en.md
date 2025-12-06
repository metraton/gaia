# Gaia-Ops Slash Commands

**[Spanish version](README.md)**

Slash commands are quick shortcuts that let you invoke specific system functions directly. They're like keyboard shortcuts for common tasks.

## Purpose

Slash commands provide a fast and consistent way to access advanced features without needing to write complete natural language requests.

**Problem it solves:** Some tasks require direct invocation of specific tools. Instead of verbosely describing what you want to do, you simply use a slash command.

## How It Works

### Architecture Flow

```
User types /command
        |
[Claude Code] detects / pattern
        |
[Command Handler] loads command .md file
        |
[Orchestrator] executes command instructions
        |
Result to user
```

## Available Commands

### Meta-Analysis Commands

#### `/gaia`
Invokes Gaia, the meta-agent that analyzes and optimizes the orchestration system.

**When to use:**
- Analyze system logs
- Investigate routing problems
- Optimize workflows
- Improve documentation

**Example:**
```bash
/gaia Analyze why routing failed in the last 10 requests
```

**Expected output:**
- Detailed event analysis
- Pattern identification
- Improvement recommendations

---

### Spec-Kit Commands

The Spec-Kit framework provides a structured workflow from idea -> implementation.

#### `/speckit.init`
Initializes Spec-Kit in the current project, creating/validating `project-context.json`.

**When to use:**
- First time using Spec-Kit in a project
- To validate existing configuration

**Example:**
```bash
/speckit.init
```

---

#### `/speckit.specify [spec-root] [description]`
Creates a feature specification with auto-filled project context.

**When to use:**
- Starting a new feature
- Documenting requirements

**Example:**
```bash
/speckit.specify spec-kit-auth Add OAuth2 authentication
```

**What it generates:**
- `specs/00N-oauth2-auth/spec.md` with template
- Pre-filled project context (cluster, paths, etc.)
- User stories and functional requirements

---

#### `/speckit.plan [spec-root] [spec-id]`
Generates implementation plan with automatic integrated clarification.

**When to use:**
- After creating the specification
- Before generating tasks

**Example:**
```bash
/speckit.plan spec-kit-auth 003-oauth2-auth
```

**What it generates:**
- `plan.md` - Detailed technical plan
- `data-model.md` - Data model
- `contracts/` - API contracts
- Clarification questions (if ambiguities exist)

---

#### `/speckit.tasks [spec-root] [spec-id]`
Generates enriched task list with inline metadata.

**When to use:**
- After completing the plan
- Before implementing

**Example:**
```bash
/speckit.tasks spec-kit-auth 003-oauth2-auth
```

**What it generates:**
- `tasks.md` with complete metadata:
  - Assigned agent
  - Security tier
  - Category tags
  - Confidence score
- Automatic coverage validation
- Gate if critical issues exist

---

#### `/speckit.implement [spec-root] [spec-id]`
Executes tasks using specialized agents.

**When to use:**
- After generating tasks
- To implement automatically

**Example:**
```bash
/speckit.implement spec-kit-auth 003-oauth2-auth
```

**What it does:**
- Reads enriched tasks.md
- Invokes appropriate agents per task
- T2/T3 tasks -> automatic pre-execution analysis
- Approval gates when needed
- Generates code, tests, documentation

---

#### `/speckit.add-task [spec-root] [spec-id]`
Adds an ad-hoc task during implementation.

**When to use:**
- During implementation
- For tasks not foreseen in the plan

**Example:**
```bash
/speckit.add-task spec-kit-auth 003-oauth2-auth
```

**Asks interactively:**
- Task description
- Task ID
- Implementation phase
- Dependencies

---

#### `/speckit.analyze-task [spec-root] [spec-id] [task-id]`
Deep analysis of a specific task (auto-triggered for T2/T3).

**When to use:**
- For high-risk tasks
- Before executing T3 operations

**Example:**
```bash
/speckit.analyze-task spec-kit-auth 003-oauth2-auth T055
```

**What it analyzes:**
- Potential risks
- Dependencies
- System impact
- Execution recommendations

---

## General Usage

### Basic Syntax

```bash
/command [arguments]
```

### Common Features

**Autocomplete:**
Claude Code suggests available commands when typing `/`

**Inline help:**
All commands support contextual help if invoked without arguments

**Validation:**
Commands validate arguments and give clear feedback if information is missing

### Difference vs Natural Language

| Natural Language | Slash Command |
|------------------|---------------|
| "Analyze system logs" | `/gaia Analyze logs` |
| "Create a spec for OAuth authentication" | `/speckit.specify auth-spec Add OAuth2` |

**Advantages of slash commands:**
- Faster
- Consistent syntax
- Direct tool invocation
- Less ambiguous

**When to use natural language:**
- Exploratory questions
- Problem diagnosis
- Complex queries

## Technical Details

### Command Structure

Each command is a Markdown file in `commands/[name].md` with frontmatter:

```markdown
---
name: command
description: Brief description
usage: Usage syntax
---

# Command

[Detailed instructions for the orchestrator]
```

### Available Commands

```
commands/
├── gaia.md                  (~100 lines)
├── speckit.init.md          (~90 lines)
├── speckit.specify.md       (~120 lines)
├── speckit.plan.md          (~150 lines)
├── speckit.tasks.md         (~140 lines)
├── speckit.implement.md     (~180 lines)
├── speckit.add-task.md      (~70 lines)
└── speckit.analyze-task.md  (~85 lines)
```

**Total:** 8 commands (1 meta + 7 spec-kit)

## References

**Related documentation:**
- [Orchestration Workflow](../config/orchestration-workflow.md) - How the orchestrator processes commands
- [Spec-Kit Framework](../speckit/README.md) - Complete Spec-Kit details
- [Gaia Agent](../agents/gaia.md) - The meta-agent
- [Episodic Memory](../tools/4-memory/episodic.py) - Context memory system

**Underlying tools:**
- Task manager: `tools/5-task-management/task_manager.py`
- Episodic memory: `tools/4-memory/episodic.py`
- Spec-Kit scripts: `speckit/scripts/`

---

**Version:** 2.0.0  
**Last updated:** 2025-12-06  
**Total commands:** 8 (1 meta, 7 spec-kit)  
**Maintained by:** Gaia (meta-agent)
