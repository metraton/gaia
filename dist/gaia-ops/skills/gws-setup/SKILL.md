---
name: gws-setup
description: Use when installing or configuring the Google Workspace CLI (gws) for a Google account
metadata:
  user-invocable: false
  type: technique
---

# GWS Setup

The gws CLI looks straightforward but breaks in subtle ways -- Google's OAuth flow has undocumented constraints that differ between personal and organizational accounts, and the gws tool itself has open bugs around scopes and multi-account. This procedure captures the working path and the traps discovered through real setup sessions, so you avoid the silent failures that waste hours.

**Prerequisites:** gcloud CLI installed, browser access for OAuth, interactive terminal for auth steps.

## Procedure

### 1. Install gws binary

- Download latest Linux x86_64 from https://github.com/googleworkspace/cli/releases
- Place in `~/.local/bin/`, `chmod +x`
- Verify: `gws --version`

### 2. Add Google account to gcloud

- `gcloud auth login <email> --no-launch-browser` (interactive terminal required)
- This adds alongside existing accounts -- does NOT replace them
- After login, restore work account: `gcloud config set account <work-account>`

### 3. Create or select GCP project

- Check existing: `gcloud projects list --account=<email>`
- Create new: `gcloud projects create gaia-<name> --name="Gaia <Name>" --account=<email>`
- Naming: `gaia-<identifier>-personal` or `gaia-<identifier>-work`

### 4. gws auth setup

- `gws auth setup --project <project-id>`
- TRAP: When selecting scopes, DO NOT use presets ("Recommended" or "Read Only") -- they include organizational scopes that break for @gmail.com accounts

### 5. Scope selection (CRITICAL)

For safe scopes and blocked scopes by account type, read `reference.md` in this directory.

Known bug: gws issue #119 -- `gws auth login` unusable with personal @gmail.com when organizational scopes are included. Google returns `400: invalid_scope`.

### 6. OAuth consent screen (manual in browser)

- URL: `https://console.cloud.google.com/apis/credentials/consent?project=<project-id>`
- User Type: External | App name: gws CLI | Support email: the account email
- TRAP: Add the account as a Test User BEFORE attempting login
  - Test Users section -> Add Users -> enter the email
  - Without this, Google returns `403: access_denied` ("app not verified")

### 7. OAuth client (manual in browser)

- URL: `https://console.cloud.google.com/apis/credentials?project=<project-id>`
- TRAP: Application type must be **Desktop app** (NOT "Web application")
  - Web application type causes `401: invalid_client`
- Download `client_secret_*.json` -> save to `~/.config/gws/client_secret.json`

### 8. gws auth login

- `gws auth login` -- opens browser for OAuth consent (interactive terminal required)
- If Google shows "app not verified" warning -> click "Advanced" -> "Go to gws-cli (unsafe)" -- safe for personal use

### 9. Verification

- `gws auth status` -- confirm token valid
- `gws gmail users messages list --params '{"userId":"me","maxResults":5}'` -- test Gmail
- `gws gmail users labels list --params '{"userId":"me"}'` -- test labels

### 10. Restore gcloud

- `gcloud config set account <original-work-account>`
- Verify: `gcloud config get account`

## Multi-account (future)

gws supports multi-account via `gws auth login --account <email>`, `gws auth list`, `gws auth default <email>`, `gws --account <email> <command>`.

Known bug: issue #181 -- `--account` flag doesn't work correctly yet.

## Anti-Patterns

- Using scope presets for personal @gmail.com -- causes `400: invalid_scope`
- Skipping Test User in OAuth consent screen -- causes `403: access_denied`
- Choosing "Web application" as OAuth client type -- causes `401: invalid_client`
- Forgetting to restore `gcloud config set account` after setup
- Including `admin.*`, `cloud-identity.*`, or `directory.*` scopes for personal accounts

## Related Skills

- `gmail-policy` -- operational Gmail security (tiers, labels, no-delete rule)

## References

- https://github.com/googleworkspace/cli
- https://github.com/googleworkspace/cli/issues/119 (personal account scope bug)
- https://github.com/googleworkspace/cli/issues/181 (multi-account bug)
