---
name: gmail-policy
description: Use when managing Gmail messages, labels, or email workflows via gws CLI or Gmail MCP tools
metadata:
  user-invocable: false
  type: technique
---

# Gmail Policy

## Reading User Intent Before Acting

The most common mistake is treating every email-related request as an execution command. Before touching a single API, ask: is the user giving me context, or is the user giving me a command?

This is a reasoning step, not a checklist. Run it silently before every response.

### The Four Questions

1. **Context or command?** Is the user describing a situation, or directing an action?
2. **If command -- explicit or ambiguous?** Explicit means the verb leaves no doubt (send, dile que sí y envíaselo). Ambiguous means the verb could be draft or send.
3. **Reversible or sensitive?** A simple scheduling reply is reversible. A lease acceptance, financial form, or commitment with a third party is sensitive -- draft first unless the user explicitly says send.
4. **Am I in a proactive triage context?** If I was just reviewing the inbox, I have permission to generate drafts ahead of being asked, then present them.

### Intent Classification Table

| Lo que dice el user | Intent real | Acción correcta |
|---------------------|-------------|-----------------|
| "necesito analizar un correo y enviar unos correos importantes" | Contexto -- está contándote el plan, no ejecutando | No hacer nada de envío; esperar el comando específico |
| "chequea mis correos y ve si hay algo importante" | Review + iniciativa concedida | Leer inbox, triage, **generar drafts** para los que merezcan respuesta, presentar lista al user |
| "dile que aceptamos y envíaselo" | Comando explícito de envío | Crear y enviar directamente (un solo ciclo T3, no draft→send) |
| "mándale un correo a X diciéndole Y" | Ambiguo | Preguntar: ¿quiere draft para revisar o envío directo? |
| "respóndele a Assetplan aceptando" | Ambiguo, tendencia a draft | Default a draft si el contenido involucra datos personales, decisiones comerciales, o formularios |
| "dile que llego a las 5pm" | Comando simple, contenido reversible | Envío directo está bien sin pasar por draft |
| "prepara una respuesta para X" | Draft explícito | Crear draft y reportar |

### The Anti-Drift Rule

There is no fixed pipeline where every send goes through draft→approve→send. That workflow exists as a safety net for sensitive cases, not as the default for every email. When the user says "envíaselo", they mean send -- one T3 approval, one action, done.

The question is not "should I always draft first?" The question is: **what did the user actually ask for, and how reversible is this action?**

If you're uncertain, ask once. Do not silently choose draft when the user said send.

## Proactive Draft Generation (Triage Context)

During a triage or inbox review session ("chequea mis correos", "ve si hay algo importante"), the user grants implicit permission for proactive drafts. You do not need to ask for approval before creating each one.

Pattern:
1. Read inbox, identify threads that clearly need a response
2. For each, assess: does the reply require user input I don't have, or can I draft a reasonable response from context?
3. If draftable -- draft it. Store the draft in Gmail. Note the draft ID.
4. At the end of the review, present the complete list: "Generé 3 drafts: [subject 1], [subject 2], [subject 3]. ¿Quieres revisarlos?"

The user reviews and approves individual drafts before sending. The generation step does not require one-by-one confirmation -- the presentation step does.

Do not generate drafts proactively outside triage context. If the user opens a conversation about a single email, default to their explicit instruction.

## Sending: When Draft and When Direct

| Scenario | Default action |
|----------|---------------|
| User says "envíaselo" / "mándalo" / "dile que sí y envíaselo" | Send direct -- T3 approval for `send`, not for draft then send again |
| User says "prepara una respuesta" / "redacta" | Draft |
| Reply contains PII (RUT, cuenta bancaria, dirección, DOB) | Draft even if user said "mándale" -- confirm before send |
| Reply is a business commitment (arrendamiento, contrato, formulario) | Draft unless user explicitly says send |
| Simple logistics (hora, confirmación de asistencia, "llegaré tarde") | Direct send fine |
| Ambiguous command + first time with this recipient | Ask once |

When you do create a draft, verify it with `gws gmail users drafts list` and report the draft ID and snippet to the user. This closes the loop.

## Multi-Source Data Completion

Before asking the user for a datum (RUT, dirección, cuenta bancaria, etc.), check these sources in order:

1. **Other Gmail threads** (priority 1) -- search for related threads. A user's RUT might appear in a Colmena thread. A property address might appear in a previous landlord thread. Connecting emails is the preferred path.
2. **Local structured documents** -- `~/Documents/personal/**/data.json`, spreadsheets
3. **PDFs** -- notarial documents (compraventa, hipoteca, tasación) carry DOB, nationality, m², civil status
4. Only ask the user for data not found in any source

When you find data in another thread, cite the source: "Tu RUT lo saqué de un correo de Colmena del 2024-03." This builds trust and shows the search was real.

## PII Hygiene

Any `.eml` or temporary file containing PII (RUT, cuenta bancaria, teléfono, DOB, dirección) must be deleted with `rm` after the draft is created. Verify deletion with Glob or `ls`. Report: "Archivo temporal eliminado."

## Security Tier Classification

