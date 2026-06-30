# AC-8 — Mapeo en papel: soportar Codex como host de Gaia

**Brief #88** — "Desacoplar la lógica de Gaia de Claude Code"
**Plan #12 / M3 / T3.1** — validación en papel contra Codex
**Tipo de evidencia:** `artifact` (documento; no cambia código)
**Estado de partida:** M1 (commit `19d0ac9`) + M2 (commit `0b9dedd`) cerrados. Suite verde (5313 passed, 42 skipped, 0 failures).

---

## Tesis a demostrar

> Sumar un host nuevo de la familia *hook-interception* (aquí: **Codex**) es **escribir un adapter + declarar capacidades**. NO se modifica la lógica de negocio (el "core").

Este documento toma cada uno de los seams de desacople que existen tras M1+M2 (todos bajo `hooks/adapters/`) y los mapea contra Codex en tres ejes:

1. **Abstracción de Gaia** que un host nuevo debe satisfacer (el contrato, nombrado en términos de Gaia, NO de Claude Code).
2. **Cómo lo cumpliría Codex** concretamente, dentro de su propio adapter.
3. **Qué capacidades** declararía vía `capabilities()` y cuáles **degradaría** (`CapabilityDegradation`) si Codex carece de ellas.

Regla de oro del mapeo: el contrato conceptual **no depende de ningún tipo de Claude Code**. Lo que Codex traduce es su JSON/entorno propio *hacia* los tipos agnósticos de Gaia (`HookEvent`, `ConsentRequest`, `HostCapability`, …). Si el mapeo de un seam exige nombrar una API de CC en el core, eso es un acoplamiento residual y se reporta como gap honesto (sección final).

### Premisa de familia (no es un gap)

Codex pertenece a la misma familia que Claude Code: **hook-interception**. Gaia se inserta como un proceso de hook que (a) recibe un evento por `stdin`, (b) decide, y (c) responde por un canal que el host respeta (decisión estructurada y/o exit code). Todo el mapeo de abajo asume esa familia. Un host de **otra** familia (p. ej. uno sin punto de intercepción pre-tool) quedaría fuera del alcance de este adapter y requeriría una abstracción nueva — eso es trabajo de otro plan, no un fallo del desacople actual.

---

## Resumen ejecutivo (tabla de cobertura)

| Seam | Archivo | AC | Abstracción de Gaia | Capacidad(es) implicada(s) | ¿Codex la cubre nativo? |
|------|---------|----|--------------------|----------------------------|-------------------------|
| Session ID del host | `host_session.py` | AC-1 | "dame el id de sesión del host" | — (env-var encapsulado) | Sí (otra env-var) |
| Tipos agnósticos | `types.py` | AC-3 / AC-6 | vocabulario CLI-agnóstico (`ConsentRequest`, `HostCapability`, `CapabilityDegradation`) | todas | N/A (es el vocabulario) |
| Consent | `base.py::request_consent` + `claude_code.py` | AC-3 | "obtén el consentimiento del usuario para esta operación" | `INTERACTIVE_CONSENT`, `OUT_OF_BAND_APPROVAL`, `STRUCTURED_PERMISSION_DECISION`, `UPDATED_INPUT` | Parcial — degrada |
| Transcript del host | `host_transcript.py` | AC-4 | "itera entradas `(role, content)` del transcript" | `TRANSCRIPT_ACCESS` | Probable — formato propio o degrada |
| Registry / factory | `registry.py` | AC-5 / AC-7 | "construye el adapter del host activo" | — (un `register_adapter`) | Sí (una línea) |
| Capacidades + degradación | `base.py::capabilities/supports/degrade_when_missing` + `types.py` | AC-6 | "declara qué puede el host; degrada lo ausente de forma declarada" | el mecanismo mismo | Sí (declara su subconjunto) |
| Inyección de contexto | `base.py::format_context_response` (transversal) | AC-6 | "inyecta contexto en sesión a tiempo de hook" | `CONTEXT_INJECTION` | Parcial — degrada |

Las seis capacidades de `HostCapability` están cubiertas por el mapeo. Claude Code declara las seis hoy; Codex declararía el subconjunto que soporte y degradaría el resto. **Ningún archivo del core cambia en ninguna fila.**

---

## Seam 1 — Session ID del host (`host_session.py`, AC-1)

