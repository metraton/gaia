# Spec-Kit 2.0 - Framework de Desarrollo de Features

**[English version](README.en.md)**

Framework para desarrollo dirigido por especificaciones con orquestacion inteligente de agentes.

## Vision General

```
Idea -> Spec -> Plan -> Tasks -> Implementation
```

**Mejoras en 2.0:**
- 5 comandos core (antes 10)
- Clarificacion, validacion y enrichment integrados
- Auto-context desde project-context.json
- Governance: ADRs inmutables + governance.md

## Workflow

### Paso 0: Bootstrap

```bash
/speckit.init
```

### Paso 1: Especificar

```bash
/speckit.specify spec-kit-tcm-plan Add dark mode toggle
```

### Paso 2: Planear

```bash
/speckit.plan spec-kit-tcm-plan 00N-dark-mode
```

### Paso 3: Tareas

```bash
/speckit.tasks spec-kit-tcm-plan 00N-dark-mode
```

### Paso 4: Implementar

```bash
/speckit.implement spec-kit-tcm-plan 00N-dark-mode
```

## Comandos

| Comando | Proposito |
|---------|-----------|
| `/speckit.init` | Bootstrap project |
| `/speckit.specify` | Create feature spec |
| `/speckit.plan` | Generate plan (+ clarify) |
| `/speckit.tasks` | Generate tasks (+ validate + enrich) |
| `/speckit.implement` | Execute with agents |
| `/speckit.add-task` | Add single task |
| `/speckit.analyze-task` | Deep analysis |

## Estructura de Proyecto

```
.claude/speckit/
├── governance.md
├── decisions/
│   └── ADR-001-example.md
├── templates/
└── scripts/

<speckit-root>/specs/
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

- **Standards:** `.claude/speckit/governance.md`
- **Decisions:** `.claude/speckit/decisions/ADR-XXX-title.md`
- **Security Tiers:** T0 (read) -> T1 (validate) -> T2 (plan) -> T3 (apply)

## Ver Tambien

- [Agents](../agents/README.md)
- [CLAUDE.md](../../CLAUDE.md)

---

**Version:** 2.0.0 | **Actualizado:** 2025-12-06
