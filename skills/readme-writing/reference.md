# README Writing Reference

Filled examples and blank skeletons for the readme-writing skill.

**This file branches the same way the skill does.** Step 1 of [`SKILL.md`](SKILL.md) names one of three gates, and that gate decides which part of this file is yours. Read your gate's part; do not borrow sections from another one. A section written well but belonging to a different gate is still the wrong artifact.

| Gate | Its filled example | Its blank skeleton |
|------|--------------------|--------------------|
| **Repository root** | "Repository root -- filled example" | "Repository root -- skeleton" |
| **Component folder** | "Component folder -- filled example: a migrations folder" | "Component folder -- skeleton" |
| **Shipped template** | "Shipped template -- filled example" | "Shipped template -- skeleton" |

## Fill every section of your gate, and only of your gate

Within a gate every section is written, and a thin one is declared thin rather than deleted or invented -- that rule and the home for a decision record are in [`SKILL.md`](SKILL.md), Step 2.

**Across gates, the opposite holds.** A repository root gets no "How it is wired in", and a shipped template gets no "Conventions", because those sections answer a question that gate's reader never asks. Carrying a section over is not rigor; it is filling in the wrong template carefully. When a section from another gate feels unfillable, that is the correct signal -- it was never yours to fill.

The cost of getting this backwards is not a rough draft. It is a finished, well-written README that answers the wrong reader's question, which is far harder to notice than an incomplete one.

## Every flow in this file is plain text

Numbered steps and simple `->` arrows inside a plain code block, never mermaid -- the reason is in [`SKILL.md`](SKILL.md) and is not repeated here. Every flow below is written that way, including the ones inside the filled examples.

---

## Repository root -- filled example

Abridged from a real infrastructure-factory repo. It is here for **shape** -- section order, how a flow reads as plain text, how a tree annotates -- not to be copied literally. The original runs about 190 lines, down from 665, and nearly all of that reduction came from moving content out to the documents that own it, per Step 4 of the skill.

````markdown
# acme-factory

A GCP environment factory: one versioned Terraform catalog, one generated infrastructure repo per client account.

## What it is and why it exists

Standing up a client's cloud environment by hand means someone recreates the
same network, the same identities, and the same CI wiring for every client --
slightly differently each time. Forking a reference repo per client ends the
same way: the copies agree on the day they are made and drift apart from the
first fix applied to only one of them.

This repository removes both by making the environment a *product* of a
reviewed catalog rather than a copy of a previous environment. It is a
factory, and it holds no client instance of itself: its parts are the
Terraform modules under [`modules/`](modules/), versioned by git tag, and its
assembly line is the installer under [`installer/`](installer/), which renders
one self-contained repository per client account.

Two audiences touch it. A **platform engineer** authors or reviews the modules
and the installer templates. An **operator onboarding a client** runs the
installer and then one manual bootstrap. After that, CI in the generated repo
is the only thing that reaches infrastructure.

## Flow

```
1. Platform engineer opens a pull request
     -> the field-limit gate runs against modules/
2. A version tag is pushed
     -> module tarballs publish to the registry bucket
3. Onboarding operator runs the installer
     -> a self-contained client repo is rendered
4. Operator runs one manual bootstrap against the client GCP account
     -> deploy identities and the state bucket now exist
5. The client repo resolves modules from the registry by pinned tag
6. Its CI plans on every pull request and applies on merge
```

## Requirements

The engine of record is Terraform, pinned `1.11.4`, driven by Terragrunt
`0.72.6`. The installer is Python 3 standard library only -- there is nothing
to `pip install`. Onboarding additionally needs `gcloud`, `gh`, and `git`.

Module resolution authenticates through Application Default Credentials, so
`gcloud auth application-default login` must be current. `gcloud config get
account` reads a *different* store and proves nothing about what an apply will
fetch with.

| Grant | On | Why |
|---|---|---|
| `roles/billing.user` | the billing account | link billing to the seed project |
| `roles/owner` | the seed project | the bootstrap provisions a WIF pool; Editor cannot |
| `roles/storage.objectViewer` | the registry bucket | every module is fetched from it |

Read on the registry is not granted by default and is the first prerequisite
of the whole flow. Without it, `terragrunt init` fails with a 403 on a storage
object, which reads like a bad URL rather than an IAM gap.

## How it is used

```bash
python3 installer/scaffold.py --list-fields
python3 installer/scaffold.py --out-dir ./acme-iac --client=acme --env=staging
```

The render makes no network calls and creates nothing remote, so running it
before the registry grant is in place costs nothing. It reports what it
produced and the layout it chose:

