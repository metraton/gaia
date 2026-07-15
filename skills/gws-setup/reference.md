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

> **Keyring backend callout.** `gws` resolves the encryption key through an OS keyring and prints `Using keyring backend: <name>` to **stderr** on every invocation — a harmless banner, not an error, and it does not touch stdout (JSON output stays clean). It only becomes a problem in **headless / WSL / SSH** environments where no Secret Service (e.g. GNOME Keyring, `libsecret`) is running: keyring resolution then fails and `gws auth login` cannot store or read credentials. Fix by ensuring a keyring backend is available (start the Secret Service, or install/configure `keyring` with an alternative backend) BEFORE running `gws auth setup`. This matters for scheduled / unattended Gmail runs — see "Headless Mode" in `gmail-triage/SKILL.md`.

## Error Quick Reference

| Error | Cause | Fix |
|-------|-------|-----|
| `400: invalid_scope` | Organizational scope on personal account | Remove blocked scopes, re-run `gws auth setup` |
| `403: access_denied` | Missing Test User in OAuth consent | Add email to Test Users in GCP console |
| `401: invalid_client` | Wrong OAuth client type | Recreate as "Desktop app", not "Web application" |
| `403: app not verified` | Normal for dev apps | Click "Advanced" -> "Go to gws-cli (unsafe)" |
| Token expires / `gws auth status` fails after ~7 days | OAuth consent app left in **Testing** (7-day refresh-token cap with Gmail scopes) | Publish the app to **In production** (SKILL.md step 10), then re-run `gws auth login` |
| keyring / `No recommended backend was available` / `Using keyring backend` fails | No Secret Service in headless/WSL/SSH session | Start a keyring backend (GNOME Keyring / `libsecret`) or configure an alternative `keyring` backend before `gws auth setup` |
