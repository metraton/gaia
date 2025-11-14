# Guía de Instalación de Gaia-Ops

**[🇺🇸 English version](INSTALL.en.md)**

Esta guía te ayudará a instalar y configurar Gaia-Ops en tu proyecto. El proceso es automático y toma menos de 5 minutos.

## 🎯 ¿Qué es Gaia-Ops?

Gaia-Ops es un sistema de agentes IA especializados que automatizan tareas DevOps. Piensa en ello como tener un equipo de expertos (Terraform, Kubernetes, GCP, AWS) trabajando juntos, coordinados por un orquestador inteligente.

---

## 🚀 Instalación Rápida (Recomendado)

### Opción 1: Instalación Interactiva

La forma más fácil - el instalador te guiará paso a paso:

```bash
npx gaia-init
```

Esto te hará preguntas como:
- ¿Dónde están tus archivos de GitOps?
- ¿Dónde está tu código Terraform?
- ¿Cuál es tu proyecto GCP?

### Opción 2: Instalación No-Interactiva

Para scripts CI/CD o si ya sabes los valores:

```bash
npx gaia-init --non-interactive \
  --gitops ./gitops \
  --terraform ./terraform \
  --app-services ./app-services \
  --project-id mi-proyecto-gcp \
  --cluster mi-cluster-gke
```

---

## 🔄 Cómo Funciona la Instalación

### Flujo de Instalación

```
Usuario ejecuta: npx gaia-init
        ↓
[Detector] escanea tu proyecto
        ↓
   Encuentra automáticamente:
   - Directorio GitOps
   - Directorio Terraform
   - Directorio de apps
        ↓
[Instalador] pregunta datos faltantes:
   - GCP Project ID
   - Región
   - Nombre del cluster
        ↓
[Instalador] verifica Claude Code
        ↓
    ¿Ya instalado?
    ┌────┴────┐
    ↓         ↓
   SÍ        NO
    ↓         ↓
  Usa el  Instala
 existente  nuevo
    ↓         ↓
    └────┬────┘
         ↓
Crea estructura .claude/
         ↓
Crea symlinks a gaia-ops:
  .claude/agents → node_modules/.../agents
  .claude/tools → node_modules/.../tools
  .claude/hooks → node_modules/.../hooks
  .claude/commands → node_modules/.../commands
  .claude/config → node_modules/.../config
         ↓
Genera archivos de configuración:
  - CLAUDE.md (orquestador)
  - AGENTS.md (symlink)
  - project-context.json
  - settings.json
         ↓
Valida instalación:
  ✅ Symlinks correctos
  ✅ Claude Code disponible
  ✅ Configuración válida
         ↓
¡Listo! Puedes usar: claude-code
```

### Ejemplo Real de Instalación

```
Ejemplo: Instalación en proyecto con GitOps y Terraform

1. Usuario: npx gaia-init
   ↓
2. Detector encuentra:
   ✅ ./gitops (52 archivos YAML detectados)
   ✅ ./terraform (15 archivos .tf detectados)
   ❌ ./app-services (no encontrado)
   ↓
3. Instalador pregunta:
   ? GCP Project ID: → aaxis-rnd-general-project
   ? Primary region: → us-central1
   ? Cluster name: → tcm-gke-non-prod
   ↓
4. Verifica Claude Code:
   ✅ Claude Code ya instalado en /usr/local/bin/claude
   ⏭️  Omitiendo reinstalación
   ↓
5. Crea estructura:
   ✅ .claude/ creado
   ✅ 6 symlinks creados
   ✅ CLAUDE.md generado (196 líneas)
   ✅ project-context.json creado
   ↓
6. Resultado:
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ✅ Gaia-Ops instalado exitosamente!
   
   📚 Documentación disponible:
   - .claude/agents/README.md
   - .claude/config/README.md
   - .claude/commands/README.md
   
   🚀 Próximos pasos:
   1. Ejecuta: claude-code
   2. Pregunta: "Muéstrame los clusters GKE"
   3. O usa: /gaia para explorar el sistema
```

---

## ⚙️ Opciones de Instalación

### Variables de Entorno

Configura antes de instalar para evitar preguntas:

