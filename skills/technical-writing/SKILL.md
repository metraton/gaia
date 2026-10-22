---
name: technical-writing
description: Use when writing, rewriting, cleaning up or updating a document people will read later, without the conversation that produced it -- a README (repository root, a folder that holds one kind of thing, a shipped template), a runbook, a pull request body, a report or status document, a CHANGELOG entry. Triggers -- "escribe el README", "actualiza el README", "el README está desactualizado", "falta el README de esta carpeta", "write the README", "the README is stale", "write a runbook", "redacta el PR", "write the PR description", "arma el informe", "write the status report", "add a changelog entry", or a drift report flagging a README as stale.
---

# Technical Writing

Technical writing produces the documents people open later, alone: a README, a runbook, a pull request body, a report, a CHANGELOG entry. Each is the mental model its reader needs before they touch, review or adopt the thing it describes, and each is read without the conversation that produced it. The register is `human-register`'s and the explanatory parts follow `technical-explanation`; this skill owns the documents themselves -- which one the situation calls for, its sections in order, and the one section in each that carries the weight.

Four things hold for every document here, stated once:

- **Every tree is annotated.** One line per entry, giving the reason that entry exists. A bare tree adds nothing over `ls`.
- **Every link is relative.** An absolute link points at one host, one account, one branch; it breaks in a clone, in a fork, and on every branch it was not written on.
- **Every flow is plain text** -- numbered steps and simple `->` arrows in a code block. Never mermaid, never any format that must be rendered to be read. A rendered diagram can only be verified by looking at it rendered, which needs a tool that may not be installed; one shipped recently that nobody could validate for exactly that reason. Plain text reads identically on the web, in a terminal, and in a diff.
- **Nothing names the machinery that produced it.** No agents, tools, tiers, approvals, task ids or plan labels: the reader is a person who was not there, and the process is not their document.

The user's rows apply on top -- the heading set a repository or team expects, the language everything versioned is in, what a client's document never names -- read from the rows that reach the writer, never restated here.

## Step 1: Name the door

The door decides everything after it. Five documents, five readers, five questions the reader arrives with:

| Door | Who is reading | The question they arrive with |
|------|----------------|-------------------------------|
| **README, repository root** | Someone evaluating -- they may clone it, adopt it, inherit it, or walk away | "Does this serve me?" |
| **README, component folder** | Someone about to add, change, or remove something in that folder | "What is this wired to, and what do I break?" |
| **README, shipped template** | Whoever receives what was generated | "What is mine now, and what do I have to do?" |
| **Runbook** | Someone doing a rare, costly task by hand, now | "What exactly do I run, and how do I know it worked?" |
| **PR body** | A reviewer who was not present while the change was made | "What changed, why, and how do I know it is safe?" |
| **Report or status** | Someone deciding or acting on where things stand | "What is the situation, what is grave, what do I do?" |
| **CHANGELOG entry** | Someone upgrading, or finding when a behavior changed | "What is different for me in this version?" |

The root of a client's Terraform repo, a service, a library, a CLI -- all one door: repository root. A folder inside a repo that holds one kind of thing -- `migrations/`, `modules/`, `components/`, `services/`, `agents/` -- is the second. A README that travels inside generated output, into a repo its author will never see again, is the third. A repo root and one of its folders are two separate passes, each through its own door. Naming the door wrong is the expensive failure: the work is done correctly and the artifact is the wrong one.

**The one collision worth a tie-breaker: a generated repo that someone then lives in.** It was rendered by a generator, which points at the template door, and it is somebody's repository root, which points at the first. The axis that separates them is the reader's relationship to the thing, not who wrote it. A shipped template's reader did not choose to be there and has one thing to do, once -- its README is a handover note that expires when the last step is done. A repository root's reader is deciding whether to take this on and then lives with it -- its README keeps answering questions long after the first day. A generated client repo whose owners will work in it for a year takes the root door, generator or not.

## Step 2: Write that door's sections, in order

### README, repository root -- seven sections

1. **Title and one line** -- what this is, under 120 characters.
2. **What it is and why it exists** -- the problem it solves, who uses it, what it produces. This section explains, so `technical-explanation` governs it: the fixed order (what exists, how it connects, what happens, why) and the plain register are that skill's.
3. **Flow** -- one plain-text flow, and what it interacts with. One, not several.
4. **Requirements** -- tools with their versions, permissions, credentials.
5. **How it is used** -- the real invocation, with the output it is expected to produce.
6. **Structure** -- annotated tree, one line per entry.
7. **License or ownership**.

Requirements sit before usage on purpose: a reader who tries the invocation without them gets a failure they cannot interpret, and a failed first run is what makes them walk away.

### README, component folder -- five sections

