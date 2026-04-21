# Gaia Self-Check -- Reference

Operational detail for the three phases of the self-check cycle. The main
SKILL.md defines the cycle and the ask-before-fix principle; this file
holds the per-category check rules, output format, and propuesta mechanics.

This reference is intentionally a scaffold. T2 expands the per-category
check rules. T3 expands the propuesta + aprobación flow. Placeholders below
mark where each expansion lands.

## Scope

The skill operates exclusively on `.claude/`. The inventory walk covers:

| Directory | Component | Always scanned |
|-----------|-----------|----------------|
| `.claude/skills/` | Skills | Yes |
| `.claude/agents/` | Agents | Yes |
| `.claude/commands/` | Slash commands | Yes |
| `.claude/hooks/` | Hooks | Only if referenced in `settings.json` |

No path outside `.claude/` is read, regardless of what a component's
frontmatter references.

## Output Format

The report is terminal-friendly markdown: one section per category, each
with a table. Empty categories are reported as "OK" so the user can see
the scan covered them.

Columns:

| Column | Meaning |
|--------|---------|
| Componente | File or directory name |
| Tipo | Skill / Agent / Command / Hook |
| Inconsistencia | One-line description of what is wrong |
| Fix propuesto | One-line description of the proposed change |

Each category section below contains a concrete example table. An empty
category (no findings) is reported as a single "OK" row so the user can
confirm the scan covered it.

At the end of the report, a summary line: `N inconsistencias encontradas
en M componentes. Propuesta pendiente de aprobación.`

## Categorías de checks

Each category describes: what to verify, how to detect it, and what a
positive finding (inconsistency) looks like. The agent reads the relevant
files using Read and Glob tools -- no shell commands, no external state.

### Frontmatter validity

**Qué verifica:** Every `SKILL.md` (in `skills/*/`), `*.md` agent file (in
`agents/`), and `*.md` command file (in `commands/`) must have a YAML
frontmatter block delimited by `---` that parses without error.

**Cómo detectarlo:**

```
for each component file:
  content = Read(file)
  if content does not contain '---' at start and again later:
    FINDING: missing frontmatter block
  else:
    block = text between first and second '---'
    try parse as YAML:
      if parse error: FINDING: malformed YAML frontmatter
      if required fields missing (name, description):
        FINDING: missing required field <field>
```

Required fields by component type:

| Type | Required fields |
|------|----------------|
| Skill (`SKILL.md`) | `name`, `description` |
| Agent (`agents/*.md`) | `name`, `description`, `tools` |
| Command (`commands/*.md`) | `name`, `description` |

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto |
|------------|------|----------------|---------------|
| `skills/my-skill/SKILL.md` | Skill | Frontmatter YAML inválido: mapping values not allowed here (line 3) | Corregir indentación YAML en el frontmatter |
| `agents/my-agent.md` | Agent | Campo requerido `tools` ausente del frontmatter | Agregar `tools:` con la lista de herramientas del agent |

---

### Name-directory match (dirname)

**Qué verifica:** The `name` field in the frontmatter must match the
component's directory name (for skills) or file stem (for agents and
commands).

**Cómo detectarlo:**

```
skills:
  for each dir in .claude/skills/ (skip README.md, reference.md):
    skill_file = dir / SKILL.md
    name_in_frontmatter = yaml(skill_file).get('name')
    expected = dir.name          # e.g. "gaia-self-check"
    if name_in_frontmatter != expected:
      FINDING: name mismatch

agents:
  for each file in .claude/agents/*.md:
    name_in_frontmatter = yaml(file).get('name')
    expected = file.stem         # e.g. "gaia-system" from "gaia-system.md"
    if name_in_frontmatter != expected:
      FINDING: name mismatch

commands: same pattern as agents
```

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto |
|------------|------|----------------|---------------|
| `skills/gaia-ops/SKILL.md` | Skill | `name: gaia_ops` en frontmatter, directorio es `gaia-ops` | Cambiar `name` a `gaia-ops` en el frontmatter |
| `agents/terraform.md` | Agent | `name: terraform-architect` en frontmatter, archivo es `terraform.md` | Renombrar archivo a `terraform-architect.md` o corregir `name` |

---

### Cross-references resolvables (cross-reference)

**Qué verifica:** References from a component's frontmatter to other skills
must point to directories that exist physically in `.claude/skills/`. This
catches renamed or deleted skills that are still listed as dependencies.

