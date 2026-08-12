---
name: readme-writing
description: Use when writing, rewriting, cleaning up, or updating a README -- the root README of a repository, the README of a folder that holds one kind of thing (modules, migrations, components, services, scripts, environments, workloads; Gaia's own agents/, skills/ and hooks/ are one instance of that same shape), or the README shipped inside a template or scaffold handed to someone else. Triggers -- "escribí el README", "actualizá el README", "limpiá el README", "el README de este repo está desactualizado", "falta el README de esta carpeta", "write the README", "the README is stale", or a drift report flagging a README as stale.
---

# README Writing

A README is the mental model someone needs before they touch or adopt the thing it describes. One that only lists files is worse than none: it leaves the reader believing they understand something they do not, and they act on that belief.

Three things hold for every README, whatever it documents, and are stated here once rather than repeated per case:

- **Every tree is annotated.** One line per entry, giving the reason that entry exists. A bare tree adds nothing over `ls`.
- **Every link is relative.** An absolute link points at one host, one account, one branch; it breaks in a clone, in a fork, and on every branch it was not written on.
- **Every flow is plain text** -- numbered steps and simple `->` arrows in a code block. Never mermaid, never any format that must be rendered to be read. A rendered diagram can only be verified by looking at it rendered, which needs a tool that may not be installed; one shipped recently that nobody could validate for exactly that reason. Plain text reads identically on the web, in a terminal, and in a diff, so what is in the file is what the reader sees.
- **The concreteness test in Step 3** is the same test for all three cases below.

## Step 1: Name the gate

This comes first because it decides everything after it. Three READMEs, three readers, three questions the reader arrives with:

| Gate | Who is reading | The question they arrive with |
|------|----------------|-------------------------------|
| **Repository root** | Someone evaluating -- they may clone it, adopt it, inherit it, or walk away | "Does this serve me?" |
| **Component folder** | Someone about to add, change, or remove something in that folder | "What is this wired to, and what do I break?" |
| **Shipped template** | Whoever receives what was generated | "What is mine now, and what do I have to do?" |

The root of a client's Terraform repo, a service, a library, a CLI -- all one gate: repository root. A folder inside a repo that holds one kind of thing -- `migrations/`, `modules/`, `components/`, `services/`, `agents/` -- is the second gate. A README that travels inside generated output, into a repo its author will never see again, is the third.

A repo root and one of its folders are two separate passes, each through its own gate. Naming the gate wrong is the expensive failure: the work is done correctly and the artifact is the wrong one.

**The one collision worth a tie-breaker: a generated repo that someone then lives in.** It was rendered by a generator, which points at the template gate, and it is somebody's repository root, which points at the first. The axis that separates them is the reader's relationship to the thing, not who wrote it. A shipped template's reader did not choose to be there and has one thing to do, once -- so its README is a handover note that expires when the last step is done. A repository root's reader is deciding whether to take this on and then lives with it -- so its README has to keep answering questions long after the first day. A generated client repo whose owners will work in it for a year takes the root gate, generator or not; the template gate is for output whose entire relationship with its reader is the first hour.

## Step 2: Write that gate's sections, in order

### Repository root -- seven sections

1. **Title and one line** -- what this is, under 120 characters.
2. **What it is and why it exists** -- the problem it solves, who uses it, what it produces.
3. **Flow** -- one plain-text flow, and what it interacts with. One, not several.
4. **Requirements** -- tools with their versions, permissions, credentials.
5. **How it is used** -- the real invocation, with the output it is expected to produce.
6. **Structure** -- annotated tree, one line per entry.
7. **License or ownership**.

Requirements sit before usage on purpose: a reader who tries the invocation without them gets a failure they cannot interpret, and a failed first run is what makes them walk away.

### Component folder -- five sections

1. **Narrative** (2-4 paragraphs, prose, no bullets) -- what lives here; why this folder exists separately, which is its conceptual contract; how to think about it, as a mental model or analogy; who touches it -- name the actual actors, whichever they are: a developer, a CI job, an operator by hand, a build step, a runtime process, an agent, an end user running a command.
2. **How it is wired in** -- this folder's relation to everything outside it, answering the question its reader actually arrives with: *if I add, change, or remove something here, what happens, and what else has to move?* Two halves, both required -- see below.
3. **What's here** -- annotated tree, one line per file or subdirectory, with generated files marked so nobody hand-edits them.
4. **Conventions** -- how to name new files, what internal structure they must follow, what to update elsewhere when something is added here, what validation runs against this folder.
5. **See also** -- adjacent components, each link carrying its one-line reason.

**Section 2, first half: name the mode this folder lives in, and give the fact that mode demands.**

| Mode | The relation | What the section has to name |
|------|--------------|------------------------------|
| **Triggered** | something outside fires what is here | the event, and the code path that reaches it |
| **Consumed / composed** | something imports, references, or renders what is here | the consumer, and the composition boundary -- what may cross it and what may not |
| **Applied in order** | the contents run as a sequence against something that records what already ran | the ordering rule, and where that record lives -- which is what makes the past irreversible |
| **Invoked** | a person or a CI job runs it by name | the exact invocation, and who runs it |

**Do not reach for "triggered" by default.** It is the mode a README writer assumes and rarely the true one: across six folders sampled from unrelated repositories -- React components, database migrations, Terragrunt environments, IAM units, a services folder, and a GitOps workloads folder -- exactly one had a genuine trigger. Five did not, and a trigger invented for them would be the one claim in the README a reader cannot check.

A folder can sit in more than one mode: a migrations folder applied in order *by* a CI job is both applied-in-order and triggered. Name the one that decides what breaks -- the other is a detail of it.

**Section 2, second half: what breaks if you get it wrong here.** Not what breaks if the folder vanishes -- a folder rarely vanishes, and that question only has an interesting answer in the triggered mode. The question that survives all four is which mistake this folder punishes. In a migrations folder it is editing one that already ran. In a component library it is an import that crosses the boundary. In a module catalog it is changing an input without bumping the tag consumers pin. Add a plain-text flow when more than two steps chain.

### Shipped template -- four sections, all short

1. **What this is and whose it is now.**
2. **What runs on its own.**
3. **What you run, once.**
4. **Where to go for the rest.**

The reader of this gate did not choose to be here and has no context to spend. Length is what makes it unread.

### A section can be thin; it cannot be missing -- and a decision has a home

Write every section of your gate. A reader cannot tell a section that was skipped from one that was judged unnecessary, so an omission reads as an unanalyzed dimension. But the rule that every section is mandatory is what manufactures invented content: an author facing a folder whose contents genuinely share no convention writes a plausible-sounding one, and that sentence becomes the one part of the README nobody can check.

**So a thin section is declared thin, in one sentence that says why.** "These services share no framework and no layout -- each carries its own README; the only rule that holds across all of them is that the port is fixed in `compose.yaml`, never in code" is a complete Conventions section. It tells the reader the heterogeneity is deliberate rather than undocumented, which is exactly what they needed and could not have assumed.

**And a decision worth recording gets a place.** Why this tool and not the obvious one, why the boundary sits here, what the rejected alternative would have cost -- that is often the most valuable paragraph in a README, and it fits none of the standard sections, so it gets dropped. Put it in the section whose rule it explains: the reason a tool is pinned to a non-default binary belongs in Requirements, beside the pin. Give it its own short section only when the decision governs the whole folder or repo rather than one section's rule. Either way it has to carry the alternative that was rejected and the reason -- a decision recorded without its rejected alternative is just a description of the present, which the code already provides.

## Step 3: Get the load-bearing section concrete

Each gate has exactly one section that carries the weight. If that one is vague, the rest cannot compensate.

| Gate | Load-bearing section |
|------|----------------------|
| Repository root | 3 -- Flow |
| Component folder | 2 -- How it is wired in |
| Shipped template | 3 -- What you run, once |

**The test, and it can fail:** if what you wrote would be equally true of any other system of the same type, it is not concrete enough yet. Apply it against the mode you declared -- a folder that says "applied in order" and then describes ordering in general has named the mode without paying for it.

"Migrations run in order and should not be edited afterwards" is true of every migration folder ever built, so it fails. "`prisma migrate deploy` applies only the folders absent from the `_prisma_migrations` ledger, in the lexicographic order of their timestamp prefixes, and never re-runs one -- so a correction is always another folder, never an edit" is true of this repo and false of the next one, so it passes. The same test on a root README: "deploys infrastructure to the cloud" fails; "`terraform apply` against the `prod` workspace creates the VPC, the GKE cluster, and the Cloud SQL instance the `app-*` modules depend on" passes. And on a template: "configure your settings" fails; "run `make bootstrap` once to write `.env` from your project id" passes.

## Step 4: The README is the map, not the territory

A README routes to where each thing lives; it does not contain it. Long-form documentation goes to its own folder and is linked. Examples go to theirs. History goes to the changelog. Whatever is generated carries its own README, and the generator links to it. When a README already holds one of these, cleaning it up means moving the content to where it belongs and leaving the link -- not deleting it and not keeping a summary of it beside the link.

**The rule that decides the factory case:** a repo whose product is another document or another repo documents the *generation contract* -- what goes in, what comes out, how it is run -- and links to what it generated. It never reproduces it. A copy and its original are two sources of truth, and they diverge on the first edit that lands in only one of them.

**Length is the signal for this step.** Past roughly 100 lines, stop and ask what belongs somewhere else. It is an alarm, not a limit -- a genuinely large surface can justify more, but the length is almost always telling you that the README started holding what it should have been pointing at.

## Anti-patterns

- **A tree with no comments** -- it reproduces `ls` and costs the reader a scroll to learn nothing. The reason an entry exists is the only part they cannot get from the filesystem.
- **Conventions that are aspirational** -- "files should be well-named" cannot be complied with or violated, so it constrains nobody. "Every folder here is named `<utc-timestamp>_<verb>_<object>`, and the timestamp prefix is what fixes the apply order" can be checked, and tells the reader what the name is *for*.
- **Links with no reason** -- a bare list shifts the deciding onto the reader, who has to open each one to find out whether it was for them.
- **Absolute links** -- they encode one host, one account, one branch, and break in every clone and every branch that is not that one.
- **Reproducing what another artifact already documents** -- the copy and the original are two sources of truth. They do not diverge one at a time; they both become unreliable, because a reader who finds a conflict cannot tell which side is stale.

A filled example and a blank skeleton for each of the three gates are in [`reference.md`](reference.md). Open your gate's part only.
