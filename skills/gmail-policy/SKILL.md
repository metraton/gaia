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

## Permanently Blocked (inapelable)

- `gws gmail messages delete` -- permanent deletion
- `gws gmail messages trash` -- moves to trash
- `gws gmail messages purge` -- permanent purge
- `gws gmail drafts delete` -- draft deletion

## Label Convention

Gaia creates labels prefixed with `_gaia/`:

| Label | Purpose |
|-------|---------|
| `_gaia/review` | Needs human attention |
| `_gaia/processed` | Already analyzed by Gaia |
| `_gaia/urgent` | Classified as urgent |
| `_gaia/archive` | Safe to archive |
| `_gaia/spam` | Classified as spam (user deletes manually) |

## OAuth Scope

Require `gmail.modify` scope. NEVER `https://mail.google.com/` (full access including delete).
`gmail.modify` allows read + label + move but NOT delete -- this is the API-level lock.
