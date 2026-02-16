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
Copia estatica (CLAUDE.md) o merge (settings.json)
        |
Genera archivo final
```

## Templates Disponibles

### CLAUDE.template.md (~260 lineas)

Template estatico para el archivo del orquestador. Se copia tal cual por `gaia-init` y `gaia-update` — no tiene placeholders de interpolacion.

Contiene: routing table, security tiers, T3 protocol, communication style, hook enforcement, system paths.

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

## Referencias

- `bin/gaia-init.js` - Instalador
- `bin/gaia-update.js` - Actualizador

---

**Actualizado:** 2026-02-13 | **Templates:** 2
