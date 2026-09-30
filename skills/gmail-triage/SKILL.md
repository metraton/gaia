---
name: gmail-triage
description: Use when the user wants to clean up, organize, or triage their Gmail inbox interactively -- "ordena el mail", "hagamos triage del inbox", "limpiemos el correo", "let's triage my inbox", "clean up my Gmail".
---

# Gmail Triage

Interactive GTD-inspired state machine for Gmail. Gaia analyzes threads, proposes transitions. User decides. Gaia executes. This is the PROCESS layer; it depends on `gmail-policy` for every rule -- label definitions, security tiers, the interpretation of a review trigger (Intent Classification), and the Autonomous Action Boundary that decides what may run without approval. That dependency holds in every mode: a security rule cannot live only in the process that obeys it. Triage always runs with the user present; to make it recur, see "Making It a Routine".

## State Labels

Five `_gaia/*` labels (defined in `gmail-policy`) — three durable active states, plus staging and soft-delete:
- `_gaia/action` — user must act
- `_gaia/waiting` — user acted, awaiting reply
- `_gaia/someday` — interesting, no urgency
- `_gaia/pending` — staging (triage backlog)
- `_gaia/trash` — soft delete; never truly deleted

No `_gaia/*` label = processed/done.

## Thread-Awareness Rule

Before presenting ANY labeled email, check the thread: message count, who sent last, when. This determines framing:
- "necesitas responder" (user is last)
- "esperando desde [date]" (user replied, waiting on them)
- "sin actividad hace 2 semanas — ¿hacer seguimiento?" (stale waiting)

These two sections are the state-machine expression of the **Autonomous Action Boundary** in `gmail-policy` — the boundary is the rule, these are its transitions.

## Automatic Transitions (no confirmation needed)

- User replies to an `action` thread → move to `waiting`
- New message arrives in a `waiting` thread → move to `action`

## Transitions Requiring Confirmation

- Anything → `trash` or `someday`
- Clearing any label (marking done)
- `someday` → `action`

## Modes

**Modes 1–5 open with a state summary before their specific work:**
"Antes de empezar: N en action, N en waiting, N en someday." Flag `action` items stale >3 days.

### 0. Check ("chequea mi mail" / "¿algo nuevo?")

1. **Review `_gaia/action`** — present each item with thread framing. Did user already reply? Auto-apply → `waiting` (mechanical, reversible — no confirmation).
2. **Review `_gaia/waiting`** — did the other party respond? Auto-apply → `action` (mechanical, reversible — no confirmation). Stale >1 week → flag.
3. **Review `_gaia/someday`** — count only: "tienes 5 en someday." Detail only if asked.
4. **Scan inbox for new signal** — Financial (large amounts, bills, due dates), personal/important (housing, legal, health), expected reply arrived → propose `action`. Interesting, no urgency → propose `someday`.
5. **Summarize** — overall inbox state in 2-3 sentences.

### 1. Full Triage ("organicemos el correo")

Scan inbox, group by sender/category, report counts. Present top groups. User decides per group → trash/action/someday/content-label. Report progress: "Procesamos 500 de 2000. ¿Seguimos?"

### 2. Quick Cleanup ("limpiemos algo rápido")

Pick easiest batch (highest volume, most repetitive). "340 promos de retail. ¿Las mando a trash?" One confirmation = hundreds processed. Target: under 2 minutes.

### 3. Post-Vacation ("acumulé mucho")

Move unprocessed to `_gaia/pending`. Report: "847 correos: 600 promos, 120 banco, 80 LinkedIn, 47 otros." Work categories in follow-up modes.

### 4. Review ("¿qué tengo pendiente?")

Dedicated state review — all three active labels:
- `_gaia/action` — stale >3 days? move to waiting/someday/done?
- `_gaia/waiting` — any responses arrived? stale >1 week?
- `_gaia/someday` — weekly review: promote to action? trash any?

### 5. Promo Analysis ("analiza las promos")

Group by sender, identify patterns. Flag genuinely interesting vs noise. Recommend bulk trash for repetitive senders.

### 6. Category Sweep ("barre las promociones" / "limpia por categoría" / "¿cuánto tengo en cada categoría?")

Triage by Gmail's native category system labels rather than by sender. The system labels `CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES`, `CATEGORY_FORUMS`, and `CATEGORY_PERSONAL` already partition the inbox — use them as ready-made buckets for high-volume cleanup.