### (1) Abstracción de Gaia que debe satisfacer un host nuevo

El core nunca lee `os.environ["…"]` para conocer la sesión. Pregunta a una función agnóstica: *"¿cuál es el id de sesión del host?"* — con dos variantes:

- `read_host_session_id(default)` — id de sesión, o un default si el host no lo expone al subproceso de hook.
- `get_or_create_host_session_id()` — id de sesión, generando uno estable (timestamp + PID) si está ausente.

El nombre de la env-var concreta (`CLAUDE_SESSION_ID`) **vive solo en este módulo del adapter**. Es el único acoplamiento, y está confinado. La abstracción que un host debe satisfacer: *exponer un identificador de sesión que Gaia pueda leer de forma estable durante el ciclo de vida del hook.*

### (2) Cómo lo cumpliría Codex

Codex propaga su propia sesión por su propia env-var (hipotética `CODEX_SESSION_ID`, o el canal que Codex use). El `CodexAdapter` (o un módulo hermano `host_session` específico de Codex) confina ese nombre exactamente igual que `host_session.py` confina `CLAUDE_SESSION_ID`:

- Si Codex exporta la sesión al subproceso de hook → se lee de ahí.
- Si no la exporta → cae en la misma rama de `get_or_create_host_session_id`: genera un id sintético estable (timestamp + PID). Esta rama **ya es agnóstica** y funciona idéntica para cualquier host.

Además, el patrón ya recomendado (preferir el `session_id` del `HookEvent` parseado, usar la env-var solo como fallback) reduce la dependencia del nombre de la env-var a un mínimo: en la mayoría de los casos Codex provee `session_id` dentro del payload de stdin, que su `parse_event` normaliza a `HookEvent.session_id`.

### (3) Capacidades / degradación

No hay una `HostCapability` para "tener id de sesión": es un dato, no una capacidad opcional, y el `get_or_create` garantiza un valor para *cualquier* host. **No se degrada nada.** Sumar Codex aquí = un módulo que confina el nombre de su env-var. Cero cambios en el core.

---

## Seam 2 — Tipos agnósticos (`types.py`, AC-3 / AC-6)

### (1) Abstracción de Gaia

`types.py` es el **vocabulario** con el que el core habla. No es un seam que un host "implemente"; es el lenguaje que el adapter del host **traduce hacia y desde**. Los tipos relevantes para Codex:

- `HookEvent` — evento normalizado (`event_type`, `session_id`, `payload`, `channel`). El adapter de cualquier host produce esto desde su JSON crudo.
- `HookEventType` — enum de eventos. El core razona sobre `PRE_TOOL_USE`, `POST_TOOL_USE`, `SUBAGENT_STOP`, etc., no sobre nombres de eventos de un host.
- `ConsentRequest` — descripción agnóstica de "esto necesita consentimiento" (opera con `operation`, `kind`, `reason`, `tier`, `approval_id`, `updated_input`). Declara los *hechos*, nunca *cómo preguntar*.
- `HostCapability` — vocabulario para *preguntarle* a un host si puede algo, sin nombrarlo.
- `CapabilityDegradation` — el resultado declarado de esa pregunta cuando la capacidad falta.
- `ValidationResult`, `HookResponse` — el core produce el primero (agnóstico); el adapter construye el segundo (específico del host).

### (2) Cómo lo cumpliría Codex

Codex **no añade tipos nuevos** ni modifica estos. Su adapter:

- en `parse_event`, lee su JSON de stdin y construye un `HookEvent` con un `HookEventType` mapeado desde el nombre de evento de Codex;
- consume `ValidationResult` / `ConsentRequest` tal cual los emite el core;
- construye su propio `HookResponse` con la forma que Codex respeta (ver seam de consent).

### (3) Capacidades / degradación

`types.py` es donde vive el *mecanismo* de capacidades/degradación, no donde se declaran valores concretos. **Codex no toca este archivo.** Si Codex necesitara expresar un concepto que el vocabulario aún no nombra, eso sería una extensión del vocabulario del core — y por tanto un gap honesto (ver sección final). En el mapeo actual, todos los conceptos que Codex necesita ya están nombrados.