1. **Narrative** (2-4 paragraphs, prose, no bullets) -- what lives here; why this folder exists separately, which is its conceptual contract; how to think about it, as a mental model or analogy; who touches it -- name the actual actors: a developer, a CI job, an operator by hand, a build step, a runtime process, an agent, an end user running a command.
2. **How it is wired in** -- this folder's relation to everything outside it, answering the question its reader arrives with: *if I add, change, or remove something here, what happens, and what else has to move?* Two halves, both required -- see below.
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

**Reach for "triggered" only when the trigger is real.** It is the mode a README writer assumes and rarely the true one: across six folders sampled from unrelated repositories -- React components, database migrations, Terragrunt environments, IAM units, a services folder, and a GitOps workloads folder -- exactly one had a genuine trigger. A trigger invented for the other five would be the one claim in the README a reader cannot check.

A folder can sit in more than one mode: a migrations folder applied in order *by* a CI job is both. Name the one that decides what breaks -- the other is a detail of it.

**Section 2, second half: what breaks if you get it wrong here.** Not what breaks if the folder vanishes -- a folder rarely vanishes. The question that survives all four modes is which mistake this folder punishes. In a migrations folder it is editing one that already ran. In a component library it is an import that crosses the boundary. In a module catalog it is changing an input without bumping the tag consumers pin. Add a plain-text flow when more than two steps chain.

### README, shipped template -- four sections, all short

1. **What this is and whose it is now.**
2. **What runs on its own.**
3. **What you run, once.**
4. **Where to go for the rest.**

The reader of this door did not choose to be here and has no context to spend. Length is what makes it unread.

### Runbook -- five sections

Write a runbook when a task is done by hand, rarely, and a mistake is costly -- a rotation, a restore, a migration run, a release. A numbered list of commands growing inside a README is the signal to move it out. It lives beside the component it operates, as `RUNBOOK.md` (or `runbooks/<task>.md` when there are several), and the component README links it with its reason.

1. **Purpose** -- the outcome, and the trigger or symptom that says to run it.
2. **Prerequisites** -- access, tools with versions, the state that must hold before step 1.
3. **Steps** -- numbered, one exact copy-pasteable command each, with the expected output wherever a wrong result is possible. "Restart the service" fails the concreteness test in Step 3; the command passes.
4. **Verification** -- the read-only command whose output shows it worked.
5. **Rollback** -- how to undo it, or the step after which it cannot be undone.

### PR body -- written after the commits exist

A PR body explains the change to a reviewer who was not present while it was made: in English unless the repository declares another language, self-contained, with no reference to the process that produced it (no task ids, no plan or sprint labels, no "as requested", no approvals, signatures or tiers). The title reuses the commit subject's `type(scope): outcome` rule (`git-conventions`). The sections below are the default shape; a repository's PR template, or a heading set the user's rows fix for their PRs, replaces it and keeps the order of this table -- plain words first, technical detail last:

| Section | Content |
|---------|---------|
| Context | The state that existed before this change and why it needed one, as someone outside the code sees it |
| What changed | One effect per bullet, in plain words -- not a file list |
| Impact | The outcome the change produces for the system or the user; for IaC, the plan summary -- resources to add, change, destroy |
| Verification | What was run and what it showed; what is still to check, named as such, never claimed |
| Risk and rollback | One line each, when the change carries real risk |
| Technical notes | Last: root cause, files, commands, plan output; code and names allowed here, each in its own short paragraph or block |

The structure is advisory: nothing gates the body. It exists so a reviewer gets the same shape every time, and so the technical detail sits at the end where the reviewer who wants it finds it and the one who does not is not stopped by it.

### Report or status -- four parts, in this order

1. **The situation** -- where things stand, in one to three lines, the grave thing first.
2. **What it means** -- for the reader: what is at risk, what is settled, what changed since the last report.
3. **What to do or decide** -- the actions open, each with who and by when; or that none is needed.
4. **The detail** -- evidence, numbers, identifiers, each with its mark and a pointer the reader can open; or a link to where it lives.

The first line of a report is the situation, never an author or date stamp or a title block; metadata, if any, goes last. A report that opens with the detail has handed its reader the agent's register; a report whose grave line sits third misleads by emphasis (`human-register`, Rule 2). A time in a report for readers in several places is written in the standard they share.

### CHANGELOG entry -- one entry per behavior change

Under the version (or `Unreleased`) and the Keep-a-Changelog heading it belongs to -- Added, Changed, Fixed, Removed -- one entry per change a user of the thing would notice, led by the behavior in bold and told from the user's side: what is different now, what they do about it, what stays the same. Internal names appear only when the reader needs them to act (a flag, a command, a file they edit). An entry that lists the files touched is a commit message in the wrong place.

### A section can be thin; it cannot be missing -- and a decision has a home

Write every section of your door. A reader cannot tell a section that was skipped from one that was judged unnecessary, so an omission reads as an unanalyzed dimension. But the rule that every section is mandatory is what manufactures invented content: an author facing a folder whose contents share no convention writes a plausible-sounding one, and that sentence becomes the one part of the document nobody can check.