```bash
# Configurar paths
export CLAUDE_GITOPS_DIR="./gitops"
export CLAUDE_TERRAFORM_DIR="./terraform"
export CLAUDE_APP_SERVICES_DIR="./app-services"

# Configurar proyecto
export CLAUDE_PROJECT_ID="mi-proyecto-gcp"
export CLAUDE_REGION="us-central1"
export CLAUDE_CLUSTER_NAME="mi-cluster-gke"

# Instalar sin preguntas
npx gaia-init --non-interactive
```

### Opciones CLI Completas

```
gaia-init [opciones]

Opciones:
  --non-interactive          No hacer preguntas, usar valores provistos o defaults
  --gitops <path>           Path del directorio GitOps
  --terraform <path>        Path del directorio Terraform
  --app-services <path>     Path del directorio de aplicaciones
  --project-id <id>         ID del proyecto GCP
  --region <region>         Región principal (default: us-central1)
  --cluster <name>          Nombre del cluster
  --skip-claude-install     Omitir instalación de Claude Code
```

### Instalación Global

Si prefieres tener `gaia-init` disponible globalmente:

```bash
npm install -g @jaguilar87/gaia-ops
gaia-init
```

---

## 📦 ¿Qué se Instala?

### Estructura Creada

```
tu-proyecto/
├── .claude/                    ← Nuevo directorio
│   ├── agents/ (symlink)       → Agentes especializados
│   ├── tools/ (symlink)        → Herramientas de orquestación
│   ├── hooks/ (symlink)        → Validaciones de seguridad
│   ├── commands/ (symlink)     → Comandos slash
│   ├── config/ (symlink)       → Configuración y docs
│   ├── templates/ (symlink)    → Templates de instalación
│   ├── project-context.json    ← Tu configuración
│   ├── logs/                   ← Logs de auditoría
│   └── tests/                  ← Tests del sistema
├── CLAUDE.md                   ← Orquestador principal
├── AGENTS.md (symlink)         ← Overview del sistema
└── node_modules/
    └── @jaguilar87/gaia-ops/   ← Paquete npm
```

### Archivos Generados

| Archivo | Descripción | Personalizado |
|---------|-------------|---------------|
| `.claude/` | Directorio principal | ✅ |
| `CLAUDE.md` | Instrucciones del orquestador | ✅ Con tus paths |
| `AGENTS.md` | Symlink a documentación | ❌ |
| `project-context.json` | Tu configuración de proyecto | ✅ Con tus datos |
| `settings.json` | Configuración de seguridad | ✅ |

---

## 📚 Documentación Disponible Después de Instalar

Una vez instalado, tienes acceso a **documentación completa** en cada directorio:

### READMEs de Directorios

```
.claude/
├── agents/README.md              Sistema de 6 agentes especialistas
├── commands/README.md            11 comandos slash disponibles
├── config/README.md              17 archivos de configuración
├── hooks/README.md               7 hooks de seguridad
├── tools/README.md               Herramientas de orquestación
└── templates/README.md           Templates de instalación
```

**Todos con versión en inglés:** `.../README.en.md`

### Documentación Especial

- **Principios de Documentación:** `.claude/config/documentation-principles.md`
  - Cómo está escrita toda la documentación
  - Estándares para crear nuevos docs
  
- **Orchestration Workflow:** `.claude/config/orchestration-workflow.md`
  - Flujo completo Phase 0-6
  - Cómo funciona el sistema
  
- **Agent Catalog:** `.claude/config/agent-catalog.md`
  - Capacidades de cada agente
  - Cuándo usar cada uno

### ¿Cómo Navegar la Documentación?

```bash
# Ver documentación de agentes
cat .claude/agents/README.md

# Ver comandos disponibles
cat .claude/commands/README.md

# Ver configuración del sistema
cat .claude/config/README.md

# Ver workflow completo
cat .claude/config/orchestration-workflow.md
```

---

## ✅ Post-Instalación

### 1. Verifica la Instalación

```bash
# Verifica estructura creada
ls -la .claude/

# Debe mostrar symlinks:
# agents -> ../node_modules/@jaguilar87/gaia-ops/agents
# tools -> ../node_modules/@jaguilar87/gaia-ops/tools
# ...
```

### 2. Revisa Configuración Generada

```bash
# Ver CLAUDE.md generado
cat CLAUDE.md

# Ver project-context.json
cat .claude/project-context.json

# Ajusta paths si es necesario
```

### 3. Inicia Claude Code

