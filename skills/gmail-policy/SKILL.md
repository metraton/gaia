---
name: gmail-policy
description: Use when managing Gmail messages, labels, or email workflows via gws CLI or Gmail MCP tools
metadata:
  user-invocable: false
  type: domain
---

# Gmail Policy

## Principle

```
NEVER DELETE. ONLY LABEL.
addLabelIds is safe. removeLabelIds changes visibility. delete is forbidden.
```

## Allowed Operations (T0 -- automatic)

- `gws gmail messages list` -- search/filter messages
- `gws gmail messages get` -- read message content
- `gws gmail labels list` -- list available labels
- `gws gmail labels get` -- read label details
- `gws gmail messages modify --addLabelIds` -- add labels (non-destructive)

## Controlled Operations (T3 -- requires approval)

- `gws gmail messages modify --removeLabelIds` -- removes label visibility
- `gws gmail +send` -- sends email on user's behalf
- `gws gmail labels create` -- creates new label

## Permanently Blocked

- `gws gmail messages delete` -- permanent deletion
- `gws gmail messages trash` -- moves to trash
- `gws gmail messages purge` -- permanent purge
- `gws gmail drafts delete` -- draft deletion

## Label Convention

### Workflow Labels (Capa 0 — `_gaia/*`)

| Label | Purpose |
|-------|---------|
| `_gaia/pending` | Staging area — unprocessed emails during triage sessions |
| `_gaia/remind` | Flagged as important by user or Gaia |
| `_gaia/trash` | Soft delete — policy forbids actual deletion |

No `_gaia/*` label = processed/done. No extra label needed.

### Content Labels (Capa 1)

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
| Legacy | `_gaia/legacy` — retired: Buzz!!, Isercon, WaReS, +1, multi-forward, GDrive, PokerStar |

## OAuth Scope

Require `gmail.modify` scope. NEVER `https://mail.google.com/` (full access including delete).
`gmail.modify` allows read + label + move but NOT delete -- this is the API-level lock.

## Related Skills

- `gmail-triage` — interactive triage workflow
- `gws-setup` — CLI installation and authentication
