---
name: session-reflection
description: Use at the end of a session with substantial conversational work -- briefs closed, decisions taken, components modified -- to offer the user a structured reflection before closing.
metadata:
  user-invocable: false
  type: technique
---

# Session Reflection

Al final de una sesión con peso conversacional, ayuda al usuario a cerrar
el arco ofreciéndole una reflexión corta y estructurada. No es un reporte
técnico -- es una devolución de qué pasó desde el lado de los acuerdos,
no desde el lado de las acciones.

## Cuando llegas aquí

El orquestador te cargó porque detectó que la sesión tuvo trabajo
conversacional suficiente (no sólo acciones aisladas) y el usuario aceptó
la oferta de reflexión. Tu trabajo no es resumir comandos ejecutados;
es devolver el arco desde la perspectiva del usuario, en el lenguaje
que ya existe en la conversación.

## Qué produce

Una respuesta corta con tres secciones:

### Qué acordamos

2-4 bullets nombrando las decisiones que emergieron en la sesión, en el
lenguaje que se usó durante la conversación (no técnico-imperativo). Si
un acuerdo se enunció como "dejemos el planner como está", no lo traduzcas
a "mantener el estado actual del componente planner".

### Qué quedó abierto

Lo que se mencionó pero no se cerró -- ideas, preguntas diferidas,
follow-ups que aparecieron y no alcanzaron cierre. No fuerces items si
no los hay; un "nada significativo quedó abierto" es válido.

### Qué merece cristalizarse

Sugerencia opcional de qué decisiones o aprendizajes valdría la pena
guardar en memoria persistente (MEMORY.md, un brief, un doc). Propone;
no prescribas. El usuario decide.

## Principios

- Usa el vocabulario de la conversación. No inventes términos nuevos ni
  traduzcas a jerga técnica lo que emergió en español coloquial.
- No repitas hashes de commits ni outputs técnicos que el usuario ya vio
  durante la sesión. Esto es devolución de arco, no resumen de log.
- Si la sesión fue puramente ejecutiva (no hubo acuerdos conversacionales
  significativos, sólo comandos y acciones), devuelve con honestidad:
  "esta sesión fue mayormente ejecución -- no hay arco conversacional
  que reflexionar." Mejor breve y honesto que inflar tres bullets.
- Máximo 200 palabras de respuesta total.
- Offer closure, don't force it. La skill no persiste nada automáticamente;
  el usuario decide qué hacer con la devolución.

## Anti-Patterns

- **Resumen de commits** -- repetir lo que el usuario ya leyó no es reflexión.
- **Jerga técnica nueva** -- traducir "cerrar la idea" a "materializar el
  requirement" quiebra la continuidad del lenguaje de la sesión.
- **Forzar tres secciones** -- si no hay nada abierto, dilo. No inventes
  follow-ups para rellenar estructura.
- **Persistir sin permiso** -- la sección "qué merece cristalizarse" es
  sugerencia. Si el usuario no responde, no escribas a MEMORY.md solo.
