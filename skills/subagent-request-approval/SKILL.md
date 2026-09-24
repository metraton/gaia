---
name: subagent-request-approval
description: Use when a T3 command was blocked or a predictable ordered T3 set must be requested before execution
---

# Request Approval — Producer Branch

You ask for a signature by writing a few short phrases. Gaia builds everything
the user sees -- one question per command with Approve / Reject / Details, and
behind Details a second question that explains that command -- and shows the
same thing in Claude Code and in OpenCode. No model writes or copies any of it.

## What you write

Every phrase is required: Gaia refuses a request that lacks one, or has one
over its limit, and names the flag. Write them in the user's language, with
full spelling (accents included), for someone who does not read commands.

| Flag | What it says | Limit |
|------|--------------|-------|
| `--what` | The title: what the request does, in human words, with no commands, flags, paths or IDs. | 120 characters |
| `--question` | The short question the user answers. | 60 characters |
| `--does` | Once per `--command`, in order: what that command does. | 100 characters |
| `--impact` | Once per `--command`, in order: what changes for the user, and whether it can be undone. | 100 characters |
| `--rollback` | Once per request: how to undo it, as a sentence, not a command -- or, when it cannot be undone, a sentence that says so plainly. | one line |

`--does`, `--impact` and `--rollback` are what the user reads when they press
Details; `--what` and `--question` stay with the signature for whoever reviews
it later, but the question itself shows only you and the command.

The rollback is never optional. If the change can be undone, say how
("Borrar la rama del remoto."). If it cannot, write that it cannot and why
("No se puede deshacer: la versión publicada queda publicada."). A request
without `--rollback` is refused, and the refusal says what to write.

Optional, per request: `--verification` (how you will confirm the result).
Optional, per command:
`--cwd` (the existing directory it must run in, once for all or once per
command; the default is where you run the request) and
`--expect-exit POSITION=CODES` (non-zero exits that still let the set go on,
e.g. `2=1`). Details names a command's folder when it runs somewhere other
than where you asked from, or when the command uses relative paths.

One request holds at most 4 commands, because the host shows at most 4
questions at a time and each command is its own question. More than that goes
in another request.

A command sealed in another directory than the one your Bash runs in is run
as exactly `cd <sealed directory> && <sealed command>`, with the directory and
the command byte for byte as sealed. It is the only compound Gaia accepts; any
other directory, command or chain is refused.

Example -- the request:

```
gaia approvals request-set \
  --command 'python3 /home/jorge/ws/me/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia dev --workspace /home/jorge/ws/me --ref bbc2f09 --host all' \
  --what 'Reinstalar Gaia en tu espacio de trabajo y actualizar su base de datos.' \
  --question '¿Reinstalo Gaia?' \
  --does 'gaia dev: instala en tu espacio de trabajo la versión nueva de main.' \
  --impact 'actualiza tu base de datos; ese cambio no se deshace.' \
  --rollback 'Reinstalar la versión anterior; la base de datos queda actualizada.'
```

-- and what the user then sees, built by Gaia, headed `Firma 1/1`:

```
[GAIA-SECURITY] [ AGENT-REQUEST ] [ gaia-system ] [ COMMAND ] [ python3 /home/jorge/ws/me/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia dev --workspace /home/jorge/ws/me --ref bbc2f09 --host all ]
```

with Approve / Reject / Details. Details asks again, headed `Detalle 1/1`, with
your phrases under fixed English labels:

```
[GAIA-SECURITY] [ DETAILS ] [ gaia-system ] [ COMMAND: python3 ... --host all ] [ DOES: gaia dev: instala en tu espacio de trabajo la versión nueva de main. ] [ IMPACT: actualiza tu base de datos; ese cambio no se deshace. ] [ ROLLBACK: Reinstalar la versión anterior; la base de datos queda actualizada. ]
```

A protected-path write is requested the same way with
`gaia approvals request-file-write --path <absolute path>`, one `--does` and
`--impact` for the edit, and its `--rollback`.

Pass no `--session-id` or `--agent-id`; the request refuses them. It is sealed
to the session and agent that ran it, read from the dispatch environment on
both hosts, and only that agent in that session can use the signature.

