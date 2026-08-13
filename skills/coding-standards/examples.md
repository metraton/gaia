# Coding Standards Examples

Worked pairs for the rules in `SKILL.md`. Concrete syntax lives here so the skill body stays
stack-agnostic. The infrastructure pairs quote a production identity bootstrap
(`bootstrap/identity/main.tf` of an artifact platform); the "after" of each pair is what the rules
produce on that real file. Every "after" holds within the two-line ceiling — including the ones
whose "before" was a protected fact, because that is exactly where the ceiling bites.

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

Switching to five retries, or to a circuit breaker, falsifies the first silently, with no edit at
its site. The second stays true under any body that keeps the promise, and the edit that breaks it
is a contract change every caller must hear about anyway — the one moment documentation reliably
gets updated. Note what the second does NOT say: nothing about retries, backoff, or the
dead-letter table. A caller does not need them, and a reader who does opens the body — which the
sentence deliberately leaves them needing.

## A promise the code does not keep

```python
def purge_expired(sessions: list[Session]) -> int:
    """Deletes expired sessions and their attachments; returns how many were purged."""
    count = 0
    for session in sessions:
        if session.expired:
            db.delete(session)   # attachments: not yet handled
            count += 1
    return count
```

The docstring promises attachment deletion the body never performs. This is the iron law's case:
either the code is wrong (implement the attachment cleanup) or the sentence is (promise only what
is kept — "Deletes expired sessions; attachments are the caller's to release"). The sentence is
never left standing as a plan: a caller trusting it today leaks attachments today, and no future
implementation retroactively repairs the callers written in between.

## A trap survives, and still gets cut to two lines

The comment as it stood, five lines of it, on a production workload-identity condition:

```hcl
# ... ORed inside ONE term because the module ANDs list entries together — and
# the whole term is parenthesized, because CEL's `&&` binds tighter than `||`:
# without the outer parentheses the module's join would render
# `repo-lock && main-arm || tag-arm`, letting a tag token from ANY repo
# bypass the repository lock entirely.
```

The fact passes the test twice over: pure mechanism, yet the trap it names — a security bypass
hiding behind an innocent-looking simplification — is exactly what the code cannot show, and
deleting the comment invites the cleanup that opens the hole. Passing the test is not a licence to
keep the five lines:

```hcl
# Parenthesized: CEL `&&` binds tighter than `||`, so without the outer parens a
# tag token from ANY repo bypasses the repo lock. ORed in ONE term: the module ANDs.
additional_attribute_conditions = [
  "(assertion.ref == 'refs/heads/main' || (assertion.ref_type == 'tag' && assertion.ref.startsWith('refs/tags/')))",
]
```

What was cut is the walk-through of the rendered expression. A reader now has the precedence rule
and the module's behaviour, which is everything needed to derive that rendering. The category
protected the trap; it never protected the derivation.

## A fact the code cannot show, next to one it already shows

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

## Placement: the motive adheres to its item

Before — a block comment re-enumerates, with bullets, the roles of the list beneath it, so every
role name appears twice, ten lines apart. Whoever edits `"roles/iam.roleAdmin"` in the list edits
it far below the sentence that justifies it, and never sees that sentence:

```hcl
# Sized to what platform/registry declares — no admin catch-all beyond the
# <service>.admin convention the seed's role sets follow:
#   * storage.admin        — the registry bucket: create/configure and set
#     its bucket-scoped IAM (publisher + reader grants). Bucket creation is
#     a project-level permission, so this cannot be bucket-scoped; it also
#     subsumes the state-bucket reads the GCS backend needs at init, so no
#     separate state-bucket grant is declared for this SA.
#   * iam.roleAdmin        — the create-only custom role the registry
#     declares (project-level custom roles).
#   * artifactregistry.admin — the charts and images repositories.
#   * serviceusage.serviceUsageConsumer — call the enabled APIs.
apply_sa_roles = [
  "roles/storage.admin",
  "roles/iam.roleAdmin",
  "roles/artifactregistry.admin",
  "roles/serviceusage.serviceUsageConsumer",
]
```

After — each surviving motive rides its own item, within the ceiling, and the only sentence left
above the block is the one that fails the relocation test because it states how the SET was sized,
not what any member does:

```hcl
# Sized to what platform/registry declares — no admin catch-all beyond the
# <service>.admin convention the seed's role sets follow.
apply_sa_roles = [
  # Bucket creation is project-level, so this cannot be bucket-scoped; it also
  # subsumes the state-bucket reads the GCS backend needs at init.
  "roles/storage.admin",
  "roles/iam.roleAdmin",                     # create-only custom role
  "roles/artifactregistry.admin",
  "roles/serviceusage.serviceUsageConsumer",
]
```

Three of the four annotations went entirely: naming the charts and images repositories, or saying
that the service-usage role calls the enabled APIs, narrates what the role string already says.
The one that stayed carries a constraint no reader derives from `roles/storage.admin`.

The same `locals` block carries the correct counterpart — a comment that is entirely set-level and
enumerates nothing:

```hcl
# Read-only baseline for the plan identity: no write or admin role, ever —
# a pull request must never be able to mutate infrastructure.
plan_sa_roles = [ ... ]
```

Both sentences are invariants of the whole list; neither could sit beside a single member without
losing its meaning, so it stays where it is. The third sentence it used to carry — that this is the
same baseline as the seed's plan set — was a claim about another module's current contents, and
went with the rest.

## The second site stays silent

Three versions of one second site, from the same file. The first site already carries the
fail-closed rationale:

```hcl
# ... Both claims are asserted: `ref_type` states the intent directly;
# `ref` is what the principalSet and the audit log actually show. A missing
# claim errors, and an erroring condition denies, so the pair fails closed.
```

The near-miss — the version that catches a careful writer. It names the sibling and then restates
it whole:

```hcl
# For the tag arm both claims are asserted, exactly as the factory pool
# does: `ref_type` states the intent directly; `ref` is what the
# principalSet and the audit log actually show. A missing claim errors, and
# an erroring condition denies, so the disjunction fails closed.
```

Naming the sibling feels like compliance; everything after the colon is the duplication — two
copies that drift apart one edit at a time. Cutting the copy and keeping the pointer is the
version that used to be the cure, and it is not:

```hcl
# For the tag arm both claims are asserted, fail-closed, exactly as the
# factory pool above; ORed inside ONE term because the module ANDs entries.
```

"Exactly as the factory pool above" is an assertion about another site. It holds until someone
moves the pool, renames it, or rewrites its rationale — and none of those edits will ever pass by
this line. What is left is the fact that belongs HERE and nowhere else:

```hcl
# ORed inside ONE term: the module ANDs list entries together.
```

The rationale exists at one site. This site carries what is new at this site. Nothing points at
anything, so nothing can point at the wrong place, at a site that no longer holds the content, or
back at a site that points here.

## An exception that fails its own burden of proof

The burden of proof is checkable — here is a claim failing it:

```python
# Extracting this would tangle the flow, so it stays inline: normalize
# header keys, drop empty values, lowercase both sides.
clean = {}
for key, value in headers.items():
    if value:
        clean[key.strip().lower()] = value.strip().lower()
```

The claimed tangle, tested by writing the extraction:

```python
def normalized_headers(headers: dict[str, str]) -> dict[str, str]:
    """Lowercased, stripped copies of the non-empty headers."""
    return {k.strip().lower(): v.strip().lower() for k, v in headers.items() if v}
```

No tangle appeared: the comment and the loop dissolve into a name and a promise. A successful
exception here would have been a template for the excuse; a falsified one teaches that the excuse
is testable — which is why the burden demands the attempt, not the assertion.

## Two comments of equal length; length decides nothing

Length is not what makes a comment redundant, and brevity is not what makes it earn its place.
Two comments of comparable size, on comparable lines — single role strings in the same kind of
list:

```hcl
# Log writing. Grants the logging writer role so the node pool can send its
# log entries to Cloud Logging, which is the service where the logs are
# written and where whoever needs to read what was logged can view them.
"roles/logging.logWriter",

# Image pull. The kubelet pulls through the NODE identity, not through
# Workload Identity, so without this the first deployment of a workload whose
# image lives in Artifact Registry fails at pull with a 403 — regardless of
# how the workload's own service account is bound.
"roles/artifactregistry.reader",
```

The first narrates the role's name back at the reader and points nowhere the code does not already
go: delete it, whole. The second carries an external cause and a failure mode the reader would
otherwise diagnose from a bare 403 — nothing in the code can carry it. It survives, and the
ceiling still applies to it:

```hcl
# The kubelet pulls through the NODE identity, not Workload Identity: without
# this, an image in Artifact Registry fails at pull with a 403.
"roles/artifactregistry.reader",
```

Same length before, same shape of line. What separated them was whether the comment points outside
the code; what shortened the survivor was the ceiling, which applies to survivors precisely because
they are the only comments left to inflate.

