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

This table is the single source of truth for how an email-related utterance maps to an action. Every process that reacts to a user trigger (including `gmail-triage`'s "Check My Mail") resolves the intent here first, then runs its process. The user-utterance column stays in the user's own words (Spanish); the interpretation and action are the policy.

| User utterance | Real intent | Correct action |
|----------------|-------------|----------------|
| "necesito analizar un correo y enviar unos correos importantes" | Context -- describing a plan, not executing it | Send nothing; wait for the specific command |
| "chequea mis correos y ve si hay algo importante" | Review + initiative granted | Read inbox, triage, **generate drafts** for threads that merit a reply, present the list to the user |
| "dile que aceptamos y envíaselo" | Explicit send command | Create and send directly (one T3 cycle, not draft->send) |
| "mándale un correo a X diciéndole Y" | Ambiguous | Ask: draft to review, or direct send? |
| "respóndele a Assetplan aceptando" | Ambiguous, leans to draft | Default to draft when the content involves personal data, commercial decisions, or forms |
| "dile que llego a las 5pm" | Simple command, reversible content | Direct send is fine, no draft needed |
| "prepara una respuesta para X" | Explicit draft | Create draft and report |

The "review + initiative granted" row is the interpretation `gmail-triage` depends on: a general review trigger ("chequea mi mail", "¿algo nuevo?") means *review with initiative*, not a bare listing. `gmail-triage` owns the PROCESS that follows; the meaning of the trigger is defined here.

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

## Autonomous Action Boundary

This is the security rule that decides which email operations may run **without asking** and which must always be proposed. It governs every `gmail-triage` mode, and above all the headless run where no user is present to approve. It lives here, in the policy layer, precisely because a process must not be the only home of the rule it obeys.

**Permitted automatically** (no approval, safe unattended) -- moves that are **mechanical AND reversible**, where nothing is lost and the state can be flipped back:
- Classifying a new email into `_gaia/action` or `_gaia/waiting` on a clear signal (`addLabelIds`).
- The reversible state swaps: `action -> waiting` when the user replies, `waiting -> action` when a third party replies. Relabeling (`removeLabelIds` of the old state + `addLabelIds` of the new) is a swap, not a destruction.
- Staging unprocessed mail into `_gaia/pending`.

**Prohibited automatically** (always a proposal, never executed alone) -- operations that are **destructive OR a matter of criterion**:
- Moving to `_gaia/trash`, marking spam, unsubscribing, deleting.
- Deferring to `_gaia/someday` -- a judgment call, not a mechanical classification.
- Clearing a label to mark a thread done (here `removeLabelIds` *destroys* state).
- Sending a message, or creating any draft in an unattended/headless run. (Proactive drafts *for review* are allowed in interactive triage under the grant above; that grant is interactive-only and does not reach a headless run.)

In a headless run these prohibited operations are **listed in the report** for an interactive session to approve -- never executed. And the proactive-draft grant above is an **interactive-session grant only**: it does not carry into a headless/unattended run.

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

## Protection Lists

Some senders must never be swept to `_gaia/trash` in a bulk operation, even when the user says "manda todo a trash". These are the never-trash protection lists. Before executing any bulk trash, filter the id set against them and exclude matches, then tell the user what you held back.

**Never-trash senders (hold back and flag):**

| Category | Examples of protected senders |
|----------|------------------------------|
| Banks / financial | Bci, Santander, BancoEstado, Falabella, Tenpo, Fintual, credit-card issuers |
| Government / tax | SII, Registro Civil, ChileAtiende, municipalidad, notarías |
| Health / insurance | Colmena, Cruz Blanca, Isapre, clinics, lab results |
| Legal / contracts | landlords, property managers, notaries, anything with a contract or deadline |
| Direct human correspondence | real people in `CATEGORY_PERSONAL`, not automated senders |

**Rule:** a bulk trash sweep operates on promotions, social, forums, and repetitive automated updates. A protected sender is excluded from the sweep even inside a matching category, and surfaced separately: "Mandé 1240 promos a trash. Retuve 3 de Bci y 1 del SII — ¿los revisamos?"

These lists are heuristics, not a hardcoded allowlist — extend them from what you learn about the user's real senders. When in doubt whether a sender is protected, exclude it and ask; a false hold-back costs one question, a false trash costs a lost bank statement.

### Sweep Shield: never select a protected message in the first place

Filtering protected senders *out after listing* is fragile — the safer mechanism is to shape the sweep query so it never selects them. Two heuristics, applied to the query before any id is fetched (the concrete Gmail queries live in `reference.md`, "Sweep Shield"):

- **Domain shield (`-from:<domain>`) for transactional senders.** Append a negative sender clause for each protected/transactional domain (banks, SII, health, notaries) to the sweep query, so their mail is excluded at search time. The shield is the mechanism; the never-trash lists above are the policy it enforces.
- **Keyword guard for mixed senders.** Some senders emit BOTH promo blasts and real transactional mail from the *same* address — Ticketplus is the canonical case (marketing plus real ticket / purchase confirmations). A blanket `-from:` would either protect the promos too or trash the real ticket. Guard by keyword instead: sweep the sender's promos but hold back any message matching a purchase keyword (`compra`, `pago`, `entrada`, `comprobante`, `boleta`, `orden`, `factura`).

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
| `gws gmail users messages batchModify --json '{"addLabelIds":[...]}'` | T0 | Bulk add `_gaia/*` label to up to 1000 ids (non-destructive) |
| `gws gmail users messages batchModify --json '{"removeLabelIds":[...]}'` | T2 | Bulk visibility change over up to 1000 ids — confirm count + destination first |
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