## When a command was blocked first

A T3 command you ran without asking comes back denied, and the denial carries
a `gaia approvals request-set` (or `request-file-write`) line with the exact
command or path already in it. Replace each `<...>` with your phrases and run
that line. Your request replaces the one the block sealed without phrases:
Gaia withdraws that automatic one by itself and prints `Withdrew <id>` for it,
so you do not reject it. Do not retry the command, reword it, or reach its
effect another way.

## How many requests

Ask for the fewest signatures your knowledge allows. Everything you already
know exactly goes into one request, in the order it must run; anything whose
exact form depends on a result you have not seen yet waits for a later
request. Never request a command you cannot yet write byte for byte.

### One T3 alone

One push: one request with one `--command`.

### A known batch

Push a branch and its tag: both are known now, so they go in one request, in
order, with one `--does` and one `--impact` each:

```
gaia approvals request-set \
  --command 'git -C /repo push origin main' \
  --command 'git -C /repo push origin v1.4.0' \
  --what 'Publicar la rama principal y la versión 1.4.0.' \
  --question '¿Publico la versión 1.4.0?' \
  --does 'Sube la rama principal al repositorio remoto.' \
  --impact 'Los demás ven los cambios; se revierte con otro commit.' \
  --does 'Publica la etiqueta v1.4.0.' \
  --impact 'Dispara la publicación del paquete; no se deshace.' \
  --rollback 'La rama se revierte con otro commit; la versión publicada no se puede deshacer.'
```

### Batches in sequence

Open a pull request, then merge it: the merge needs the PR number, which only
exists after the first step. Request the push and the PR creation now; after
they run and you read the number, request the merge in a new request. The
first request, run from `/home/jorge/ws/me` with the push sealed in the
repository:

```
gaia approvals request-set \
  --command 'git push origin feature/demo-login' \
  --cwd /home/jorge/ws/me/demo-repo \
  --command 'gh pr create --repo metraton/demo --base main --head feature/demo-login --title "Login de prueba" --body-file /home/jorge/.gaia/scratch/demo-1234/pr.md' \
  --cwd /home/jorge/ws/me \
  --what 'Publicar la rama de prueba y abrir su pull request.' \
  --question '¿Publico la rama y abro el PR?' \
  --does 'Sube la rama feature/demo-login al repositorio remoto.' \
  --impact 'Otros ven la rama; se puede borrar del remoto.' \
  --does 'Abre el pull request de la rama hacia main.' \
  --impact 'Queda visible en GitHub; se puede cerrar sin fusionar.' \
  --rollback 'Cerrar el pull request sin fusionar y borrar la rama del remoto.'
```

The user gets two questions, `Firma 1/2` and `Firma 2/2`, one per command.
Only the push's Details names its folder, because only the push runs
elsewhere:

```
[GAIA-SECURITY] [ DETAILS ] [ developer ] [ COMMAND: git push origin feature/demo-login ] [ DOES: Sube la rama feature/demo-login al repositorio remoto. ] [ IMPACT: Otros ven la rama; se puede borrar del remoto. ] [ ROLLBACK: Cerrar el pull request sin fusionar y borrar la rama del remoto. ] [ CWD: /home/jorge/ws/me/demo-repo ]
```

When one question call asks several signatures, each header names its
signature by a letter and counts inside it: `Firma A 1/2`, `Firma A 2/2`,
`Firma B 1/1`, and behind Details `Detalle A1/2`...

## After requesting

The command prints the canonical `approval_id`. Set `agent_state` to
`APPROVAL_REQUEST`, fill `approval_request` (`agent-contract-handoff`) with that
id copied verbatim and the commands exactly as requested, set `pending_steps`
to the execution after consent, finalize, and stop. The orchestrator presents
it (`orchestrator-present-approval`); the approved work runs under `execution`.
This is the same in Claude Code and OpenCode: nothing is asked in your
session, neither when you request nor when your turn ends. The orchestrator
reads the approval id from your contract and decides when to ask the user.
A set that failed is not resumed: after fresh investigation, request what still
has to run as a new request.
