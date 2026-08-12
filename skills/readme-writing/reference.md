# README Writing Reference

Filled examples and blank skeletons for the readme-writing skill.

**This file branches the same way the skill does.** Step 1 of [`SKILL.md`](SKILL.md) names one of three gates, and that gate decides which part of this file is yours. Read your gate's part; do not borrow sections from another one. A section written well but belonging to a different gate is still the wrong artifact.

| Gate | Its filled example | Its blank skeleton |
|------|--------------------|--------------------|
| **Repository root** | "Repository root -- filled example" | "Repository root -- skeleton" |
| **Component folder** | "Component folder -- filled example: `skills/`" | "Component folder -- skeleton" |
| **Shipped template** | "Shipped template -- filled example" | "Shipped template -- skeleton" |

## Fill every section of your gate, and only of your gate

**Within a gate, every section is mandatory.** An omitted section signals the author never analyzed that dimension, and a reader cannot tell a section that was skipped from one that was judged unnecessary. So do not delete a section of your own gate because it looks thin -- write the sentence that says why it is thin.

**Across gates, the opposite holds.** A repository root gets no "When it activates", and a shipped template gets no "Conventions", because those sections answer a question that gate's reader never asks. Carrying a section over is not rigor; it is filling in the wrong template carefully. When a section from another gate feels unfillable, that is the correct signal -- it was never yours to fill.

The cost of getting this backwards is not a rough draft. It is a finished, well-written README that answers the wrong reader's question, which is far harder to notice than an incomplete one.

## Every flow in this file is plain text

A flow is numbered steps and simple `->` arrows inside a plain code block. Never mermaid, never any format that must be rendered before it can be read.

The reason is verification. A rendered diagram can only be checked by looking at it rendered, which needs a tool that may not be installed -- one shipped recently that nobody could validate for exactly that reason, the install being blocked. A plain-text flow has no such gap: what is in the file is what the reader sees, identically on the web, in a terminal, and in a diff. This is the same standard as the phantom-reference rule -- a claim you cannot verify with what you have at hand does not go in.

---

## Repository root -- filled example

Abridged from a real infrastructure-factory repo. It is here for **shape** -- section order, how a flow reads as plain text, how a tree annotates -- not to be copied literally. The original runs about 190 lines, down from 665, and nearly all of that reduction came from moving content out to the documents that own it, per Step 4 of the skill.

````markdown
# acme-factory

A GCP environment factory: one versioned Terraform catalog, one generated infrastructure repo per client account.

## What it is and why it exists

Standing up a client's cloud environment by hand means someone recreates the
same network, the same identities, and the same CI wiring for every client --
slightly differently each time. Forking a reference repo per client ends the
same way: the copies agree on the day they are made and drift apart from the
first fix applied to only one of them.

This repository removes both by making the environment a *product* of a
reviewed catalog rather than a copy of a previous environment. It is a
factory, and it holds no client instance of itself: its parts are the
Terraform modules under [`modules/`](modules/), versioned by git tag, and its
assembly line is the installer under [`installer/`](installer/), which renders
one self-contained repository per client account.

Two audiences touch it. A **platform engineer** authors or reviews the modules
and the installer templates. An **operator onboarding a client** runs the
installer and then one manual bootstrap. After that, CI in the generated repo
is the only thing that reaches infrastructure.

## Flow

```
1. Platform engineer opens a pull request
     -> the field-limit gate runs against modules/
2. A version tag is pushed
     -> module tarballs publish to the registry bucket
3. Onboarding operator runs the installer
     -> a self-contained client repo is rendered
4. Operator runs one manual bootstrap against the client GCP account
     -> deploy identities and the state bucket now exist
5. The client repo resolves modules from the registry by pinned tag
6. Its CI plans on every pull request and applies on merge
```

## Requirements

The engine of record is Terraform, pinned `1.11.4`, driven by Terragrunt
`0.72.6`. The installer is Python 3 standard library only -- there is nothing
to `pip install`. Onboarding additionally needs `gcloud`, `gh`, and `git`.

Module resolution authenticates through Application Default Credentials, so
`gcloud auth application-default login` must be current. `gcloud config get
account` reads a *different* store and proves nothing about what an apply will
fetch with.

| Grant | On | Why |
|---|---|---|
| `roles/billing.user` | the billing account | link billing to the seed project |
| `roles/owner` | the seed project | the bootstrap provisions a WIF pool; Editor cannot |
| `roles/storage.objectViewer` | the registry bucket | every module is fetched from it |

