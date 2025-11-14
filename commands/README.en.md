# Gaia-Ops Slash Commands

**[🇪🇸 Versión en español](README.md)**

Slash commands are quick shortcuts that let you invoke specific system functions directly. They're like keyboard shortcuts for common tasks.

## 🎯 Purpose

Slash commands provide a fast and consistent way to access advanced features without needing to write complete natural language requests.

**Problem it solves:** Some tasks require direct invocation of specific tools. Instead of verbosely describing what you want to do, you simply use a slash command.

## 🔄 How It Works

### Architecture Flow

```
User types /command
        ↓
[Claude Code] detects / pattern
        ↓
[Command Handler] loads command .md file
        ↓
[Orchestrator] executes command instructions
        ↓
Result to user
```

### Real Example Flow

```
Example: "/save-session production-deploy"

1. User types: /save-session production-deploy
   ↓
2. [Claude Code] detects slash command
   ↓
3. [Command Handler] reads → commands/save-session.md
   ↓
4. [Save Session Tool] executes:
   - Gathers active context
   - Saves session/active/active-context.json
   - Creates bundle: session/bundles/production-deploy.bundle.json
   - Generates summary
   ↓
5. Result:
   "✅ Session saved: production-deploy
    Files: 12 | Size: 45KB | Context: 3.2K tokens"
```

## 📋 Available Commands

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

### Session Commands

#### `/save-session [name]`
Saves current work context in a persistent bundle.

**When to use:**
- Before ending the day
- After completing an important task
- Before switching context to another task
- To share context with another developer

**Example:**
```bash
/save-session deploy-auth-v2
```

**What it saves:**
- Open and modified files
- Relevant conversations
- Project state (project-context.json)
- Executed commands

---

#### `/restore-session [name]`
Restores a previously saved work context.

**When to use:**
- When starting the day
- When resuming a paused task
- When onboarding a new dev

**Example:**
```bash
/restore-session deploy-auth-v2
```

**What it restores:**
- Bundle file list
- Previous conversations
- Project state
- Complete context to continue

---

#### `/session-status`
Shows current active session status.

**When to use:**
- To check what will be saved
- To see context size
- To review tracked files

**Example:**
```bash
/session-status
```

**Information shown:**
```
📊 Active Session Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Files tracked: 12
Context size: 3.2K tokens
Last updated: 2 minutes ago

Recent activity:
- Modified: gitops/deployment.yaml
- Executed: kubectl apply
- Agent: gitops-operator
```

---

### Spec-Kit Commands

The Spec-Kit framework provides a structured workflow from idea → implementation.

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
- T2/T3 tasks → automatic pre-execution analysis
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

## 🚀 General Usage

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
| "Save current context with name deploy-v2" | `/save-session deploy-v2` |
| "Analyze system logs" | `/gaia Analyze logs` |
| "Create a spec for OAuth authentication" | `/speckit.specify auth-spec Add OAuth2` |

**Advantages of slash commands:**
- ✅ Faster
- ✅ Consistent syntax
- ✅ Direct tool invocation
- ✅ Less ambiguous

**When to use natural language:**
- Exploratory questions
- Problem diagnosis
- Complex queries

## 🔧 Technical Details

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
├── save-session.md          (~80 lines)
├── restore-session.md       (~75 lines)
├── session-status.md        (~60 lines)
├── speckit.init.md          (~90 lines)
├── speckit.specify.md       (~120 lines)
├── speckit.plan.md          (~150 lines)
├── speckit.tasks.md         (~140 lines)
├── speckit.implement.md     (~180 lines)
├── speckit.add-task.md      (~70 lines)
└── speckit.analyze-task.md  (~85 lines)
```

**Total:** 11 commands (1 meta + 3 session + 7 spec-kit)

## 📖 References

**Related documentation:**
- [Orchestration Workflow](../config/orchestration-workflow.md) - How the orchestrator processes commands
- [Spec-Kit Framework](../speckit/README.md) - Complete Spec-Kit details
- [Gaia Agent](../agents/gaia.md) - The meta-agent
- [Session Management](../tools/5-task-management/README.md) - Session system

**Underlying tools:**
- Session manager: `tools/5-task-management/session-manager.py`
- Task manager: `tools/5-task-management/task_manager.py`
- Spec-Kit scripts: `speckit/scripts/`

---

**Version:** 1.0.0  
**Last updated:** 2025-11-14  
**Total commands:** 11 (1 meta, 3 session, 7 spec-kit)  
**Maintained by:** Gaia (meta-agent)

