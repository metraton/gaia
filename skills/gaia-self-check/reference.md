# Gaia Self-Check -- Reference

Operational detail for the three phases of the self-check cycle. The main
SKILL.md defines the cycle and the ask-before-fix principle; this file
holds the per-phase mechanics, per-category check rules, output format,
and propuesta + target-selection flow.

## Scope

The skill operates on `.claude/` always, and on the user-declared dev repo
only when `target_mode == installed+repo` (see Fase 1 below). The
inventory walk under `.claude/` covers:

| Directory | Component | Always scanned |
|-----------|-----------|----------------|
| `.claude/skills/` | Skills | Yes |
| `.claude/agents/` | Agents | Yes |
| `.claude/commands/` | Slash commands | Yes |
| `.claude/hooks/` | Hooks | Yes |

No path outside `.claude/` or the declared dev repo is read, regardless
of what a component's frontmatter references.

## Output Format

The report is terminal-friendly markdown: one section per category, each
with a table. Empty categories are reported as "OK" so the user can see
the scan covered them.

Columns:

| Column | Meaning |
|--------|---------|
| Componente | File or directory name |
| Tipo | Skill / Agent / Command / Hook / Pillar / Config |
| Inconsistencia | One-line description of what is wrong |
| Fix propuesto | One-line description of the proposed change |
| Target | `repo` / `.claude/` / `n/a` -- where the fix would land, pending Fase 3 user choice |

The `Target` column is filled with a suggested default after Fase 1
resolves `target_mode`: `repo` when `installed+repo`, `.claude/` when
`installed_only`. The final value is the one the user picks in Fase 3
via `AskUserQuestion`. `n/a` is used for findings that are pure reports
(no fix possible without external input, e.g. `requires_human_review`).

Each category section below contains a concrete example table. An empty
category (no findings) is reported as a single "OK" row so the user can
confirm the scan covered it.

At the end of the report, a summary line: `N inconsistencias encontradas
en M componentes. Propuesta pendiente de aprobación.`

## Fase 1 -- Repo detection heuristic

Fase 1 produces a single value: `target_mode`. The procedure runs three
inputs in order of precedence and stops at the first that resolves.

### Step 1: `GAIA_DEV_REPO` env var (highest precedence)

```
if env.get('GAIA_DEV_REPO') is set:
  path = env['GAIA_DEV_REPO']
  if is_dir(path) and has_file(path / 'package.json'):
    pkg = parse_json(path / 'package.json')
    if pkg.get('name') == '@jaguilar87/gaia':
      target_mode = 'installed+repo'
      dev_repo   = path
      return
  # env var set but does not resolve -- fall through, do NOT silently ignore
  warn: "GAIA_DEV_REPO set but did not resolve to a valid Gaia repo"
```

### Step 2: Symlink resolution + sibling lookup

```
target = readlink('.claude/agents')
if 'node_modules/@jaguilar87/gaia/agents' in target:
  # consumer install -- look for sibling source repo
  candidates = [
    '../gaia/',           # cwd is workspace, sibling repo at parent
    '../../gaia/',        # cwd is workspace, repo two levels up
    '../gaia-dev/',       # sibling named gaia-dev
  ]
  for candidate in candidates:
    abs_path = resolve(cwd / candidate)
    if is_dir(abs_path) and has_file(abs_path / 'package.json'):
      pkg = parse_json(abs_path / 'package.json')
      if pkg.get('name') == '@jaguilar87/gaia':
        target_mode = 'installed+repo'
        dev_repo   = abs_path
        return

elif target is the working tree itself (live install):
  # We ARE inside the dev repo workspace
  target_mode = 'installed+repo'
  dev_repo   = resolve(target).parent  # the gaia/ folder containing agents/
  return
```

### Step 3: Explicit AskUserQuestion fallback

When neither step 1 nor step 2 resolves a dev repo, ask:

```
AskUserQuestion(
  question="No detecté un repo de desarrollo de Gaia accesible. ¿Tienes uno?",
  options=[
    {
      "label": "Sí, está en <path>",
      "description": "Indica la ruta absoluta del repo. Si la confirmas, también puedo aplicar fixes ahí en Fase 3."
    },
    {
      "label": "No, continuar en installed_only",
      "description": "Solo `.claude/` será inspeccionado. Los fixes solo podrán aterrizar en `.claude/` (con advertencia de overwrite)."
    }
  ]
)
```