```
Rendered 47 files into ./acme-iac (account=acme, environment=staging)
```

## Structure

```
acme-factory/
├── modules/       # the factory's parts: 18 Terraform modules, versioned by git tag
├── installer/     # renders a client repo from those modules
├── operations/    # the factory's OWN infrastructure, not a client instance
├── scripts/       # repo-level tooling run by CI and by hand
└── CONTRIBUTING.md
```

- [`installer/README.md`](installer/README.md) -- mechanism rationale and the
  complete per-field input catalogue with defaults.
- [`operations/README.md`](operations/README.md) -- why the factory's own
  infrastructure is categorically distinct from a client instance.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) -- why `main` is squash-only, and how
  modules are tagged for consumers.

## Ownership

Internal and proprietary. Not licensed for distribution outside the
organization.
````

Two things in there are worth copying deliberately. **Requirements names the failure each missing grant produces**, not just the grant -- a 403 that "reads like a bad URL" is the sentence that saves the reader an hour. And **the Structure section links out rather than explaining down**: each bullet says what that document owns, so the root never becomes a second copy of it.

## Repository root -- skeleton

````markdown
# <name>

<One line, under 120 characters: what this is.>

## What it is and why it exists

<The problem it solves. Who uses it. What it produces. Why the obvious
alternative was rejected.>

## Flow

```
1. <actor or trigger>
     -> <what it produces>
2. <next step>
     -> <what it produces>
```

<Plain text only: numbered steps and `->` arrows. No mermaid, no rendered
format. What it interacts with outside this repo goes here.>

## Requirements

<Tools with pinned versions. Permissions and credentials -- and for each one,
the failure a reader gets when it is missing.>

## How it is used

```bash
<the real invocation>
```

<The output it is expected to produce.>

## Structure

```
<repo>/
├── <dir>/   # <why this exists>
└── <dir>/   # <why this exists>
```

- [`<path>`](<path>) -- <what that document owns that this README does not>

## License or ownership
````

---

## Component folder -- filled example: a migrations folder

Written against the real `prisma/migrations/` of a Next.js mail application -- 17 migration folders, a `migration_lock.toml`, and a GitHub Actions job that applies them. Use it as the standard for voice, section depth, and the detail the wiring section needs **for this gate**.

It is deliberately **applied in order** rather than triggered. That folder does have a trigger -- a merge to `main` runs the apply job -- and the trigger is still not the load-bearing fact: what a person changing something there needs to know is that the past is frozen and corrections only go forward. Read how the mode is named, and how the second half names the mistakes the folder actually punishes rather than what would break if the folder disappeared.

````markdown
# Migrations

This folder is the database's history, not its description. Every subfolder is one
irreversible step that has already run somewhere, and the schema you end up with is
the sum of them applied in order -- never any single file in isolation. What the
database looks like right now is described in [`../schema.prisma`](../schema.prisma);
this folder is how it got there.

It exists separately from that schema for one reason: the schema can be edited and
this cannot. `prisma migrate dev` diffs the edited schema against the history here
and writes the difference as a new folder. From the moment that folder has been
applied anywhere, its SQL is frozen -- correcting it means adding another step, never
changing the step that ran.

The mental model is an append-only ledger with a counterpart on the other side.
Postgres keeps a `_prisma_migrations` table naming every folder it has already
executed. Applying compares the two lists and runs only the difference. Nothing here
is ever re-run and nothing here is ever rolled back.

A developer creates folders here by running `prisma migrate dev` locally; nobody
writes one by hand. CI is what applies them to production. No application code
imports anything from this folder.

---

## How it is wired in

**Mode: applied in order.** The order is the lexicographic sort of the folder names,
which is why each one carries a UTC timestamp prefix and why the hand-named
`0001_init` baseline sorts ahead of all of them. The record of what has already run
lives in the target database, in `_prisma_migrations` -- not in this repository. That
split is the whole mechanic: this folder is the intent, that table is the fact, and
the two can disagree.

```
1. A developer edits ../schema.prisma and runs `prisma migrate dev`
     -> a new folder appears here, its SQL generated by diffing schema against history
2. The pull request merges to main
     -> .github/workflows/migrate.yml runs `bun prisma migrate deploy`
3. deploy reads _prisma_migrations from the production database
     -> it applies only the folders absent from that table, in lexicographic order
4. Each folder it applies is recorded there by name
     -> that folder will never be considered again
```

Applying is also the only place production credentials appear: the workflow injects
`DATABASE_URL` and `DIRECT_URL` from repository secrets, and nothing else in this
repository holds them.