## Several facts, one price each

A real rejected-alternatives block that survived a cold application of this skill at nine lines,
because "at most two lines" was read as a budget for the comment rather than for each fact:

```hcl
# roles/viewer does not carry storage.buckets.getIamPolicy, so refreshing a
# google_storage_bucket_iam_member 403s the moment a unit declares
# bucket-scoped IAM. Rejected alternatives:
#   * no predefined read-only role grants getIamPolicy without also
#     granting setIamPolicy (legacyBucketReader lacks it; legacyBucketOwner
#     also grants setIamPolicy, making it a write role);
#   * iam.securityReviewer grants *.getIamPolicy across every service — far
#     wider than the need;
#   * storage.admin is a write role and can never sit on the plan identity.
```

Every fact in it passes the test — the external cause, and three alternatives a reader would
otherwise re-litigate. Nothing is deleted here; what goes is the prose around each one:

```hcl
# roles/viewer lacks storage.buckets.getIamPolicy, so refreshing a bucket IAM
# member 403s once a unit declares bucket-scoped IAM. Rejected:
#   * every predefined read-only role that grants getIamPolicy also grants setIamPolicy
#   * iam.securityReviewer: *.getIamPolicy across every service, far wider than the need
#   * storage.admin: a write role, never on the plan identity
```

Nine lines to five, with the same four facts standing. The dry list is what the ceiling produces
when there are several facts: one line apiece, no paragraph apiece.

## The fact without its ancestor

Lineage survived a cold pass under an explicit defence — "durable provenance, design lineage, not
elsewhere-state". Three specimens from one file:

```hcl
# ... Same pool ID the seed's own registry instance uses, so a factory-owned
# pool is legible as such wherever it appears.

# ... Must contain no write/admin role. Same baseline as the seed's
# deploy-sa-roles plan set.

# ... it also subsumes the state-bucket object read/write the GCS backend needs
# at init (the objectAdmin + legacyBucketReader lesson from the seed's
# iam-deploy-sa) ...
```

Each carries a real fact and an ancestor. The fact is why the platform behaves this way — durable,
re-derivable from the provider. The ancestor is a comparison against a codebase that keeps moving,
and no edit over there will ever reach these lines. Keep the first, drop the second:

```hcl
# A factory-owned pool is legible as such wherever this ID appears.

# No write/admin role: a pull request must never be able to mutate infrastructure.

# Subsumes the state-bucket object read/write the GCS backend needs at init.
```

Provenance would have been a source a reader can re-check — the provider's published role
definitions, a standard, an issue in the dependency. "As the seed does" is not that.

## A banner is exempt as form, not as content

```hcl
# --- Operator impersonation (explicit identity, phase 2) ---------------------
```

Survived three consecutive measurements of this skill, the last one sheltering inside the
separator. The layout is not the question — the repository uses these banners, so it keeps them.
"phase 2" is a claim about a process, riding along inside the exempt form:

```hcl
# --- Operator impersonation (explicit identity) ------------------------------
```

## The constraint here, not the state over there

The claims that rot silently are the ones asserting state outside the file, and an earlier version
of this skill tried to rescue them with a coordinate:

```python
# Retries are handled upstream.

# Retries are handled by JobRunner.dispatch (worker/runner.py), which
# re-delivers on timeout — so this handler must stay idempotent.
```

The first cannot be verified without already knowing the answer. The second is cheaper to check and
still rots: `JobRunner.dispatch` can move, be renamed, or stop re-delivering, and nothing in that
change ever reaches this line. Only the last clause is about this code, and only it survives:

```python
# Must stay idempotent: deliveries can repeat.
```

The requirement holds no matter which component upstream causes the repeat, or whether it exists at
all yet. Ask of every sentence about elsewhere: what does it require of THIS code? That is the part
to keep, and usually it fits in one line.

## Provenance versus process trace

```python
# TASK-142: implement retry per AC-3          <- process trace, delete
# Finding 7 remediation                        <- process trace, delete
# verified live against acme-prod-7734         <- environment trace, delete

# Verified against the provider's published role definitions; the API caps
# this field at 300 bytes, so the detail lives here.   <- provenance, keep
```

The first two point at a process a future reader cannot open. The third carries a real fact and a
client identifier at once — keep the fact in durable form, drop the identifier, and say plainly
that exact re-verification was what you traded away. The last points at something the reader can
re-check, and records why the documentation could not live in the native slot.