If the user provides a path, validate it the same way as Step 1 (must be
a directory with `package.json` whose `name == "@jaguilar87/gaia"`). If
validation fails, return to the question with the failure reason.

### Decision table

| `GAIA_DEV_REPO` | symlink resolves to dev repo | user provides valid path | `target_mode` |
|-----------------|------------------------------|--------------------------|---------------|
| set & valid | -- | -- | `installed+repo` |
| set & invalid | yes | -- | `installed+repo` (with warning) |
| set & invalid | no | yes | `installed+repo` (with warning) |
| set & invalid | no | no | `installed_only` (with warning) |
| unset | yes | -- | `installed+repo` |
| unset | no | yes | `installed+repo` |
| unset | no | no | `installed_only` |

`installed_only` is the safe fallback. The skill is fully functional in
this mode -- only Fase 3 loses one target option.

## Categorías de checks

Each category describes: what to verify, how to detect it, and what a
positive finding (inconsistency) looks like. The agent reads the
relevant files using Read and Glob tools -- no shell commands, no
external state beyond the declared dev repo.

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

| Type | Required fields | Notes |
|------|----------------|-------|
| Skill (`SKILL.md`) | `name`, `description` | |
| Agent (`agents/*.md`) | `name`, `description`, `tools` | `tools` is the correct field; `allowed-tools` is not valid here |
| Command (`commands/*.md`) | `name`, `description` | Commands use `allowed-tools` (not `tools`) for tool restrictions -- both field names are valid depending on whether the command is a CC slash command or an agent-facing command |

**Convención `tools` vs `allowed-tools`:** Agent frontmatters declare their tool access with `tools`. Command frontmatters (slash commands) use `allowed-tools` when restricting tool access. These are two distinct conventions for two distinct component types. When validating frontmatter, apply the correct expected field per component type -- flagging `allowed-tools` in a command as "wrong field" is a false positive.

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `skills/my-skill/SKILL.md` | Skill | Frontmatter YAML inválido: mapping values not allowed here (line 3) | Corregir indentación YAML en el frontmatter | repo |
| `agents/my-agent.md` | Agent | Campo requerido `tools` ausente del frontmatter | Agregar `tools:` con la lista de herramientas del agent | repo |

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

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `skills/gaia-ops/SKILL.md` | Skill | `name: gaia_ops` en frontmatter, directorio es `gaia-ops` | Cambiar `name` a `gaia-ops` en el frontmatter | repo |
| `agents/terraform.md` | Agent | `name: terraform-architect` en frontmatter, archivo es `terraform.md` | Renombrar archivo a `terraform-architect.md` o corregir `name` | repo |

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
mentions a `skills/<name>/` path, verify that path exists under `.claude/`
or under the declared dev repo. This is best-effort -- report only paths
that look like structured references (e.g., `` `skills/foo/SKILL.md` ``),
not every mention of a name.

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `agents/gaia-system.md` | Agent | Skill `nah-patterns` referenciada en frontmatter no existe en `.claude/skills/` | Eliminar `nah-patterns` del frontmatter o crear la skill | repo |

---

### Orphan/listed consistency (routing)

**Qué verifica:** Three independent sub-checks. Each sub-check targets a
distinct source of truth that drifts independently.

#### Sub-check A: Orphan detection (skills)

A skill is an orphan only when it meets both conditions simultaneously:

1. No agent frontmatter anywhere in `.claude/agents/` lists it under `skills:`.
2. It is absent from the directory tree in `skills/README.md`.

If the skill appears in at least one agent's `skills:` list, it is a
**referenced skill** -- not an orphan. It may still be missing from the README
tree (that is doc drift, see Sub-check B), but it is not orphaned.

```
skills_on_disk   = {dir.name for dir in .claude/skills/ if (dir/SKILL.md).exists()}
agent_referenced = {skill for each agent in .claude/agents/*.md
                         for skill in yaml(agent).get('skills', [])}
skills_in_tree   = {name parsed from directory tree section of skills/README.md}

orphans          = skills_on_disk - agent_referenced - skills_in_tree
doc_drift        = (skills_on_disk & agent_referenced) - skills_in_tree
```

