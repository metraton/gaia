---
name: gmail-policy
description: Use when managing Gmail messages, labels, or email workflows via gws CLI or Gmail MCP tools
metadata:
  user-invocable: false
  type: reference
---

# Gmail Policy

## Operation Classification

| Operation | Tier | Notes |
|-----------|------|-------|
| `gws gmail users messages list` | T0 | Search/filter messages |
| `gws gmail users messages get` | T0 | Read message content |
| `gws gmail users labels list` | T0 | List available labels |
| `gws gmail users labels get` | T0 | Read label details |
| `gws gmail users messages modify --addLabelIds` | T0 | Add any `_gaia/*` label (non-destructive) |
| `gws gmail users messages modify --removeLabelIds` | T3 | Changes message visibility |
| `gws gmail users messages modify` (action→waiting after send) | T1 | Auto-transition after user reply -- logged, no approval |
| `gws gmail +send` | T3 | Sends on user's behalf |
| `gws gmail users labels create` | T3 | Creates new label |

### Blocked Operations

Permanently denied by the hook -- `gmail.modify` OAuth scope excludes delete at the API level.

| Operation | Reason |
|-----------|--------|
| `gws gmail users messages delete` | Permanent, unrecoverable |
| `gws gmail users messages trash` | Moves to trash (use `_gaia/trash` label instead) |
| `gws gmail users messages purge` | Permanent purge |
| `gws gmail users drafts delete` | Draft deletion |

## Label Convention

### Workflow Labels (Layer 0 -- `_gaia/*`)

| Label | Purpose | Lifecycle |
|-------|---------|-----------|
| `_gaia/action` | I need to do something (respond, pay, read) | Clears when user acts → moves to `waiting` or removed |
| `_gaia/waiting` | I already acted, waiting for the other party | Clears when other party responds → back to `action` or removed |
| `_gaia/someday` | Interesting but no urgency (promos, articles, ideas) | Resurfaces in weekly review, user clears manually |
| `_gaia/pending` | Staging area during mass triage | Empties during triage sessions |
| `_gaia/trash` | Soft delete | Accumulates, user reviews |
| `_gaia/remind` | **DEPRECATED** -- migrate to `action`, `waiting`, or `someday` | Will be removed |

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