Read on the registry is not granted by default and is the first prerequisite
of the whole flow. Without it, `terragrunt init` fails with a 403 on a storage
object, which reads like a bad URL rather than an IAM gap.

## How it is used

```bash
python3 installer/scaffold.py --list-fields
python3 installer/scaffold.py --out-dir ./acme-iac --client=acme --env=staging
```

The render makes no network calls and creates nothing remote, so running it
before the registry grant is in place costs nothing. It reports what it
produced and the layout it chose:

```
Rendered 47 files into ./acme-iac (account=acme, environment=staging)
```

## Structure

```
acme-factory/
├── modules/       # the factory's parts: 18 Terraform modules, versioned by git tag
├── installer/     # renders a client repo from those modules
├── operations/    # the factory's OWN infrastructure, not a client instance
├── scripts/       # repo-level tooling run by CI and by hand
└── CONTRIBUTING.md
```

- [`installer/README.md`](installer/README.md) -- mechanism rationale and the
  complete per-field input catalogue with defaults.
- [`operations/README.md`](operations/README.md) -- why the factory's own
  infrastructure is categorically distinct from a client instance.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) -- why `main` is squash-only, and how
  modules are tagged for consumers.

## Ownership

Internal and proprietary. Not licensed for distribution outside the
organization.
````

Two things in there are worth copying deliberately. **Requirements names the failure each missing grant produces**, not just the grant -- a 403 that "reads like a bad URL" is the sentence that saves the reader an hour. And **the Structure section links out rather than explaining down**: each bullet says what that document owns, so the root never becomes a second copy of it.

## Repository root -- skeleton

````markdown
# <name>

<One line, under 120 characters: what this is.>

## What it is and why it exists

<The problem it solves. Who uses it. What it produces. Why the obvious
alternative was rejected.>

## Flow

```
1. <actor or trigger>
     -> <what it produces>
2. <next step>
     -> <what it produces>
```

<Plain text only: numbered steps and `->` arrows. No mermaid, no rendered
format. What it interacts with outside this repo goes here.>

## Requirements

<Tools with pinned versions. Permissions and credentials -- and for each one,
the failure a reader gets when it is missing.>

## How it is used

```bash
<the real invocation>
```

<The output it is expected to produce.>

## Structure

```
<repo>/
├── <dir>/   # <why this exists>
└── <dir>/   # <why this exists>
```

- [`<path>`](<path>) -- <what that document owns that this README does not>

## License or ownership
````

---

## Component folder -- filled example: `skills/`

An abridged illustration modeled on Gaia's `skills/` folder. Use it as the standard for voice, section depth, and activation detail **for this gate**. It is not a copy of [`skills/README.md`](../README.md) and does not track it -- that file is the live one and is longer; this is shortened to show the shape.

````markdown
# Skills

Las skills son conocimiento procedimental inyectado en los agentes. No son código que se ejecuta -- son texto que el agente recibe y sigue. Piénsalas como el manual de procedimientos que le das a un contractor: le dices cómo clasificar riesgos, cómo reportar resultados, cómo ejecutar comandos. El agente trae su identidad (qué es, qué puede hacer); la skill trae el proceso (cómo lo hace).

Esta carpeta existe separada de `agents/` porque el mismo procedimiento aplica a múltiples agentes. `security-tiers` la siguen seis agentes distintos. Si esa lógica viviera inline en cada `.md`, tendríamos seis copias divergiendo. Una skill es la SSOT del proceso.

Mental model: una skill es como un módulo importable, pero para texto. El agente la "importa" en el dispatch (si está en su frontmatter) o la "requiere" en runtime (si la lee bajo demanda).

Las skills las toca el developer cuando crea o refina procedimientos, y el agente en runtime cuando las lee on-demand. El hook layer nunca las lee ni las inyecta.

---

## Cuándo se activa

Hay dos rutas de activación.

**RUTA 1 -- Preload por frontmatter (solo subagentes)**

```
1. El .md del agente declara `skills: [agent-protocol, security-tiers]`
     -> el HOST (Claude Code) lee ese frontmatter al despachar el subagente
2. El host precarga cada SKILL.md en el contexto del subagente
     -> el agente tiene el proceso antes de su primera tool call
3. En SubagentStop, hooks/modules/agents/skill_injection_verifier.py
   busca en el transcript los SKILL_FINGERPRINTS de cada skill esperada
     -> una skill declarada que nunca apareció se reporta como anomalía advisory
```

Ningún hook de Gaia lee el frontmatter ni inyecta skills: `pre_tool_use.py` valida el dispatch y no carga nada. El paso 3 solo VERIFICA después del hecho.