`orphans` -> FINDING: skill not referenced by any agent and absent from README
`doc_drift` -> FINDING (lower severity): skill is referenced by agents but missing from README tree

#### Sub-check B: README sources of truth

`skills/README.md` contains two distinct structures that drift independently:

1. **Directory tree**: the visual listing of skill directories.
2. **Skill-to-agent assignment matrix**: which skills are assigned to which agents.

Verify both explicitly:

```
# Tree check
skills_in_tree   = {name from directory tree section}
skills_on_disk   = {dir.name for dir in .claude/skills/ if (dir/SKILL.md).exists()}
missing_from_tree = skills_on_disk - skills_in_tree
stale_in_tree    = skills_in_tree - skills_on_disk

# Matrix check
skills_in_matrix = {name from each row of the assignment table}
for each skill in skills_in_matrix:
  if skill not in skills_on_disk:
    FINDING: matrix references skill that does not exist on disk
```

Report tree drift and matrix drift as separate findings -- they require
different fixes (update the tree listing vs update the assignment table).

The same two-source check applies to `agents/README.md` and
`commands/README.md`: each surface has its own README and each may contain
both a directory listing and cross-reference tables.

#### Sub-check C: READMEs for all three surfaces

The check covers all three surface READMEs explicitly:

| README | Surface | What to check |
|--------|---------|---------------|
| `skills/README.md` | Skills | Directory tree + assignment matrix |
| `agents/README.md` | Agents | Directory listing vs `.claude/agents/*.md` |
| `commands/README.md` | Commands | Directory listing vs `.claude/commands/*.md` |

If a README does not exist for a surface, report "README absent for
`<surface>/`" rather than skipping silently.

#### Sub-check D: Routing config

If `.claude/config/surface-routing.json` exists, each `primary_agent` value
must match a file stem in `.claude/agents/`. A routing entry pointing to a
non-existent agent is a broken cross-reference between config and agents.

```
routing        = parse .claude/config/surface-routing.json
agents_on_disk = {f.stem for f in .claude/agents/*.md}
for each surface in routing.surfaces:
  agent = surface.primary_agent
  if agent not in agents_on_disk:
    FINDING: routing references missing agent
```

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `skills/gaia-self-check/` | Skill | En disco y referenciada por agents, ausente del árbol en `skills/README.md` | Agregar al árbol de directorios en `skills/README.md` (doc drift, no orphan) | repo |
| `skills/draft-skill/` | Skill | En disco, sin referencias en ningún agent, ausente del README | requires_human_review: ¿skill en construcción o puede eliminarse? | n/a |
| `skills/README.md` | Doc | `nah-skill` en la matriz de asignación pero directorio ausente en disco | Eliminar `nah-skill` de la matriz o restaurar la skill | repo |
| `skills/old-skill/` | Skill | Listado en árbol del README pero directorio ausente en disco | Eliminar entrada del árbol en el README o restaurar la skill | repo |
| `agents/README.md` | Doc | README ausente para la superficie `agents/` | Crear `agents/README.md` con listado de agents | repo |
| `config/surface-routing.json` | Config | `primary_agent: ghost-agent` no existe en `.claude/agents/` | Actualizar `primary_agent` o crear `ghost-agent.md` | repo |

---

### hooks/ (siempre)

**Qué verifica:** Hooks are always part of the scan. Two directions:

1. **settings.json -> disk**: Every hook file declared in `settings.json`
   must exist on disk. A hook registered but missing on disk causes silent
   runtime failures -- the harness calls the hook and gets a file-not-found
   error.
2. **disk -> settings.json**: Every file under `.claude/hooks/` must be
   registered in `settings.json`. A hook file present on disk but not
   registered is dead code -- it runs nowhere.

**Cómo detectarlo:**

