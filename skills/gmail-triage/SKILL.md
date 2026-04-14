---
name: gmail-triage
description: Use when the user wants to clean up, organize, or triage their Gmail inbox interactively
metadata:
  user-invocable: false
  type: technique
---

# Gmail Triage

Interactive GTD-inspired state machine for Gmail. Gaia analyzes threads, proposes transitions. User decides. Gaia executes. The `gmail-policy` skill governs allowed operations and label definitions.

## State Labels

Four active states (defined in `gmail-policy`):
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

1. **Review `_gaia/action`** — present each item with thread framing. Did user already reply? Auto-propose → `waiting`.
2. **Review `_gaia/waiting`** — did the other party respond? Auto-propose → `action`. Stale >1 week → flag.
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

## Presentation Format

Group by sender/topic. Show count + sample subject. Flag unusual items ("movimiento de $50K en Bci"). Propose action per group. Max 5-7 groups per interaction.

## Batch Rules

- Max 500 emails per API call. Always confirm before moving: state count and destination.
- After each batch: "Moví X a trash, Y a action. Z restantes."
- On "todo trash": double-check — "¿Seguro? Son N correos de [sender]."

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
