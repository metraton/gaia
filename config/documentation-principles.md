# Principios para Escribir Documentación en Gaia-Ops

**[🇺🇸 English version](documentation-principles.en.md)**

Este documento define los estándares y principios que Gaia (el meta-agente) debe seguir al crear y actualizar documentación en el ecosistema gaia-ops.

## 📚 Visión General

La documentación en gaia-ops está orientada a **humanos primero**. Debe ser clara, accesible y explicativa, permitiendo que cualquier persona entienda el propósito y funcionamiento del sistema sin necesidad de experiencia previa.

---

## 🎯 Principios Fundamentales

### 1. Lenguaje Simple y Directo

**✅ HACER:**
- Usar oraciones cortas y claras
- Explicar conceptos técnicos con analogías sencillas
- Evitar jerga innecesaria
- Escribir como si estuvieras explicando a un colega

**❌ EVITAR:**
- Lenguaje rebuscado o excesivamente formal
- Acrónimos sin explicación
- Verbosidad innecesaria (ser conciso pero completo)
- Suposiciones sobre conocimiento previo

**Ejemplo:**

❌ Malo: "El orquestador utiliza un paradigma de enrutamiento semántico basado en embeddings vectoriales para determinar el agente óptimo mediante análisis de similitud coseno."

✅ Bueno: "El orquestador analiza tu pregunta y la envía al agente más apropiado. Por ejemplo, si preguntas sobre Terraform, te conecta con `terraform-architect`."

---

### 2. Explicativo Sobre Descriptivo

**✅ HACER:**
- Explicar **qué hace** el componente
- Explicar **por qué existe** 
- Explicar **cómo funciona** (flujo básico)
- Incluir ejemplos prácticos

**❌ EVITAR:**
- Listas de archivos sin contexto
- Descripciones técnicas sin explicación
- Estructuras de carpetas estáticas (cambian frecuentemente)

**Ejemplo:**

❌ Malo:
```markdown
## Estructura
- agent_router.py
- context_provider.py
- approval_gate.py
```

✅ Bueno:
```markdown
## Componentes Principales

Este sistema tiene tres piezas clave que trabajan juntas:

**agent_router.py** - El Enrutador
- Analiza tu pregunta y decide qué agente especialista debe responderla
- Funciona como un recepcionista que te dirige al departamento correcto

**context_provider.py** - El Proveedor de Contexto
- Recopila información relevante antes de invocar un agente
- Es como preparar un expediente con todo lo necesario antes de una reunión

**approval_gate.py** - La Puerta de Aprobación
- Solicita tu confirmación antes de operaciones importantes
- Como un doble-check de seguridad antes de cambios críticos
```

---

### 3. Diagramas de Flujo Simples (ASCII)

Todos los READMEs que explican procesos o arquitectura **DEBEN incluir** un diagrama ASCII simple que muestre:

#### A. Flujo de Arquitectura (Componentes)
Muestra cómo interactúan los componentes del sistema.

**Formato:**
```
Usuario
  ↓
[Componente A]
  ↓
[Componente B] → [Componente C]
  ↓
Resultado
```

#### B. Flujo de Historia de Uso (Ejemplo Real)
Muestra cómo fluye una solicitud real a través del sistema.

**Formato:**
```
Ejemplo: "Despliega el servicio auth en producción"

1. Usuario hace pregunta
   ↓
2. [Orquestador] analiza la solicitud
   ↓
3. [Router] identifica → "gitops-operator"
   ↓
4. [Context Provider] recopila info del cluster
   ↓
5. [Agent] genera plan de despliegue
   ↓
6. [Approval Gate] solicita confirmación → Usuario aprueba
   ↓
7. [Agent] ejecuta: kubectl apply
   ↓
8. [Agent] verifica: kubectl get pods
   ↓
9. Resultado: "✅ Servicio desplegado exitosamente"
```

**Características de los diagramas:**
- ✅ Simples (máximo 10-12 pasos)
- ✅ Usan símbolos básicos: `↓`, `→`, `[]`, `()`
- ✅ Incluyen texto descriptivo
- ✅ Muestran un ejemplo real y concreto
- ❌ No son complejos ni usan ASCII art elaborado

---

### 4. Estructura Consistente de README

Todos los READMEs deben seguir esta estructura (adaptable según el contexto):

```markdown
# [Título del Componente]

**[🇺🇸 English version](README.en.md)**

[1-2 oraciones de qué hace este componente]

## 🎯 Propósito

[Explicar por qué existe este componente y qué problema resuelve]

## 🔄 Cómo Funciona

### Flujo de Arquitectura

[Diagrama ASCII de componentes]

### Flujo de Ejemplo

[Diagrama ASCII con historia de uso real]

## 📋 Componentes Principales

[Explicar cada componente importante con analogías]

## 🚀 Uso

[Ejemplos prácticos de cómo se usa]

## 🔧 Características Técnicas

[Detalles técnicos relevantes, pero explicados claramente]

## 📖 Referencias

[Links a documentación relacionada]
```