> **Observación sobre `HookEventType`:** el docstring del enum dice "All Claude Code hook events". Los *valores string* del enum (`"PreToolUse"`, `"SubagentStop"`, …) coinciden hoy con los nombres que usa Claude Code. Esto se analiza como posible acoplamiento residual nominal en la sección de gaps; no bloquea a Codex porque su `parse_event` puede mapear sus propios nombres de evento a estos miembros del enum.

---

## Seam 3 — Consent (`base.py::request_consent` + `claude_code.py`, AC-3)

### (1) Abstracción de Gaia

`HookAdapter.request_consent(request: ConsentRequest) -> HookResponse` es **el único punto** por el que el core pide consentimiento. El core entrega los *hechos* agnósticos (`ConsentRequest`) y **nunca nombra cómo el host pregunta**. Postcondiciones del contrato (de `base.py`):

- devuelve un `HookResponse` que *conduce* al host a obtener consentimiento (no permite en silencio ni bloquea permanentemente);
- si `approval_id` está seteado → la respuesta liga la decisión a ese identificador (flujo out-of-band);
- si `approval_id` es `None` → el host recoge consentimiento inline;
- si `updated_input` está seteado → la respuesta lo preserva a través del paso de consentimiento.

La clasificación de tiers, los grants y la validación del core quedan intactos: cambiar el flujo de consentimiento de un host es un cambio a *este método del adapter y nada más*.

### (2) Cómo lo cumpliría Codex

El `CodexAdapter.request_consent` traduce el `ConsentRequest` al mecanismo de consentimiento de Codex. Tres escenarios según lo que Codex ofrezca:

- **Codex tiene prompt de permiso inline estructurado** (caso ideal, equivale a la rama `approval_id is None` de Claude Code): emite su forma de "pregunta al usuario" preservando `updated_input`. Declara `INTERACTIVE_CONSENT` + `STRUCTURED_PERMISSION_DECISION` (+ `UPDATED_INPUT`).
- **Codex tiene flujo de aprobación out-of-band** (equivale a la rama `approval_id is not None`): emite una negación ligada al `approval_id`; el subagente reporta `APPROVAL_REQUEST`; el usuario aprueba; el grant se activa en el reintento. Declara `OUT_OF_BAND_APPROVAL`.
- **Codex solo expone un exit code** (sin decisión estructurada, sin prompt nativo): aquí entra la degradación — ver (3).

El punto clave: el core llama `request_consent(...)` sin saber cuál de estas formas existe. La mecánica vive **solo** en el adapter de Codex.

### (3) Capacidades / degradación

Capacidades implicadas: `INTERACTIVE_CONSENT`, `OUT_OF_BAND_APPROVAL`, `STRUCTURED_PERMISSION_DECISION`, `UPDATED_INPUT`.

- Si Codex **carece de `INTERACTIVE_CONSENT`** pero tiene `OUT_OF_BAND_APPROVAL`: el core, vía `degrade_when_missing(HostCapability.INTERACTIVE_CONSENT, fallback="out_of_band")`, recibe `available=False` y enruta por el flujo de approval-id. (Y viceversa si solo tiene el inline.)
- Si Codex **carece de `STRUCTURED_PERMISSION_DECISION`** (solo exit code): `request_consent` de Codex traduce la negación a un exit code que su host respeta como bloqueo, y declara la degradación con `fallback="deny"` para `STRUCTURED_PERMISSION_DECISION`. El consentimiento se vuelve "denegar por defecto, reintentar tras aprobación fuera de banda" — seguro por construcción, nunca un allow silencioso.
- Si Codex **carece de `UPDATED_INPUT`** (no puede aplicar input modificado de forma transparente, p. ej. footer-stripping): `degrade_when_missing(HostCapability.UPDATED_INPUT, fallback="deny")`. El core, al no poder garantizar que la modificación sobreviva, degrada a denegar la operación modificada en lugar de ejecutar input no-modificado. El `fallback` lo elige *el caller del core* y se devuelve como valor observable en `CapabilityDegradation` — no es un efecto secundario que el host deba recordar.

En los tres casos, sumar Codex = declarar el subconjunto en `capabilities()` y escribir `request_consent`. **El core no se ramifica sobre `if host == "codex"`**; se ramifica sobre `supports(capability)` / `degrade_when_missing(...)`, que son agnósticos.

---

## Seam 4 — Transcript del host (`host_transcript.py`, AC-4)

