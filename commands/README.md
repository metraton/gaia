# Comandos Slash de Gaia-Ops

**[🇺🇸 English version](README.en.md)**

Los comandos slash son atajos rápidos que te permiten invocar funcionalidades específicas del sistema directamente. Son como accesos directos del teclado para tareas comunes.

## 🎯 Propósito

Los comandos slash proporcionan una forma rápida y consistente de acceder a funcionalidades avanzadas sin necesidad de escribir solicitudes completas en lenguaje natural.

**Problema que resuelve:** Algunas tareas requieren invocación directa de herramientas específicas. En lugar de describir verbosamente lo que quieres hacer, simplemente usas un comando slash.

## 🔄 Cómo Funciona

### Flujo de Arquitectura

```
Usuario escribe /comando
        ↓
[Claude Code] detecta el patrón /
        ↓
[Command Handler] carga el archivo .md del comando
        ↓
[Orquestador] ejecuta instrucciones del comando
        ↓
Resultado al usuario
```

### Flujo de Ejemplo Real

```
Ejemplo: "/save-session production-deploy"

1. Usuario escribe: /save-session production-deploy
   ↓
2. [Claude Code] detecta slash command
   ↓
3. [Command Handler] lee → commands/save-session.md
   ↓
4. [Save Session Tool] ejecuta:
   - Recopila contexto activo
   - Guarda session/active/active-context.json
   - Crea bundle: session/bundles/production-deploy.bundle.json
   - Genera resumen
   ↓
5. Resultado:
   "✅ Session saved: production-deploy
    Files: 12 | Size: 45KB | Context: 3.2K tokens"
```

## 📋 Comandos Disponibles

### Comandos de Meta-Análisis

#### `/gaia`
Invoca a Gaia, el meta-agente que analiza y optimiza el sistema de orquestación.

**Cuándo usar:**
- Analizar logs del sistema
- Investigar problemas de routing
- Optimizar workflows
- Mejorar documentación

**Ejemplo:**
```bash
/gaia Analiza por qué falló el routing en las últimas 10 solicitudes
```

**Salida esperada:**
- Análisis detallado de los eventos
- Identificación de patrones
- Recomendaciones de mejora

---

### Comandos de Sesiones

#### `/save-session [nombre]`
Guarda el contexto actual de trabajo en un bundle persistente.

**Cuándo usar:**
- Antes de terminar el día
- Después de completar una tarea importante
- Antes de cambiar de contexto a otra tarea
- Para compartir contexto con otro desarrollador

**Ejemplo:**
```bash
/save-session deploy-auth-v2
```

**Lo que guarda:**
- Archivos abiertos y modificados
- Conversaciones relevantes
- Estado del proyecto (project-context.json)
- Comandos ejecutados

---

#### `/restore-session [nombre]`
Restaura un contexto de trabajo guardado previamente.

**Cuándo usar:**
- Al comenzar el día
- Al retomar una tarea pausada
- Al hacer onboarding de un nuevo dev

**Ejemplo:**
```bash
/restore-session deploy-auth-v2
```

**Lo que restaura:**
- Lista de archivos del bundle
- Conversaciones previas
- Estado del proyecto
- Contexto completo para continuar

---

#### `/session-status`
Muestra el estado actual de la sesión activa.

**Cuándo usar:**
- Para verificar qué se guardará
- Para ver el tamaño del contexto
- Para revisar archivos rastreados

**Ejemplo:**
```bash
/session-status
```

**Información que muestra:**
```
📊 Active Session Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Files tracked: 12
Context size: 3.2K tokens
Last updated: 2 minutes ago

Recent activity:
- Modified: gitops/deployment.yaml
- Executed: kubectl apply
- Agent: gitops-operator
```

---

### Comandos de Spec-Kit

El framework Spec-Kit proporciona un workflow estructurado de idea → implementación.

#### `/speckit.init`
Inicializa Spec-Kit en el proyecto actual, creando/validando `project-context.json`.

**Cuándo usar:**
- Primera vez usando Spec-Kit en un proyecto
- Para validar configuración existente

**Ejemplo:**
```bash
/speckit.init
```

---

#### `/speckit.specify [spec-root] [descripción]`
Crea una especificación de feature con contexto del proyecto auto-llenado.

**Cuándo usar:**
- Inicio de una nueva feature
- Documentar requisitos

**Ejemplo:**
```bash
/speckit.specify spec-kit-auth Add OAuth2 authentication
```

**Lo que genera:**
- `specs/00N-oauth2-auth/spec.md` con template
- Contexto de proyecto pre-llenado (cluster, paths, etc.)
- User stories y requisitos funcionales

---

#### `/speckit.plan [spec-root] [spec-id]`
Genera plan de implementación con clarificación automática integrada.

**Cuándo usar:**
- Después de crear la especificación
- Antes de generar tareas

**Ejemplo:**
```bash
/speckit.plan spec-kit-auth 003-oauth2-auth
```