```
# Parse settings.json (may not exist)
if .claude/settings.json does not exist:
  report: "no active hooks detected -- settings.json absent"
  skip hooks check
else:
  settings = parse .claude/settings.json
  hooks_in_settings = {resolve path from each hook entry in settings.hooks}

  # Direction 1: registered -> disk
  for each path in hooks_in_settings:
    if file does not exist at path:
      FINDING: hook registered in settings.json but file missing on disk

  # Direction 2: disk -> registered
  hooks_on_disk = {f for f in .claude/hooks/*.py}
  for each file in hooks_on_disk:
    if file not in hooks_in_settings:
      FINDING: hook file on disk but not registered in settings.json

  if hooks_in_settings is empty:
    report: "no active hooks detected -- settings.json present but no hooks entries"
```

**Ejemplo de finding:**

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `settings.json` | Config | Hook `.claude/hooks/post_tool_use.py` registrado pero archivo no existe en disco | Crear el archivo del hook o eliminar la entrada de `settings.json` | repo |
| `hooks/pre_tool_use.py` | Hook | Archivo presente en disco pero no registrado en `settings.json` | Agregar entrada en `settings.json` o eliminar el archivo | repo |

---

## Fase 2 -- Pillar source-of-truth integrity

This category and the next ("Pillar coverage") are the link between the
declared architecture in `agents/gaia-system.md` and the live state of
the repo or `.claude/`. They are what makes the self-check more than a
cross-reference checker.

### Parsing the 8-pillar table

The pillar table in `agents/gaia-system.md` lives under the heading
`## The 8 pillars of Gaia` (or `## Los 8 pilares de Gaia`). Locate the
heading, then parse the next markdown table block. Expected columns:

| Pillar | What it means | Source of truth |
|--------|---------------|-----------------|

Extract each row as `{pillar_name, description, st_path}`. The `st_path`
may be a single path or a comma-separated list of paths. Normalize to a
list.

If the heading or table cannot be found, this is itself a finding:

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `agents/gaia-system.md` | Agent | Tabla de pilares ausente o malformada bajo `## The 8 pillars of Gaia` | Restaurar la tabla con columnas Pilar / Significado / Source of truth | repo |

### Per-ST checks

For each declared source of truth `st_path`, run two checks:

**Check 1: existence**

```
candidates = [
  cwd / st_path,                       # relative to workspace
  dev_repo / st_path,                  # relative to declared dev repo (if installed+repo)
  .claude / st_path,                   # relative to .claude/
]
exists = any(is_file_or_dir(c) for c in candidates)
if not exists:
  FINDING: pillar ST does not exist on disk
```

**Check 2: referenced by active code/config**

```
# Heuristic grep over active layers
search_roots = [
  dev_repo / 'bin/',
  dev_repo / 'hooks/',
  dev_repo / 'config/',
  dev_repo / 'agents/',
  dev_repo / 'skills/',
  dev_repo / 'tools/',
  dev_repo / 'scripts/',
]
# When installed_only, search under .claude/<same subdirs> instead

needle_variants = [
  st_path,                              # full path
  basename(st_path),                    # filename
  stem(st_path),                        # filename without extension
]
matches = 0
for root in search_roots:
  for needle in needle_variants:
    matches += Grep(needle, path=root)

if matches == 0:
  FINDING: pillar ST exists but is not referenced by active layers (unreferenced)

# Additional: if file is empty or contains only comments
if exists and file_is_effectively_empty(st_path):
  FINDING: pillar ST exists but is empty
```

`file_is_effectively_empty` means: zero bytes, or content consists only
of whitespace, comments, or a single placeholder heading.

### Failure modes

| Mode | Detection | Meaning |
|------|-----------|---------|
| `missing` | existence check fails | Pillar declared but ST file/dir is not there |
| `empty` | existence ok, file_is_effectively_empty | Pillar declared, file exists, but has no real content |
| `unreferenced` | existence ok, content present, zero matches | Pillar declared, file exists, but no active layer uses it -- dead code |

### Output table

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| Pilar `Routing` | Pillar | ST declarado `config/surface-routing.json` -- missing | Crear el archivo o reescribir el pilar para apuntar al ST real | repo |
| Pilar `Memory` | Pillar | ST declarado `gaia/store/schema.sql` -- empty (0 bytes) | Poblar el schema o eliminar el pilar de `gaia-system.md` | repo |
| Pilar `Approvals` | Pillar | ST declarado `hooks/modules/security/approvals.py` -- unreferenced (0 matches en bin/, hooks/, config/) | El pilar es ficción: eliminar o reconectar el ST a código vivo | repo |