### (1) Abstracción de Gaia

El core nunca abre el archivo de transcript ni llama `json.loads`. Itera entradas normalizadas vía:

```
iter_transcript_entries(transcript_path) -> Iterator[(role, content)]
```

El **formato concreto** del transcript (en Claude Code: JSONL, una línea por mensaje, con `role`/`content` anidados bajo un campo `message`) vive **solo** en este módulo. El docstring del propio módulo lo dice explícitamente: *"Si un host futuro anuncia su transcript con otra forma (un único array JSON, otra anidación), solo cambia este módulo; los lectores en `modules/agents/transcript_reader.py` siguen iterando entradas normalizadas."* La abstracción que un host debe satisfacer: *exponer su transcript de subagente de modo que se pueda producir un stream uniforme de `(role, content)`.*

### (2) Cómo lo cumpliría Codex

El `CodexAdapter` provee su variante de `iter_transcript_entries` que entiende el formato de transcript de Codex:

- Si Codex también usa JSONL pero con otra anidación (p. ej. `role`/`content` al tope, sin `message`) → el `fallback` ya existente en el módulo CC (`entry.get("message", entry)`) cubre la forma plana; aun así, lo limpio es un módulo `host_transcript` propio de Codex que confine el detalle.
- Si Codex usa un **único array JSON** o un formato distinto → solo cambia ese módulo de Codex; emite el mismo `Iterator[(role, content)]`.
- Las salvaguardas (expansión de `~`, check de existencia, skip silencioso de líneas mal formadas, "nunca crashear un hook") son parte del *contrato de comportamiento* que el módulo de Codex también debe honrar.

### (3) Capacidades / degradación

Capacidad implicada: `TRANSCRIPT_ACCESS`.

- Codex **expone transcript** → declara `TRANSCRIPT_ACCESS`; provee su `iter_transcript_entries`. El análisis de contrato / detección de anomalías del core funciona igual.
- Codex **no expone transcript** (o no en una forma legible) → **no** declara `TRANSCRIPT_ACCESS`. El core que necesita post-inspección llama `degrade_when_missing(HostCapability.TRANSCRIPT_ACCESS, fallback="skip", reason="codex no persiste transcript de subagente")`. El análisis post-hoc se omite de forma declarada y observable; no se intenta abrir un archivo que no existe ni se crashea.

Sumar Codex = (si soporta) un módulo de transcript propio + declarar la capacidad; (si no) simplemente no declararla y dejar que el core degrade.

---

## Seam 5 — Registry / factory (`registry.py`, AC-5 / AC-7)

### (1) Abstracción de Gaia

`registry.py` es **el único punto de construcción** del `HookAdapter`. Todos los entry points (`pre_tool_use`, `post_tool_use`, `stop_hook`, `subagent_start`, `subagent_stop`, `task_completed`, `hook_entry`) y el builder compartido `hook_response` obtienen su adapter vía `get_adapter()` — **nunca** llaman a una clase concreta. El nombre de la clase concreta (`ClaudeCodeAdapter`) aparece en *exactamente un* call site de todo el core. La abstracción: *el core pide "el adapter del host activo"; no nombra cuál es.*

API:
- `register_adapter(host, adapter_cls)` — registra la clase del adapter para una clave de host (valida que sea subclase de `HookAdapter`).
- `get_adapter(host=None)` — construye y cachea (stateless → una instancia por host) el adapter; default `DEFAULT_HOST = "claude_code"`.

### (2) Cómo lo cumpliría Codex

Soportar Codex aquí es **literalmente una línea de registro** más la clase nueva:

```python
register_adapter("codex", CodexAdapter)
```

Ningún entry point cambia. Cuando haya más de un host instalado, `get_adapter` puede extenderse para resolver la clave desde una señal de detección de host (análoga a `detect_channel`) — y esa extensión vive *dentro de `registry.py`*, no en los entry points. Este seam es la materialización más directa de la tesis del brief: **sumar un host = registrar un adapter.**

### (3) Capacidades / degradación

El registry no tiene capacidades propias que declarar/degradar: es el mecanismo de selección. La única precondición es que `CodexAdapter` sea subclase de `HookAdapter` (lo valida `register_adapter` con `TypeError` si no). Cero cambios en el core.

