# Gmail Policy -- Technical Reference

Patterns learned from live sessions. Load this file when constructing drafts, encoding messages, or debugging `gws` command failures.

## Draft Creation: Correct Schema

Three failed attempts taught this: `gws gmail users drafts create` has a specific parameter split that looks wrong but is correct.

```bash
gws gmail users drafts create \
  --params '{"userId":"me"}' \
  --json '{"message":{"threadId":"<THREAD_ID>","raw":"<BASE64URL>"}}'
```

- `--params` carries only `userId` (query parameter, not body)
- `--json` carries the request body with `message` as the top-level key -- no `resource` wrapper
- `raw` is the full RFC 2822 message encoded as **base64url** (not standard base64)
- `threadId` is optional but required to link the draft into an existing thread

Wrong patterns that look right but fail:
- `--params '{"userId":"me","message":{...}}'` -- message does not belong in params
- `--json '{"resource":{"message":{...}}}'` -- no resource wrapper in this API
- Standard base64 in raw -- Gmail rejects it; must be base64url

## Base64url Encoding Pipeline (Shell Only)

Avoids a Python T3 approval for a simple transform:

```bash
RAW=$(base64 -w 0 /tmp/reply.eml | tr '+/' '-_' | tr -d '=')
```

- `base64 -w 0` disables line wrapping (required -- Gmail rejects newlines mid-string)
- `tr '+/' '-_'` converts standard base64 alphabet to URL-safe alphabet
- `tr -d '='` strips padding (Gmail requires no padding)

The result goes directly into the `raw` field of `--json`.

If writing the `.eml` first via the Write tool, then encode with the pipeline above. This is T0 (read-only transformation).

## RFC 2822 Reply Construction

Minimum headers for a threading-aware reply:

```
From: Nombre Apellido <email@example.com>
To: recipient@example.com
Subject: Re: Asunto Original
In-Reply-To: <message-id-of-the-message-being-replied-to@mail.gmail.com>
References: <message-id-1@...> <message-id-2@...>
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8
Content-Transfer-Encoding: 8bit

Cuerpo del mensaje.
```

For Spanish content with accents and UTF-8: `Content-Transfer-Encoding: 8bit` works correctly. Do not use quoted-printable for Spanish -- it creates unnecessary encoding noise.

`In-Reply-To` and `References` must use the raw `Message-ID` value from `gws gmail users messages get` -- it looks like `<some-long-hex@mail.gmail.com>` including the angle brackets.

### HTML Reply Template with Gmail Quote Collapse

For replies where visual quality matters (external recipients, business correspondence):

```
From: {{SENDER_NAME}} <{{SENDER_EMAIL}}>
To: {{RECIPIENT_EMAIL}}
Subject: Re: {{ORIGINAL_SUBJECT}}
In-Reply-To: <{{ORIGINAL_MESSAGE_ID}}>
References: <{{ORIGINAL_MESSAGE_ID}}>
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="----=_Part_boundary_001"

------=_Part_boundary_001
Content-Type: text/plain; charset=UTF-8

{{PLAINTEXT_BODY}}

-- 
{{SENDER_NAME}}

> {{QUOTED_ORIGINAL_TEXT_SINGLE_LINE_SUMMARY}}

------=_Part_boundary_001
Content-Type: text/html; charset=UTF-8

<html>
<body>
<p>{{HTML_BODY_PARAGRAPH_1}}</p>
<p>{{HTML_BODY_PARAGRAPH_2}}</p>
<br>
-- <br>
{{SENDER_NAME}}<br>
<br>
<div class="gmail_quote">
  <div dir="ltr">On {{ORIGINAL_DATE}}, {{ORIGINAL_SENDER}} wrote:</div>
  <blockquote class="gmail_quote" style="margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex">
    {{QUOTED_ORIGINAL_HTML}}
  </blockquote>
</div>
</body>
</html>

------=_Part_boundary_001--
```