---

## Fase 2 -- Pillar coverage

Reciprocal check: every active top-level component must fall under at
least one declared pillar, and every declared pillar's ST must
correspond to at least one active component.

### Enumeration of active components

```
components = []

# CLI plugins
for f in glob('bin/cli/*.py'):
  components.append({'kind': 'cli', 'path': f})

# Skills
for d in glob('skills/*/'):
  if exists(d / 'SKILL.md'):
    components.append({'kind': 'skill', 'path': d})

# Agents
for f in glob('agents/*.md'):
  components.append({'kind': 'agent', 'path': f})

# Hook modules
for f in glob('hooks/modules/*/*.py'):
  components.append({'kind': 'hook_module', 'path': f})

# Release scripts
for f in glob('scripts/*.py'):
  components.append({'kind': 'script', 'path': f})

# Schema tables
if exists('gaia/store/schema.sql'):
  for table in parse_create_table_statements('gaia/store/schema.sql'):
    components.append({'kind': 'schema_table', 'path': f'gaia/store/schema.sql#{table}'})
```

In `installed_only` mode, swap the roots: enumerate under `.claude/`
instead of the dev repo. Some component kinds (CLI plugins, scripts,
schema tables) may not be present in `.claude/` -- skip kinds that have
no enumeration root.

### Mapping algorithm

For each component, attempt to match it against the ST paths of declared
pillars:

```
for each component in components:
  matched_pillars = []
  for each pillar in declared_pillars:
    for each st_path in pillar.st_paths:
      if component.path starts with st_path:
        matched_pillars.append(pillar.name)
      elif st_path is a directory and component.path is under that directory:
        matched_pillars.append(pillar.name)
      elif basename(component.path) == basename(st_path):
        matched_pillars.append(pillar.name)
  component.pillars = matched_pillars

orphans = [c for c in components if len(c.pillars) == 0]
```

### Obsolete pillars (inverse)

```
obsolete = []
for each pillar in declared_pillars:
  has_component = any(pillar.name in c.pillars for c in components)
  if not has_component:
    obsolete.append(pillar)
```

A pillar with no matching active component is obsolete: either the
pillar should be removed from `gaia-system.md`, or its ST path is wrong
and points away from the real implementation.

### Output: two tables

**Tabla de huérfanos** (components covered by no pillar):

| Componente | Tipo | Path | Pilar sugerido | Target |
|------------|------|------|----------------|--------|
| `bin/cli/gaia_status.py` | CLI plugin | `bin/cli/` | "Observability" o crear nuevo pilar | repo |
| `hooks/modules/memory/atoms.py` | Hook module | `hooks/modules/memory/` | "Memory" (pilar declarado pero ST no incluye este path) | repo |
| `skills/gmail-triage/` | Skill | `skills/` | "Skills" o pilar específico de Email | repo |

The "Pilar sugerido" column is a heuristic based on the component kind
and path semantics. It is a suggestion, not a binding decision -- the
user picks the final pillar in Fase 3.

**Tabla de obsoletos** (pillars with no matching active component):

| Pilar | ST declarado | Razón | Fix propuesto | Target |
|-------|--------------|-------|---------------|--------|
| `Federation` | `config/federation.yaml` | Existe en disco pero no es invocado por ninguna capa activa | Eliminar el pilar o reconectar el ST | repo |
| `Audit` | `gaia/store/audit.sql` | ST missing en disco | Crear el ST o eliminar el pilar | repo |

---

## Fase 3 -- Propuesta y selección de target

The ask-before-fix principle governs every corrective action the skill
might take. The skill is allowed to detect, describe, and propose --
never to apply. Aprobación explícita del usuario is the only gate that
unlocks a fix, and that aprobación now covers two questions:

1. *Qué arreglar?* -- approve or reject each finding.
2. *Dónde aterrizarlo?* -- choose the target file system for each
   approved finding.

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
  aprobado?  ----no-----> Sin cambios + registrar como "ignored by user"
     |
     yes
     |
     v
