# Agentes Especialistas de Gaia-Ops

**[🇺🇸 English version](README.en.md)**

Los agentes son especialistas de IA que manejan tareas específicas en tu infraestructura DevOps. Cada agente es experto en un dominio particular y trabaja de forma coordinada con el orquestador principal.

## 🎯 Propósito

Los agentes permiten dividir el trabajo complejo en especialidades manejables. En lugar de tener un solo sistema que intente hacerlo todo, cada agente se enfoca en lo que mejor sabe hacer - como tener un equipo de expertos en lugar de un generalista.

**Problema que resuelve:** Las tareas DevOps son diversas y complejas. Un agente único no puede ser experto en todo (Terraform, Kubernetes, GCP, AWS, aplicaciones). Los agentes especialistas permiten profundidad de conocimiento en cada área.

## 🔄 Cómo Funciona

### Flujo de Arquitectura

```
Usuario envía pregunta
        ↓
[Orquestador (CLAUDE.md)]
        ↓
[Agent Router] ← analiza la pregunta
        ↓
   Selecciona agente apropiado
        ↓
    ┌───┴───┬───────┬────────┬─────────┬────────┐
    ↓       ↓       ↓        ↓         ↓        ↓
[terraform] [gitops] [gcp]  [aws]  [devops]  [gaia]
 architect  operator troubl. troubl. developer meta-agent
    ↓       ↓       ↓        ↓         ↓        ↓
    └───┬───┴───────┴────────┴─────────┴────────┘
        ↓
[Context Provider] ← proporciona información relevante
        ↓
Agente ejecuta tarea
        ↓
Resultado al usuario
```

### Flujo de Ejemplo Real

```
Ejemplo: "Despliega el servicio auth en el cluster de producción"

1. Usuario hace la pregunta
   ↓
2. [Orquestador] recibe la solicitud
   ↓
3. [Agent Router] analiza palabras clave:
   - "despliega" → operación de deployment
   - "servicio" → aplicación en Kubernetes
   - "cluster" → GitOps
   ↓
4. Router selecciona → **gitops-operator**
   ↓
5. [Context Provider] prepara información:
   - Namespace actual
   - Releases existentes
   - Configuración del cluster
   ↓
6. [gitops-operator] recibe contexto y pregunta
   ↓
7. Agente genera plan:
   - Actualizar deployment.yaml
   - Incrementar versión de imagen
   - Aplicar con kubectl
   ↓
8. [Approval Gate] pide confirmación (es operación T3)
   - Muestra cambios propuestos
   - Usuario aprueba ✅
   ↓
9. [gitops-operator] ejecuta:
   - kubectl apply -f deployment.yaml
   - kubectl rollout status deployment/auth
   ↓
10. Verifica éxito:
    - Pods running: 3/3
    - Health checks: OK
    ↓
11. Reporta resultado: "✅ auth desplegado exitosamente en producción"
```

## 📋 Agentes Disponibles

### 1. terraform-architect 🏗️
**Experto en:** Infraestructura como código

Maneja todo lo relacionado con Terraform y Terragrunt. Es como el arquitecto que diseña y construye los cimientos de tu infraestructura cloud.

**Cuándo se usa:**
- Crear clusters GKE
- Configurar VPCs y redes
- Gestionar buckets de almacenamiento
- Configurar permisos IAM

**Ejemplo de pregunta:**
- "Crea un nuevo cluster GKE para el ambiente de staging"
- "Agrega una subnet adicional en us-east1"

**Tiers:** T0 (leer), T1 (validar), T2 (planear), T3 (aplicar)

---

### 2. gitops-operator ⚙️
**Experto en:** Kubernetes y despliegues

Maneja aplicaciones en Kubernetes, deployments, services y todo lo relacionado con GitOps. Es como el operador que mantiene las aplicaciones funcionando en los clusters.

**Cuándo se usa:**
- Desplegar servicios
- Actualizar deployments
- Configurar ingress
- Escalar aplicaciones

**Ejemplo de pregunta:**
- "Despliega la versión 1.2.3 del backend"
- "Escala el servicio auth a 5 réplicas"

**Tiers:** T0 (leer), T1 (validar), T2 (planear), T3 (aplicar)

---

### 3. gcp-troubleshooter 🔍
**Experto en:** Diagnóstico de Google Cloud Platform

Identifica problemas y recopila información sobre recursos en GCP. Es como el detective que investiga qué está pasando en la nube.

**Cuándo se usa:**
- Diagnosticar errores en GCP
- Revisar logs de Cloud Logging
- Verificar estado de recursos
- Analizar permisos IAM

**Ejemplo de pregunta:**
- "¿Por qué está fallando el cluster?"
- "Muestra los logs del servicio auth en las últimas 2 horas"