Key points:
- The `<div class="gmail_quote"><blockquote class="gmail_quote" style="...">` wrapper is what Gmail collapses into the "..." toggle. Without it, quoted text renders as a wall of plain text.
- Signature separator: `-- \n` (dash-dash-space-newline per RFC 3676). In HTML: `-- <br>`.
- The boundary string must match exactly between the `Content-Type` header and the body part delimiters (including the `------` prefix with 6 hyphens).

## Multi-Source Data Lookup: Real Examples

These examples are from an actual Assetplan session (2026-04-17). They show how connecting threads avoids asking the user for data they have already shared elsewhere.

| Data needed | Where it was found |
|-------------|-------------------|
| RUT | Thread from Colmena (health insurance) -- appeared in a form confirmation |
| Property address (depto arrendado) | Thread from Samuel Aranda (previous property manager) |
| Contrato de arrendamiento | Thread from Condominio Evolución -- PDF attachment |
| m² and property details | PDF notarial (Tasación) attached to mortgage thread |
| DOB and civil status | PDF notarial (Compraventa / Hipoteca) |

Search pattern: before asking the user for any datum, run a targeted `gws gmail +search` for the topic. Examples:
- RUT: `gws gmail +search "RUT OR cédula OR 12.345"` (use known name patterns)
- Address: `gws gmail +search "{{street name}} OR {{condo name}}"`
- Contract: `gws gmail +search "contrato arrendamiento"`

If found, cite the source to the user ("Tu dirección la saqué del correo de Samuel Aranda de marzo 2025.").

## Volume Counting

To report how many messages match a query or sit in a category WITHOUT fetching them, read `resultSizeEstimate` from a `list` response with `maxResults` set to 1. The estimate is returned regardless of how many ids you actually page.

```bash
# Total in a native category (promotions/social/updates/forums/personal)
gws gmail users messages list --params '{"userId":"me","q":"category:promotions","maxResults":1}'
# -> response includes "resultSizeEstimate": <count>

# Count by workflow label
gws gmail users messages list --params '{"userId":"me","labelIds":["Label_25"],"maxResults":1}'

# Count by search query
gws gmail users messages list --params '{"userId":"me","q":"from:linkedin.com","maxResults":1}'
```

- `resultSizeEstimate` is an ESTIMATE — Gmail rounds for large mailboxes. Report it as "~1240", not an exact count, when it is large.
- This is T0 (read-only) and cheap: one API call per category. Use it to drive Mode 6 Category Sweep in `gmail-triage` instead of listing thousands of ids just to count them.
- `q:"category:promotions"` and `labelIds:["CATEGORY_PROMOTIONS"]` both work; the `q` form composes with other search terms.

## Sweep Shield: Query-Level Protection

A Category Sweep must never *select* a protected or transactional message in the first place. Both mechanisms below shape the sweep QUERY (the `q` field of `messages list`) so protected mail drops out at search time, before any id is fetched or handed to `batchModify`. This is the query-level enforcement of the never-trash Protection Lists in `SKILL.md`.

### Domain shield (`-from:`) — transactional senders

Append a negative sender clause per protected domain. Every message from that domain drops out of the result set:

```bash
gws gmail users messages list --params '{"userId":"me","q":"category:promotions -from:bancochile.cl -from:sii.cl -from:colmena.cl","maxResults":1}'
```

Chain one `-from:<domain>` per protected domain. Resolve the domain list from the never-trash Protection Lists (banks, government/tax, health/insurance, legal/notaries) plus whatever you have learned about the user's real senders.

### Keyword guard — mixed senders (Ticketplus case)

Some senders emit BOTH promotional blasts and real transactional mail (a purchase confirmation, a ticket, a payment receipt) from the SAME address — Ticketplus is the canonical case. A blanket `-from:ticketplus.cl` would protect the promos too (defeating the sweep) or, if omitted, trash the real ticket. Guard by keyword instead: sweep the sender's promos but subtract the messages that match a purchase keyword:

```bash
gws gmail users messages list --params '{"userId":"me","q":"category:promotions -(from:ticketplus.cl (compra OR pago OR entrada OR comprobante OR boleta OR orden OR factura))","maxResults":1}'
```

- `-( ... )` negates the whole group; a space inside the group is AND, so `from:ticketplus.cl (compra OR pago OR ...)` matches only that sender's purchase mail, and the leading `-` excludes exactly those from the sweep.
- `OR` must be uppercase in Gmail query syntax; parentheses group.
- Purchase keywords: `compra`, `pago`, `entrada`, `comprobante`, `boleta`, `orden`, `factura`. Extend per the user's real transactional vocabulary.

Apply the shield to the SAME query used for both counting (`maxResults:1`, read `resultSizeEstimate`) and for paging the ids you feed to `batchModify` — so the count you report and the ids you move are already protected identically.

## Bulk Label Operations

`batchModify` applies one label change to many messages in a single call — the primitive behind Category Sweep and any high-volume triage. Verb spelling and flags confirmed against `gws gmail users messages batchModify --help`:

```bash
gws gmail users messages batchModify \
  --params '{"userId":"me"}' \
  --json '{"ids":["<id1>","<id2>","..."],"addLabelIds":["<LABEL_ID>"],"removeLabelIds":["INBOX"]}'
```

- `--params` carries only `userId` (query parameter).
- `--json` carries the request body: `ids` (the messages), plus `addLabelIds` and/or `removeLabelIds`.
- **Per-call limit: up to 1000 ids.** For a sweep larger than 1000, chunk into batches of 1000 and call once per chunk.
- Adding a label is T0 (non-destructive); removing a label (e.g. `INBOX`, or moving to `_gaia/trash` while removing `INBOX`) is T2 — confirm count and destination first.

### Resolving label ids — do NOT hardcode `Label_NN`

The numeric `Label_NN` ids for user labels (including `_gaia/*`) are **assigned per-account and are not stable across accounts**. In the reference account `_gaia/trash` happens to be `Label_25`, but the ids are sparse (gaps at Label_20, Label_24) and one label even carries a long numeric id — a fresh account will map the same names to different ids. Always resolve the id at runtime from the label NAME:

```bash
# Resolve _gaia/trash to its id for THIS account before using it in batchModify
gws gmail users labels list --params '{"userId":"me"}' --format json
# -> find the object whose "name" == "_gaia/trash", use its "id"
```

Never bake `Label_25` (or any `Label_NN`) into a command string. Resolve the name → id via `labels list` at the start of a session, then reuse the resolved id for that session's `batchModify` calls. System labels (`INBOX`, `TRASH`, `SPAM`, `CATEGORY_*`) are the exception — their ids ARE their names and are stable.

> gws prints `Using keyring backend: <name>` to **stderr**, not stdout, on each invocation — it does not pollute the stdout JSON body, so capturing stdout for a JSON parser is safe. See the keyring callout in `gws-setup/reference.md` if that line becomes an error instead of a banner.

## Draft Verification

After `gws gmail users drafts create`, always verify:

```bash
gws gmail users drafts list --params '{"userId":"me"}'
```

Report to the user: draft ID, threadId (if linked), and snippet. This closes the loop and confirms the API call succeeded. Do not just assume the create worked.

## PII Cleanup Protocol

After a draft is created from a `.eml` file containing sensitive data:

1. Delete the `.eml`: `rm /tmp/reply.eml` (T3 -- file mutation, but within PII hygiene flow)
2. Verify deletion with Glob: `Glob /tmp/*.eml`
3. Report: "Archivo temporal eliminado."

Sensitive data includes: RUT, número de cuenta bancaria, teléfono, DOB, dirección física, números de contrato, número de pasaporte.