**What breaks if you get it wrong here**, in descending order of how often it happens:

*Editing a folder that has already been applied.* It will not re-run -- its name is
already in `_prisma_migrations` -- so the edit reaches no database, while the file now
misrepresents what the schema actually did. The next `migrate deploy` reports drift
and refuses to continue, on a machine that is not yours.

*Renaming a folder.* The name **is** the ledger key. `20260406032340_rename_allow_users_tabel_to_allowed`
carries a typo in the word "tabel" and it stays, because renaming it would make deploy
see one migration missing and one it has never heard of. Names here are permanent,
typos included.

*Fixing a mistake in place instead of forward.* The three folders
`..._added_allow_users_table`, `..._rename_allow_users_tabel_to_allowed` and
`..._renamed_allowed_users_to_allowed_token` are one table renamed twice, the second
time reshaped. That is the correct shape of a fix here: new steps, forward. Two of
them `DROP TABLE` to rebuild it, which was safe only because it was empty at the
time -- the same generated SQL against a populated table is data loss no later
migration recovers.

---

## What's here

```
migrations/
├── migration_lock.toml   <- generated; pins the provider (postgresql). Never hand-edit: a changed provider invalidates every folder here
├── 0001_init/            <- the baseline, hand-named; sorts first because "0" precedes "2"
│   └── migration.sql     <- generated SQL, frozen once applied
├── 20260317065147_added_timezone_field_in_draft_pref/
│   └── migration.sql
└── ...                   <- one folder per step, 17 so far, ordered oldest to newest by name
```

---

## Conventions

- One migration per folder, the file always named `migration.sql`; the folder is
  `<utc-timestamp>_<snake_case_description>` and is generated, never chosen by hand
- Never edit or rename a folder that exists on any branch someone else has merged --
  add a new one instead
- Never delete a folder: the ledger still names it, and deploy fails on the gap
- A migration that drops or renames a populated column carries the data move in the
  same file, or it is data loss
- Every change starts in [`../schema.prisma`](../schema.prisma), never here. A folder
  written by hand and not derived from the schema makes the two disagree permanently
- Read the generated `migration.sql` before merging -- `migrate dev` reaches for
  DROP-and-recreate more often than an author expects

Nothing validates this folder locally. The only check is `migrate deploy` against the
real database in CI, which means a bad migration is discovered at deploy time.

---

## See also

- [`../schema.prisma`](../schema.prisma) -- the current shape of the database, and the
  source every folder here is derived from
- [`../generated/prisma/`](../generated/prisma/) -- the typed client, regenerated from
  the schema at build time; it is not derived from this folder
- [`../../.github/workflows/migrate.yml`](../../.github/workflows/migrate.yml) -- the
  job that applies these on a merge to `main`
````

Three things to copy deliberately. **The mode is named in the first two words of the section**, so a reader knows which question the rest answers. **The wiring is stated as a split between two stores** -- the folder is the intent, `_prisma_migrations` is the fact -- which is the sentence that makes every rule below it derivable instead of arbitrary. And **the frozen typo is left in and explained**: it is the single most convincing line in that README, because it proves the rule is real rather than aspirational.

## Component folder -- skeleton

````markdown
# <Folder Name>

<Paragraph 1: in one sentence, what lives here.>

<Paragraph 2: why this folder exists separately -- its conceptual contract.>

<Paragraph 3: how to think about it -- a mental model or analogy.>

<Paragraph 4: who touches it, naming the real actors -- developer, CI job,
operator by hand, build step, runtime process, agent, end user.>

---

## How it is wired in

**Mode: <triggered | consumed / composed | applied in order | invoked>.**
<The fact that mode demands: the event and the code path that reaches it; or the
consumer and the composition boundary; or the ordering rule and where the record of
what already ran lives; or the exact invocation and who runs it.>

```
1. <step>
     -> <what it produces>
2. <step>
     -> <what it produces>
```

<Plain text only: numbered steps and `->` arrows. Nothing that must be rendered.
Include the flow only when more than two steps chain.>

**What breaks if you get it wrong here.**

<The mistake this folder punishes, and what the reader sees when they make it.
Not "what if the folder disappeared" -- that question only has an answer in the
triggered mode.>

---

## What's here

```
<folder>/
├── <file>      <- <one-line reason this exists>
└── <subdir>/   <- <one-line reason; mark whatever is generated>
```

---

## Conventions

- <Naming rule for new files, and what the name is for>
- <Required internal structure>
- <What to update elsewhere when something is added here>
- <What validation runs against this folder -- naming the file and symbol, or
  stating plainly that nothing does>