---

## Seam 6 — Capacidades + degradación (`base.py::capabilities/supports/degrade_when_missing` + `types.py`, AC-6)

### (1) Abstracción de Gaia

Tres piezas, todas agnósticas al host:

- `capabilities() -> FrozenSet[HostCapability]` (abstracto) — **el único lugar** donde un host declara qué puede hacer. Nada inferido, nada implícito.
- `supports(capability) -> bool` (concreto, compartido) — pregunta agnóstica sobre `capabilities()`. El core se ramifica sobre *qué puede el host*, no sobre *qué host es*.
- `degrade_when_missing(capability, fallback, reason) -> CapabilityDegradation` (concreto, compartido) — devuelve la degradación **declarada**: si la capacidad está, `available=True`; si no, `available=False` cargando el `fallback` que eligió el caller y un `reason`. Reemplaza los dos modos de fallo que el brief prohíbe: (a) crashear cuando falta una capacidad, y (b) un `if host == ...` implícito.

### (2) Cómo lo cumpliría Codex

`CodexAdapter.capabilities()` devuelve el `frozenset` **exacto** de lo que Codex ofrece. Ejemplo hipotético — un Codex que tiene aprobación out-of-band y decisión estructurada, pero **carece** de prompt inline, de transcript y de inyección de contexto:

```python
_CAPABILITIES = frozenset({
    HostCapability.OUT_OF_BAND_APPROVAL,
    HostCapability.STRUCTURED_PERMISSION_DECISION,
    HostCapability.UPDATED_INPUT,
})
# Omitidos (degradan): INTERACTIVE_CONSENT, CONTEXT_INJECTION, TRANSCRIPT_ACCESS
```

Compárese con Claude Code, que declara las **seis**. El core no nota la diferencia salvo a través de `supports` / `degrade_when_missing`. Para cada capacidad ausente, el caller del core ya tiene definido su `fallback` seguro (`"deny"`, `"skip"`, `"log_only"`), y la degradación se vuelve un valor observable, no un branch oculto.

### (3) Capacidades / degradación

Este seam **es** el mecanismo de capacidades/degradación. Sumar Codex = escribir un `capabilities()` que liste su subconjunto. `supports` y `degrade_when_missing` son heredados sin cambio (están definidos no-abstractos en `base.py` justamente para que toda adapter comparta una sola semántica de query). **Cero cambios en el core.**

---

## Seam transversal — Inyección de contexto (`CONTEXT_INJECTION`, AC-6)

No es un archivo aparte sino una capacidad que cruza varios formatters/adapters (`format_context_response`, `adapt_subagent_start`, `adapt_session_start`). Se documenta para cerrar las seis capacidades.

### (1) Abstracción de Gaia

El core produce un `ContextResult` (agnóstico) y el adapter lo traduce a la forma de inyección de contexto que el host respeta a tiempo de hook (SessionStart / SubagentStart). Claude Code lo materializa con su `additionalContext` dentro de `hookSpecificOutput` — detalle que **vive solo en el adapter**.

### (2) Cómo lo cumpliría Codex

- Si Codex puede inyectar contexto en sesión a tiempo de hook → su adapter traduce `ContextResult` a la forma de Codex; declara `CONTEXT_INJECTION`.
- Si no → no la declara.

### (3) Capacidades / degradación

Capacidad: `CONTEXT_INJECTION`.
- Presente → se inyecta el contexto del proyecto/episódico como hoy.
- Ausente → `degrade_when_missing(HostCapability.CONTEXT_INJECTION, fallback="skip")`. Gaia sigue operando con menos contexto enriquecido pero no crashea ni asume un canal que Codex no tiene. Degradación declarada.

---

## Conclusión del mapeo

Para los **seis seams** + las **seis capacidades**, soportar Codex consiste, sin excepción, en:

1. **Escribir `CodexAdapter(HookAdapter)`** con las implementaciones concretas de los métodos abstractos (`parse_event`, `request_consent`, `capabilities`, formatters, `adapt_*`).
2. **Declarar el subconjunto de capacidades** que Codex ofrece en `capabilities()`.
3. **Registrar** una línea: `register_adapter("codex", CodexAdapter)`.
4. (Si aplica) **módulos host-específicos confinados** para session-id y transcript, espejando `host_session.py` / `host_transcript.py`.