**Lo que genera:**
- `plan.md` - Plan técnico detallado
- `data-model.md` - Modelo de datos
- `contracts/` - Contratos de API
- Preguntas de clarificación (si hay ambigüedades)

---

#### `/speckit.tasks [spec-root] [spec-id]`
Genera lista de tareas enriquecidas con metadata inline.

**Cuándo usar:**
- Después de completar el plan
- Antes de implementar

**Ejemplo:**
```bash
/speckit.tasks spec-kit-auth 003-oauth2-auth
```

**Lo que genera:**
- `tasks.md` con metadata completa:
  - Agent asignado
  - Tier de seguridad
  - Tags de categoría
  - Confidence score
- Validación de cobertura automática
- Gate si hay issues críticos

---

#### `/speckit.implement [spec-root] [spec-id]`
Ejecuta las tareas usando agentes especializados.

**Cuándo usar:**
- Después de generar tareas
- Para implementar automáticamente

**Ejemplo:**
```bash
/speckit.implement spec-kit-auth 003-oauth2-auth
```

**Lo que hace:**
- Lee tasks.md enriquecido
- Invoca agentes apropiados por tarea
- T2/T3 tasks → análisis automático pre-ejecución
- Approval gates cuando necesario
- Genera código, tests, documentación

---

#### `/speckit.add-task [spec-root] [spec-id]`
Agrega una tarea ad-hoc durante la implementación.

**Cuándo usar:**
- Durante implementación
- Para tareas no previstas en el plan

**Ejemplo:**
```bash
/speckit.add-task spec-kit-auth 003-oauth2-auth
```

**Pregunta interactivamente:**
- Descripción de la tarea
- ID de la tarea
- Fase de implementación
- Dependencias

---

#### `/speckit.analyze-task [spec-root] [spec-id] [task-id]`
Análisis profundo de una tarea específica (auto-triggered para T2/T3).

**Cuándo usar:**
- Para tareas de alto riesgo
- Antes de ejecutar operaciones T3

**Ejemplo:**
```bash
/speckit.analyze-task spec-kit-auth 003-oauth2-auth T055
```

**Lo que analiza:**
- Riesgos potenciales
- Dependencias
- Impacto en sistema
- Recomendaciones de ejecución

---

## 🚀 Uso General

### Sintaxis Básica

```bash
/comando [argumentos]
```

### Características Comunes

**Autocompletado:**
Claude Code sugiere comandos disponibles al escribir `/`

**Help inline:**
Todos los comandos soportan ayuda contextual si se invocan sin argumentos

**Validación:**
Los comandos validan argumentos y dan feedback claro si falta información

### Diferencia vs Lenguaje Natural

| Lenguaje Natural | Comando Slash |
|------------------|---------------|
| "Guarda el contexto actual con el nombre deploy-v2" | `/save-session deploy-v2` |
| "Analiza los logs del sistema" | `/gaia Analiza logs` |
| "Crea una spec para autenticación OAuth" | `/speckit.specify auth-spec Add OAuth2` |

**Ventajas de comandos slash:**
- ✅ Más rápido
- ✅ Sintaxis consistente
- ✅ Invocación directa de herramientas
- ✅ Menos ambiguo

**Cuándo usar lenguaje natural:**
- Preguntas exploratorias
- Diagnóstico de problemas
- Consultas complejas

## 🔧 Características Técnicas

### Estructura de un Comando

Cada comando es un archivo Markdown en `commands/[nombre].md` con frontmatter:

```markdown
---
name: comando
description: Breve descripción
usage: Sintaxis de uso
---

# Comando

[Instrucciones detalladas para el orquestador]
```

### Comandos Disponibles

```
commands/
├── gaia.md                  (~100 líneas)
├── save-session.md          (~80 líneas)
├── restore-session.md       (~75 líneas)
├── session-status.md        (~60 líneas)
├── speckit.init.md          (~90 líneas)
├── speckit.specify.md       (~120 líneas)
├── speckit.plan.md          (~150 líneas)
├── speckit.tasks.md         (~140 líneas)
├── speckit.implement.md     (~180 líneas)
├── speckit.add-task.md      (~70 líneas)
└── speckit.analyze-task.md  (~85 líneas)
```

**Total:** 11 comandos (1 meta + 3 session + 7 speckit)

## 📖 Referencias

**Documentación relacionada:**
- [Orchestration Workflow](../config/orchestration-workflow.md) - Cómo el orquestador procesa comandos
- [Spec-Kit Framework](../speckit/README.md) - Detalles completos de Spec-Kit
- [Gaia Agent](../agents/gaia.md) - El meta-agente
- [Session Management](../tools/5-task-management/README.md) - Sistema de sesiones

**Herramientas subyacentes:**
- Session manager: `tools/5-task-management/session-manager.py`
- Task manager: `tools/5-task-management/task_manager.py`
- Spec-Kit scripts: `speckit/scripts/`

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Total de comandos:** 11 (1 meta, 3 sesión, 7 spec-kit)  
**Mantenido por:** Gaia (meta-agent)

