# Gaia-Ops Configuration Files

**[🇪🇸 Versión en español](README.md)**

This directory contains the central configuration and reference documentation of the orchestration system. It's like the knowledge library that agents consult to understand how to work.

## 🎯 Purpose

Configuration files define system behavior, project standards and contracts between components. They provide the "source of truth" for how the system should operate.

**Problem it solves:** Complex systems need centralized configuration and reference documentation. Instead of having scattered information, everything is organized in one accessible place.

## 🔄 How It Works

### Architecture Flow

```
[Agents] need information
        ↓
   Consult config/
        ↓
    ┌──────┴──────┐
    ↓              ↓
[Standards]   [Contracts]
    ↓              ↓
Apply rules   Use context
    ↓              ↓
Consistent operation
```

### Real Example Flow

```
Example: Agent needs to validate a commit message

1. [devops-developer] receives commit message
   ↓
2. Consults → config/git-standards.md
   ↓
3. Reads Conventional Commits rules:
   - Format: <type>(<scope>): <description>
   - Allowed types: feat, fix, docs, etc.
   - Forbidden footer: "Verified by Claude Code"
   ↓
4. Validates against git_standards.json
   ↓
5. Result:
   ✅ "feat(auth): add OAuth2 support" → VALID
   ❌ "updated stuff" → INVALID (doesn't follow format)
```

## 📋 Main Files

### System Documentation

**`AGENTS.md`** - System overview and entry point  
**`orchestration-workflow.md`** (~735 lines) - Complete Phase 0-6 workflow  
**`agent-catalog.md`** (~603 lines) - Complete agent catalog with capabilities  

### Standards and Conventions

**`git-standards.md`** (~682 lines) - Complete Git commit and workflow standards  
**`git_standards.json`** - Programmatic version for automated validation  

### Context Contracts

**`context-contracts.md`** (~673 lines) - What information each agent needs  
**`context-contracts.gcp.json`** - GCP-specific context schema  
**`context-contracts.aws.json`** - AWS-specific context schema  

### Rules and Policies

**`clarification_rules.json`** - Clarification engine configuration (Phase 0)  
**`delegation-matrix.md`** - Decision matrix for delegation  

### Machine Learning Configuration

**`embeddings_info.json`** - Embeddings metadata for semantic matching  
**`intent_embeddings.json`** - Intent vectors for semantic routing  
**`intent_embeddings.npy`** - NumPy version for fast loading  

### Metrics and Targets

**`metrics_targets.json`** - System performance targets  

### Documentation Principles

**`documentation-principles.md`** (NEW) - Standards for writing docs  
**`documentation-principles.en.md`** (NEW) - Doc standards in English  

## 🚀 Using Configuration Files

### For Agents

Agents automatically consult config/ when they need:

```python
# Example: Agent loads git standards
import json
with open('.claude/config/git_standards.json') as f:
    standards = json.load(f)

# Validates commit message
if commit_type not in standards['commit_types']:
    raise ValidationError(f"Invalid type: {commit_type}")
```

### For Developers

Consult Markdown files to understand the system:

```bash
# View complete workflow
cat .claude/config/orchestration-workflow.md

# View Git standards
cat .claude/config/git-standards.md

# View agent catalog
cat .claude/config/agent-catalog.md
```

### For Gaia (Meta-Agent)

Gaia reads config/ for analysis and optimization:

```python
# Gaia analyzes metrics
import json
with open('.claude/config/metrics_targets.json') as f:
    targets = json.load(f)

routing_target = targets['routing_accuracy']
# Compare with current metrics...
```

## 🔧 Technical Details

### Directory Structure

```
config/
├── AGENTS.md                              # System overview
├── orchestration-workflow.md              # Phase 0-6 workflow
├── agent-catalog.md                       # Agent capabilities
├── git-standards.md                       # Git conventions
├── git_standards.json                     # Git rules (programmatic)
├── context-contracts.md                   # Agent context needs
├── context-contracts.gcp.json             # GCP context schema
├── context-contracts.aws.json             # AWS context schema
├── clarification_rules.json               # Clarification config
├── delegation-matrix.md                   # Delegation decisions
├── embeddings_info.json                   # ML metadata
├── intent_embeddings.json                 # Intent vectors
├── intent_embeddings.npy                  # NumPy embeddings
├── metrics_targets.json                   # Performance targets
├── documentation-principles.md            # Doc standards (NEW)
└── documentation-principles.en.md         # Doc standards EN (NEW)
```

**Total:** 17 configuration files

### File Types

| Type | Purpose | Consumers |
|------|---------|-----------|
| **.md** | Human-readable documentation | Humans, Gaia |
| **.json** | Programmatic configuration | Python tools, Tests |
| **.npy** | Optimized ML data | agent_router.py |

## 📖 References

**Tools using config/:**
- `tools/1-routing/agent_router.py` - Reads embeddings
- `tools/2-context/context_provider.py` - Reads contracts
- `tools/3-clarification/engine.py` - Reads clarification_rules
- `tools/4-validation/commit_validator.py` - Reads git_standards
- `agents/gaia.md` - Reads all files

**Related documentation:**
- [Agents](../agents/README.md) - Agent system
- [Tools](../tools/README.md) - Orchestration tools
- [Tests](../tests/README.md) - Test suite

---

**Version:** 1.0.0  
**Last updated:** 2025-11-14  
**Total files:** 17 configuration files  
**Maintained by:** Gaia (meta-agent) + DevOps team

