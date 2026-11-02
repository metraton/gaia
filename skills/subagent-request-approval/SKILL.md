---
name: subagent-request-approval
description: Use when a T3 command was blocked or a predictable ordered T3 set must be requested before execution
---

# Request Approval — Producer Branch

You ask for a signature by writing a few short phrases. Gaia builds everything
the user sees -- in Claude Code one question per signature listing its
commands, in OpenCode one question per command, each with Approve / Reject /
Details, and behind Details a second question that explains it. No model
writes or copies any of it.

## What you write

Every phrase is required: Gaia refuses a request that lacks one, or has one
over its limit, and names the flag. Write them in English, in full sentences,
for someone who does not read commands: Gaia shows each phrase verbatim inside
its own English frame.

| Flag | What it says | Limit |
|------|--------------|-------|
| `--what` | The title: what the request does, in human words, with no commands, flags, paths or IDs. | 120 characters |
| `--question` | The short question the user answers. | 60 characters |
| `--does` | Once per `--command`, in order: what that command does. | 100 characters |
| `--impact` | Once per `--command`, in order: what changes for the user, and whether it can be undone. | 100 characters |
| `--rollback` | Once per request: how to undo it, as a sentence, not a command -- or, when it cannot be undone, a sentence that says so plainly. | one line |
| `--verification` | Once per `request-set`: how you will confirm the result. | one line |
| `--shared-state` | Once per `request-set`: whether it rewrites state other people rely on (a shared branch, a cluster, a registry) and which -- or that it touches none. | one line |

`--does`, `--impact`, `--verification`, `--shared-state` and `--rollback` are
what the user reads when they press Details. In Claude Code the question itself
shows you, the commands and `--what` as what the set does, so `--what` must
describe the whole set; in OpenCode it shows only you and the command.
`--question` stays with the signature for whoever reviews it later.

The rollback is never optional. If the change can be undone, say how
("Delete the branch from the remote."). If it cannot, write that it cannot and why
("Cannot be undone: the published version stays published."). A request
without `--rollback` is refused, and the refusal says what to write. A
`request-set` without `--verification` or `--shared-state` is refused the same
way.

The signature covers what the command executes, not only its text. The content
of each file a command runs or reads -- an interpreter's script (`bash
deploy.sh`), a program given as a path, a `--<name>-file` value, a
`-f`/`--file`/`--filename`/`--values` value -- is sealed when you request. So
write the file first: a script or `--<name>-file` that does not exist yet is
refused, as is a directory. A `-f` value that is not a
file yet (it may be a word, as in `git push -f origin`) is sealed as absent.
An ELF binary or a file over 64 MiB is sealed by its identity (device, inode,
size, modification and change times) instead of its content, so replacing or
rewriting it breaks the match all the same.
If any sealed file changes before the command runs -- or an absent one appears
-- the command no longer matches the signature and is blocked; request it
again.

Optional, per command:
`--cwd` (the existing directory it must run in, once for all or once per
command; the default is where you run the request) and
`--expect-exit POSITION=CODES` (non-zero exits that still let the set go on,
e.g. `2=1`; repeat the flag, or list codes with commas, for more than one code).
Declare it for every command whose non-zero exit is part of its normal contract
-- a `git rebase` that stops on a conflict you will resolve, a `git cherry-pick`
likewise -- because only a declaration made here lets the set go on; an
undeclared non-zero exit freezes it and the rest needs a new request. The user
sees each declared exit beside that command, as `(allowed exit: 1)` in
Claude Code and `ALLOWED EXIT` in OpenCode. Details
names a command's folder when it runs somewhere other
than where you asked from, or when the command uses relative paths.

When every command of a request is `git -C <same absolute folder> ...`,
Claude Code's question names that folder once in its first line
(`Signature 1/1: gaia-system requests 2 commands in ~/repo`, `~` only for an exact home
prefix) and numbers each command without its `-C <folder>`; Details still
shows every full command. Any other block is shown verbatim. Only the display
changes: the signature binds each full command and its sealed folder.

One request holds at most 4 signed commands (`surface.check_phrases` refuses
more when you request), because OpenCode asks each signed command as its own
question and a host shows at most 4 at a time. More than that goes in another
request.

A step of the same plan that is not T3 -- a local `git commit` between two
pushes, a test run -- goes in the request in its position, with its own
`--does` and `--impact`. Gaia seals it unsigned: the user sees it in order,
marked `(unsigned step)` in Claude Code and `UNSIGNED STEP` in OpenCode, but
it asks nothing of its own and holds no place in the
signature, so you run it like any other command of yours. A request with no
T3 step at all is refused.

A command sealed in another directory than the one your Bash runs in is run
as exactly `cd <sealed directory> && <sealed command>`, with the directory and
the command byte for byte as sealed. It is the only compound Gaia accepts; any
other directory, command or chain is refused.

Example -- the request:

```
gaia approvals request-set \
  --command 'python3 <workspace>/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia dev --workspace <dev-install-folder> --ref bbc2f09 --channel npm' \
  --what 'Reinstall Gaia in your workspace and upgrade its database.' \
  --question 'Reinstall Gaia?' \
  --does 'gaia dev: installs the new version of main in your workspace.' \
  --impact 'Upgrades your database; that change is not undone.' \
  --verification 'gaia doctor shows the new version.' \
  --shared-state 'No: only your local workspace.' \
  --rollback 'Reinstall the previous version; the database stays upgraded.'
