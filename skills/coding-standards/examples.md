# Coding Standards Examples

Worked pairs for the rules in `SKILL.md`. Concrete syntax lives here rather
than in the skill body, which stays stack-agnostic.

## The sentence states the promise

```python
# mechanism — true of today's body and of nothing else
def deliver(msg: Message) -> None:
    """Retries three times with exponential backoff, then writes the
    failure to the dead-letter table."""

# promise — binds the callers, not the body
def deliver(msg: Message) -> None:
    """Delivers msg exactly once, or raises DeliveryError; callers never
    observe a partial delivery."""
```

Switching to five retries, or to a circuit breaker, falsifies the first
silently, with no edit at its site. The second stays true under any body that
keeps the promise, and the edit that breaks it is a contract change every
caller must hear about anyway — the one moment documentation reliably gets
updated. Note what the second does NOT say: nothing about retries, backoff, or
the dead-letter table. A caller does not need them, and a reader who does
opens the body — which the sentence deliberately leaves them needing.

## The exception clause, earned: mechanism hiding a trap

```
# Keep the parentheses: && binds tighter than ||. Unparenthesized this
# becomes (locked-repo && protected-branch) || release-tag — and a tag
# token from ANY repository passes, bypassing the repository lock entirely.
request.repository == locked_repo && (ref.is_protected_branch || ref.is_release_tag)
```

Pure mechanism — operator precedence, visible in the line below — and still
protected: the trap it names, a security bypass behind an innocent-looking
simplification, is exactly what the code cannot show. Deleting the comment
invites the cleanup that opens the hole. This is the promise rule's exception
clause applied, not a violation of it.

## The why-not-what test

```python
# narrates — delete
# loop over items
for item in items:
    process(item)

# earns its line — states the non-obvious constraint
# insertion order matters: downstream dedup relies on last-write-wins
for item in items:
    process(item)
```

## The same rationale repeated across sibling blocks

The most common form of real redundancy is not a comment beside a line of
code — it is one rationale restated at every site that shares it. Sibling
blocks written in sequence attract it, and the copies drift apart one edit at
a time.

```hcl
# Opt-in and absent by default: when the variable is null the block is
# omitted, so a caller that never sets it sees no diff.
dynamic "addons_config" { ... }

# Opt-in and absent by default, same contract as addons_config above: when
# the variable is null the block is omitted, so a caller that never sets it
# sees no diff.
dynamic "gateway_api_config" { ... }

# Opt-in and absent by default, same contract as the two blocks above: when
# the variable is empty the block is omitted, so a caller that never sets it
# sees no diff.
dynamic "authenticator_groups_config" { ... }
```

The second and third comments announce that they are repeating, and repeat
anyway. State the contract once where it is first established; later sites
point at it without restating it.

## A comment that looks deletable and is not

Length is not what makes a comment redundant, and brevity is not what makes it
earn its place. This one survives every cleanup pass, because nothing in the
code can carry it:

```hcl
# Image pull. The kubelet pulls through the NODE identity, not through
# Workload Identity, so without this the first deployment of a workload whose
# image lives in Artifact Registry fails at pull with a 403 — regardless of
# how the workload's own service account is bound.
"roles/artifactregistry.reader",
```

The line it annotates is a single string. The comment carries an external
cause and a failure mode the reader would otherwise diagnose from a 403.

## A claim about elsewhere, made checkable

The claims that rot silently are the ones asserting state outside the file.
When one must be made, the coordinate is what separates a checkable claim from
folklore:

```python
# Retries are handled upstream.

# Retries are handled by JobRunner.dispatch (worker/runner.py), which
# re-delivers on timeout — so this handler must stay idempotent.
```

The first cannot be verified without already knowing the answer. The second
names the file and symbol to check, and it also carries a constraint on THIS
file — the idempotency requirement — that stays true even if the pointer goes
stale.

## Provenance versus process trace

```python
# TASK-142: implement retry per AC-3          <- process trace, delete
# Finding 7 remediation                        <- process trace, delete

# Permission set verified against the provider's own published role
# definitions; the API caps this field at 300 bytes, which is why the
# detail lives here rather than in the declaration.   <- provenance, keep
```

The first two point at a process a future reader cannot open. The third points
at a fact that reader can re-check, and it also records why the documentation
could not live in the native slot.
