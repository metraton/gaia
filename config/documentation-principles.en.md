# Documentation Writing Principles for Gaia-Ops

**[🇪🇸 Versión en español](documentation-principles.md)**

This document defines the standards and principles that Gaia (the meta-agent) should follow when creating and updating documentation in the gaia-ops ecosystem.

## 📚 Overview

Documentation in gaia-ops is **human-first**. It should be clear, accessible, and explanatory, allowing anyone to understand the purpose and operation of the system without prior experience.

---

## 🎯 Core Principles

### 1. Simple and Direct Language

**✅ DO:**
- Use short and clear sentences
- Explain technical concepts with simple analogies
- Avoid unnecessary jargon
- Write as if you're explaining to a colleague

**❌ AVOID:**
- Complex or overly formal language
- Acronyms without explanation
- Unnecessary verbosity (be concise but complete)
- Assumptions about prior knowledge

**Example:**

❌ Bad: "The orchestrator uses a semantic routing paradigm based on vector embeddings to determine the optimal agent through cosine similarity analysis."

✅ Good: "The orchestrator analyzes your question and sends it to the most appropriate agent. For example, if you ask about Terraform, it connects you with `terraform-architect`."

---

### 2. Explanatory Over Descriptive

**✅ DO:**
- Explain **what** the component does
- Explain **why** it exists
- Explain **how** it works (basic flow)
- Include practical examples

**❌ AVOID:**
- Lists of files without context
- Technical descriptions without explanation
- Static folder structures (they change frequently)

**Example:**

❌ Bad:
```markdown
## Structure
- agent_router.py
- context_provider.py
- approval_gate.py
```

✅ Good:
```markdown
## Main Components

This system has three key pieces that work together:

**agent_router.py** - The Router
- Analyzes your question and decides which specialist agent should answer it
- Works like a receptionist who directs you to the right department

**context_provider.py** - The Context Provider
- Gathers relevant information before invoking an agent
- Like preparing a file with everything needed before a meeting

**approval_gate.py** - The Approval Gate
- Asks for your confirmation before important operations
- Like a safety double-check before critical changes
```

---

### 3. Simple Flow Diagrams (ASCII)

All READMEs that explain processes or architecture **MUST include** a simple ASCII diagram showing:

#### A. Architecture Flow (Components)
Shows how system components interact.

**Format:**
```
User
  ↓
[Component A]
  ↓
[Component B] → [Component C]
  ↓
Result
```

#### B. Use Story Flow (Real Example)
Shows how a real request flows through the system.

**Format:**
```
Example: "Deploy auth service to production"

1. User asks question
   ↓
2. [Orchestrator] analyzes request
   ↓
3. [Router] identifies → "gitops-operator"
   ↓
4. [Context Provider] gathers cluster info
   ↓
5. [Agent] generates deployment plan
   ↓
6. [Approval Gate] requests confirmation → User approves
   ↓
7. [Agent] executes: kubectl apply
   ↓
8. [Agent] verifies: kubectl get pods
   ↓
9. Result: "✅ Service deployed successfully"
```

**Diagram characteristics:**
- ✅ Simple (maximum 10-12 steps)
- ✅ Use basic symbols: `↓`, `→`, `[]`, `()`
- ✅ Include descriptive text
- ✅ Show a real, concrete example
- ❌ Not complex or using elaborate ASCII art

---

### 4. Consistent README Structure

All READMEs should follow this structure (adaptable based on context):

```markdown
# [Component Title]

**[🇪🇸 Versión en español](README.md)**

[1-2 sentences about what this component does]

## 🎯 Purpose

[Explain why this component exists and what problem it solves]

## 🔄 How It Works

### Architecture Flow

[ASCII diagram of components]

### Example Flow

[ASCII diagram with real use story]

## 📋 Main Components

[Explain each important component with analogies]

## 🚀 Usage

[Practical examples of how to use it]

## 🔧 Technical Details

[Relevant technical details, but clearly explained]

## 📖 References

[Links to related documentation]
```