```bash
claude-code
```

### 4. Prueba el Sistema

```bash
# En Claude Code, prueba con:
"Muéstrame los clusters GKE"
"Lista los deployments en el namespace production"

# O usa comandos slash:
/gaia Explica cómo funciona el routing
/save-session mi-sesion
/session-status
```

---

## 🔄 Actualizaciones del Paquete

### ⚠️ Archivos que se Sobrescriben

Cuando actualizas `@jaguilar87/gaia-ops`, estos archivos se **regeneran desde templates**:

| Archivo | Comportamiento | Acción Recomendada |
|---------|----------------|-------------------|
| `CLAUDE.md` | ⚠️ **Se sobrescribe** | Haz backup si personalizas |
| `.claude/settings.json` | ⚠️ **Se sobrescribe** | Haz backup si personalizas |
| `.claude/project-context.json` | ✅ **Se preserva** | Seguro |
| `.claude/logs/` | ✅ **Se preserva** | Seguro |
| Otros archivos en `.claude/` | ✅ **Auto-actualizados via symlinks** | Seguro |

### Proceso de Actualización

```bash
# 1. Backup (opcional, si personalizaste)
cp CLAUDE.md CLAUDE.md.backup
cp .claude/settings.json .claude/settings.json.backup

# 2. Actualizar paquete
npm install @jaguilar87/gaia-ops@latest

# 3. El postinstall hook regenera automáticamente:
#    - CLAUDE.md
#    - .claude/settings.json

# 4. Si hiciste backup, compara y fusiona cambios
diff CLAUDE.md CLAUDE.md.backup
```

### ¿Por qué se Sobrescriben?

Los archivos se regeneran para:
- ✅ Incorporar mejoras del sistema
- ✅ Agregar nuevos agentes/comandos
- ✅ Actualizar configuración de seguridad
- ✅ Mantener sincronización con el paquete

**Tu configuración en `project-context.json` SIEMPRE se preserva.**

---

## 🛠️ Gestión de Claude Code

### Evitando Instalaciones Múltiples

Gaia-Ops **detecta automáticamente** si ya tienes Claude Code instalado y **NO lo reinstala**.

#### Verificación de Instalación

```bash
# Ver dónde está instalado Claude Code
which claude-code

# Debe mostrar UNA ubicación:
# ✅ /usr/local/bin/claude-code (nativo - recomendado)
# ⚠️ /home/user/.npm-packages/bin/claude-code (npm global)
```

#### Si Tienes Múltiples Instalaciones

**Opción 1: Cleanup Automático**
```bash
npx gaia-cleanup
```

**Opción 2: Cleanup Manual**
```bash
# Remover instalación npm global (si existe)
npm -g uninstall @anthropic-ai/claude-code

# Verificar que quedó solo una
which claude-code
claude-code --version
```

### Tabla de Comportamientos

| Escenario | Comportamiento |
|-----------|----------------|
| Claude Code instalado (nativo) | ✅ Usa el existente, no reinstala |
| Claude Code no instalado | ✅ Instala versión nativa |
| `--skip-claude-install` provisto | ✅ Omite instalación completamente |
| npm global + nativo | ⚠️ Ejecuta cleanup automático |

---

## 🐛 Troubleshooting

### Problema: Claude Code No Encontrado

**Síntoma:**
```bash
$ claude-code
bash: claude-code: command not found
```

**Solución:**
```bash
# Verifica instalación
which claude-code

# Si no está, instala manualmente
npm install -g @anthropic-ai/claude-code

# O instala versión nativa (recomendado)
curl -fsSL https://install.anthropic.com | bash
```

---

### Problema: Múltiples Instalaciones de Claude Code

**Síntoma:**
```
Warning: Multiple Claude Code installations detected
```

**Solución:**
```bash
# Opción 1: Script automático
npx gaia-cleanup

# Opción 2: Manual
npm -g uninstall @anthropic-ai/claude-code
npm list -g @anthropic-ai/claude-code  # Debe dar error (no encontrado)
```

---

### Problema: Permisos Denegados en npm global

**Síntoma:**
```
EACCES: permission denied
```

**Solución 1: Fix de permisos npm (recomendado)**
```bash
mkdir ~/.npm-global
npm config set prefix '~/.npm-global'
export PATH=~/.npm-global/bin:$PATH
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
```