El agente primario no tiene equivalente -- `gaia-orchestrator.md` no lleva campo `skills:`, porque en el hilo principal el host solo hereda system prompt, restricciones de tools y modelo.

Lo que se lista en frontmatter se carga en cada dispatch. Mantené la lista corta: todo lo que esté acá cuesta tokens siempre.

**RUTA 2 -- On-demand**

```
1. El agente encuentra una tarea cuyo proceso no trae
     -> lee skills/<nombre>/SKILL.md, o invoca Skill(<nombre>)
2. Sigue el proceso inline
```

Las skills de workflow (execution, investigation) se leen on-demand porque solo hacen falta para ciertos tipos de tarea. Listarlas en frontmatter gastaría tokens en cada invocación.

**Qué se rompe si falta o se corrompe `skills/`:**
- Preload por frontmatter: el agente sigue sin el proceso, en silencio. Sin error. Comportamiento incorrecto.
- On-demand: el agente recibe file-not-found y tiene que improvisar o frenar. Improvisar produce resultados inconsistentes entre agentes.

---

## Qué hay aquí

```
skills/
├── README.md                  <- este archivo
├── gaia-patterns/
│   └── reference.md           <- índice de componentes (incluye skills) con tipo y descripción
├── agent-protocol/
│   ├── SKILL.md               <- protocol: response contract, state machine
│   └── examples.md            <- filled agent_contract_handoff examples
├── security-tiers/
│   ├── SKILL.md               <- reference: T0-T3 tier definitions
│   └── reference.md           <- cloud CLI examples, conditional commands
├── skill-creation/
│   ├── SKILL.md               <- technique: how to build a skill
│   └── reference.md           <- tone guide by skill type
└── ... (una carpeta por skill)
```

---

## Convenciones

- El nombre de la carpeta = campo `name:` del frontmatter de `SKILL.md`, en kebab-case
- Toda carpeta de skill contiene como mínimo un `SKILL.md`
- El frontmatter de `SKILL.md` debe traer `name:` y `description:`
- `description:` contiene condiciones de disparo únicamente -- nunca un resumen del proceso
- `SKILL.md` respeta su presupuesto: bajo 100 líneas si se carga siempre, bajo 500 si se carga on-demand; el contenido pesado va a `reference.md`
- Al crear una skill nueva, actualizá la sección "Qué hay aquí" de este README
- Al crear una skill nueva, actualizá el inventario de componentes en `skills/gaia-patterns/reference.md`

Validación: `tests/layer1_prompt_regression/test_skills_cross_reference.py` verifica en `TestSkillDirectoryStructure` que toda carpeta de skill tenga `SKILL.md` (`test_every_skill_dir_has_skill_md`), que su frontmatter sea válido, y que `name:` coincida con el nombre de la carpeta. `gaia doctor` corre las mismas dos verificaciones estructurales: `check_component_naming` (chequeo 52) y `check_skill_cross_refs` (chequeo 53).

Nada verifica que este README exista ni que esté al día. Mantenerlo es responsabilidad del reporte de drift.

---

## Ver también

- `agents/` -- definiciones de agentes que consumen skills vía frontmatter
- `hooks/modules/agents/skill_injection_verifier.py` -- chequea en SubagentStop que las skills esperadas hayan aparecido en el transcript; no inyecta nada
- `skills/skill-creation/SKILL.md` -- cómo construir una skill (tipo, presupuesto de líneas, reglas del description)
- `tests/layer1_prompt_regression/test_skills_cross_reference.py` -- verifica la estructura de las carpetas de skill y los cross-refs con los agentes
````

## Component folder -- skeleton

````markdown
# <Folder Name>

<Párrafo 1: en una frase, qué vive acá.>

<Párrafo 2: por qué esta carpeta existe separada -- su contrato conceptual.>

<Párrafo 3: cómo pensarla -- modelo mental o analogía.>

<Párrafo 4: quién la toca: developer / agente en runtime / CI / admin.>

---

## Cuándo se activa

<El trigger concreto: qué evento, condición o code path la dispara.>

```
1. <paso>
     -> <lo que produce>
2. <paso>
     -> <lo que produce>
```

<Texto plano: pasos numerados y flechas `->`. Nada que haya que renderizar.>

<Qué se rompe si esta carpeta falta o está corrupta.>

---

## Qué hay aquí

```
<folder>/
├── <file>      <- <comentario de una línea>
└── <subdir>/   <- <comentario de una línea; marcá lo generado>
```

---

