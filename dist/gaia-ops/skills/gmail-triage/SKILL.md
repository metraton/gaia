---
name: gmail-triage
description: Use when the user wants to clean up, organize, or triage their Gmail inbox interactively
metadata:
  user-invocable: false
  type: technique
---

# Gmail Triage

Interactive workflow for managing Gmail. Gaia analyzes, summarizes, proposes. User decides. Gaia executes. The `gmail-policy` skill governs what operations are allowed.

## Workflow Labels

Three state labels (defined in gmail-policy):
- `_gaia/pending` — Staging area. Unprocessed emails during triage. Empties as user works through them.
- `_gaia/remind` — Flagged as important. Gaia checks when user asks "what's important?"
- `_gaia/trash` — Soft delete. Accumulates. User reviews when they want. Never actually deleted.

No `_gaia/*` label = processed/done.

## Session Types

### 1. Full Triage ("organicemos el correo")
- Scan inbox, group by sender/category, report counts
- Present top groups: "142 de Lider, 98 de Santa Isabel..."
- User decides per group → trash/remind/content-label
- Report progress: "Procesamos 500 de 2000. ¿Seguimos?"

### 2. Quick Cleanup ("limpiemos algo rápido")
- Pick easiest batch (highest volume, most repetitive)
- "340 promos repetidas de retail. ¿Las mando a trash?"
- One confirmation = hundreds processed. Target: under 2 minutes.

### 3. Post-Vacation ("acumulé mucho")
- Move unprocessed to `_gaia/pending`
- Report: "847 correos: 600 promos, 120 bank, 80 LinkedIn, 47 otros"
- Work through each category in sessions

### 4. What to Remember ("¿qué hay importante?")
- Check `_gaia/remind`, present summary with context
- Ask if any can be cleared

### 5. Promotion Analysis ("analiza las promos")
- Group by sender, identify patterns
- Flag genuinely interesting vs noise
- Recommend bulk trash for repetitive senders

## Presentation Format

Always:
1. Group by sender/topic — never list individual emails when there are many
2. Show count per group + sample subject
3. Flag unusual items ("movimiento de $50K en Bci")
4. Propose action: "¿Trash todo el grupo?" or "¿Revisar uno por uno?"
5. Max 5-7 groups per interaction

## Batch Rules

- Max 500 emails per API call
- Always confirm before moving: state count and destination
- After each batch: "Moved X to trash, Y to remind. Z remaining."
- On "todo trash": double-check — "¿Seguro? Son N correos de [sender]."

## Progress Tracking

After each session report: messages processed, remaining in pending, cumulative in trash, cumulative in remind.

## Anti-Patterns

- Listing individual emails when hundreds exist — the user drowns in noise and disengages. Group first, detail on request.
- Moving without explicit confirmation — `removeLabelIds` changes visibility with no undo; trust depends on the user always knowing what happened.
- Auto-processing `_gaia/trash` — that label is the user's safety net. They review it when they want, not when Gaia decides.
- Assuming promos are trash — some are genuinely interesting (sale alerts, event invites). Always ask.
- Too many options at once — decision fatigue kills triage momentum. Max 5-7 groups per round.

## Related Skills

- `gmail-policy` — security rules, label definitions
- `gws-setup` — CLI installation and authentication