**Cómo detectarlo:**

```
for each SKILL.md:
  yaml_data = parse frontmatter
  refs = yaml_data.get('skills', [])       # list of skill names
  for each ref in refs:
    target = .claude/skills/<ref>/
    if target directory does not exist:
      FINDING: cross-reference to missing skill
```

Also check narrative cross-references in the body: if the file body
mentions a `skills/<name>/` path, verify that path exists under `.claude/`.
This is best-effort -- report only paths that look like structured
references (e.g., `` `skills/foo/SKILL.md` ``), not every mention of a name.

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto |
|------------|------|----------------|---------------|
| `agents/gaia-system.md` | Agent | Skill `nah-patterns` referenciada en frontmatter no existe en `.claude/skills/` | Eliminar `nah-patterns` del frontmatter o crear la skill |

---

### Orphan/listed consistency (routing)

**Qué verifica:** Two independent checks:

1. **Disk vs listing**: Every skill directory under `.claude/skills/` that
   contains a `SKILL.md` should appear in `skills/README.md`. Skills
   present on disk but absent from the README are orphans.
2. **Listing vs disk**: Every skill listed in `skills/README.md` should
   have a matching directory with a `SKILL.md` on disk. Listed-but-missing
   skills are broken references.

For agents: same pattern against any listing in `agents/README.md` if one
exists.

For routing config: if `.claude/config/surface-routing.json` exists,
each `primary_agent` value must match a file stem in `.claude/agents/`.
A routing entry pointing to a non-existent agent is a broken cross-reference
between config and agents directory.

**Cómo detectarlo:**

```
skills on disk   = {dir.name for dir in .claude/skills/ if (dir/SKILL.md).exists()}
skills in README = {name parsed from each row in skills/README.md table}

orphans  = skills_on_disk - skills_in_README
missing  = skills_in_README - skills_on_disk

routing  = parse .claude/config/surface-routing.json
agents_on_disk = {f.stem for f in .claude/agents/*.md}
for each surface in routing.surfaces:
  agent = surface.primary_agent
  if agent not in agents_on_disk:
    FINDING: routing references missing agent
```

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto |
|------------|------|----------------|---------------|
| `skills/gaia-self-check/` | Skill | Directorio existe en disco, no listado en `skills/README.md` | Agregar entrada en `skills/README.md` |
| `skills/old-skill/` | Skill | Listado en `skills/README.md` pero directorio ausente en disco | Eliminar entrada del README o restaurar la skill |
| `config/surface-routing.json` | Config | `primary_agent: ghost-agent` no existe en `.claude/agents/` | Actualizar `primary_agent` o crear `ghost-agent.md` |

---

### hooks/ (opcional)

**Cuándo aplicar:** Only when `settings.json` contains `hooks` entries that
reference files under `.claude/hooks/`.

**Qué verifica:** Each hook file referenced in `settings.json` must exist
at the declared path. A hook registered but absent on disk causes silent
failures at runtime -- the harness calls the hook and gets a file-not-found
error.

**Cómo detectarlo:**

```
settings = parse .claude/settings.json
for each hook entry in settings.hooks:
  path = hook entry command or file path
  if path starts with .claude/hooks/:
    if file does not exist at path:
      FINDING: hook registered in settings.json but file missing
```

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto |
|------------|------|----------------|---------------|
| `settings.json` | Config | Hook `.claude/hooks/post_tool_use.py` registrado pero archivo no existe | Crear el archivo del hook o eliminar la entrada de `settings.json` |

## Propuesta y Aprobación

The ask-before-fix principle governs every corrective action the skill
might take. The skill is allowed to detect, describe, and propose --
never to apply. Aprobación explícita del usuario is the only gate that
unlocks a fix. This section operationalizes that principle into a
repeatable flow.

### El flujo completo

```
Inconsistencia detectada
        |
        v
Construir propuesta (qué archivo, qué cambio exacto, qué efecto)
        |
        v
Presentar al usuario via AskUserQuestion (una por finding)
        |
        v
  aprobado?  ----yes----> Aplicar fix + registrar como aprobado
     |
     no
     |
     v
  Sin cambios + registrar como "ignored by user"
```

One approval per delta. Each finding is its own propuesta -- no bulk
approval. If the user approves items 1 and 3 but rejects item 2, fixes
1 and 3 are applied and item 2 is left untouched.

### Plantilla de propuesta

Every propuesta presented to the user must include these fields:

