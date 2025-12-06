# Templates de Gaia-Ops

**[English version](README.en.md)**

Templates usados durante la instalacion para generar archivos de configuracion personalizados.

## Proposito

Proporcionan una base consistente para archivos de configuracion, permitiendo personalizacion segun las necesidades de cada proyecto.

## Como Funciona

```
[gaia-init] recopila datos
        |
Lee template/
        |
Reemplaza placeholders
        |
Genera archivo final
```

## Templates Disponibles

### CLAUDE.template.md (~200 lineas)

Template principal para generar el archivo del orquestador.

**Placeholders:**
```
{{GITOPS_PATH}}        - Path GitOps
{{TERRAFORM_PATH}}     - Path Terraform
{{APP_SERVICES_PATH}}  - Path app services
{{GCP_PROJECT_ID}}     - ID proyecto GCP
{{GCP_REGION}}         - Region GCP
{{CLUSTER_NAME}}       - Nombre cluster
{{AWS_ACCOUNT_ID}}     - ID cuenta AWS
{{AWS_REGION}}         - Region AWS
```

**Salida:** `./CLAUDE.md`

### settings.template.json (~220 lineas)

Template para configuracion de permisos y seguridad.

**Contiene:**
- Security tiers (T0-T3)
- Comandos bloqueados
- Comandos que requieren confirmacion
- Reglas GitOps security

**Salida:** `./.claude/settings.json`

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

## Versionamiento

- **MAJOR:** Cambios incompatibles en placeholders
- **MINOR:** Nuevos placeholders opcionales
- **PATCH:** Correcciones de texto

## Referencias

- [bin/](../bin/README.md) - Scripts de instalacion
- [INSTALL.md](../INSTALL.md) - Guia de instalacion

---

**Actualizado:** 2025-12-06 | **Templates:** 2
