# Spec-Kit 2.3 - Framework de Desarrollo de Features

**[English version](README.en.md)**

Framework para desarrollo dirigido por especificaciones con orquestacion inteligente de agentes.

## Vision General

```
Idea -> Spec -> Plan -> Tasks -> Implementation
```

**Arquitectura 2.3 (agent + skills + governance auto-sync):**
- Un agente lean (`speckit-planner`) detecta la fase automaticamente segun los artefactos existentes
- 9 skills por fase reemplazan los comandos slash — el usuario interactua en lenguaje natural
- Clarificacion, validacion y enrichment integrados
- Auto-context desde project-context.json (`paths.speckit_root` configura la ubicacion)
- Governance: `governance.md` vive en `<speckit-root>/` — generado automaticamente en el primer uso, sincronizado en cada sesion
- GOVERNANCE_UPDATE: las skills detectan nuevas tecnologias y actualizan governance.md automaticamente
- State machine: deteccion de fase y siguiente paso exacto
- Drift detection: verificacion de completitud real vs. declarada

## Workflow

El agente `speckit-planner` detecta la fase actual leyendo los artefactos existentes y aplica la skill correspondiente. El usuario interactua en lenguaje natural.

### Paso 0: Bootstrap

```
"inicializa speckit para este proyecto"
"bootstrap spec-kit-tcm-plan"
```

### Paso 1: Especificar

```
"quiero agregar un dark mode toggle"
"crea una spec para autenticacion con OAuth"
```

### Paso 2: Planear

```
"planea la feature 001-dark-mode"
"genera el plan de implementacion para autenticacion"
```

### Paso 3: Tareas

```
"genera las tareas para 001-dark-mode"
"crea el tasks.md de la feature de autenticacion"
```

### Paso 4: Implementar

```
"implementa las tareas de 001-dark-mode"
"ejecuta el plan de autenticacion"
```

## Skills disponibles

El agente `speckit-planner` carga la skill correspondiente segun la fase detectada. No hay comandos slash — el usuario describe lo que necesita en lenguaje natural.

| Skill | Fase | Proposito |
|-------|------|-----------|
| `speckit.init` | Step 0 (automatico) | Verificar prerequisitos + generar/sincronizar governance.md |
| `speckit.specify` | Especificar | Parsear descripcion y generar spec.md |
| `speckit.plan` | Planear | Generar plan.md + artefactos de diseno |
| `speckit.tasks` | Tareas | Generar tasks.md con enrichment completo |
| `speckit.implement` | Implementar | Ejecutar tareas en orden con agentes |
| `speckit.add-task` | Ad-hoc | Agregar tarea con auto-enrichment |
| `speckit.analyze-task` | Analisis | Deep-dive de tarea antes de ejecutarla |
| `speckit.status` | Orientacion | Estado actual + siguiente paso exacto |
| `speckit.validate` | Verificacion | Detecta drift entre tasks.md y codigo real |

**Nota sobre `speckit.init`:** el agente lo ejecuta automaticamente como Step 0 antes de cualquier accion. El usuario no necesita invocarlo directamente.

## Estructura de Proyecto

```
.claude/project-context/
└── project-context.json   ← paths.speckit_root configura la ubicacion del speckit-root

<speckit-root>/            ← ej: spec-kit-tcm-plan/
├── governance.md          ← generado por gaia-init/speckit.init, sincronizado automaticamente
├── decisions/
│   └── ADR-001-example.md
└── specs/
    └── 001-feature-name/
        ├── spec.md
        ├── plan.md
        ├── tasks.md
        └── data-model.md
```

## Agent Routing

| Agent | Triggers | Tier |
|-------|----------|------|
| terraform-architect | terraform, .tf, gke | T0-T3 |
| gitops-operator | kubectl, helm, flux | T0-T3 |
| cloud-troubleshooter | gcloud, GCP, IAM | T0 |
| devops-developer | docker, npm, test | T0-T1 |

## Governance

- **Standards:** `<speckit-root>/governance.md` — generado por `gaia-init`, sincronizado automaticamente en cada sesion desde `project-context.json`
- **Auto-sync:** el agente compara el Stack Definition con `project-context.json` al inicio de cada sesion. Si hay diferencias, actualiza governance.md silenciosamente
- **GOVERNANCE_UPDATE:** cuando una spec o plan detecta nuevas tecnologias, el agente agrega la linea correspondiente al Stack Definition
- **Decisions:** `<speckit-root>/decisions/ADR-XXX-title.md`
- **Security Tiers:** T0 (read) -> T1 (validate) -> T2 (plan) -> T3 (apply)

## Ver Tambien

- [Agents](../agents/README.md)
- [CLAUDE.md](../../CLAUDE.md)

---

**Version:** 2.3.0 | **Actualizado:** 2026-02-23
