---
name: cli-login
description: Use when a CLI command fails because its login is missing or expired (gcloud, gh, gws, Microsoft 365) and the user has to sign in -- "necesito loguearme en gcloud", "la sesion de gcloud expiro", "log me in to gcloud", "reauthenticate gcloud".
---

# CLI Login

A login is the one step only the user can take: he opens a link in his own
browser and signs in with his password manager or passkey. The agent's part is
to start a login that needs no terminal, put the link in front of him, and
confirm afterwards that the session exists. No password and no token passes
through the agent, and the user's active account never changes unless he asks.

## Why the obvious ways fail

- **A login that waits for pasted input hangs.** `gcloud auth login
  --no-launch-browser` and `--no-browser` read a code from the terminal; a
  dispatched agent has none.
- **A background task can die before the click.** The host may stop an agent's
  background task when the turn ends, and the localhost listener that would
  receive the browser's answer dies with it.
- **A plain login switches the active account.** `gcloud auth login` activates
  the new account for every session on the machine, so it asks for a signature;
  `--no-activate` adds the account without switching.

## Procedure (gcloud)

1. **Start it detached.** `gaia login start gcloud --account <email>` runs
   `gcloud auth login <email> --no-activate` in its own session and prints JSON
   with `login_id`, `state: pending` and `url`. It runs free: it cannot switch
   the account.
   - **Already held.** When gcloud already stores a credential for the account
     it re-uses it and prints no link; the start exits 0 with
     `state: already_signed_in` and no `url`, and `gaia login status` reports
     the same. Nothing to click: continue the task.
   - **Expired or stale.** If commands with `--account <email>` still fail
     after `already_signed_in`, renew with `gaia login start gcloud --account
     <email> --force`. It runs `gcloud auth login <email> --no-activate --force`,
     returns a fresh link, and is free for the same reason: it never activates
     the account.
2. **Close the turn on the link.** Close `NEEDS_INPUT`. Put the `url` in your
   `conclusion` for the orchestrator, which hands it to the user as a link with
   one line: open it, sign in, and reply when done. Say it must be opened in a
   browser profile, or an incognito window, that holds only the target account:
   each link allows exactly one
   sign-in attempt, because gcloud stops listening after the browser's first
   answer. Record the `login_id` in `key_outputs`.
3. **On resume, confirm.** `gaia login status <login_id>` reports `pending`,
   `complete` or `failed`. `complete` means gcloud exited cleanly and `gcloud
   auth list` shows the account. Then continue the task with `--account <email>`
   on the commands that need it.
4. **Wrong account.** If he signs in with another account, gcloud refuses it
   with "the received credentials were for account [...]" and status goes to
   `failed`. The link is spent; recover with a fresh `gaia login start gcloud
   --account <email> --force`.
5. **On `failed` or a refused start, report it.** A start that reports the
   copy-code flow means gcloud found no browser it could launch; that machine
   needs the live interop check, not a retry.

The link redirects to `http://localhost:<port>`. Windows forwards localhost into
WSL, so the browser on Windows reaches the listener whether or not WSL interop
is enabled. The login stops on its own after 10 minutes.

## Other CLIs

Not wired yet. `gh auth login` makes the new account the active one, so adding
a GitHub account goes through an isolated gh config directory (next slice). gws
and Microsoft 365 follow the same start, link, confirm cycle once their
launchers exist.

## Anti-patterns

- **Running the login in a background Bash task and waiting.** The host can
  stop it before the user clicks; use `gaia login start`.
- **Reading `~/.config/gcloud` to check the session.** That path is
  read-refused; `gaia login status` and `gcloud auth list` are the checks.
- **Activating the account to make later commands shorter.** Pass `--account`
  per command instead.
