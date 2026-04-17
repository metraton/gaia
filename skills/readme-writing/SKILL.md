---
name: readme-writing
description: Use when writing or updating a README for a Gaia component folder (agents/, skills/, hooks/, commands/, config/, bin/, tests/, build/, templates/, or the repo root)
metadata:
  user-invocable: false
  type: technique
---

# README Writing

A folder README is not a table of contents. It is the mental model a developer or agent needs before touching anything in that folder. A README that only lists files is worse than none -- it creates the impression the folder is understood when it is not.

Gaia is event-driven. Every component has a trigger: a hook fires, a skill is injected, a command is dispatched, a config file is loaded. A README that does not explain WHEN and HOW a component activates leaves the reader guessing the most important thing.

## Step 1: Choose your target

Write or update a README when:
- A new folder is created (agents/, skills/<name>/, hooks/, etc.)
- You add a file that changes what the folder does or when it activates
- A drift report in `cross_layer_impacts` flags a README as stale

## Step 2: Write the 5 sections in order

Every README uses this structure. Order is not optional -- a reader skimming top-to-bottom should understand activation before they see a file tree.

**Section 1: Intro narrative** (2-4 paragraphs, no bullets, conversational)
- One sentence on what lives here
- Why this folder exists separately (the conceptual contract)
- How to think about this folder (mental model or analogy)
- Who touches it: developer, agent at runtime, CI, admin

**Section 2: When activated** (the core -- do not skip)
- The concrete trigger: what event, condition, or code path fires this
- ASCII diagram if more than 2 steps chain together
- Step-by-step list as complement when the diagram is not enough
- What happens if this folder is absent or broken

**Section 3: What's here** (annotated tree)
- One-line comment per file or subdirectory
- Mark generated files so they are not edited by hand

**Section 4: Conventions** (concrete rules, not aspirations)
- How to name new files
- What internal structure new files must follow
- What to update elsewhere when adding something here
- What validation runs against this folder

**Section 5: See also** (relative links with reason)
- Adjacent components with a one-line reason per link

## Step 3: Write the activation section for judgment

The activation section fails when it describes intent ("skills are injected at startup") without describing mechanism ("the pre_tool_use hook reads `skills:` from agent frontmatter, then calls `skill_injection.py`, which reads each SKILL.md and prepends it to the agent context").

Concrete mechanism is the test. If the description would be true for any event-driven system, it is not concrete enough.

## Step 4: Integration points

**With skill-creation:** When completing a new skill, update the `skills/` README to reflect the new entry. This is the last step of the skill-creation workflow, not optional cleanup.

**With gaia-patterns (Documentation Drift Awareness):** When an agent adds a file to `agents/`, `skills/`, `hooks/`, or any top-level folder, it must include the relevant README in `cross_layer_impacts` if the README no longer accurately describes the folder. The orchestrator dispatches a readme-writing task from that signal. The agent that added the file does NOT update the README itself -- it reports drift and stops.

**With test_directory_structure.py:** The system test verifies README existence for all key folders. Adding a new top-level folder without a README will cause a test failure. See `tests/system/test_directory_structure.py`.

## Anti-Patterns

- **Activation section describes intent, not mechanism** -- "agents use skills" is intent; "pre_tool_use.py reads frontmatter and calls skill_injection.py" is mechanism.
- **File tree without comments** -- a bare tree adds no value over `ls`; every entry needs a reason.
- **Conventions that are aspirational** -- "files should be well-named" is not a convention; "skill folders use kebab-case matching the `name:` field in frontmatter" is.
- **See also without reasons** -- a link list without context shifts the burden to the reader.
- **Updating README inline during feature work** -- drift reporting exists so README updates happen as deliberate tasks, not rushed afterthoughts mid-feature.
