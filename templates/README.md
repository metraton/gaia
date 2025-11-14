# Templates de Gaia-Ops

**[🇺🇸 English version](README.en.md)**

Este directorio contiene templates que se usan durante la instalación para generar archivos de configuración personalizados para cada proyecto.

## 🎯 Propósito

Los templates proporcionan una base consistente para archivos de configuración, mientras permiten personalización según las necesidades de cada proyecto. Son como moldes que se rellenan con información específica.

**Problema que resuelve:** Cada proyecto tiene diferentes rutas, IDs de proyecto y configuraciones. Los templates permiten generar archivos correctos automáticamente en lugar de requerir configuración manual propensa a errores.

## 🔄 Cómo Funciona

### Flujo de Arquitectura

```
[gaia-init] recopila datos del proyecto
        ↓
Lee template/
        ↓
Reemplaza placeholders con datos reales
        ↓
Genera archivo final en proyecto
```

### Flujo de Ejemplo Real

```
Ejemplo: Generar CLAUDE.md para un proyecto nuevo

1. [gaia-init.js] recopila datos:
   - gitops_path: "./gitops"
   - terraform_path: "./terraform"
   - project_id: "aaxis-rnd-general"
   ↓
2. Lee: templates/CLAUDE.template.md
   ↓
3. Template contiene:
   "GitOps directory: {{GITOPS_PATH}}"
   "Terraform directory: {{TERRAFORM_PATH}}"
   "GCP Project: {{GCP_PROJECT_ID}}"
   ↓
4. Reemplaza placeholders:
   {{GITOPS_PATH}} → "./gitops"
   {{TERRAFORM_PATH}} → "./terraform"
   {{GCP_PROJECT_ID}} → "aaxis-rnd-general"
   ↓
5. Genera: ./CLAUDE.md con valores reales:
   "GitOps directory: ./gitops"
   "Terraform directory: ./terraform"
   "GCP Project: aaxis-rnd-general"
   ↓
6. Resultado: CLAUDE.md personalizado y listo para usar
```

## 📋 Templates Disponibles

### `CLAUDE.template.md` (~200 líneas)
Template principal para generar el archivo del orquestador.

**Qué contiene:**
- Instrucciones del orquestador (Phase 0-6)
- Placeholders para rutas del proyecto
- Referencias a agentes y herramientas
- Workflow de operación

**Placeholders:**
```
{{GITOPS_PATH}}        - Path al directorio GitOps
{{TERRAFORM_PATH}}     - Path al directorio Terraform
{{APP_SERVICES_PATH}}  - Path al directorio de app services
{{GCP_PROJECT_ID}}     - ID del proyecto GCP
{{GCP_REGION}}         - Región principal GCP
{{CLUSTER_NAME}}       - Nombre del cluster
{{AWS_ACCOUNT_ID}}     - ID de cuenta AWS (si aplica)
{{AWS_REGION}}         - Región AWS (si aplica)
```

**Generado por:** `bin/gaia-init.js`

**Archivo de salida:** `./CLAUDE.md` (en la raíz del proyecto)

---

### `settings.template.json` (~220 líneas)
Template para configuración de permisos y seguridad.

**Qué contiene:**
- Definiciones de security tiers (T0-T3)
- Comandos bloqueados (`always_blocked`)
- Comandos que requieren confirmación (`ask_permissions`)
- Configuración de production mode
- Reglas de GitOps security

**Placeholders:**
```json
{
  "project_id": "{{GCP_PROJECT_ID}}",
  "environment": "{{ENVIRONMENT}}",
  "cluster_name": "{{CLUSTER_NAME}}",
  "security_tiers": { ... },
  "always_blocked": [ ... ],
  "ask_permissions": [ ... ]
}
```

**Generado por:** `bin/gaia-init.js`

**Archivo de salida:** `./.claude/settings.json`

---

## 🚀 Uso de Templates

### Durante Instalación

