# Comandos Slash de Gaia-Ops

**[English version](README.en.md)**

Los comandos slash son atajos rapidos que te permiten invocar funcionalidades especificas del sistema directamente. Son como accesos directos del teclado para tareas comunes.

## Proposito

Los comandos slash proporcionan una forma rapida y consistente de acceder a funcionalidades avanzadas sin necesidad de escribir solicitudes completas en lenguaje natural.

**Problema que resuelve:** Algunas tareas requieren invocacion directa de herramientas especificas. En lugar de describir verbosamente lo que quieres hacer, simplemente usas un comando slash.

## Como Funciona

### Flujo de Arquitectura

```
Usuario escribe /comando
        |
[Claude Code] detecta el patron /
        |
[Command Handler] carga el archivo .md del comando
        |
[Orquestador] ejecuta instrucciones del comando
        |
Resultado al usuario
```

## Comandos Disponibles

### Comandos de Meta-Analisis

#### `/gaia`
Invoca a Gaia, el meta-agente que analiza y optimiza el sistema de orquestacion.

**Cuando usar:**
- Analizar logs del sistema
- Investigar problemas de routing
- Optimizar workflows
- Mejorar documentacion

**Ejemplo:**
```bash
/gaia Analiza por que fallo el routing en las ultimas 10 solicitudes
```

**Salida esperada:**
- Analisis detallado de los eventos
- Identificacion de patrones
- Recomendaciones de mejora

---

### Comandos de Spec-Kit

El framework Spec-Kit proporciona un workflow estructurado de idea -> implementacion.

#### `/speckit.init`
Inicializa Spec-Kit en el proyecto actual, creando/validando `project-context.json`.

**Cuando usar:**
- Primera vez usando Spec-Kit en un proyecto
- Para validar configuracion existente

**Ejemplo:**
```bash
/speckit.init
```

---

#### `/speckit.specify [spec-root] [descripcion]`
Crea una especificacion de feature con contexto del proyecto auto-llenado.

**Cuando usar:**
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
Genera plan de implementacion con clarificacion automatica integrada.

**Cuando usar:**
- Despues de crear la especificacion
- Antes de generar tareas

**Ejemplo:**
```bash
/speckit.plan spec-kit-auth 003-oauth2-auth
```

**Lo que genera:**
- `plan.md` - Plan tecnico detallado
- `data-model.md` - Modelo de datos
- `contracts/` - Contratos de API
- Preguntas de clarificacion (si hay ambiguedades)

---

#### `/speckit.tasks [spec-root] [spec-id]`
Genera lista de tareas enriquecidas con metadata inline.

**Cuando usar:**
- Despues de completar el plan
- Antes de implementar

**Ejemplo:**
```bash
/speckit.tasks spec-kit-auth 003-oauth2-auth
```

**Lo que genera:**
- `tasks.md` con metadata completa:
  - Agent asignado
  - Tier de seguridad
  - Tags de categoria
  - Confidence score
- Validacion de cobertura automatica
- Gate si hay issues criticos

---

#### `/speckit.implement [spec-root] [spec-id]`
Ejecuta las tareas usando agentes especializados.

**Cuando usar:**
- Despues de generar tareas
- Para implementar automaticamente

**Ejemplo:**
```bash
/speckit.implement spec-kit-auth 003-oauth2-auth
```

**Lo que hace:**
- Lee tasks.md enriquecido
- Invoca agentes apropiados por tarea
- T2/T3 tasks -> analisis automatico pre-ejecucion
- Approval gates cuando necesario
- Genera codigo, tests, documentacion

---

#### `/speckit.add-task [spec-root] [spec-id]`
Agrega una tarea ad-hoc durante la implementacion.

**Cuando usar:**
- Durante implementacion
- Para tareas no previstas en el plan

**Ejemplo:**
```bash
/speckit.add-task spec-kit-auth 003-oauth2-auth
```

**Pregunta interactivamente:**
- Descripcion de la tarea
- ID de la tarea
- Fase de implementacion
- Dependencias

---

#### `/speckit.analyze-task [spec-root] [spec-id] [task-id]`
Analisis profundo de una tarea especifica (auto-triggered para T2/T3).

**Cuando usar:**
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
- Recomendaciones de ejecucion

---

## Uso General

### Sintaxis Basica

```bash
/comando [argumentos]
```

### Caracteristicas Comunes

**Autocompletado:**
Claude Code sugiere comandos disponibles al escribir `/`

**Help inline:**
Todos los comandos soportan ayuda contextual si se invocan sin argumentos

**Validacion:**
Los comandos validan argumentos y dan feedback claro si falta informacion

### Diferencia vs Lenguaje Natural

| Lenguaje Natural | Comando Slash |
|------------------|---------------|
| "Analiza los logs del sistema" | `/gaia Analiza logs` |
| "Crea una spec para autenticacion OAuth" | `/speckit.specify auth-spec Add OAuth2` |

**Ventajas de comandos slash:**
- Mas rapido
- Sintaxis consistente
- Invocacion directa de herramientas
- Menos ambiguo

**Cuando usar lenguaje natural:**
- Preguntas exploratorias
- Diagnostico de problemas
- Consultas complejas

## Caracteristicas Tecnicas

### Estructura de un Comando

Cada comando es un archivo Markdown en `commands/[nombre].md` con frontmatter:

```markdown
---
name: comando
description: Breve descripcion
usage: Sintaxis de uso
---

# Comando

[Instrucciones detalladas para el orquestador]
```

### Comandos Disponibles

```
commands/
├── speckit.init.md
├── speckit.specify.md
├── speckit.plan.md
├── speckit.tasks.md
├── speckit.implement.md
├── speckit.add-task.md
└── speckit.analyze-task.md
```

**Total:** 7 comandos speckit

> **Nota:** El meta-agente Gaia se invoca directamente via el agent `gaia` (ver [agents/gaia.md](../agents/gaia.md)), no como comando slash.

## Referencias

**Documentacion relacionada:**
- [Spec-Kit Framework](../speckit/README.md) - Detalles completos de Spec-Kit
- [Gaia Agent](../agents/gaia.md) - El meta-agente
- [Episodic Memory](../tools/memory/episodic.py) - Sistema de memoria de contexto
- [Config](../config/) - Configuracion del sistema

**Herramientas subyacentes:**
- Context provider: `tools/context/context_provider.py`
- Episodic memory: `tools/memory/episodic.py`
- Spec-Kit scripts: `speckit/scripts/`

---

**Version:** 2.0.0  
**Ultima actualizacion:** 2025-12-06  
**Total de comandos:** 7 spec-kit
**Mantenido por:** Gaia (meta-agent)