Preguntar target via AskUserQuestion (target selection)
        |
        v
  target elegido  ----no aplicar----> Registrar finding como aprobado pero no aplicado
     |
     repo / .claude/
     |
     v
Aplicar fix en el target elegido + registrar
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
Archivo:   skills/gaia-ops/SKILL.md
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

### Target selection -- AskUserQuestion schema

After the user approves a finding, ask where to land the fix. The
question shape depends on `target_mode`.

**Caso `installed+repo`** (3 opciones):

```
AskUserQuestion(
  question="¿Dónde aterrizo este fix?",
  options=[
    {
      "label": "Aplicar en repo dev (<dev_repo_path>)",
      "description": "El cambio va al working tree del repo. Tú luego haces commit y abres PR. Este es el target recomendado -- es la fuente de verdad."
    },
    {
      "label": "Aplicar en `.claude/` directamente",
      "description": "ADVERTENCIA: un `gaia install` o `gaia update` futuro sobrescribirá este cambio. La fuente de verdad sigue siendo el paquete npm. Útil solo para fixes locales temporales."
    },
    {
      "label": "No aplicar",
      "description": "Solo reportar; tú decides después qué hacer."
    }
  ]
)
```

**Caso `installed_only`** (2 opciones):

```
AskUserQuestion(
  question="¿Dónde aterrizo este fix?",
  options=[
    {
      "label": "Aplicar en `.claude/` directamente",
      "description": "ADVERTENCIA: un `gaia install` o `gaia update` futuro sobrescribirá este cambio. La fuente de verdad sigue siendo el paquete npm. Útil solo para fixes locales temporales."
    },
    {
      "label": "No aplicar",
      "description": "Solo reportar; tú decides después qué hacer."
    }
  ]
)
```

### Verbatim del overwrite warning

The warning that must accompany the `.claude/` option, exactly:

```
ADVERTENCIA: un `gaia install` o `gaia update` futuro sobrescribirá
este cambio. La fuente de verdad sigue siendo el paquete npm. Útil
solo para fixes locales temporales.
```

Omitting or paraphrasing this warning breaks informed consent. If the
agent presents the `.claude/` option without this text (or its
substantive equivalent), the consentimiento is invalid and the fix
must not be applied.

### Dispatch contract for the application phase

The mechanics of *applying* a fix depend on the chosen target. The
orchestrator that consumes this skill's output must dispatch the
application subagent with the right `mode`, or the writes will fail
silently.

| Target | Required dispatch mode | Why |
|--------|------------------------|-----|
| `repo dev` | `mode: acceptEdits` | Repo working tree is not protected by CC native `.claude/` guard. `acceptEdits` covers Edit/Write without per-file prompts. |
| `.claude/` | `mode: acceptEdits` or `mode: bypassPermissions`, OR run in foreground | CC native blocks writes under `.claude/**` from background dispatches with `mode: default`. There is no `approval_id` path here (this is CC native protection, not the Gaia bash_validator). Foreground works too if no other mode is set. |
| `No aplicar` | n/a | No dispatch is made. |

**Why this matters at the contract level.** A dispatch with
`mode: default` and `run_in_background: true` against `.claude/` will
emit an auto-deny with no resumable nonce. The orchestrator will see a
generic "permission denied" and may interpret it as transient -- but
the fix never lands, no matter how many times the cycle retries. The
only correct fix is to re-dispatch with `mode: acceptEdits` or to run
the application phase in foreground.

### Multi-step bundles -- cross-link

When a single fix expands into multiple file writes (e.g., updating
`gaia-system.md` AND adding the missing ST file AND wiring it into a
hook), the dispatch behavior across the bundle is governed by:

- `security-tiers/SKILL.md` -> "R3 -- `mode` does NOT survive a
  SendMessage resume". If any step in the bundle emits APPROVAL_REQUEST,
  the resume runs in `default` and the next protected operation
  re-blocks even though the original dispatch was `acceptEdits`.
- `orchestrator-present-approval/SKILL.md` -> "Re-dispatch instead of resume".
  For bundles that span an approval, the orchestrator must re-dispatch
  fresh with the same mode rather than resuming.

The self-check skill itself never applies fixes -- but the orchestrator
that consumes its output and dispatches the application subagent must
honor these rules.