Los templates se usan automáticamente cuando ejecutas `gaia-init`:

```bash
npx gaia-init
# gaia-init lee templates/
# Pregunta por valores
# Genera archivos personalizados
```

### Personalización Manual

Si necesitas modificar un template:

1. Edita el template en `node_modules/@jaguilar87/gaia-ops/templates/`
2. O mejor: contribuye cambios al repositorio

**Nota:** NO edites los archivos generados directamente si planeas regenerarlos. Tus cambios se perderán.

### Regeneración

Para regenerar archivos desde templates:

```bash
# Regenerar CLAUDE.md
node node_modules/@jaguilar87/gaia-ops/bin/gaia-update.js

# O reinstalar completamente
npx gaia-init --force
```

## 🔧 Características Técnicas

### Sistema de Placeholders

Los placeholders usan sintaxis `{{NOMBRE}}`:

```javascript
// En gaia-init.js
const template = fs.readFileSync('templates/CLAUDE.template.md', 'utf8');

const rendered = template
  .replace(/\{\{GITOPS_PATH\}\}/g, gitopsPath)
  .replace(/\{\{TERRAFORM_PATH\}\}/g, terraformPath)
  .replace(/\{\{GCP_PROJECT_ID\}\}/g, projectId);

fs.writeFileSync('CLAUDE.md', rendered);
```

### Validación de Templates

Antes de publicar, los templates se validan:

```bash
npm run pre-publish:validate
# Verifica que todos los placeholders sean válidos
# Verifica sintaxis de JSON templates
# Verifica consistencia entre templates
```

### Versionamiento

Los templates siguen el versionamiento del paquete:

- **MAJOR:** Cambios incompatibles en estructura de placeholders
- **MINOR:** Nuevos placeholders opcionales
- **PATCH:** Correcciones en templates, mejoras de texto

**Importante:** Al actualizar gaia-ops, verifica si hay cambios en templates:

```bash
# Ver changelog de templates
cat node_modules/@jaguilar87/gaia-ops/CHANGELOG.md | grep -A 5 "templates"
```

## 📖 Referencias

**Archivos de templates:**
```
templates/
├── CLAUDE.template.md       (~200 líneas) - Orquestador principal
└── settings.template.json   (~220 líneas) - Configuración de seguridad
```

**Scripts que usan templates:**
- `bin/gaia-init.js` - Instalador (genera todos los archivos)
- `bin/gaia-update.js` - Actualizador (regenera si hay cambios)

**Archivos generados:**
- `./CLAUDE.md` - Desde CLAUDE.template.md
- `./.claude/settings.json` - Desde settings.template.json

**Documentación relacionada:**
- [Installation Guide](../INSTALL.md) - Guía de instalación
- [bin/](../bin/README.md) - Scripts de instalación

---

## 💡 Tips para Maintainers

### Agregar un Nuevo Placeholder

1. Agrega el placeholder al template:
```markdown
Project region: {{GCP_REGION}}
```

2. Actualiza `gaia-init.js` para recopilar el dato:
```javascript
const region = await prompts({
  type: 'text',
  name: 'value',
  message: 'GCP Region?',
  initial: 'us-central1'
});
```

3. Agrega el reemplazo:
```javascript
.replace(/\{\{GCP_REGION\}\}/g, region.value)
```

4. Actualiza tests de validación en `pre-publish-validate.js`

### Mantener Compatibilidad

**DO:**
- ✅ Agregar nuevos placeholders como OPCIONALES
- ✅ Documentar todos los placeholders
- ✅ Proveer valores default sensatos

**DON'T:**
- ❌ Remover placeholders existentes (breaking change)
- ❌ Cambiar nombres de placeholders (breaking change)
- ❌ Hacer placeholders obligatorios que antes eran opcionales

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Total de templates:** 2 templates principales  
**Mantenido por:** Gaia (meta-agent) + maintainers del paquete

