---
name: subagent-request-approval
description: Use when a T3 command was blocked or a predictable ordered T3 set must be requested before execution
---

# Request Approval — Producer Branch

You ask for a signature by writing a few short phrases. Gaia builds everything
else the user sees -- the header, the numbered commands, the question with
Approve / Reject / Details, the Details page, the 30-minute validity, the ID
and the fingerprints -- and shows the same thing on every host.

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

Optional, per request: `--rollback` (how to undo it, shown in Details),
`--verification` (how you will confirm the result). Optional, per command:
`--cwd` (the existing directory it must run in, once for all or once per
command; the default is where you run the request) and
`--expect-exit POSITION=CODES` (non-zero exits that still let the set go on,
e.g. `2=1`). The signature names a command's directory only when it is not
the one you run the request from.

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
  --rollback 'volver al código anterior con --ref c1d8b89; la base queda actualizada.'
```

-- and the question the user then answers, built by Gaia, with every command
exact:

```
Solicitud de aprobación · gaia-system
Reinstalar Gaia en tu espacio de trabajo y actualizar su base de datos.

Comandos (1)
  1  python3 /home/jorge/ws/me/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia \
         dev \
         --workspace /home/jorge/ws/me \
         --ref bbc2f09 \
         --host all

¿Reinstalo Gaia?
```

with Approve / Reject / Details. Details asks again with each command's
`--does` and `--impact`, the rollback, the 30-minute validity, the exact
command, the ID and the fingerprint in place of the text.

A protected-path write is requested the same way with
`gaia approvals request-file-write --path <absolute path>` and one `--does` and
`--impact` for the edit.

Pass no `--session-id` or `--agent-id`: the request is sealed to the session and
agent that ran it, read from the dispatch environment on both hosts, and only
that agent in that session can use the signature.

## When a command was blocked first

A T3 command you ran without asking comes back denied, and the denial carries
a `gaia approvals request-set` (or `request-file-write`) line with the exact
command or path already in it. Replace each `<...>` with your phrases and run
that line. Your request replaces the one the block sealed without phrases. Do
not retry the command, reword it, or reach its effect another way.

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
  --impact 'Dispara la publicación del paquete; no se deshace.'
```

### Batches in sequence

Open a pull request, then merge it: the merge needs the PR number, which only
exists after the first step. Request the push and the PR creation now; after
they run and you read the number, request the merge in a new request.

## After requesting

The command prints the canonical `approval_id`. Set `agent_state` to
`APPROVAL_REQUEST`, fill `approval_request` (`agent-contract-handoff`) with that
id copied verbatim and the commands exactly as requested, set `pending_steps`
to the execution after consent, finalize, and stop. The orchestrator presents
it (`orchestrator-present-approval`); the approved work runs under `execution`.
In OpenCode, Gaia itself asks the user in your session as soon as your turn
ends, with no further step from you or the orchestrator.
A set that failed is not resumed: after fresh investigation, request what still
has to run as a new request.