**So a thin section is declared thin, in one sentence that says why.** "These services share no framework and no layout -- each carries its own README; the only rule that holds across all of them is that the port is fixed in `compose.yaml`, never in code" is a complete Conventions section.

**And a decision worth recording gets a place.** Why this tool and not the obvious one, why the boundary sits here, what the rejected alternative would have cost -- often the most valuable paragraph, and it fits none of the standard sections, so it gets dropped. Put it in the section whose rule it explains; give it its own short section only when it governs the whole document. Either way it carries the alternative that was rejected and the reason -- a decision recorded without its rejected alternative is a description of the present, which the code already provides.

## Step 3: Get the load-bearing section concrete

Each door has exactly one section that carries the weight. If that one is vague, the rest cannot compensate.

| Door | Load-bearing section |
|------|----------------------|
| README, repository root | 3 -- Flow |
| README, component folder | 2 -- How it is wired in |
| README, shipped template | 3 -- What you run, once |
| Runbook | 3 -- Steps |
| PR body | What changed |
| Report or status | 1 -- The situation |
| CHANGELOG entry | the behavior in bold |

**The test, and it can fail:** if what you wrote would be equally true of any other system of the same type, it is not concrete enough yet. "Migrations run in order and should not be edited afterwards" is true of every migration folder ever built, so it fails. "`prisma migrate deploy` applies only the folders absent from the `_prisma_migrations` ledger, in the lexicographic order of their timestamp prefixes, and never re-runs one -- so a correction is always another folder, never an edit" is true of this repo and false of the next one, so it passes. On a PR body: "improves logging" fails; "the relay now logs the upstream status code on every retry, so a 429 storm is visible in one query" passes. On a status: "progress is good" fails; "two of three services are migrated; the third waits on a credential only the client can issue" passes.

## Step 4: The document is the map, not the territory

A README routes to where each thing lives; it does not contain it. Long-form documentation goes to its own folder and is linked. A procedure goes to a runbook beside the component. History goes to the changelog. Whatever is generated carries its own README, and the generator links to it. When a README already holds one of these, cleaning it up means moving the content to where it belongs and leaving the link -- not deleting it and not keeping a summary beside the link.

**The rule that decides the factory case:** a repo whose product is another document or another repo documents the *generation contract* -- what goes in, what comes out, how it is run -- and links to what it generated. It never reproduces it. A copy and its original are two sources of truth, and they diverge on the first edit that lands in only one of them.

**Length is the signal for this step.** Past roughly 100 lines, stop and ask what belongs somewhere else. It is an alarm, not a limit.

## Every sentence is a claim about somewhere else

Nearly every sentence a README or runbook can hold asserts the current state of something the reader is not looking at -- a file, a workflow, a pin, an environment. That is the one kind of written claim that becomes false without anyone touching it, which is why a README rots faster than any comment in the code: a comment at least sits beside what it describes, while nothing that edits a workflow re-reads the README describing it. A real audit landed exactly here: of the false claims found across a 33-file repository, the root README alone carried four.

- **Writing: the repository already answers most questions.** Config files, the directory layout, a tool's own `--help` are sources of truth; a sentence restating one is a cache of a lookup, worth writing only when the lookup is expensive. What earns its place is what no lookup returns: the convention, the decision with its rejected alternative, the mistake the folder punishes. When a sentence must assert the state of another file, it names the file and the symbol, key, or job inside it -- a name survives edits that a line number does not.
- **Updating: every sentence kept is a claim re-made.** Verify each sentence you keep against what it asserts, or say which ones you did not check -- a reader takes the document as true, and a model measurably absorbs a false sentence and builds on it. An update whose diff touched three sentences has re-published all the others.

## Freshness: the document changes with the component

A change that adds, removes or renames a file, or a public entry point (a CLI verb, an exported function, a route, an environment variable), under a component updates that component's README in the same change: the tree line, the wiring, the convention it affects. A runbook asserts the state of what it runs, so a change to a command it names updates it in the same change. A document fixed in a later change was false in between, and the later change is the one that never happens.

## Anti-patterns

- **A tree with no comments** -- it reproduces `ls` and costs the reader a scroll to learn nothing.
- **Conventions that are aspirational** -- "files should be well-named" cannot be complied with or violated, so it constrains nobody. "Every folder here is named `<utc-timestamp>_<verb>_<object>`, and the timestamp prefix is what fixes the apply order" can be checked.
- **Links with no reason** -- a bare list shifts the deciding onto the reader, who has to open each one to find out whether it was for them.
- **Reproducing what another artifact already documents** -- the copy and the original are two sources of truth, and a reader who finds a conflict cannot tell which side is stale.
- **The process in the document** -- a PR body that cites the task id, a README that names the agent, a report that lists the commands run before the situation. The reader was not there and did not ask.

A filled example and a blank skeleton for each of the three README doors are in [`reference.md`](reference.md). Open your door's part only.