### Mecanismo de aprobación (qué + dónde)

**Preferred:** `AskUserQuestion` per finding, then `AskUserQuestion` per
target. The agent pauses after each propuesta, waits for the
qué-answer, and if approved, pauses again for the dónde-answer before
moving to the next finding.

**Fallback (when per-item mechanism is unavailable):** Present all
propuestas as a numbered list in a single message, then ask the user
to reply in the form `Apruebo: 1 -> repo, 3 -> .claude/`. Items not
listed are treated as rechazado. Items listed without a target use the
suggested default from the report.

Never apply any fix before receiving both answers. The skill must
wait -- it cannot infer "likely approved" from silence or from the
fact that the fix looks trivial.

### Estado post-flow

After all propuestas have been answered:

| Resultado | Acción | Registro |
|-----------|--------|----------|
| `aprobado` + target `repo` | Aplicar el fix en `<dev_repo>/<path>` (Edit/Write) | Log: "Fix aplicado en repo: <finding>" |
| `aprobado` + target `.claude/` | Aplicar el fix en `.claude/<path>` (Edit/Write) -- requires correct dispatch mode | Log: "Fix aplicado en .claude/: <finding> (sobrevivirá hasta el próximo gaia install/update)" |
| `aprobado` + target `No aplicar` | Nada se toca | Log: "Approved but not applied: <finding>" |
| `rechazado` | Nada se toca | Log: "Ignored by user: <finding>" |

The final report summary line must reflect all counts:

```
Fixes: N aplicados en repo, M aplicados en .claude/, K aprobados sin
aplicar, J ignorados por el usuario.
```

If a fix fails after aprobación (e.g., the file changed between scan
and apply, or the dispatch mode was wrong), report the failure
explicitly and stop. Do not silently skip.

### Edge cases: requires_human_review

Some findings are ambiguous -- the skill cannot determine the correct fix
without context only the user has. In these cases the skill must not
propose a fix at all. Instead, mark the finding as `requires_human_review`
in the report and describe what is unclear.

Situations that trigger `requires_human_review`:

| Situation | Why it is ambiguous |
|-----------|---------------------|
| Orphan skill directory (has `SKILL.md`, not referenced in any agent frontmatter, absent from README) | Could be deliberate (WIP skill not yet published) or a forgotten leftover |
| Agent `name` vs file stem mismatch where both the name and the stem look intentional | Renaming the file or the field both produce valid results -- only the user knows the intent |
| Cross-reference to a skill that existed and was deleted (deletion was recent per git blame) | Could be a stale ref or could be that the user intends to restore the skill |
| Routing entry for an agent with no skills list | Might be a new agent mid-construction or a misconfiguration |
| Pillar ST that points to a path with active code AND inactive code (mixed) | Could be that the pillar is partially obsolete; user must split |

When marking `requires_human_review`, the report row looks like:

| Componente | Tipo | Inconsistencia | Fix propuesto | Target |
|------------|------|----------------|---------------|--------|
| `skills/draft-skill/` | Skill | Directorio presente en disco, ausente del README -- propósito incierto | requires_human_review: ¿es una skill en construcción o puede eliminarse? | n/a |

The agent should describe the ambiguity in plain language so the user can
make an informed decision. After the user clarifies, the agent may
construct and present a normal propuesta for the now-unambiguous fix.

### Cross-reference

The approval mechanism used here is semantically equivalent to the one
in `skills/subagent-request-approval/SKILL.md` (operation / exact_content /
scope / risk / rollback fields). The difference is context: `subagent-request-
approval` handles hook-blocked Bash commands; this flow handles
documentation, frontmatter, and pillar fixes. The same informed-consent
principle applies to both.

## Notes

- Tolerance: a malformed frontmatter is itself an inconsistency, not a
  fatal error. The scan continues and reports the component as broken.
- No external state beyond the declared repo: the skill never reads
  outside `.claude/` or the user-declared dev repo. Any reference to a
  path outside those roots is reported as an inconsistency, not
  followed.
- Mode awareness: every output table includes a `Target` column whose
  default value comes from `target_mode`. The user can override per
  finding in Fase 3.