---

## See also

- `<path>` -- <one-line reason>
````

## Component folder -- what the wiring section needs, by mode

The mode from [`SKILL.md`](SKILL.md) Step 2 decides the section's depth, not the folder's name:

| Mode | The section is not done until it names | Flow block? |
|------|----------------------------------------|-------------|
| **Triggered** | the event, the code path that reaches this folder, and what happens when the trigger fires and finds nothing here | Yes -- the chain from event to effect is the content |
| **Consumed / composed** | every consumer, and the composition boundary: which imports are legal, which are not, and what enforces that | Optional -- only when composition is multi-step; a boundary rule is prose |
| **Applied in order** | what fixes the order, where the record of what already ran lives, and the point past which a step is frozen | Yes -- intent, apply, record is a chain, and the reader needs where it can diverge |
| **Invoked** | the exact invocation, who runs it, and what state it expects to already exist | No -- a command and its preconditions are two lines, and a flow inflates them |

---

## Shipped template -- filled example

The README that travels inside generated output, into a repo its author will never see again. Abridged from a real installer template -- `@@TOKEN@@` marks a value the generator substitutes at render time. The original is 53 lines, and that is the point: its reader did not choose to be here and has no context to spend.

````markdown
# @@NAMING_PREFIX@@ — @@ENVIRONMENT@@ infrastructure

This repository was generated by the environment factory and is
**self-contained**: it carries its own `root.hcl` and `account.hcl`, so
Terragrunt resolves everything inside this repo -- nothing here clones or
refers to the factory. Its only external dependency is the Terraform modules,
consumed as immutable artifacts from `gs://@@REGISTRY_BUCKET@@` at release
`@@MODULE_REF@@`.

| | |
|---|---|
| Account | `@@ACCOUNT_ID@@` |
| Environment | `@@ENVIRONMENT@@` — project `@@PROJECT_ID@@` |
| Region | `@@REGION@@` |
| GitHub repo | `@@GITHUB_ORG@@/@@GITHUB_REPO@@` |

Every value above was fixed when this repo was rendered; there is nothing to
fill in before you start.

## What runs by itself

Once onboarding is done, no human applies infrastructure again. Every change
goes through a pull request:

```
1. Push a feature branch and open a PR against main
     -> CI runs a read-only plan, and a reviewer reads it
2. Squash-merge the PR
     -> CI applies, gated by a protected deployment environment
```

Both workflows discover work by scanning for unit **folders**, so adding or
removing a component never needs a workflow edit.

## What you run once

Six steps, by hand, in this order -- each one creates what the next one reads:

1. Get read access on the module registry (only the factory operator can grant it).
2. Create the GitHub repo and push this tree.
3. Confirm both of those before applying anything.
4. Bootstrap the account -- `cd bootstrap && ./bootstrap.sh`, the one manual apply.
5. Re-send the CI half of the registry grant, now that the deploy service accounts exist.
6. Open the first pull request; a green plan on it is the finish line.

You must already hold `roles/owner` on the seed project `@@SEED_PROJECT_ID@@`
and read on `gs://@@REGISTRY_BUCKET@@` before step 4. Nothing in this repo can
provision those grants, so ask for them early.

## Where to go for the rest

**Start here → [docs/onboarding.md](docs/onboarding.md)** -- the six steps in
full: the grants you need and why, what each one creates, how to verify it.

Day-2 work -- editing this repo, turning a component off, bumping the module
release, the failure table and the file-by-file layout -- is in
**[docs/reference.md](docs/reference.md)**.
````

What makes this gate work is the fact table plus the sentence under it: the reader's first question is "what is mine now", and answering it in a glance is what buys the attention for the six steps. Note also that step 1 hands off to a human the reader has to go find -- a prerequisite this repo cannot satisfy is stated as such, not left to fail at step 4.

## Shipped template -- skeleton

Keep all four sections short. Length is what makes this one unread.

````markdown
# <@@NAME@@> — <what it is>

<What this is, and that it is now theirs. State whether it is self-contained
and what it still depends on.>

| | |
|---|---|
| <fact> | `<@@TOKEN@@>` |

<Whether anything needs filling in before they start.>

## What runs by itself

<The automation they inherit, and what triggers it.>

```
1. <what they do>
     -> <what happens on its own>
```

## What you run once

<Numbered manual steps, in order.>

<Any prerequisite this repo cannot provision itself, stated as such.>

## Where to go for the rest

**Start here → [<path>](<path>)** -- <what it covers>

<Where day-2 work is documented.>
````