**Tiers:** T0 únicamente (solo lectura, no hace cambios)

---

### 4. aws-troubleshooter 🔍
**Experto en:** Diagnóstico de Amazon Web Services

Similar a gcp-troubleshooter pero para AWS. Diagnostica problemas y recopila información sobre recursos en Amazon Web Services.

**Cuándo se usa:**
- Diagnosticar errores en AWS
- Revisar logs de CloudWatch
- Verificar estado de recursos EC2/EKS
- Analizar políticas IAM

**Ejemplo de pregunta:**
- "¿Por qué está fallando el EKS cluster?"
- "Muestra métricas de la instancia EC2"

**Tiers:** T0 únicamente (solo lectura)

---

### 5. devops-developer 💻
**Experto en:** Código de aplicaciones y CI/CD

Trabaja con código de aplicaciones, Dockerfiles, builds y tests. Es como el desarrollador que asegura que el código funcione correctamente.

**Cuándo se usa:**
- Crear/modificar Dockerfiles
- Configurar npm/yarn builds
- Escribir scripts de automatización
- Configurar CI pipelines

**Ejemplo de pregunta:**
- "Optimiza el Dockerfile del backend"
- "Agrega tests unitarios al servicio"

**Tiers:** T0 (leer), T1 (validar), T2 (probar builds)

---

### 6. Gaia 🧠
**Experto en:** El propio sistema de agentes

El meta-agente que entiende cómo funciona todo el sistema de orquestación. Es como el arquitecto de sistemas que optimiza y documenta el funcionamiento de los propios agentes.

**Cuándo se usa:**
- Analizar logs del sistema
- Optimizar routing de agentes
- Mejorar documentación
- Diagnosticar problemas del orquestador

**Ejemplo de pregunta:**
- "¿Por qué falló el routing en este caso?"
- "Analiza la precisión del agent router"

**Tiers:** T0-T2 (análisis y propuestas, no ejecuta cambios)

## 🚀 Cómo se Invocan los Agentes

### Invocación Automática (Recomendado)

El orquestador analiza tu pregunta y automáticamente selecciona el agente apropiado:

```bash
# En Claude Code, simplemente pregunta:
"Despliega auth-service versión 1.2.3"
# → El orquestador invoca gitops-operator automáticamente
```

### Invocación Manual (Avanzado)

Para casos específicos donde quieres invocar un agente directamente:

```bash
# Usar el comando Task
Task(
  subagent_type="gitops-operator",
  description="Deploy auth service",
  prompt="Deploy auth-service version 1.2.3 to production cluster"
)
```

## 🔧 Características Técnicas

### Estructura de un Agente

Cada agente es un archivo Markdown (`agente.md`) con estas secciones:

```markdown
---
name: agent-name
description: Brief description
tools: List of allowed tools
model: Model configuration
---

# Agent Name

[Comprehensive instructions for the agent]
```

### Tiers de Seguridad

Los agentes operan en diferentes niveles de seguridad:

| Tier | Descripción | Requiere Aprobación |
|------|-------------|---------------------|
| **T0** | Solo lectura (get, describe, list) | No |
| **T1** | Validación (validate, dry-run, test) | No |
| **T2** | Planificación (plan, simulate) | No |
| **T3** | Ejecución (apply, create, delete) | **Sí** ✅ |

**Nota importante:** Las operaciones T3 SIEMPRE requieren aprobación explícita del usuario a través del Approval Gate.

### Routing Inteligente

El sistema usa múltiples técnicas para seleccionar el agente correcto:

1. **Palabras clave:** Términos específicos del dominio
2. **Semantic matching:** Similitud semántica usando embeddings
3. **Context awareness:** Considera el contexto del proyecto

**Precisión actual:** ~92.7% (basado en tests)

## 📖 Referencias

**Documentación relacionada:**
- [Orchestration Workflow](../config/orchestration-workflow.md) - Cómo fluye una solicitud
- [Agent Catalog](../config/agent-catalog.md) - Detalles completos de cada agente
- [Context Contracts](../config/context-contracts.md) - Qué información recibe cada agente
- [Agent Router](../tools/1-routing/agent_router.py) - Código del routing

**Archivos de agentes:**
```
agents/
├── terraform-architect.md    (~800 líneas)
├── gitops-operator.md        (~750 líneas)
├── gcp-troubleshooter.md     (~600 líneas)
├── aws-troubleshooter.md     (~600 líneas)
├── devops-developer.md       (~500 líneas)
└── gaia.md                   (~1650 líneas)
```

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Total de agentes:** 6 especialistas  
**Mantenido por:** Gaia (meta-agent)