1. **Count per category** — get the volume of each category without listing bodies (see "Volume Counting" in `gmail-policy/reference.md`). Report: "Promotions: 1240, Social: 380, Updates: 610, Forums: 45."
2. **Sweep the highest-volume, lowest-value category first** — usually `CATEGORY_PROMOTIONS`. Propose one bulk transition for the whole bucket: "¿Mando las 1240 de Promotions a trash?"
3. **One confirmation per category** — on approval, apply the label change in bulk via `batchModify` (see "Bulk Label Operations" in `gmail-policy/reference.md`), chunking to the API's per-call id limit.
4. **Shield protected and transactional senders out of the sweep query first** — banks, SII, health, notaries must never be selected, and mixed senders like Ticketplus (promos + real tickets from one address) need the keyword guard. Apply the "Sweep Shield" mechanism (heuristic in `gmail-policy` Protection Lists, concrete `-from:` / keyword-guard queries in `gmail-policy/reference.md`) to the SAME query used for counting and for paging ids, so the count reported and the ids moved are protected identically.
5. **Never sweep `CATEGORY_PERSONAL` blindly** — it holds real correspondence. Sweep it only sender-by-sender, not as a bucket.
6. **Report per category** — "Promotions: 1240 → trash. Social: dejé las 380, ¿las revisamos por remitente?"

Category Sweep is the fastest path for post-vacation or long-neglected inboxes; pair it with Mode 3 (Post-Vacation) staging when volume is very high.

## Presentation Format

Group by sender/topic. Show count + sample subject. Flag unusual items ("movimiento de $50K en Bci"). Propose action per group. Max 5-7 groups per interaction.

## Batch Rules

- `batchModify` accepts up to 1000 message ids per call; `messages list` returns up to 500 per page (paginate for more). Chunk large sweeps to 1000 ids per `batchModify`. Always confirm before moving: state count and destination.
- After each batch: "Moví X a trash, Y a action. Z restantes."
- On "todo trash": double-check — "¿Seguro? Son N correos de [sender]."

## Check My Mail

The process that runs when the user gives a general review trigger — "chequea mi mail", "tengo un mail importante", "¿algo nuevo?". The *meaning* of that trigger — "review with initiative granted", not a bare listing — is defined in `gmail-policy` (Intent Classification Table); this section is only the PROCESS that meaning invokes. Mode 0 above is the interactive step-list; this section is the contract behind it: one shared triage logic, the mode chosen by the caller.

**One process, always with the user present.** The user typing "chequea mi mail" — or accepting the offer of a due routine (see "Making It a Routine") — gets quick info plus proposals, live. There is no mode that runs triage without them.

**Order of "chequea mi mail" (simple — no watermark, no tracking of the last run):**
1. **Review the Gaia filters** (`_gaia/action`, `_gaia/waiting`, `_gaia/someday`) and report their state.
2. **Read the inbox and corroborate against those filters.**

**What the user gets:**
- Give quick info of what there is: what sits in `action`, who has been waiting for a reply for days, new noise (with an offer to sweep it).
- **Analysis with context** — this is the point, what makes it useful rather than a bare listing. Connect information: "hay una promo de X, sé que tienes Santander, te sirve." Detect recurring **subscriptions** and offer a decision (mantener / desuscribir / spam).
- Move to filters whatever the user approves.

**Autonomy.** Every mode obeys the **Autonomous Action Boundary** in `gmail-policy`: mechanical, reversible filter moves run without approval; destructive or criterion moves (trash, spam, unsubscribe, delete, marking done, sending) are always proposed to the user. The rule is defined once in the policy layer; this process only obeys it.

**Multi-account (brief note).** The focus is Gmail. The same filter ORDER is reusable for another Gmail account (e.g. a work address) the day it is connected. If another account has no `_gaia/*` filters, detect it and be able to create them — `gws gmail users labels create` is **T3** (see `gmail-policy`), so it is proposed to the user, never run on its own.

## Making It a Routine

Triage is a conversation, so a routine does not run it: a routine is a recurring notification that brings the user back to it. Nothing executes on its own; the routine comes due the next time Gaia is used after its time. One command creates it, pointing at this skill:

```
gaia notifications add --kind routine --every 1d --headline 'Triage mail' --skill gmail-triage
```

Use `--cron '<five fields>'` (local time) instead of `--every` for a fixed clock time, and `--workspace` to scope it to one workspace; without it the routine is global.

When the routine comes due:
1. The orchestrator offers to do the triage now.
2. If the user accepts, it runs interactively with them, as "Check My Mail" describes.
3. When the user says it is done, `gaia notifications ack <id>` moves the routine to its next occurrence.
4. If the user says not now, `gaia notifications snooze <id> --for 2h` (or `--until <local time>`) hides it until later.

`gaia notifications cancel <id>` ends the routine for good.

## Anti-Patterns

- Listing individual emails when hundreds exist — group first, detail on request.
- Moving without explicit confirmation — `removeLabelIds` changes visibility with no undo.
- Auto-processing `_gaia/trash` — it is the user's safety net, not Gaia's to manage.
- Assuming promos are trash — some are genuinely interesting. Always ask.
- Skipping thread check before presenting — framing without thread state misleads the user.
- More than 5-7 groups per round — decision fatigue kills triage momentum.

## Related Skills

- `gmail-policy` — security rules, label definitions, operation tiers
- `gws-setup` — CLI installation and authentication