## Convenciones

- <Regla de nombre para archivos nuevos>
- <Estructura interna obligatoria>
- <Qué actualizar en otro lado al agregar algo acá>
- <Qué validación corre contra esta carpeta -- nombrando archivo y símbolo>

---

## Ver también

- `<path>` -- <razón en una línea>
````

## Component folder -- section depth guide

How much the activation section of *this gate* typically needs, by folder type:

| Folder | Activation complexity | Flow block? |
|--------|----------------------|-------------|
| `hooks/` | High -- event-driven, multi-module | Yes |
| `agents/` | Medium -- routing dispatch | Optional |
| `skills/` | Medium -- two activation routes | Yes |
| `commands/` | Low -- user-invoked slash commands | No |
| `config/` | Low -- read at startup or on-demand | No |
| `bin/` | Low -- CLI tools, user-invoked | No |
| `tests/` | Low -- run by CI or developer | No |
| `build/` | Medium -- triggered by the pack step | Optional |

---

## Shipped template -- filled example

The README that travels inside generated output, into a repo its author will never see again. Abridged from a real installer template -- `@@TOKEN@@` marks a value the generator substitutes at render time. The original is 53 lines, and that is the point: its reader did not choose to be here and has no context to spend.

````markdown
# @@NAMING_PREFIX@@ — @@ENVIRONMENT@@ infrastructure

This repository was generated by the environment factory and is
**self-contained**: it carries its own `root.hcl` and `account.hcl`, so
Terragrunt resolves everything inside this repo -- nothing here clones or
refers to the factory. Its only external dependency is the Terraform modules,
consumed as immutable artifacts from `gs://@@REGISTRY_BUCKET@@` at release
`@@MODULE_REF@@`.

| | |
|---|---|
| Account | `@@ACCOUNT_ID@@` |
| Environment | `@@ENVIRONMENT@@` — project `@@PROJECT_ID@@` |
| Region | `@@REGION@@` |
| GitHub repo | `@@GITHUB_ORG@@/@@GITHUB_REPO@@` |

Every value above was fixed when this repo was rendered; there is nothing to
fill in before you start.

## What runs by itself

Once onboarding is done, no human applies infrastructure again. Every change
goes through a pull request:

```
1. Push a feature branch and open a PR against main
     -> CI runs a read-only plan, and a reviewer reads it
2. Squash-merge the PR
     -> CI applies, gated by a protected deployment environment
```

Both workflows discover work by scanning for unit **folders**, so adding or
removing a component never needs a workflow edit.

## What you run once

Six steps, by hand, in this order -- each one creates what the next one reads:

1. Get read access on the module registry (only the factory operator can grant it).
2. Create the GitHub repo and push this tree.
3. Confirm both of those before applying anything.
4. Bootstrap the account -- `cd bootstrap && ./bootstrap.sh`, the one manual apply.
5. Re-send the CI half of the registry grant, now that the deploy service accounts exist.
6. Open the first pull request; a green plan on it is the finish line.

You must already hold `roles/owner` on the seed project `@@SEED_PROJECT_ID@@`
and read on `gs://@@REGISTRY_BUCKET@@` before step 4. Nothing in this repo can
provision those grants, so ask for them early.

## Where to go for the rest

**Start here → [docs/onboarding.md](docs/onboarding.md)** -- the six steps in
full: the grants you need and why, what each one creates, how to verify it.

Day-2 work -- editing this repo, turning a component off, bumping the module
release, the failure table and the file-by-file layout -- is in
**[docs/reference.md](docs/reference.md)**.
````

What makes this gate work is the fact table plus the sentence under it: the reader's first question is "what is mine now", and answering it in a glance is what buys the attention for the six steps. Note also that step 1 hands off to a human the reader has to go find -- a prerequisite this repo cannot satisfy is stated as such, not left to fail at step 4.

## Shipped template -- skeleton

Keep all four sections short. Length is what makes this one unread.

````markdown
# <@@NAME@@> — <what it is>

<What this is, and that it is now theirs. State whether it is self-contained
and what it still depends on.>

| | |
|---|---|
| <fact> | `<@@TOKEN@@>` |

<Whether anything needs filling in before they start.>

## What runs by itself

<The automation they inherit, and what triggers it.>

```
1. <what they do>
     -> <what happens on its own>
```

## What you run once

<Numbered manual steps, in order.>

<Any prerequisite this repo cannot provision itself, stated as such.>

## Where to go for the rest

**Start here → [<path>](<path>)** -- <what it covers>

<Where day-2 work is documented.>
````
