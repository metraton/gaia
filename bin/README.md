# Scripts Utilitarios de Gaia-Ops

**[🇺🇸 English version](README.en.md)**

Este directorio contiene scripts de utilidades para instalar, actualizar y gestionar el paquete gaia-ops. Son las herramientas de línea de comandos que facilitan el lifecycle del sistema.

## 🎯 Propósito

Los scripts bin/ automatizan tareas comunes de gestión del paquete. Proporcionan una interfaz amigable para operaciones que de otro modo requerirían pasos manuales complejos.

**Problema que resuelve:** Instalar y configurar un sistema de agentes puede ser complejo. Estos scripts automatizan la detección, instalación y configuración, reduciendo errores y ahorrando tiempo.

## 🔄 Cómo Funciona

### Flujo de Arquitectura

```
Usuario ejecuta bin/script
        ↓
[Script] detecta estado actual
        ↓
    Ejecuta acciones
    ┌────────┴────────┐
    ↓                 ↓
[Instalación]    [Limpieza]
    ↓                 ↓
Configura symlinks  Remueve archivos
    ↓                 ↓
Valida resultado
```

### Flujo de Ejemplo Real

```
Ejemplo: "npx gaia-init" en un nuevo proyecto

1. Usuario ejecuta: npx gaia-init
   ↓
2. [gaia-init.js] inicia:
   - Detecta directorio actual
   - Escanea estructura del proyecto
   ↓
3. Detección automática:
   - Encuentra: ./gitops → GitOps directory
   - Encuentra: ./terraform → Terraform directory
   - No encuentra: app-services
   ↓
4. Pregunta interactiva:
   "GCP Project ID? " → usuario: aaxis-rnd-general
   "Primary region? " → usuario: us-central1
   "Cluster name? " → usuario: tcm-gke-non-prod
   ↓
5. Instalación de Claude Code:
   - Verifica: claude-code no instalado
   - Ejecuta: npm install -g claude-code
   ✅ Claude Code installed
   ↓
6. Creación de estructura:
   - mkdir -p .claude/
   - Crea symlinks:
     .claude/agents → node_modules/.../agents
     .claude/tools → node_modules/.../tools
     .claude/hooks → node_modules/.../hooks
     .claude/commands → node_modules/.../commands
     .claude/config → node_modules/.../config
     .claude/templates → node_modules/.../templates
   ↓
7. Generación de archivos:
   - CLAUDE.md (desde template)
   - AGENTS.md (symlink)
   - project-context.json (con datos ingresados)
   ↓
8. Validación:
   ✅ All symlinks created
   ✅ CLAUDE.md generated
   ✅ project-context.json created
   ↓
9. Resultado:
   "
   ✅ Gaia-Ops installed successfully!
   
   Next steps:
   1. Run: claude-code
   2. Try: Ask any DevOps question
   "
```

## 📋 Scripts Disponibles

### Instalación y Setup

#### `gaia-init.js` (~1000 líneas)
El instalador principal - configura gaia-ops en cualquier proyecto.

**Qué hace:**
- Auto-detecta estructura del proyecto
- Pregunta configuración interactivamente
- Instala Claude Code (si no existe)
- Crea directorio `.claude/` con symlinks
- Genera `CLAUDE.md` desde template
- Crea `project-context.json`

**Uso:**
```bash
# Modo interactivo (recomendado)
npx gaia-init

# Con opciones
npx gaia-init --gitops ./gitops --terraform ./tf

# No interactivo (CI/CD)
npx gaia-init --non-interactive --project-id aaxis-rnd \
  --region us-central1 --cluster tcm-gke
```

**Opciones CLI:**
```
--non-interactive       No hacer preguntas, usar defaults
--gitops <path>        Path de GitOps
--terraform <path>     Path de Terraform
--app-services <path>  Path de app services
--project-id <id>      GCP Project ID
--region <region>      Primary region
--cluster <name>       Cluster name
--skip-claude-install  No instalar Claude Code
```

---

#### `gaia-update.js` (~300 líneas)
Actualiza configuración sin reinstalar todo (postinstall hook).

**Cuándo se ejecuta:**
- Automáticamente después de `npm install`
- Automáticamente después de `npm update`

**Qué actualiza:**
- Regenera `CLAUDE.md` si el template cambió
- Actualiza symlinks rotos
- NO sobrescribe `project-context.json` (preserva tu config)

**Opciones:**
```bash
# Ver qué se actualizará
npm run postinstall --dry-run

# Forzar actualización
node bin/gaia-update.js --force
```

---

### Limpieza y Desinstalación

#### `gaia-cleanup.js` (~200 líneas)
Limpia archivos temporales sin remover configuración.

**Qué limpia:**
- Caches temporales
- Logs antiguos (>30 días)
- Session bundles viejos
- __pycache__ directories

