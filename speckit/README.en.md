# Spec-Kit - Structured Feature Development System

**[🇪🇸 Versión en Español](README.md)**

Structured workflow framework for specification-driven feature development. Spec-Kit is an open-source framework that we've integrated and modified as agentic functionality for Claude Code, providing templates, scripts, and commands that guide features from initial specification through complete implementation with automatic routing to specialized agents.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Commands Reference](#commands-reference)
- [Scripts Reference](#scripts-reference)
- [Templates](#templates)
- [Auto-Enrichment](#auto-enrichment)
- [Agent Routing](#agent-routing)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)
- [References](#references)
- [Support](#support)

## Overview

### What is Spec-Kit?

Spec-Kit provides structured workflow for feature development:
1. **Specify** - Define feature specifications
2. **Clarify** - Resolve ambiguities before planning
3. **Plan** - Create technical implementation plans
4. **Tasks** - Generate actionable task lists with metadata
5. **Analyze** - Validate consistency across artifacts
6. **Implement** - Execute tasks with automatic risk analysis
7. **Constitution** - Maintain project governance principles

### Key Features

- ✅ **Explicit arguments** - Zero setup, everything via parameters
- ✅ **Multi-project** - Work with multiple spec-kits simultaneously
- ✅ **Portable** - Works with any project structure
- ✅ **Auto-enrichment** - Tasks automatically tagged with agent routing metadata
- ✅ **Risk analysis** - High-risk tasks (T2/T3) analyzed before execution
- ✅ **Agent routing** - Tasks routed to specialized agents automatically
- ✅ **Git-agnostic** - User controls Git workflow independently
- ✅ **Template-based** - Consistent structure across features

## Architecture

### Directory Structure

```
.claude/speckit/
├── README.md                # Spanish documentation
├── README.en.md             # This file - English documentation
├── scripts/                 # 5 bash scripts for automation
│   ├── common.sh            # Shared functions (get_feature_paths)
│   ├── create-new-feature.sh   # Create feature directory
│   ├── check-prerequisites.sh  # Validate prerequisites
│   ├── setup-plan.sh        # Setup plan template
│   └── update-agent-context.sh # Update agent context
├── templates/               # 5 markdown templates
│   ├── spec-template.md     # Feature specification template
│   ├── plan-template.md     # Implementation plan template
│   ├── tasks-template.md    # Task list template
│   ├── adr-template.md      # Architecture Decision Record template
│   └── agent-file-template.md  # Agent context file template

.claude/commands/            # 7 /speckit.* commands
├── speckit.specify.md       # Create specification
├── speckit.init.md          # Initialize Spec-Kit structure
├── speckit.plan.md          # Create implementation plan
├── speckit.tasks.md         # Generate task list
├── speckit.analyze-task.md  # Analyze specific task (deep-dive)
├── speckit.implement.md     # Execute implementation
└── speckit.add-task.md      # Add ad-hoc task (with auto-validation)

.claude/tools/               # Python utilities
├── agent_router.py          # Route tasks to agents
├── task_manager.py          # Manage task lifecycle
└── clarify_engine.py        # Ambiguity detection

<project-root>/              # User-specified root (e.g., spec-kit-tcm-plan/)
├── constitution.md          # Project governance principles
└── specs/                   # Feature specifications
    ├── 001-feature-name/
    │   ├── spec.md          # Feature specification
    │   ├── plan.md          # Implementation plan
    │   ├── tasks.md         # Task list (auto-enriched)
    │   ├── research.md      # Research notes (optional)
    │   ├── data-model.md    # Data model (optional)
    │   └── contracts/       # API contracts (optional)
    └── 002-feature-name/
```

### Component Responsibilities

| Component | Responsibility | Used By |
|-----------|---------------|---------|
| **Scripts** | Automation and validation | Commands via Bash |
| **Templates** | Consistent feature structure | Scripts during creation |
| **Commands** | User-facing workflow steps | Claude orchestrator |
| **Tools** | Auto-enrichment, routing | Commands automatically |
| **Constitution** | Project governance | All planning commands |

## Installation

### Initial Setup

**Step 1: Create project directory**
```bash
mkdir -p spec-kit-tcm-plan/specs
```

**Step 2: Initialize Spec-Kit structure**
```bash
/speckit.init spec-kit-tcm-plan
```

**Ready!** Commands are available immediately. Example:

```bash
/speckit.specify spec-kit-tcm-plan "Add dark mode"
/speckit.plan spec-kit-tcm-plan 001-add-dark
/speckit.tasks spec-kit-tcm-plan 001-add-dark
/speckit.implement spec-kit-tcm-plan 001-add-dark
```

---

## Commands Reference

| Command | Syntax | Purpose | When to Use |
|---------|--------|---------|-------------|
| **init** | `/speckit.init <root>` | Bootstrap Spec-Kit structure | Initial project setup |
| **specify** | `/speckit.specify <root> "description"` | Create new feature specification | Start of workflow |
| **plan** | `/speckit.plan <root> <feature>` | Create technical implementation plan | After specify |
| **tasks** | `/speckit.tasks <root> <feature>` | Generate task list with metadata | After plan |
| **implement** | `/speckit.implement <root> <feature>` | Execute tasks with automatic routing | After tasks |
| **add-task** | `/speckit.add-task <root> <feature> "desc"` | Add ad-hoc task with validation | During implement |
| **analyze-task** | `/speckit.analyze-task <root> <feature> T###` | Deep analysis of specific task | Before executing risky tasks |

### Usage Examples

```bash
# Basic complete workflow
/speckit.init spec-kit-tcm-plan
/speckit.specify spec-kit-tcm-plan "Project Guidance Deployment"
/speckit.plan spec-kit-tcm-plan 004-project-guidance-deployment
/speckit.tasks spec-kit-tcm-plan 004-project-guidance-deployment
/speckit.implement spec-kit-tcm-plan 004-project-guidance-deployment

# During implementation
/speckit.add-task spec-kit-tcm-plan 004-project-guidance-deployment "Fix config error"
/speckit.analyze-task spec-kit-tcm-plan 004-project-guidance-deployment T042
```

---

## Scripts Reference

Location: `.claude/speckit/scripts/`

- `common.sh`: Shared functions (`get_feature_paths`, path resolution from arguments)
- `create-new-feature.sh`: Creates feature structure and `spec.md`
- `check-prerequisites.sh`: Validates required and optional artifacts
- `setup-plan.sh`: Creates `plan.md` from template
- `update-agent-context.sh`: Syncs agent context

---
**Purpose:** Task list template

**Location:** `.claude/speckit/templates/tasks-template.md`

**Format:**
```markdown
- [ ] T001 Task description
```

**Used by:** `/speckit.tasks`

---

## Auto-Enrichment

### What is Auto-Enrichment?

Automatic injection of metadata into tasks for agent routing and risk assessment.

### When Does It Happen?

**Automatic enrichment:**
- ✨ `/speckit.tasks` - All tasks enriched when generated
- ✨ `/speckit.add-task` - New task enriched when added

**No manual `/enrich` step needed**

### Enrichment Process

**Step 1: Agent routing**
```bash
python3 .claude/tools/agent_router.py --json "Task description"
```

**Step 3: Metadata injection**
```markdown
- [ ] T001 Create GKE cluster
  <!-- 🤖 Agent: terraform-architect | ✅ T1 | ❓ 0.85 -->
  <!-- 🏷️ Tags: #terraform #gcp #gke -->
  <!-- 🎯 skill: terraform_operations (8.0) -->
  <!-- 🔄 Fallback: devops-developer -->
```

### Metadata Components

**Agent assignment:**
```
🤖 Agent: terraform-architect
```
Primary agent for task execution

**Risk tier:**
```
✅ T0 (read-only)
✅ T1 (validation)
🔒 T2 (simulation) - Requires analysis
🚫 T3 (blocked) - Not executed
```

**Confidence score:**
```
❓ 0.85 (0.0-1.0 scale)
```
Router confidence in agent assignment

**Tags:**
```
🏷️ Tags: #terraform #gcp #gke
```
Technology and domain tags

**Skill scores:**
```
🎯 skill: terraform_operations (8.0)
```
Agent capability match

**Fallback agent:**
```
🔄 Fallback: devops-developer
```
Alternative if primary fails

**High-risk warning:**
```
⚠️ HIGH RISK: Analyze before execution
💡 Suggested: /speckit.analyze-task T001
```
For T2/T3 tasks only

### Enrichment Benefits

- [x] Automatic agent routing
- [x] Risk visibility
- [x] Execution safety
- [x] Audit trail
- [x] Team coordination

## Agent Routing

### How Routing Works

**Step 1: Parse task metadata**
```markdown
<!-- 🤖 Agent: gitops-operator | ✅ T0 | ❓ 0.92 -->
```

**Step 2: Load agent context**
```python
from .claude.tools.context_section_reader import ContextSectionReader
context = reader.get_for_agent('gitops-operator')
```

**Step 3: Invoke specialized agent**
```python
Task(
    subagent_type='gitops-operator',
    prompt=f"{context}\n\n{task_instructions}"
)
```

### Available Agents

| Agent | Specialization | Risk Tiers |
|-------|---------------|-----------|
| **terraform-architect** | Terraform/Terragrunt validation | T0-T1 |
| **gitops-operator** | Kubernetes/Flux operations | T0-T1 |
| **cloud-troubleshooter** | GCP diagnostics | T0 only |
| **devops-developer** | Application development | T0-T2 |
| **cloud-troubleshooter** | AWS diagnostics (standby) | T0 only |

### Routing Decision Factors

**Keyword matching:**
- "terraform" → terraform-architect
- "kubectl", "flux" → gitops-operator
- "gcp", "gke" → cloud-troubleshooter
- "build", "test" → devops-developer

**Skill scoring:**
```
Agent: gitops-operator
- skill: kubernetes_operations (9.0)
- skill: flux_operations (8.5)
- skill: helm_management (7.0)
```

**Context requirements:**
- Cluster name → gitops-operator, cloud-troubleshooter
- Terraform path → terraform-architect
- Repository → devops-developer

### Routing CLI

**Manual routing (for debugging):**
```bash
python3 .claude/tools/agent_router.py --json "Check pods in namespace"
python3 .claude/tools/agent_router.py --explain "Validate terraform config"
python3 .claude/tools/agent_router.py --test
```

### Fallback Behavior

**If primary agent fails:**
1. Check fallback agent in metadata
2. Retry with fallback
3. If fallback fails, escalate to user

**If no agent specified:**
- Default to `devops-developer` (general-purpose)

## Troubleshooting

### Config Not Found

**Error:**
```
ERROR: Spec-Kit not initialized. Run: /speckit.init --root <directory>
```

**Solution:**
```bash
# Initialize Spec-Kit first
/speckit.init --root spec-kit-tcm-plan

# Verify config created
cat .claude/speckit/config.json
```

---

### Constitution Not Found

**Error:**
```
WARNING: constitution.md not found at spec-kit-tcm-plan/constitution.md
```

**Solution:**
```bash
# Create constitution
# Create governance document manually if needed
```

---

### Feature Directory Missing

**Error:**
```
ERROR: Feature directory not found
Run /specify first to create the feature structure.
```

**Solution:**
```bash
# Create new feature
/speckit.specify "Feature description"
```

---

### Plan Missing

**Error:**
```
ERROR: plan.md not found in spec-kit-tcm-plan/specs/003-feature-name
Run /plan first to create the implementation plan.
```

**Solution:**
```bash
# Create plan
/speckit.plan "Architecture decisions"
```

---

### Tasks Not Enriched

**Symptoms:**
- Tasks missing metadata comments
- No agent assignments
- No risk tiers

**Solution:**
Tasks are automatically enriched by `/speckit.tasks` and `/speckit.add-task`. No manual action needed.

**Verify enrichment:**
```bash
# Check tasks.md for metadata
grep "🤖 Agent:" spec-kit-tcm-plan/specs/003-feature-name/tasks.md
```

---

### Wrong Agent Assigned

**Symptoms:**
- Task routed to incorrect agent
- Low confidence score (<0.5)

**Solution:**
```bash
# Manually test routing
python3 .claude/tools/agent_router.py --explain "Task description"

# Check suggested agent and confidence
# Edit tasks.md metadata if needed
```

**Manual override:**
```markdown
- [ ] T001 Task description
  <!-- 🤖 Agent: correct-agent | ✅ T1 | ❓ 0.85 -->
```

---

### High-Risk Task Blocked

**Symptoms:**
- Task marked with ⚠️ HIGH RISK
- `/speckit.implement` requests confirmation

**This is expected behavior for T2/T3 tasks**

**Solution:**
1. Review task carefully
2. Run `/speckit.analyze-task T001`
3. Confirm if safe to proceed
4. If not safe, modify approach

---

### Paths Hardcoded in Old Code

**Symptoms:**
- Scripts fail to find files
- Errors about missing `specs/` directory

**Solution:**
Verify all scripts load config:

```bash
# Check scripts source common.sh and call load_config
grep -n "load_config" .claude/speckit/scripts/*.sh

# Should see in:
# - create-new-feature.sh
# - check-prerequisites.sh
# - setup-plan.sh
# - update-agent-context.sh
```

---

### JQ Not Installed

**Error:**
```
ERROR: jq is required but not installed
```

**Solution:**
```bash
# macOS
brew install jq

# Ubuntu/Debian
sudo apt-get install jq

# Verify installation
jq --version
```

## Best Practices

### Configuration Management

- ✅ Run `/speckit.init` once per project
- ✅ Commit config.json to git
- ✅ Keep constitution in project root (not .claude/)
- ✅ Don't hardcode paths in custom scripts

### Feature Development

- ✅ Follow workflow order (specify → plan → tasks → implement)
- ✅ Use clarify_engine.py for ambiguity detection (automatic)
- ✅ Run `/speckit.analyze-task` for high-risk tasks before execution
- ✅ Let auto-enrichment handle metadata (don't edit manually)

### Risk Management

- ✅ Always analyze T2/T3 tasks before execution
- ✅ Review agent assignments for high-risk tasks
- ✅ Keep confidence scores >0.7 for critical operations
- ✅ Use fallback agents when primary confidence is low

### Git Workflow

- ✅ User controls Git workflow (no auto-branching)
- ✅ Branch when ready (not enforced by scripts)
- ✅ Commit regularly during implementation
- ✅ Use descriptive commit messages

### Documentation

- ✅ Keep constitution up-to-date with learnings
- ✅ Document architecture decisions in plan.md
- ✅ Create research.md for investigation notes
- ✅ Use contracts/ for API specifications

## References

### Internal Documentation

- `.claude/README.md` - Complete agent system documentation
- `.claude/project-context/project-context.json` - Project-specific context
- `CLAUDE.md` - Repository guidance for Claude Code
- `spec-kit-tcm-plan/constitution.md` - Project principles

### Command Files

All commands in `.claude/commands/speckit.*.md`:
- speckit.init.md
- speckit.specify.md
- speckit.plan.md
- speckit.tasks.md
- speckit.analyze-task.md
- speckit.implement.md
- speckit.add-task.md

### Tool Files

- `.claude/tools/agent_router.py` - Agent routing logic
- `.claude/tools/task_manager.py` - Task lifecycle management
- `.claude/tools/clarify_engine.py` - Ambiguity detection
- `.claude/tools/context_provider.py` - Context provisioning

**Framework Base**

Spec-Kit is an open-source framework adapted as agentic functionality for Claude Code. Main modifications:

- ✅ Explicit arguments - No centralized configuration
- ✅ Zero setup - No initialization required
- ✅ Auto-enrichment - Tasks with routing metadata
- ✅ Risk analysis - T0-T3 with automatic validation
- ✅ Multi-project - Simultaneous spec support
- ✅ Agentic integration - Automatic routing to specialized agents

---

## Support

**For Claude orchestrator:**
- Read this file when user mentions "speckit" or "spec-kit"
- Reference specific sections as needed
- Use commands, not direct file manipulation

**For users:**
- **Zero setup** - No initialization needed
- Create project directory: `mkdir -p spec-kit-tcm-plan/specs`
- Use explicit arguments: `<speckit-root> <feature-name>`
- Follow workflow phases in order
- Trust auto-enrichment (don't edit metadata manually)
- Analyze high-risk tasks before execution

**For Spanish documentation:** Ver [README.md](README.md)

---