| Operation | Tier | Notes |
|-----------|------|-------|
| `gws gmail users messages list` | T0 | Search/filter messages |
| `gws gmail users messages get` | T0 | Read message content |
| `gws gmail users labels list` | T0 | List available labels |
| `gws gmail users labels get` | T0 | Read label details |
| `gws gmail +search` | T0 | Macro search (syntactic sugar over list) |
| `gws gmail users messages modify --addLabelIds` | T0 | Add any `_gaia/*` label (non-destructive) |
| `gws gmail users messages modify --removeLabelIds` | T2 | Changes message visibility |
| `gws gmail users messages modify` (action→waiting after send) | T2 | Auto-transition after user reply -- logged, no approval |
| `gws gmail users drafts create` | T3 | Creates draft on user's behalf |
| `gws gmail users drafts list` | T0 | Verify draft was created |
| `gws gmail +reply --message-id --body` | T3 | Sends reply on user's behalf |
| `gws gmail users messages send --params` | T3 | Sends/replies via raw RFC 2822 |
| `gws gmail users labels create` | T3 | Creates new label |

### Blocked Operations

Permanently denied by the hook -- `gmail.modify` OAuth scope excludes delete at the API level.

| Operation | Reason |
|-----------|--------|
| `gws gmail users messages delete` | Permanent, unrecoverable |
| `gws gmail users messages trash` | Moves to trash (use `_gaia/trash` label instead) |
| `gws gmail users messages purge` | Permanent purge |
| `gws gmail users drafts delete` | Draft deletion |

### Macro Prefix Handling

`gws` CLI exposes convenience macros prefixed with `+` (e.g. `+reply`, `+send`, `+search`). The hook strips the leading `+` before the verb taxonomy lookup inside `detect_mutative_command()`, so each macro classifies like its base verb:

- `gws gmail +reply` → token `reply` → match in MUTATIVE_VERBS → T3 block
- `gws gmail +send` → token `send` → match in MUTATIVE_VERBS → T3 block
- `gws gmail +search` → token `search` → match in READ_ONLY_VERBS → safe

Fix applied 2026-04-17 in `hooks/modules/security/mutative_verbs.py` after a `+reply` invocation slipped through as "safe by elimination" during a Gmail session.

## Sending Replies

### When to use `+reply` vs `send --params`

| Use case | Command | Pros | Cons |
|----------|---------|------|------|
| Simple plaintext reply | `gws gmail +reply --message-id <id> --body "<text>"` | Simple, handles threading headers automatically | Plaintext only, no HTML, no collapsed quote, no signature |
| HTML reply with signature + collapsed quote | `gws gmail users messages send --params '{"userId":"me","threadId":"<tid>","raw":"<base64url>"}'` | Full control over MIME, looks native in Gmail | Must construct RFC 2822 manually and base64url-encode |

Use `+reply` for quick operational replies where formatting does not matter. Use `send --params` when the recipient will see the mail in a mail client and visual quality matters.

For the correct `gws gmail users drafts create` schema, RFC 2822 template, base64url encoding pipeline, and other technical patterns -- see `reference.md` in this skill directory.

## Label Convention

### Workflow Labels (Layer 0 -- `_gaia/*`)

| Label | Purpose | Lifecycle |
|-------|---------|-----------|
| `_gaia/action` | I need to do something (respond, pay, read) | Clears when user acts → moves to `waiting` or removed |
| `_gaia/waiting` | I already acted, waiting for the other party | Clears when other party responds → back to `action` or removed |
| `_gaia/someday` | Interesting but no urgency (promos, articles, ideas) | Resurfaces in weekly review, user clears manually |
| `_gaia/pending` | Staging area during mass triage | Empties during triage sessions |
| `_gaia/trash` | Soft delete | Accumulates, user reviews |

No `_gaia/*` label = processed/done. No extra label needed.

### State Transitions

```
inbox ──→ action   (user or AI: I need to act)
inbox ──→ waiting  (AI detects user already replied in thread)
inbox ──→ someday  (user defers, no urgency)
inbox ──→ trash    (not wanted)
inbox ──→ pending  (mass triage staging)

action  ──→ waiting  (user replied/acted → auto T1 transition)
action  ──→ done     (handled, no follow-up → remove label)
action  ──→ someday  (user defers)

waiting ──→ action  (other party replied → needs user attention)
waiting ──→ done    (resolved → remove label)

someday ──→ action  (user decides to act)
someday ──→ trash   (not worth it)
someday ──→ done    (reviewed, no action needed → remove label)

pending ──→ {action, waiting, someday, trash, done}  (triage output)
```

### Calendar Rule

When an email contains a specific date/time deadline (bill due date, event, appointment): create a calendar event AND label the email `_gaia/action`. The calendar is the time-trigger; the label is the state-tracker.

### Content Labels (Layer 1)

| Category | Labels |
|----------|--------|
| Finance | `Finance/Bank`, `Finance/Transfers`, `Finance/Insurance` |
| Jobs | `Jobs/Alerts`, `Jobs/Academic` |
| Shopping | `Shopping/Promos`, `Shopping/Orders` |
| Music | `Music/Nucleo`, `Music/DJ` |
| Social | `Social/LinkedIn`, `Social/Facebook` |
| Services | `Services/Subscriptions`, `Services/Utilities` |
| Tech | `Tech/Programming`, `Tech/SalesForce` |
| Personal | `Personal/Notes`, `Personal/Travel`, `Personal/Downloads` |
| Legacy | `_gaia/legacy` -- retired: Buzz!!, Isercon, WaReS, +1, multi-forward, GDrive, PokerStar |

## OAuth Scope

Use `gmail.modify` scope (read + label + move, no delete). Full access scope (`https://mail.google.com/`) is blocked -- it includes delete permissions that bypass both hook and label controls.

## Related Skills

- `gmail-triage` -- interactive triage workflow
- `gws-setup` -- CLI installation and authentication