**Ningún archivo de lógica de negocio del core cambia.** Las ausencias de capacidad se canalizan por `degrade_when_missing` como valores `CapabilityDegradation` observables, nunca por un `if host == "codex"` ni por un crash. Esto valida la tesis de AC-8.

---

## Gaps honestos — acoplamiento residual con Claude Code detectado al mapear

El mapeo reveló dos puntos donde el core **aún nombra Claude Code de forma nominal**. Ninguno bloquea a Codex (su adapter los puede sortear), pero son acoplamiento residual que el core todavía no abstrae del todo. Se reportan como gaps honestos para visibilidad de M3, no como bloqueos de T3.1.

### Gap 1 — `HookEventType` lleva los nombres de evento de Claude Code como valores del enum

`hooks/adapters/types.py::HookEventType` documenta "All Claude Code hook events" y sus valores string (`"PreToolUse"`, `"PostToolUse"`, `"SubagentStop"`, `"SessionStart"`, …) son **los nombres literales de Claude Code**. El enum vive en `types.py`, que se presenta como CLI-agnóstico ("No dependencies on any existing gaia-ops module").

- **Por qué no bloquea a Codex:** el `CodexAdapter.parse_event` puede mapear *sus* nombres de evento a estos miembros del enum (el enum es un vocabulario interno de Gaia; el valor string es solo su representación). Si Codex llama "pre_tool" a lo que CC llama "PreToolUse", el adapter de Codex hace `HookEventType.PRE_TOOL_USE` igual.
- **El residual:** el *vocabulario agnóstico* hereda la nomenclatura de un host concreto. Es agnosticismo nominal imperfecto. Una abstracción 100% limpia tendría nombres de evento neutrales (p. ej. `PRE_TOOL_USE` sin que su `.value` sea el string de CC), y cada adapter mapearía *su* string ⇄ el miembro neutral. Hoy CC "gana" la representación por ser el host fundacional.
- **Severidad:** baja. Cosmético/nominal; no fuerza ningún branch en la lógica de negocio.

### Gap 2 — `DistributionChannel` modela solo los canales de Claude Code (`NPM` / `PLUGIN`) y `detect_channel` lee env-vars de CC

`hooks/adapters/types.py::DistributionChannel` enumera `NPM` y `PLUGIN`, que son las **dos formas en que se distribuye/invoca gaia-ops bajo Claude Code**. `ClaudeCodeAdapter.detect_channel` resuelve el canal leyendo `CLAUDE_PLUGIN_ROOT`. `HookEvent` lleva `channel: DistributionChannel` y `plugin_root` como campos de primer nivel.

- **Por qué no bloquea a Codex:** `detect_channel` es un método abstracto del adapter; `CodexAdapter.detect_channel` decide su propio valor. Codex probablemente no tenga el concepto "plugin de Claude Code", así que devolvería `NPM` (o el canal que aplique) y dejaría `plugin_root=None`.
- **El residual:** el *tipo* `DistributionChannel` y el campo `plugin_root` de `HookEvent` están modelados alrededor del modelo de distribución de Claude Code. Si Codex tuviera un modelo de distribución distinto (p. ej. una extensión nativa con su propia raíz), el vocabulario actual no lo nombra; habría que extender el enum/los campos en `types.py` — y eso *sí* sería tocar un archivo del core. Es el caso límite donde "solo escribir un adapter" no alcanzaría.
- **Severidad:** media. No bloquea a Codex en el modelo NPM/plugin asumido, pero es el seam con mayor probabilidad de exigir una extensión del core si Codex introduce un canal de distribución que el enum no contempla. Recomendación para M3: evaluar si `DistributionChannel` debe generalizarse (p. ej. un valor `HOST_NATIVE` o un canal opaco por host) antes de aterrizar un segundo host real.

### Nota de alcance (no es gap)

La premisa "Codex es de la familia hook-interception" es una **suposición del mapeo**, no un hecho verificado contra una API real de Codex (este es un ejercicio en papel, T3.1). Si al implementar el adapter real de Codex (M-posterior) resultara que Codex no ofrece un punto de intercepción pre-tool equivalente, el desacople actual no cubriría ese caso — pero eso pertenece a otra familia de hosts y a otro plan, fuera del alcance de AC-8.
