---
name: readme-writing
description: Use when writing, rewriting, cleaning up, or updating a README -- the root README of a repository, the README of a component folder (Gaia's agents/, skills/, hooks/, config/, bin/, tests/, build/, or the equivalent folder in any repo), or the README shipped inside a template or scaffold that is handed to someone else. Triggers -- "escribí el README", "actualizá el README", "limpiá el README", "el README de este repo está desactualizado", "falta el README de esta carpeta", "write the README", "the README is stale", or a drift report flagging a README as stale.
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
| **Component folder** | Someone about to touch that folder | "When does this activate, and what do I break?" |
| **Shipped template** | Whoever receives what was generated | "What is mine now, and what do I have to do?" |

The root of a client's Terraform repo, a service, a library, a CLI -- all one gate: repository root. A folder inside a repo that holds one kind of component -- `agents/`, `modules/`, `hooks/` -- is the second gate. A README that travels inside generated output, into a repo its author will never see again, is the third.

A repo root and one of its folders are two separate passes, each through its own gate. Naming the gate wrong is the expensive failure: the work is done correctly and the artifact is the wrong one.

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

1. **Narrative** (2-4 paragraphs, prose, no bullets) -- what lives here; why this folder exists separately, which is its conceptual contract; how to think about it, as a mental model or analogy; who touches it -- developer, agent at runtime, CI, admin.
2. **When it activates** -- the concrete trigger: the event, condition, or code path that fires this. A plain-text flow when more than two steps chain. And what happens if this folder is absent or broken.
3. **What's here** -- annotated tree, one line per file or subdirectory, with generated files marked so nobody hand-edits them.
4. **Conventions** -- how to name new files, what internal structure they must follow, what to update elsewhere when something is added here, what validation runs against this folder.
5. **See also** -- adjacent components, each link carrying its one-line reason.

**Inside Gaia, this gate carries three integration points.** Finishing a new skill includes updating [`skills/README.md`](../README.md) so the index lists it -- that is the closing step of `skill-creation`, not optional cleanup. An agent that adds a file to `agents/`, `hooks/`, `skills/` or any top-level folder reports the stale README through `cross_layer_impacts` and stops there; the orchestrator dispatches the README work as its own task, so the update is a deliberate pass rather than an afterthought squeezed into the middle of feature work, where it gets written from what the author happens to remember. And nothing mechanical catches a stale or missing one: [`tests/system/test_directory_structure.py`](../../tests/system/test_directory_structure.py) checks that the key folders hold their required files -- agent `.md`s, hooks, critical tools -- and never looks for a README, so that drift report is the only thing keeping these current.

### Shipped template -- four sections, all short

1. **What this is and whose it is now.**
2. **What runs on its own.**
3. **What you run, once.**
4. **Where to go for the rest.**

The reader of this gate did not choose to be here and has no context to spend. Length is what makes it unread.

## Step 3: Get the load-bearing section concrete

Each gate has exactly one section that carries the weight. If that one is vague, the rest cannot compensate.

| Gate | Load-bearing section |
|------|----------------------|
| Repository root | 3 -- Flow |
| Component folder | 2 -- When it activates |
| Shipped template | 3 -- What you run, once |

**The test, and it can fail:** if what you wrote would be equally true of any other system of the same type, it is not concrete enough yet.

"Skills are injected at startup" passes for every plugin system ever built, so it fails. "The host reads `skills:` from the agent's frontmatter and preloads each `SKILL.md` before the subagent's first turn" is true of this system and false of the next one, so it passes. The same test on a root README: "deploys infrastructure to the cloud" fails; "`terraform apply` against the `prod` workspace creates the VPC, the GKE cluster, and the Cloud SQL instance the `app-*` modules depend on" passes. And on a template: "configure your settings" fails; "run `make bootstrap` once to write `.env` from your project id" passes.

## Step 4: The README is the map, not the territory

A README routes to where each thing lives; it does not contain it. Long-form documentation goes to its own folder and is linked. Examples go to theirs. History goes to the changelog. Whatever is generated carries its own README, and the generator links to it. When a README already holds one of these, cleaning it up means moving the content to where it belongs and leaving the link -- not deleting it and not keeping a summary of it beside the link.

**The rule that decides the factory case:** a repo whose product is another document or another repo documents the *generation contract* -- what goes in, what comes out, how it is run -- and links to what it generated. It never reproduces it. A copy and its original are two sources of truth, and they diverge on the first edit that lands in only one of them.

**Length is the signal for this step.** Past roughly 100 lines, stop and ask what belongs somewhere else. It is an alarm, not a limit -- a genuinely large surface can justify more, but the length is almost always telling you that the README started holding what it should have been pointing at.

## Anti-patterns

- **A tree with no comments** -- it reproduces `ls` and costs the reader a scroll to learn nothing. The reason an entry exists is the only part they cannot get from the filesystem.
- **Conventions that are aspirational** -- "files should be well-named" cannot be complied with or violated, so it constrains nobody. "Skill folders use kebab-case matching the `name:` field in frontmatter" can be checked.
- **Links with no reason** -- a bare list shifts the deciding onto the reader, who has to open each one to find out whether it was for them.
- **Absolute links** -- they encode one host, one account, one branch, and break in every clone and every branch that is not that one.
- **Reproducing what another artifact already documents** -- the copy and the original are two sources of truth. They do not diverge one at a time; they both become unreliable, because a reader who finds a conflict cannot tell which side is stale.

A filled example and a blank skeleton for each of the three gates are in [`reference.md`](reference.md). Open your gate's part only.
