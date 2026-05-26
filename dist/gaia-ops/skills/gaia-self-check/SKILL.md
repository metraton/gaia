---
name: gaia-self-check
description: Use when the user asks to validate Gaia internal consistency, audit the local installation, or check that skills, agents, and commands in .claude/ are coherent against the declared 8-pillar architecture
metadata:
  user-invocable: true
  type: technique
---

# Gaia Self-Check

## Overview

Validates the internal consistency of a Gaia installation against the
architecture declared in `agents/gaia-system.md` (the 8 pillars + their
sources of truth). The skill has one job: inventory what physically
exists, compare it against what is declared, and surface drift -- both
ways. Cross-refs that point nowhere are drift. Pillars whose source of
truth is empty are drift. Components that exist but fall under no
declared pillar are drift.

The skill operates in one of two **target modes** depending on what the
running workspace has access to:

- `installed_only` -- only `.claude/` exists (consumer install via npm).
- `installed+repo` -- both `.claude/` and the development repo are
  reachable (developer workspace, or sibling clone).

Mode detection happens in Fase 1 and shapes what Fase 3 can offer as a
landing target for fixes.

The principle that keeps this skill safe is **ask-before-fix**: the
skill may detect drift and know exactly how to repair it, but it never
applies the fix on its own. Every proposed change is presented as a
concrete propuesta and waits for explicit aprobación before any edit
happens. The "ask" now covers two questions: *qué arreglar* and *dónde
aterrizarlo*.

## When to activate

The user says things like:
- "check gaia", "valida consistencia", "audita la instalación"
- "mis skills están rotas?", "hay referencias colgantes?"
- "gaia self-check", "self-check", "sanity check de .claude"
- "los pilares de gaia-system están vivos?", "hay componentes huérfanos?"

If the intent is to verify the install **pipeline** (npm, dry-run, beta,
release), that is `gaia-verify`, not this skill. If the intent is to
diagnose a symlink or path problem at the CLI level, that is
`gaia doctor`.

## The 3-phase cycle

Every run follows the same three phases. Detailed operational
instructions for each phase live in `reference.md`.

### Fase 1 -- Inventario + detección de repo

Walk `.claude/skills/`, `.claude/agents/`, `.claude/commands/` and build
a list of every component present. Read each component's frontmatter
and record declared metadata (name, description, references). Hooks are
only inventoried if `settings.json` references them.

Then determine `target_mode`:

1. Resolve symlink targets. `readlink .claude/agents` -- if it points
   into `node_modules/@jaguilar87/gaia/agents/`, look for a sibling
   source repo (`../gaia/`, `../../gaia/`). If found, that path is the
   dev repo.
2. If `GAIA_DEV_REPO` is set in the environment, prefer that path.
3. If neither resolves, ask the user explicitly via `AskUserQuestion`:
   "¿Tienes acceso al repo de desarrollo de Gaia? Si sí, ruta. Si no,
   continuamos en modo `installed_only`."

Record `target_mode` as `installed_only` or `installed+repo`. This
value is consumed by Fase 3.

*[expanded in reference.md -- exact symlink resolution heuristic,
fallback paths, env var precedence]*

### Fase 2 -- Checks de consistencia

For each component, compare declared state against physical state. The
categories of checks are:

- **Frontmatter validity** -- YAML parses, required fields present.
- **Name vs dirname** -- the `name` field matches the directory or file
  name.
- **Cross-references** -- skill-to-skill or agent-to-skill references
  point to components that exist physically.
- **Routing consistency** -- agents mentioned in `config/surface-routing.json`
  exist.
- **README listings** -- if a README exists, listed files are present
  and present files are listed.
- **Pillar source-of-truth integrity** -- parse the 8-pillar table in
  `agents/gaia-system.md`. For each declared pillar, verify that its
  source of truth (ST) physically exists AND is referenced by active
  code/config. A pillar whose ST is missing, empty, or unreferenced is
  fiction -- flag it.
- **Pillar coverage** -- every top-level component (CLI plugins,
  skills, agents, hook modules, release scripts, schema tables) must
  fall under at least one declared pillar. Components that fall under
  none are huérfanos: the pillar is missing or its name is inadequate.
  The converse is also flagged: pillars whose ST resolves to nothing
  active are obsolete.