**Qué NO toca:**
- `project-context.json`
- `CLAUDE.md`
- Symlinks de `.claude/`
- Session activa

**Uso:**
```bash
# Limpieza estándar
node bin/gaia-cleanup.js

# Limpieza profunda (incluye logs recientes)
node bin/gaia-cleanup.js --deep
```

---

#### `gaia-uninstall.js` (~250 líneas)
Desinstala completamente gaia-ops del proyecto.

**Qué remueve:**
- Directorio `.claude/` completo
- `CLAUDE.md`
- `AGENTS.md`
- Symlinks creados

**Qué preserva (opcional):**
- `project-context.json` (pregunta antes)
- Logs históricos (pregunta antes)

**Uso:**
```bash
# Desinstalación interactiva
node bin/gaia-uninstall.js

# Desinstalación completa (sin preguntas)
node bin/gaia-uninstall.js --force --remove-all
```

---

### Validación

#### `pre-publish-validate.js` (~400 líneas)
Valida el paquete antes de publicar a npm.

**Qué valida:**
- Estructura de archivos correcta
- Templates válidos
- Symlinks correctos en ejemplo
- Tests pasan
- package.json correcto
- CHANGELOG actualizado

**Uso (para maintainers):**
```bash
# Validar antes de publish
npm run pre-publish:validate

# Dry-run de publish
npm run pre-publish:dry
```

---

#### `cleanup-claude-install.js` (~150 líneas)
Limpia instalaciones parciales o fallidas de Claude Code.

**Cuándo usar:**
- Instalación de claude-code falló parcialmente
- Claude Code no funciona correctamente después de instalar

**Uso:**
```bash
node bin/cleanup-claude-install.js
```

---

## 🚀 Uso Común

### Primera Instalación

```bash
# 1. Instalar el paquete
npm install @jaguilar87/gaia-ops

# 2. Ejecutar instalador
npx gaia-init

# 3. Iniciar Claude Code
claude-code
```

### Actualización

```bash
# Actualizar paquete
npm update @jaguilar87/gaia-ops

# Postinstall hook actualiza automáticamente
# Si no, ejecutar manualmente:
node bin/gaia-update.js
```

### Desinstalación

```bash
# Opción 1: Desinstalar limpiamente
node bin/gaia-uninstall.js
npm uninstall @jaguilar87/gaia-ops

# Opción 2: Force remove todo
node bin/gaia-uninstall.js --force --remove-all
rm -rf .claude/ CLAUDE.md AGENTS.md
npm uninstall @jaguilar87/gaia-ops
```

## 🔧 Características Técnicas

### Binarios npm

Definidos en `package.json`:

```json
{
  "bin": {
    "gaia-init": "bin/gaia-init.js",
    "gaia-cleanup": "bin/gaia-cleanup.js",
    "gaia-uninstall": "bin/gaia-uninstall.js"
  },
  "scripts": {
    "postinstall": "node bin/gaia-update.js",
    "preuninstall": "node bin/gaia-cleanup.js"
  }
}
```

### Detección Automática

`gaia-init.js` usa heurísticas para detectar:

```javascript
// GitOps detection
const gitopsCandidates = [
  'gitops',
  'non-prod-rnd-gke-gitops',
  'k8s',
  'kubernetes',
  'manifests'
];

// Terraform detection
const terraformCandidates = [
  'terraform',
  'tf',
  'infrastructure',
  'iac'
];

// Valida contenido para confirmar
if (hasManifests(dir) || hasHelmCharts(dir)) {
  detected.gitops = dir;
}
```

### Variables de Entorno

Scripts respetan variables de entorno:

```bash
# Configurar antes de init
export CLAUDE_GITOPS_DIR="./my-gitops"
export CLAUDE_TERRAFORM_DIR="./my-tf"
export CLAUDE_PROJECT_ID="my-gcp-project"

# Ejecutar init (usará las vars)
npx gaia-init --non-interactive
```

## 📖 Referencias

**Archivos de scripts:**
```
bin/
├── gaia-init.js              (~1000 líneas) - Instalador principal
├── gaia-update.js            (~300 líneas)  - Actualizador
├── gaia-cleanup.js           (~200 líneas)  - Limpieza
├── gaia-uninstall.js         (~250 líneas)  - Desinstalador
├── pre-publish-validate.js   (~400 líneas)  - Validador pre-publish
└── cleanup-claude-install.js (~150 líneas)  - Limpieza de Claude
```

**Templates usados:**
- `templates/CLAUDE.template.md` - Template para CLAUDE.md
- `templates/settings.template.json` - Template para settings.json

**Documentación relacionada:**
- [INSTALL.md](../INSTALL.md) - Guía de instalación detallada
- [README.md](../README.md) - Overview del paquete

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Total de scripts:** 6 utilitarios  
**Mantenido por:** Gaia (meta-agent) + maintainers del paquete