**Notas:**
- ❌ **NO incluir** "Estructura de Carpetas" (cambia frecuentemente)
- ✅ **SÍ incluir** diagramas de flujo
- ✅ **SÍ incluir** ejemplos reales y concretos
- ✅ **SÍ explicar** con analogías cuando sea posible

---

### 5. Idioma y Versiones

**Idioma por defecto: Español**
- Todos los READMEs principales en español (`README.md`)
- Inglés como versión alternativa (`README.en.md`)

**Inglés Simple:**
- Al crear `README.en.md`, usar inglés accesible
- No asumir que el lector es hablante nativo de inglés
- Usar vocabulario directo y estructuras gramaticales simples
- Evitar modismos o expresiones coloquiales

**Ejemplos:**

❌ Inglés complejo: "The orchestrator leverages a sophisticated multi-tiered routing paradigm..."

✅ Inglés simple: "The orchestrator uses a smart routing system..."

---

### 6. Emojis como Navegación Visual

Usar emojis para mejorar la navegación y escaneo del documento:

| Emoji | Uso |
|-------|-----|
| 🎯 | Propósito / Objetivo |
| 🔄 | Flujo / Proceso |
| 📋 | Lista / Componentes |
| 🚀 | Uso / Cómo Empezar |
| 🔧 | Detalles Técnicos |
| ⚡ | Importante / Nota |
| ✅ | Buena Práctica |
| ❌ | Mala Práctica |
| 📖 | Referencias / Documentación |
| 🇺🇸/🇪🇸 | Versiones de idioma |

**Nota:** No abusar de emojis. Usarlos solo para secciones principales.

---

### 7. Ejemplos Concretos Sobre Conceptos Abstractos

**✅ HACER:**
- Incluir ejemplos reales de uso
- Mostrar comandos exactos
- Incluir salidas esperadas
- Usar casos de uso específicos

**❌ EVITAR:**
- Explicaciones puramente conceptuales
- Pseudo-código sin contexto
- "Ejemplo genérico" que no es práctico

**Ejemplo:**

❌ Malo:
```markdown
## Uso
Ejecute el router con un prompt y obtendrá el nombre del agente.
```

✅ Bueno:
```markdown
## Uso

Pregunta al router qué agente debe manejar tu solicitud:

\```bash
python3 agent_router.py --prompt "Despliega auth-service en prod"
# Output: gitops-operator
\```

Otro ejemplo - troubleshooting en GCP:

\```bash
python3 agent_router.py --prompt "¿Por qué está fallando el cluster GKE?"
# Output: cloud-troubleshooter
\```
```

---

### 8. Actualización Continua

**Responsabilidad de Gaia:**
- Revisar y actualizar READMEs cuando el código cambia
- Detectar inconsistencias entre documentación y código
- Proponer mejoras basadas en feedback y uso
- Mantener ejemplos actualizados

**Triggers para actualización:**
- Cambio en funcionalidad de componentes
- Nuevos agentes o herramientas añadidos
- Feedback de usuarios sobre claridad
- Cambios en estructura del proyecto

---

## 🛠️ Guía Práctica para Gaia

Cuando Gaia cree o actualice un README:

### Paso 1: Entender el Componente
1. Leer el código fuente
2. Identificar funcionalidad principal
3. Identificar dependencias y relaciones
4. Encontrar ejemplos de uso en el código

### Paso 2: Crear Diagrama de Flujo
1. Dibujar flujo de arquitectura (componentes)
2. Crear flujo de ejemplo (historia de uso)
3. Verificar que sea simple (max 10-12 pasos)
4. Asegurar que usa ASCII básico

### Paso 3: Escribir Contenido
1. Empezar con explicación de 1-2 líneas
2. Explicar **propósito** (por qué existe)
3. Explicar **funcionamiento** (cómo trabaja)
4. Incluir diagramas de flujo
5. Listar componentes con explicaciones
6. Añadir ejemplos de uso concretos
7. Agregar detalles técnicos necesarios

### Paso 4: Revisar Calidad
- [ ] ¿Lenguaje simple y directo?
- [ ] ¿Incluye diagramas de flujo?
- [ ] ¿Tiene ejemplos concretos?
- [ ] ¿Usa analogías para conceptos complejos?
- [ ] ¿NO tiene listas de estructura de carpetas?
- [ ] ¿Sigue estructura consistente?
- [ ] ¿Usa emojis para navegación?

### Paso 5: Crear Versión en Inglés
1. Traducir contenido al inglés
2. Usar vocabulario simple
3. Evitar modismos
4. Mantener estructura idéntica
5. Guardar como `README.en.md`

---

## 📖 Referencias

**Documentación relacionada:**
- [Agent Catalog](agent-catalog.md) - Lista completa de agentes
- [Orchestration Workflow](orchestration-workflow.md) - Flujo del orquestador
- [Git Standards](git-standards.md) - Estándares de commits

**Agente responsable:**
- **Gaia** (`agents/gaia.md`) - Meta-agente encargado de la documentación

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Mantenido por:** Gaia (meta-agent)