```

-- and what the user then sees in Claude Code, built by Gaia. The question's
first line carries its title, because the desktop app does not show the
question's header:

```
Signature 1/1: gaia-system requests 1 command

1. python3 <workspace>/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia dev --workspace <dev-install-folder> --ref bbc2f09 --channel npm

What it does: Reinstall Gaia in your workspace and upgrade its database.
```

with Approve ("Authorizes exactly this command") / Reject / Details. Details
asks again:

```
Details 1/1: gaia-system requests 1 command

1. python3 <workspace>/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia dev --workspace <dev-install-folder> --ref bbc2f09 --channel npm
   Does: gaia dev: installs the new version of main in your workspace.
   Impact: Upgrades your database; that change is not undone.

What it does: Reinstall Gaia in your workspace and upgrade its database.
Verification: gaia doctor shows the new version.
Shared state: No: only your local workspace.
Undo: Reinstall the previous version; the database stays upgraded.
```

OpenCode shows the same signature as one line per command, in the bracketed
format `[ GAIA-SECURITY ] [ AGENT-REQUEST ] [ gaia-system ] [ COMMAND ] [ ... ]`,
and its Details under fixed English labels (`[ DOES: ... ] [ IMPACT: ... ]`);
its headers (`Firma 1/1`, `Detalle 1/1`) and option descriptions are still Spanish.

For a `git worktree remove --force`, Details adds a `Captured:` line
(`[ CAPTURED: ... ]` in OpenCode) naming the diff `gaia worktree release`
already deposited, its size and a files/lines summary, or stating that no
capture is recorded.

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

One request is one purpose. It groups only the commands that serve that one
purpose -- the steps of one change the user decides as a whole; in Claude Code
they are one question and one answer. Commands that serve unrelated purposes
(another repository, another service, another change) go as separate requests,
each its own signature and its own question, so the user can approve one and
reject the other. Gaia does not detect a mixed set for you: the grouping is
yours.

Within one purpose, ask for the fewest signatures your knowledge allows.
Everything you already know exactly goes into one request, in the order it
must run; anything whose exact form depends on a result you have not seen yet
waits for a later request. Never request a command you cannot yet write byte
for byte.

### One T3 alone

One push: one request with one `--command`.

### A known batch

Push a branch and its tag: both are known now, so they go in one request, in
order, with one `--does` and one `--impact` each:

```
gaia approvals request-set \
  --command 'git -C /repo push origin main' \
  --command 'git -C /repo push origin v1.4.0' \
  --what 'Publish the main branch and version 1.4.0.' \
  --question 'Publish version 1.4.0?' \
  --does 'Pushes the main branch to the remote repository.' \
  --impact 'Others see the changes; reverted with another commit.' \
  --does 'Publishes the v1.4.0 tag.' \
  --impact 'Triggers the package release; cannot be undone.' \
  --verification 'git ls-remote shows main and v1.4.0 on the remote.' \
  --shared-state 'Yes: the main branch and the tags of the remote.' \
  --rollback 'The branch is reverted with another commit; the published version cannot be undone.'
```

Both commands name `/repo` with `-C`, so Claude Code asks
`Signature 1/1: gaia-system requests 2 commands in /repo` followed by `1. git push origin main`
and `2. git push origin v1.4.0`.

### Batches in sequence

Merge a pull request, then publish its version: the tag needs the merge
commit, which only exists after the merge. Pushing the feature branch and
opening the PR run free (`security-tiers`), so they are run, never requested.
Request the merge and the deletion of the merged branch now; after they run
and you read the merge commit, request the tag push in a new request. The
first request, run from `<workspace>` with the deletion sealed in the
repository:

```
gaia approvals request-set \
  --command 'gh pr merge 42 --repo metraton/demo --squash' \
  --cwd <workspace> \
  --command 'git push origin --delete feature/demo-login' \
  --cwd <workspace>/demo-repo \
  --what 'Merge the demo branch PR and delete the branch.' \
  --question 'Merge the PR and delete the branch?' \
  --does 'Merges pull request 42 into main.' \
  --impact 'main receives the changes; reverted with another commit.' \
  --does 'Deletes the feature/demo-login branch from the remote.' \
  --impact 'The branch no longer exists on GitHub; it can be pushed again from the local clone.' \
  --verification 'gh pr view 42 shows the PR merged; git ls-remote no longer lists the branch.' \
  --shared-state 'Yes: the main branch and the branches of the remote.' \
  --rollback 'Revert the merge commit on main and push the branch again.'
```

In Claude Code the user gets one question, `Signature 1/1`, listing both
commands verbatim; in OpenCode two, `Firma 1/2` and `Firma 2/2`, one per
command. Only the deletion's Details names its folder (`Folder:
<workspace>/demo-repo`, `[ CWD: ... ]` in OpenCode), because only the deletion
runs elsewhere.

When one Claude Code question call asks several signatures, each is its own
question, `Signature 1/2` and `Signature 2/2`, approved or rejected on its own. In
OpenCode each header names its signature by a letter and counts inside it:
`Firma A 1/2`, `Firma A 2/2`, `Firma B 1/1`, and behind Details `Detalle A1/2`...

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
has to run as a new request. A single command that failed keeps its signature
for one retry of the same bytes in the same directory within the window; a
second failure needs a new request.
