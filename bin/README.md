# Scripts Utilitarios de Gaia-Ops

**[English version](README.en.md)**

Scripts de utilidades para instalar, actualizar y gestionar el paquete gaia-ops.

## Proposito

Automatizan tareas comunes de gestion del paquete, proporcionando una interfaz amigable para operaciones que de otro modo requeririan pasos manuales complejos.

## Como Funciona

```
Usuario ejecuta bin/script
        |
[Script] detecta estado actual
        |
    Ejecuta acciones
    |               |
[Instalacion]    [Limpieza]
    |               |
Configura symlinks  Remueve archivos
```

## Scripts Disponibles

### Instalacion y Setup

| Script | Lineas | Descripcion |
|--------|--------|-------------|
| `gaia-init.js` | ~1000 | Instalador principal |
| `gaia-update.js` | ~300 | Actualizador de configuracion |

### Limpieza y Desinstalacion

| Script | Lineas | Descripcion |
|--------|--------|-------------|
| `gaia-cleanup.js` | ~200 | Limpia archivos temporales |
| `gaia-uninstall.js` | ~250 | Desinstala completamente |

### Validacion

| Script | Lineas | Descripcion |
|--------|--------|-------------|
| `pre-publish-validate.js` | ~400 | Valida antes de publicar |
| `cleanup-claude-install.js` | ~150 | Limpia instalaciones fallidas |

## Uso Comun

### Primera Instalacion

```bash
npm install @jaguilar87/gaia-ops
npx gaia-init
claude-code
```

### Actualizacion

```bash
npm update @jaguilar87/gaia-ops
# Postinstall hook actualiza automaticamente
```

### Desinstalacion

```bash
node bin/gaia-uninstall.js
npm uninstall @jaguilar87/gaia-ops
```

## gaia-cleanup.js

**Que limpia:**
- Caches temporales
- Logs antiguos (>30 dias)
- __pycache__ directories

**Que NO toca:**
- `project-context.json`
- `CLAUDE.md`
- Symlinks de `.claude/`

## Binarios npm

Definidos en `package.json`:

```json
{
  "bin": {
    "gaia-init": "bin/gaia-init.js",
    "gaia-cleanup": "bin/gaia-cleanup.js",
    "gaia-uninstall": "bin/gaia-uninstall.js"
  }
}
```

## Variables de Entorno

```bash
export CLAUDE_GITOPS_DIR="./my-gitops"
export CLAUDE_PROJECT_ID="my-gcp-project"
npx gaia-init --non-interactive
```

## Referencias

- [INSTALL.md](../INSTALL.md) - Guia de instalacion
- [README.md](../README.md) - Overview del paquete

---

**Version:** 1.0.0 | **Actualizado:** 2025-12-06 | **Scripts:** 6
