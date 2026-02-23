# Templates de Gaia-Ops

**[English version](README.en.md)**

Templates usados durante la instalacion para generar archivos de configuracion personalizados.

## Proposito

Proporcionan una base consistente para archivos de configuracion, permitiendo personalizacion segun las necesidades de cada proyecto.

## Como Funciona

```
[gaia-init] recopila datos del proyecto
        |
Lee template/
        |
Copia estatica (CLAUDE.md) o merge (settings.json) o interpolacion (governance.template.md)
        |
Genera archivo final en el proyecto del usuario
```

## Templates Disponibles

### CLAUDE.template.md (~66 lineas)

Template estatico para el archivo del orquestador. Se copia tal cual por `gaia-init` y `gaia-update` — no tiene placeholders de interpolacion.

Contiene: identity, routing table, descripciones de agentes, sintaxis del Task tool, work unit rule, processing agent responses.

**Salida:** `./CLAUDE.md`

### settings.template.json (~727 lineas)

Template para configuracion de permisos y seguridad.

**Contiene:**
- Security tiers (T0-T3)
- Comandos bloqueados
- Comandos que requieren confirmacion
- Reglas GitOps security

**Salida:** `./.claude/settings.json`

### governance.template.md

Template para el documento de governance del proyecto. Contiene placeholders que se rellenan con los valores del `project-context.json` generado por `gaia-init`.

**Contiene:**
- Stack Definition con placeholders: `[CLOUD_PROVIDER]`, `[PRIMARY_REGION]`, `[PROJECT_ID]`, `[CLUSTER_NAME]`, `[GITOPS_PATH]`, `[TERRAFORM_PATH]`, `[POSTGRES_INSTANCE]`, `[CONTAINER_REGISTRY]`
- 6 principios arquitectonicos inmutables (Code-First, GitOps SSOT, Security Tiers, Conventional Commits, Workload Identity, Resource Limits)
- Proceso de ADRs y evolucion de standards

**Generado por:** `speckit.init` skill (via agente en el primer uso de speckit, o manualmente)
**Sincronizado por:** agente `speckit-planner` al inicio de cada sesion (Step 0 automatico)
**Salida:** `<speckit-root>/governance.md`

## Uso

### Durante Instalacion

```bash
npx gaia-init
# Lee templates/, pregunta valores, genera archivos
```

### Regeneracion

```bash
# Regenerar desde templates
node node_modules/@jaguilar87/gaia-ops/bin/gaia-update.js

# O reinstalar completamente
npx gaia-init --force
```

## Referencias

- `bin/gaia-init.js` - Instalador
- `bin/gaia-update.js` - Actualizador

---

**Actualizado:** 2026-02-23 | **Templates:** 3
