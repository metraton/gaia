---
name: gaia-self-check
description: Use when the user asks to validate Gaia internal consistency, audit the local installation, or check that skills, agents, and commands in .claude/ are coherent
metadata:
  user-invocable: true
  type: technique
---

# Gaia Self-Check

## Overview

Validates the internal consistency of a Gaia installation by inspecting only
`.claude/` on disk. The skill has one job: inventory the components, compare
their declared state against their physical state, and surface discrepancies.
It never reaches outside the installation -- no external repo, no network, no
cloud API.

The principle that keeps this skill safe is **ask-before-fix**: the skill may
detect a broken cross-reference and know exactly how to repair it, but it
never applies the fix on its own. Every proposed change is presented to the
user as a concrete propuesta and waits for explicit aprobación before any
edit happens.

## When to activate

The user says things like:
- "check gaia", "valida consistencia", "audita la instalación"
- "mis skills están rotas?", "hay referencias colgantes?"
- "gaia self-check", "self-check", "sanity check de .claude"

If the intent is to verify the install **pipeline** (npm, dry-run, beta,
release), that is `gaia-verify`, not this skill. If the intent is to diagnose
a symlink or path problem at the CLI level, that is `gaia-doctor`.

## The 3-step cycle

Every run follows the same three phases. Detailed operational instructions
for each phase live in `reference.md`.

### 1. Inventario

Walk `.claude/skills/`, `.claude/agents/`, `.claude/commands/` and build a
list of every component present. Read each component's frontmatter and
record declared metadata (name, description, references). Hooks are only
inventoried if `settings.json` references them. Nothing outside `.claude/`
is touched.

*[expanded in T2 -- details on which directories to scan and how to parse
frontmatter tolerantly]*

### 2. Checks de consistencia

For each component, compare declared state against physical state. The
categories of checks are:

- **Frontmatter validity** -- YAML parses, required fields present.
- **Name vs dirname** -- the `name` field matches the directory or file name.
- **Cross-references** -- skill-to-skill or agent-to-skill references point
  to components that exist physically.
- **Routing consistency** -- agents mentioned in routing config exist.
- **README listings** -- if a README exists, listed files are present and
  present files are listed.

*[expanded in T2 -- full per-category check rules and report format]*

### 3. Propuesta con aprobación

For every inconsistency found, build a concrete propuesta: which file, what
change, what effect. Present the list to the user and wait for explicit
aprobación per item (or a global confirmation if the mechanism does not
support per-item). Record which fixes were aprobado and which were rechazado.
Never apply a change without this approval step -- that is the
ask-before-fix guard.

*[expanded in T3 -- full propuesta format, approval mechanism, handling of
ambiguous cases]*

## Operating principle: ask-before-fix

The skill is allowed to be wrong. A proposed fix may misread the user's
intent, may touch a file the user wanted stale on purpose, or may conflict
with an in-flight change. The ask-before-fix principle exists precisely
because the skill cannot distinguish "inconsistency" from "deliberate
deviation" on its own.

Practical consequence: the output of this skill is always a **report + a
list of propuestas**, never a mutated file. The skill surfaces findings and
waits. The user decides.

## Output shape

The terminal output is the report. Structure and examples live in
`reference.md` under "Output Format". The short version: one table per
category, columns for component, type, inconsistencia, and fix propuesto.

## Out of scope

- Anything outside `.claude/` -- no cloning repos, no fetching remotes.
- Running tests or builds -- consistency checks only, no execution.
- Applying fixes automatically -- ask-before-fix applies always.
- Network access of any kind.

## Anti-patterns

- **Auto-fixing "obvious" issues** -- every auto-fix bypasses ask-before-fix
  and teaches the skill that some categories of change are safe to take
  unilaterally. None are.
- **Hard-failing on one bad frontmatter** -- one malformed YAML should be
  reported as an inconsistency, not stop the whole scan.
- **Cross-referencing external state** -- the moment the skill reads outside
  `.claude/`, it stops being a self-check and becomes an environment audit.
- **Silent propuestas** -- a fix that is not shown to the user in
  human-readable form cannot be aprobado with informed consent.