**Notes:**
- ❌ **DO NOT include** "Folder Structure" (changes frequently)
- ✅ **DO include** flow diagrams
- ✅ **DO include** real, concrete examples
- ✅ **DO explain** with analogies when possible

---

### 5. Language and Versions

**Default language: Spanish**
- All main READMEs in Spanish (`README.md`)
- English as alternative version (`README.en.md`)

**Simple English:**
- When creating `README.en.md`, use accessible English
- Don't assume reader is a native English speaker
- Use direct vocabulary and simple grammatical structures
- Avoid idioms or colloquial expressions

**Examples:**

❌ Complex English: "The orchestrator leverages a sophisticated multi-tiered routing paradigm..."

✅ Simple English: "The orchestrator uses a smart routing system..."

---

### 6. Emojis as Visual Navigation

Use emojis to improve document navigation and scanning:

| Emoji | Use |
|-------|-----|
| 🎯 | Purpose / Goal |
| 🔄 | Flow / Process |
| 📋 | List / Components |
| 🚀 | Usage / Getting Started |
| 🔧 | Technical Details |
| ⚡ | Important / Note |
| ✅ | Good Practice |
| ❌ | Bad Practice |
| 📖 | References / Documentation |
| 🇺🇸/🇪🇸 | Language Versions |

**Note:** Don't overuse emojis. Use them only for main sections.

---

### 7. Concrete Examples Over Abstract Concepts

**✅ DO:**
- Include real use examples
- Show exact commands
- Include expected outputs
- Use specific use cases

**❌ AVOID:**
- Purely conceptual explanations
- Pseudo-code without context
- "Generic example" that's not practical

**Example:**

❌ Bad:
```markdown
## Usage
Run the router with a prompt and you'll get the agent name.
```

✅ Good:
```markdown
## Usage

Ask the router which agent should handle your request:

\```bash
python3 agent_router.py --prompt "Deploy auth-service to prod"
# Output: gitops-operator
\```

Another example - GCP troubleshooting:

\```bash
python3 agent_router.py --prompt "Why is the GKE cluster failing?"
# Output: cloud-troubleshooter
\```
```

---

### 8. Continuous Update

**Gaia's Responsibility:**
- Review and update READMEs when code changes
- Detect inconsistencies between documentation and code
- Propose improvements based on feedback and usage
- Keep examples up to date

**Update triggers:**
- Change in component functionality
- New agents or tools added
- User feedback about clarity
- Changes in project structure

---

## 🛠️ Practical Guide for Gaia

When Gaia creates or updates a README:

### Step 1: Understand the Component
1. Read the source code
2. Identify main functionality
3. Identify dependencies and relationships
4. Find usage examples in code

### Step 2: Create Flow Diagram
1. Draw architecture flow (components)
2. Create example flow (use story)
3. Verify it's simple (max 10-12 steps)
4. Ensure it uses basic ASCII

### Step 3: Write Content
1. Start with 1-2 line explanation
2. Explain **purpose** (why it exists)
3. Explain **how it works** (how it operates)
4. Include flow diagrams
5. List components with explanations
6. Add concrete usage examples
7. Add necessary technical details

### Step 4: Review Quality
- [ ] Simple and direct language?
- [ ] Includes flow diagrams?
- [ ] Has concrete examples?
- [ ] Uses analogies for complex concepts?
- [ ] NO folder structure lists?
- [ ] Follows consistent structure?
- [ ] Uses emojis for navigation?

### Step 5: Create English Version
1. Translate content to English
2. Use simple vocabulary
3. Avoid idioms
4. Maintain identical structure
5. Save as `README.en.md`

---

## 📖 References

**Related documentation:**
- [Agent Catalog](agent-catalog.md) - Complete list of agents
- [Orchestration Workflow](orchestration-workflow.md) - Orchestrator flow
- [Git Standards](git-standards.md) - Commit standards

**Responsible agent:**
- **Gaia** (`agents/gaia.md`) - Meta-agent in charge of documentation

---

**Version:** 1.0.0  
**Last updated:** 2025-11-14  
**Maintained by:** Gaia (meta-agent)