**Solución 2: Usar sudo (no recomendado)**
```bash
sudo npm install -g @jaguilar87/gaia-ops --unsafe-perm
```

---

### Problema: Symlinks No Creados

**Síntoma:**
```
ls .claude/agents
# Error: No such file or directory
```

**Solución:**
```bash
# Re-ejecutar instalación
npx gaia-init --force

# O crear manualmente
cd .claude
ln -s ../node_modules/@jaguilar87/gaia-ops/agents agents
ln -s ../node_modules/@jaguilar87/gaia-ops/tools tools
ln -s ../node_modules/@jaguilar87/gaia-ops/hooks hooks
ln -s ../node_modules/@jaguilar87/gaia-ops/commands commands
ln -s ../node_modules/@jaguilar87/gaia-ops/config config
ln -s ../node_modules/@jaguilar87/gaia-ops/templates templates
```

---

### Problema: project-context.json Inválido

**Síntoma:**
```
Error parsing project-context.json
```

**Solución:**
```bash
# Validar JSON
cat .claude/project-context.json | jq .

# Si es inválido, regenerar
rm .claude/project-context.json
npx gaia-init
```

---

## 🧹 Desinstalación

### Desinstalación Completa

```bash
# Opción 1: Script interactivo (recomendado)
npx gaia-uninstall

# Te preguntará si quieres preservar:
# - project-context.json
# - Logs históricos

# Opción 2: Desinstalación forzada (sin preguntas)
npx gaia-uninstall --force --remove-all
```

### Desinstalación Manual

```bash
# 1. Remover directorio .claude/
rm -rf .claude/

# 2. Remover archivos generados
rm CLAUDE.md AGENTS.md

# 3. Desinstalar paquete npm
npm uninstall @jaguilar87/gaia-ops
```

---

## 🎓 Comandos Útiles Post-Instalación

### Explorar el Sistema

```bash
# Ver estructura instalada
tree .claude/ -L 2

# Ver documentación disponible
find .claude -name "README.md" -o -name "README.en.md"

# Ver agentes disponibles
ls .claude/agents/

# Ver comandos disponibles
ls .claude/commands/
```

### Gestión de Limpieza

```bash
# Limpiar caches y temporales
npx gaia-cleanup

# Limpiar logs antiguos (>30 días)
npx gaia-cleanup --deep

# Ver tamaño de .claude/
du -sh .claude/
```

### Validación

```bash
# Validar estructura
test -L .claude/agents && echo "✅ Agents OK" || echo "❌ Agents missing"
test -f CLAUDE.md && echo "✅ CLAUDE.md OK" || echo "❌ CLAUDE.md missing"
test -f .claude/project-context.json && echo "✅ Context OK" || echo "❌ Context missing"
```

---

## 💡 Principios de Diseño

Gaia-Ops está diseñado con estos principios:

✅ **Mínimal** - Solo crea lo necesario, sin duplicados  
✅ **Adaptativo** - Auto-detecta instalaciones existentes  
✅ **No-invasivo** - Funciona desde cualquier directorio  
✅ **Seguro** - Valida paths y omite reinstalaciones  
✅ **Claro** - Feedback explícito en cada paso  
✅ **Documentado** - Documentación completa en cada directorio  

---

## 📞 Soporte

### Recursos

- **Documentación:** Dentro de `.claude/*/README.md`
- **Issues:** https://github.com/metraton/gaia-ops/issues
- **Email:** jaguilar1897@gmail.com

### Preguntas Frecuentes

**P: ¿Puedo usar gaia-ops en múltiples proyectos?**  
R: Sí. Instala en cada proyecto y cada uno tendrá su propio `project-context.json`.

**P: ¿Los symlinks funcionan en Windows?**  
R: Sí, pero requieres habilitar modo desarrollador o ejecutar como administrador.

**P: ¿Puedo personalizar CLAUDE.md sin que se sobrescriba?**  
R: No directamente. Mejor: contribuye cambios al template en el repositorio.

**P: ¿Cómo actualizo solo la documentación sin cambiar código?**  
R: `npm update @jaguilar87/gaia-ops` - los symlinks apuntan a la nueva versión automáticamente.

---

**Versión:** 2.6.0  
**Última actualización:** 2025-11-14  
**Mantenido por:** Jorge Aguilar + Gaia (meta-agent)
