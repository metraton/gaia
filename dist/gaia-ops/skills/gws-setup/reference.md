# GWS Setup Reference

Heavy reference material for the `gws-setup` skill. Read on-demand during scope selection and command authoring.

## Safe Scopes (personal @gmail.com)

| Scope | Purpose |
|-------|---------|
| `gmail.modify` | Read, send, label messages (no delete) |
| `gmail.readonly` | Read-only Gmail access |
| `gmail.labels` | Manage labels |
| `drive.readonly` | Read-only Drive access |
| `drive.metadata.readonly` | Read Drive file metadata |
| `drive.file` | Access files created by the app |
| `calendar.readonly` | Read-only Calendar access |
| `calendar.events.readonly` | Read calendar events |
| `contacts.readonly` | Read-only Contacts access |
| `tasks` | Manage Tasks |
| `userinfo.email` | Read email address |
| `userinfo.profile` | Read basic profile |
| `cloud-platform` | GCP platform access -- granted and working |

## Blocked Scopes (organizational / enterprise only)

NEVER select these for personal @gmail.com accounts:

| Scope | Reason |
|-------|--------|
| `admin.*` | Google Workspace admin only |
| `cloud-identity.*` | Organizational accounts only |
| `classroom.*` | Google Classroom (educational) |
| `ediscovery.*` | Enterprise Vault only |
| `directory.*` | Organizational directory only |

Including any of these causes `400: invalid_scope` for personal accounts (gws issue #119).

## Command Syntax

All gws gmail commands require the `userId` parameter:

```bash
# List messages
gws gmail users messages list --params '{"userId":"me","maxResults":N}'

# List labels
gws gmail users labels list --params '{"userId":"me"}'

# Create label
gws gmail users labels create --params '{"userId":"me"}' --json '{"name":"label-name"}'

# Get message
gws gmail users messages get --params '{"userId":"me","id":"<message-id>"}'

# Modify message labels
gws gmail users messages modify --params '{"userId":"me","id":"<message-id>"}' --json '{"addLabelIds":["LABEL_ID"]}'
```

## Credential Paths

| File | Path | Notes |
|------|------|-------|
| Client secret | `~/.config/gws/client_secret.json` | Downloaded from GCP console |
| Encrypted credentials | `~/.config/gws/credentials.enc` | Created by `gws auth login` |
| Encryption | AES-256-GCM | Key stored in OS keyring |

## Error Quick Reference

| Error | Cause | Fix |
|-------|-------|-----|
| `400: invalid_scope` | Organizational scope on personal account | Remove blocked scopes, re-run `gws auth setup` |
| `403: access_denied` | Missing Test User in OAuth consent | Add email to Test Users in GCP console |
| `401: invalid_client` | Wrong OAuth client type | Recreate as "Desktop app", not "Web application" |
| `403: app not verified` | Normal for dev apps | Click "Advanced" -> "Go to gws-cli (unsafe)" |