*[expanded in reference.md -- per-category check rules, how to parse
the pillar table, how to compute coverage, report format]*

### Fase 3 -- Propuesta + selección de target

For every drift found, build a concrete propuesta: which file, what
change, what effect. Present the list to the user. Then, before
applying anything, ask **where** to land each fix via
`AskUserQuestion`:

- **Aplicar en repo dev** -- only offered when
  `target_mode == installed+repo`. The edit goes to the repo working
  tree; the user later commits and opens a PR.
- **Aplicar en `.claude/` directamente** -- the edit goes to the live
  installation. Useful for `installed_only` users and for local-only
  fixes. **Advertencia obligatoria**: un `gaia install` o `gaia update`
  futuro sobrescribirá estos cambios -- la fuente de verdad sigue
  siendo el paquete npm. Esta advertencia es parte del consentimiento
  informado y debe mostrarse explícitamente antes de pedir la
  aprobación.
- **No aplicar** -- solo reportar; el usuario decide después.

The skill records both aprobación de qué (per item) y aprobación de
dónde (per target). Nunca aplica sin ambas.

**Constraint operacional importante.** Editar bajo `.claude/` requiere
`mode: acceptEdits` o `mode: bypassPermissions` en el dispatch del
subagente que aplica, O ejecutar la fase de aplicación en foreground.
CC native bloquea writes en `.claude/**` desde dispatches en background
con `mode: default` -- y en ese path no hay `approval_id` (es protección
nativa de Claude Code, no del hook de Gaia, por lo que el flujo normal
de APPROVAL_REQUEST no aplica). Si el usuario eligió "aplicar en
`.claude/`", el orquestador debe garantizar que el dispatch que ejecuta
la aplicación lleve el `mode` correcto o se corra en foreground.

*[expanded in reference.md -- formato de propuesta, ejemplos del
AskUserQuestion de target, mecánica del dispatch para cada modo]*

## Operating principle: ask-before-fix

The skill is allowed to be wrong. A proposed fix may misread the
user's intent, may touch a file the user wanted stale on purpose, or
may conflict with an in-flight change. The ask-before-fix principle
exists precisely because the skill cannot distinguish "drift" from
"deliberate deviation" on its own.

Practical consequence: the output of this skill is always a **report +
a list of propuestas + a target choice**, never a mutated file. The
skill surfaces findings and waits. The user decides what to fix and
where to land it.

## Output shape

The terminal output is the report. Structure and examples live in
`reference.md` under "Output Format". The short version: one table per
category (the 5 históricas + las 2 nuevas de pilares), columns for
componente, tipo, drift, fix propuesto, y target propuesto.

## Out of scope

- Cualquier path fuera de `.claude/` o del repo dev declarado por el
  usuario -- no se clonan otros repos, no se hacen fetches remotos.
- Running tests or builds -- consistency checks only, no execution.
- Applying fixes automatically -- ask-before-fix applies always.
- Network access of any kind.

## Anti-patterns

- **Auto-fixing "obvious" issues** -- every auto-fix bypasses
  ask-before-fix and teaches the skill that some categories of change
  are safe to take unilaterally. None are.
- **Hard-failing on one bad frontmatter** -- one malformed YAML should
  be reported as drift, not stop the whole scan.
- **Cross-referencing external state beyond the declared repo** -- the
  moment the skill reads outside `.claude/` and the user-declared dev
  repo, it stops being a self-check and becomes an environment audit.
- **Silent propuestas** -- a fix that is not shown to the user in
  human-readable form cannot be aprobado with informed consent.
- **Asumir que el usuario tiene repo dev** -- el skill debe operar
  correctamente en modo `installed_only`. Si no hay repo, "aplicar en
  repo dev" no es una opción válida y no debe ofrecerse.
- **Aplicar a `.claude/` sin advertir el overwrite** -- un fix
  aterrizado en la instalación viva sobrevive solo hasta el próximo
  `gaia install/update`. Omitir esa advertencia rompe el
  consentimiento informado.
