# README Writing Reference

Extended examples for the readme-writing skill. Load this file when you need a filled-in template to work from.

## Canonical filled example: skills/

The example below is the README for the `skills/` top-level folder. Use it as the gold standard for voice, section depth, and activation detail.

---

```markdown
# Skills

Las skills son conocimiento procedimental inyectado en los agentes. No son código que se ejecuta -- son texto que el agente recibe y sigue. Piénsalas como el manual de procedimientos que le das a un contractor: le dices cómo clasificar riesgos, cómo reportar resultados, cómo ejecutar comandos. El agente trae su identidad (qué es, qué puede hacer); la skill trae el proceso (cómo lo hace).

Esta carpeta existe separada de `agents/` porque el mismo procedimiento aplica a múltiples agentes. `security-tiers` la siguen seis agentes distintos. Si esa lógica viviera inline en cada `.md`, tendríamos seis copias divergiendo. Una skill es la SSOT del proceso.

Mental model: una skill es como un módulo importable, pero para texto. El agente la "importa" en startup (si está en su frontmatter) o la "requiere" en runtime (si la lee bajo demanda con el Read tool).

Las skills las toca el developer cuando crea o refina procedimientos, y el agente en runtime cuando las lee on-demand. El hook layer nunca las lee directamente.

---

## Cuándo se activa

Hay dos rutas de activación:

**RUTA 1 -- Startup injection (frontmatter)**

```
Agent .md frontmatter
  skills:
    - agent-protocol
    - security-tiers
          |
          v
pre_tool_use.py fires on agent start
          |
          v
adapters/claude_code.py -> modules/agents/skill_injection.py
          |
          v
Reads each SKILL.md from disk
Prepends content to agent system prompt
          |
          v
Agent receives process knowledge before first tool call
```

Skills listed in frontmatter load unconditionally on every call. Keep this list short (< 5 skills, < 100 lines each) -- everything here costs tokens on every invocation.

**RUTA 2 -- On-demand (workflow skills)**

```
Agent encounters task that needs a process
          |
          v
Agent reads SKILL.md directly via Read tool
          |
          v
Agent follows the process inline
```

Workflow skills (approval, execution, investigation) are read on-demand because they are only needed for specific task types. Listing them in frontmatter would waste tokens on every agent call.

**What breaks if skills/ is missing or corrupted:**
- Startup-injected skills: agent proceeds without process knowledge, silently. No error. Wrong behavior.
- On-demand skills: agent gets a file-not-found error and must improvise or halt. Improvising produces inconsistent results across agents.

---

## Qué hay aquí

```
skills/
├── README.md                  <- este archivo
├── reference.md               <- índice de skills con tipo y descripción
├── agent-protocol/
│   ├── SKILL.md               <- protocol: response contract, state machine
│   └── examples.md            <- filled json:contract examples
├── security-tiers/
│   ├── SKILL.md               <- reference: T0-T3 tier definitions
│   └── reference.md           <- cloud CLI examples, conditional commands
├── skill-creation/
│   ├── SKILL.md               <- technique: how to build a skill
│   └── reference.md           <- tone guide by skill type
├── command-execution/
│   ├── SKILL.md               <- discipline: no pipes, one command per step
│   └── reference.md           <- cloud CLI mutation examples
└── ... (one folder per skill)
```

---

## Convenciones

- Folder name = `name:` field in SKILL.md frontmatter, kebab-case
- Every skill folder contains at minimum `SKILL.md`
- `SKILL.md` must have valid frontmatter: `name:`, `description:`, `metadata.type:`
- `description:` contains triggering conditions only -- not process summary
- `SKILL.md` < 150 lines; heavy content goes to `reference.md`
- After creating a new skill, update this README's "Qué hay aquí" section
- After creating a new skill, update `skills/reference.md` index table

Validation: `tests/system/test_directory_structure.py` verifies all skill folders have a `SKILL.md`.

---

## Ver también

- `agents/` -- agent definitions that consume skills via frontmatter
- `hooks/modules/agents/skill_injection.py` -- runtime that reads and injects skill content
- `skills/skill-creation/SKILL.md` -- how to build a new skill (type selection, line budget, description rules)
- `tests/system/test_directory_structure.py` -- verifies README and SKILL.md existence
```

---

## Template (blank)

Copy this when writing a README from scratch. Fill every section -- do not delete sections that seem inapplicable, as the absence of a section signals the folder was not fully analyzed.

```markdown
# <Folder Name>

<Intro paragraph 1: one sentence on what lives here>

<Intro paragraph 2: why this folder exists separately -- the conceptual contract>

<Intro paragraph 3: how to think about this folder -- mental model or analogy>

<Intro paragraph 4: who touches it: developer / agent at runtime / CI / admin>

---

## Cuándo se activa

<Concrete trigger: what event, condition, or code path fires this>

```
<ASCII diagram if > 2 steps chain>
```

<Step-by-step list as complement>

<What breaks if this folder is absent or broken>

---

## Qué hay aquí

```
<folder>/
├── <file>      <- <one-line comment>
└── <subdir>/   <- <one-line comment>
```

---

## Convenciones

- <Naming rule for new files>
- <Required internal structure>
- <What to update elsewhere when adding something here>
- <Validation that runs against this folder>

---

## Ver también

- `<path>` -- <one-line reason>
```

---

## Section depth guide by folder type

| Folder | Activation complexity | Typical diagram? |
|--------|----------------------|-----------------|
| `hooks/` | High -- event-driven, multi-module | Yes |
| `agents/` | Medium -- routing dispatch | Optional |
| `skills/` | Medium -- two injection paths | Yes |
| `commands/` | Low -- user-invoked slash commands | No |
| `config/` | Low -- read at startup or on-demand | No |
| `bin/` | Low -- CLI tools, user-invoked | No |
| `tests/` | Low -- run by CI or developer | No |
| `build/` | Medium -- triggered by npm run build | Optional |
| `templates/` | Low -- read by build scripts | No |