```
Finding:   <one-line description of the inconsistency detected>
Archivo:   <absolute path of the file to be modified>
Fix:       <exact change -- field value to set, line to add/remove, etc.>
Efecto:    <what changes after the fix is applied>
Rollback:  <how to undo -- typically "revert <field> to previous value">
```

Do not omit any field. A propuesta missing "Rollback" or "Efecto" cannot
be aprobado with informed consent -- silent propuestas violate
ask-before-fix as much as auto-fixes do.

### Ejemplo concreto

The agent detects that `skills/gaia-ops/SKILL.md` has `name: gaia_ops`
but the directory is named `gaia-ops`. The propuesta presented to the
user looks like this:

---

**Propuesta 1 de 3**

```
Finding:   name en frontmatter no coincide con el nombre del directorio
Archivo:   /home/jorge/.claude/skills/gaia-ops/SKILL.md
Fix:       Cambiar `name: gaia_ops` a `name: gaia-ops` en el frontmatter
Efecto:    El self-check ya no reportará este mismatch; cross-references
           que usen "gaia-ops" resolverán correctamente
Rollback:  Revertir `name` a `gaia_ops` en el frontmatter
```

Aprobar este fix? [s/n]

---

That message block is the minimum. The agent may add context (e.g., "this
field is used by the orchestrator to route skill injection") but must not
omit any of the 5 fields.

### Mecanismo de aprobación

**Preferred:** `AskUserQuestion` per finding. The agent pauses after each
propuesta and waits for the user's answer before moving to the next.

**Fallback (when per-item mechanism is unavailable):** Present all
propuestas as a numbered list in a single message, then ask the user to
reply with the numbers they approve (e.g., "Apruebo: 1, 3"). Items not
listed are treated as rechazado.

Never apply any fix before receiving the user's answer. The skill must
wait -- it cannot infer "likely approved" from silence or from the fact
that the fix looks trivial.

### Estado post-flow

After all propuestas have been answered:

| Resultado | Acción | Registro |
|-----------|--------|----------|
| `aprobado` | Aplicar el fix (Edit/Write) | Log: "Fix aplicado: <finding>" |
| `rechazado` | Nada se toca | Log: "Ignored by user: <finding>" |

The final report summary line must reflect both counts:

```
Fixes aplicados: N aprobados, M ignorados por el usuario.
```

If a fix fails after aprobación (e.g., the file changed between scan and
apply), report the failure explicitly and stop. Do not silently skip.

### Edge cases: requires_human_review

Some findings are ambiguous -- the skill cannot determine the correct fix
without context only the user has. In these cases the skill must not
propose a fix at all. Instead, mark the finding as `requires_human_review`
in the report and describe what is unclear.

Situations that trigger `requires_human_review`:

| Situation | Why it is ambiguous |
|-----------|---------------------|
| Orphan skill directory (has `SKILL.md`, not in README, no agent references it) | Could be deliberate (WIP skill not yet published) or a forgotten leftover |
| Agent `name` vs file stem mismatch where both the name and the stem look intentional | Renaming the file or the field both produce valid results -- only the user knows the intent |
| Cross-reference to a skill that existed and was deleted (deletion was recent per git blame) | Could be a stale ref or could be that the user intends to restore the skill |
| Routing entry for an agent with no skills list | Might be a new agent mid-construction or a misconfiguration |

When marking `requires_human_review`, the report row looks like:

| Componente | Tipo | Inconsistencia | Fix propuesto |
|------------|------|----------------|---------------|
| `skills/draft-skill/` | Skill | Directorio presente en disco, ausente del README -- propósito incierto | requires_human_review: ¿es una skill en construcción o puede eliminarse? |

The agent should describe the ambiguity in plain language so the user can
make an informed decision. After the user clarifies, the agent may
construct and present a normal propuesta for the now-unambiguous fix.

### Cross-reference

The approval mechanism used here is semantically equivalent to the one
in `skills/request-approval/SKILL.md` (operation / exact_content /
scope / risk / rollback fields). The difference is context: `request-
approval` handles hook-blocked Bash commands; this flow handles
documentation and frontmatter fixes. The same informed-consent principle
applies to both.

## Notes

- Tolerance: a malformed frontmatter is itself an inconsistency, not a
  fatal error. The scan continues and reports the component as broken.
- No external state: the skill never reads outside `.claude/`. Any
  reference to an external path is reported as an inconsistency, not
  followed.
