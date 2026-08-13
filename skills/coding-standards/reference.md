# Coding Standards Reference

The arguments, evidence, and per-stack tables behind `SKILL.md`. The body states each rule and the
decision it forces; this file carries what sustains them, each factual claim with its source. It is
read when a ruling is disputed or a stack detail is needed — never in order to apply the skill.

## Why the default is delete

The inversion is measured, not preferred. Under the previous version — which protected seven
categories with no length ceiling — a real file came out at 171 comment lines against 183 code
lines while fully COMPLYING with the standard: every genuine trap arrived escorted by ten lines of
pedagogy, and the escort inherited the protection of what it escorted. The file was verbose and
green at the same time. That is not agents disobeying a rule; it is the rule working as written.

Two consequences shaped the current body. Protection had to attach to the FACT rather than to the
comment containing it, which is what the two-line ceiling enforces. And the default had to invert:
while keeping was free and deleting needed an argument, every borderline comment resolved toward
keeping, because the cost of the argument fell entirely on the side of removal.

## Why a mandated pass is defined as adjudication

Two cold agents applied this skill to the same file from the same baseline, 175 comment lines, with
the same mandate. One returned 87 lines; the other returned 161, rewrote several comments LONGER
than the baseline, and added four new narration comments — two of which an earlier commit had
already deleted as redundant. The difference between them was not doctrine: both had the same test
in front of them.

What the second one did was hunt. It repaired, with care, every failure the skill NAMED, and never
put the test to anything else — including its own new sentences. Its excuses were labels rather
than verdicts: the seed comparison was defended as "durable provenance, design lineage", the
relative pointer as "navigational, not a rationale-skip". Each is the trap table's first row
wearing a new coat.

That is the measured reason a mandated pass is defined as exhaustive adjudication, and the reason
the body says every case it names is an illustration and never a checklist. Naming cases turned out
to teach hunting: the round of amendments that named five cases verbatim produced the worse of the
two runs. Concrete specimens therefore live HERE and in `examples.md`, where they illustrate; the
body carries the discriminating rule and no bait.

## Why no density target exists

Four findings, each checkable, converge on one verdict — volume is the harmless dimension and
falsehood the harmful one — which is why no percentage, ratio, or comment-to-code target appears
anywhere in this skill, in either direction. The two-line ceiling is not a density: it caps ONE
piece of prose against one fact, and a file of a hundred two-line facts violates nothing.

- **The one maintainability metric that scored comment density was dropped by its own adopters.**
  The Maintainability Index (Oman & Hagemeister 1992; Coleman, Ash, Lowther & Oman 1994) carried a
  comment-percentage term in its four-factor variant; Visual Studio adopted the metric without that
  term, and the standing critique of the index (van Deursen, "Think Twice Before Using the
  Maintainability Index", 2014) singles out the comment weight as unfounded.
- **The strongest replicated result runs the other way.** iComment (Tan, Yuan, Krishna & Zhou,
  SOSP 2007) hunted comment–code inconsistencies automatically in Linux, Mozilla, and other
  codebases; developers confirmed the majority of its reports as real defects — some in the code,
  the rest in the comment. The harm is a comment being FALSE, not a file carrying many or few.
- **The circulating targets dead-end before a checkable source.** The most-cited density figure —
  about one comment per ten statements — reaches print through *Code Complete* (McConnell, 2nd
  ed., ch. 32) citing a 1980s IBM internal study that cannot be retrieved; the citation chain
  stops before any primary source a reader could verify.
- **"The code documents itself" is a named excuse, not a neutral position.** It is the first of
  the four excuses Ousterhout enumerates and refutes in *A Philosophy of Software Design* (ch. 12)
  — "good code is self-documenting", called a myth there.

## Why a claim about elsewhere rots, and why pointers went with it

The ban earned its place from measured failure. In a real audit of a 33-file repository, every
false claim found — six — asserted state outside the file it lived in; none was about the
language, a decision, or the code below it. Falsehood accumulates exactly there, because nothing
that edits the target ever re-reads the claim. The more of a document is made of such claims, the
faster it rots — a README is the limiting case, and `readme-writing` carries that consequence.

An earlier version tried to keep those claims and make them cheap to check, by demanding a
coordinate (file + symbol, never a line number). The coordinate lowers the cost of checking; it
does not make the claim durable. Of the ~9.6 million links researchers extracted from source code
comments across active repositories, roughly one in ten no longer resolved (Hata, Treude, Kula &
Matsumoto, "9.6 Million Links in Source Code Comments: Purpose, Evolution, and Decay", ICSE 2019).

Three defects measured in the field close the case, and all three are defects OF THE POINTER, not
of the copy it was meant to replace:

- **Circular remission.** `main.tf` says "see variables.tf", `variables.tf` says "see main.tf",
  and each carries the whole argument anyway. It reads as compliance with one-rationale-one-place
  and is its exact opposite: two copies that additionally point at each other.
- **A pointer into the void.** `backbone.yml.tmpl` said "exactly as foundation.yml does" about a
  security rationale that was not in `foundation.yml`. A link checker calls this green: the
  destination exists, the content is not in it.
- **Undecidable reach.** Two independent audits, with no contact between them, reported the same
  hole: the rule never said whether "one place" means one file, one module, or one artifact.

The current body dissolves all three by not writing the second sentence at all. The rationale
lives once, at the site whose reader needs it; the second site carries only what is new at the
second site, and asserts nothing about the first. There is no pointer to size, to verify, or to
find dangling. When two audiences genuinely cannot reach one site — the caller reading a rendered
description and the maintainer reading the module — that is the audience rule in the body, not a
pointer: each reader gets the fact once, in their own reach.

## What a false comment does to a reader

Measured: CodeCrash (Lam, Wang, Huang & Lyu, NeurIPS 2025; arXiv:2504.14119) injected comments
that explicitly contradict the code's actual logic into two code-reasoning benchmarks and ran
seventeen models against them. The misleading text alone cost double-digit reasoning accuracy:
models over-rely on natural-language cues instead of the executable semantics in front of them.
A model does not skim past a comment the way a human does — it absorbs the prose as input, where
it competes with the code itself. That is why the reading rule demands verification against the
implementation, or an explicit statement that none was done: the enemy is falsehood absorbed
unverified, never prose itself — the rule asks for verification, not for suspicion of volume.

## Volume in slot-less formats

The standards that govern slot-less formats set floors and no ceilings, in either direction:
Helm's chart best practices require every defined `values.yaml` property to be documented, and
Liquibase ships a policy check demanding a comment on every changeset.

The canonical third witness needs a distinction the volume argument depends on. OpenSSH's shipped
`sshd_config` is majority comment because its options are commented **at their defaults** — a
commented DEFAULT documents the effective value, states what holds while the line stays inactive,
and is a form of documentation. A commented IMPLEMENTATION preserves nothing and waits for a
reader to prove it unused — that is dead code, and it is deleted. The two look alike and are
opposites; `sshd_config` sits on the documentation side of the line.

So in a slot-less format a high comment proportion is not itself a defect, and cannot be cut down
to a number: the cut runs comment by comment against what the file already shows. One restating a
value visible in the lines below is redundant and is the first thing to go stale when the data
changes; one carrying a constraint, boundary, or consequence that appears nowhere else has no
other home — deleting it does not deduplicate, it destroys.

## When commenting beats restructuring

The body puts the code first: a fact that cannot be stated without describing the body is reporting
bad code. These are the three cases where that diagnosis is wrong and the code is already right —
claim one only after naming which restructuring you tried and why it did not serve, and the fact
still lands within the two-line ceiling like any other:

| Case | Why restructuring loses |
|------|-------------------------|
| Extraction produces tangle | The reader reconstructs the original anyway, jumping between units |
| Restructuring degrades something real | A witness case introduced a concurrency defect and a performance regression |
| The idea is intrinsically hard to explain | The difficulty is in the problem; no arrangement of code removes it |

## The fact classes, and what each loss costs

These are the classes the survival test admits — not an amnesty, and not a list to check a comment
against before keeping it whole. Each buys the same thing: the right to exist in at most two dry
lines. The column on the right is why the fact itself is not deleted as noise; it is not a licence
to argue the fact at length. Two of them come with a known trap:

- **A class member built on something transitory.** A rejected alternative is durable, but the
  clause justifying it from another repository's current state, or from how many consumers the
  module has today, expires. Keep the durable half, cut the clause that expires — inside the same
  comment.
- **Provenance carrying an environment or client identifier.** "Verified live against <client
  project>" is provenance and an environment trace at once. Keep the fact and its consequence in
  durable form, drop the identifier, and be explicit that exact re-verification is what was traded
  away.

| Fact class | What is lost with it |
|-----------|----------------------|
| Why, and which alternatives were rejected | The next reader re-litigates a settled decision, or reverts to an option already ruled out |
| The caller's contract, including the precision the type does not carry — units, bounds, invariants, the meaning of empty or absent | The caller infers the contract from one implementation, couples to an accident, and writes a call that type-checks and is still wrong |
| The high-level intuition | Every part is legible and the whole is not |
| External cause — a dependency defect, a regulatory duty, a provider quota, a standard dictating the shape | The workaround reads as arbitrary, gets "cleaned up", and restores the defect |
| Why THIS value and not another | A constant nobody dares change and nobody can justify |
| Legal and licensing notices | A compliance obligation silently dropped |
| Provenance — the source a fact was verified against | The only record that the claim was ever checked; without it the fact can be trusted or re-derived, never re-verified |

## Where documentation natively lives

The first lookup is not this table: it is the repository's own lint configuration, which declares
which documentation mechanism the project enforces — checkable in-repo, and it does not age the
way a tool list does. The language's native mechanism IS that language's documentation standard;
the rows below illustrate the class, they are not its membership.

| Stack | Native mechanism | Notes |
|-------|------------------|-------|
| Terraform / OpenTofu | `description` on `variable` and `output` blocks | Rendered by `terraform-docs` and the registry. Provider RESOURCES often expose `description` too — the insufficient-slot case below, not this row. |
| TypeScript / JavaScript | JSDoc (`/** ... */`) with `@param`, `@returns`, `@throws` | Read directly by editors, type-checkers, doc generators. |
| Python | Docstring as first statement of module/class/function | Follow the convention (Google, NumPy, reST) the file already uses — the repository-matching rule decides. |
| Go | Doc comment starting with the identifier's own name | The form `go doc` extracts. |
| Rust | `///` (item) and `//!` (module) doc comments | Rendered by rustdoc; embedded code compiles as doctests. |
| Java / Kotlin | Javadoc / KDoc above the declaration | Read by compiler doc tooling and IDEs. |
| C# | XML doc comments (`/// <summary>` etc.) | Extracted by the compiler into a documentation file. |
| Kubernetes manifests | `kubernetes.io/description` annotation; `description` in CRD/OpenAPI schemas | Annotations survive the API round-trip; a `#` comment is stripped on read-back from the cluster. |
| Bash / shell | Header comment block: purpose, usage, required environment | No docstring construct exists; the header block IS the native mechanism. |
| YAML / Helm values | Comment directly above the key; `# --` where the repo already uses `helm-docs` | Match what the repo's `values.yaml` already does. |

## No slot, and insufficient slot

Where no native mechanism exists, the comment is the only place the contract can live — deleting
it deletes the contract, and its ABSENCE is the defect:

| Construct with no slot | Consequence |
|------------------------|-------------|
| Terraform `locals`, `data`, `module`, `provider`, `dynamic` blocks | No description field; every rationale attached to one lives in a comment. |
| Shell functions and scripts | The header block carries purpose, usage, and required environment. |
| YAML and JSON configuration | JSON admits no comments at all; its contract lives in an adjacent schema or document. |
| SQL migrations and views | The why of a migration exists only as a comment or in the change description. |
| Dockerfile, Makefile, CI pipeline definitions | Directive-only formats; a non-obvious ordering or cache constraint survives only as a comment. |
| CSS / stylesheets | No symbol-level doc mechanism. |

The INSUFFICIENT slot is the neighboring case, and Terraform resources are its witness — not a
no-slot row. Provider schemas commonly expose `description` ON resources: a service account, a
custom role (as in the production identity unit `examples.md` quotes). But the field is a
provider's, not the language's: commonly capped (300 bytes is a frequent provider limit) and
rendered in cloud consoles the engineering rationale is not written for. A package manifest's
`description` — end-user copy — is the same case. The slot carries the summary; the overflow goes
to a comment, where it is mandatory again.

## Checkers

Bind the contract obligation to whatever the repository's lint configuration already enforces —
that binding is checkable in-repo and does not go stale. The tool names below illustrate the
class; they are not a maintained registry:

| Stack | Checker (illustrative) | Rule it enforces |
|-------|------------------------|------------------|
| Terraform / OpenTofu | `tflint` | `terraform_documented_variables`, `terraform_documented_outputs` |
| Python | `ruff` (pydocstyle-derived D rules) | Missing module/class/function docstring. Standalone `pydocstyle`'s maintenance status: unverified — confirm before recommending it. |
| TypeScript / JavaScript | `eslint` with the JSDoc plugin | Required JSDoc block, required `@param`/`@returns` |
| Go | `revive` | Exported symbol must carry a doc comment. (`golint`, its predecessor, is archived — do not cite it.) |
| Rust | compiler lint `missing_docs` | Missing doc on a public item |
| Java | `checkstyle` (Javadoc modules) | Missing or incomplete Javadoc |
| C# | compiler warning `CS1591` | Missing XML comment on a public member |
| Shell | none exists | — |

**The asymmetry that matters.** Every checker detects documentation that is MISSING; none detects
documentation that is SURPLUS. The contract obligation can be delegated to a tool; the redundancy
obligation is enforced by discipline and review alone — exactly why it is the half that erodes.

## What the environment already answers

Configuration files, scripts, the directory layout, and a tool's own `--help` are sources of truth
in their own right. A sentence restating what one of them answers states no fact the code cannot
show — it is a cache of a lookup, and the default deletes it. What survives is what no lookup
returns: the unwritten convention, the reason behind a choice, the gotcha no config confesses.

The formulation is adapted from `writing-for-agents` in mattpocock/skills. Asked while writing, it
converts staleness from a maintenance problem someone inherits into a question settled at the
moment of authorship.

## Process traces to strip

```
# TASK-142: implement retry per AC-3
# Finding 7 remediation
# as discussed with the user, added on 2026-07-21
```

None of these mean anything to a reader without the originating ticket or conversation open, and
version history already carries authorship and timing. Strip the pointer; keep the durable
rationale if any survives once it is gone — usually nothing does, and the comment simply goes.

## Edge cases

- **Doc-header and inline comment disagree.** One of the two is stale. Fix the drift at its
  source; a third comment reconciling them is itself a duplicate rationale.
- **Auto-generated code** (protobuf output, OpenAPI clients, generated bindings). The generator
  owns that file's comments; do not hand-edit them, and do not apply the doc rules to a file whose
  header says it is generated.
- **The SOURCE of a template.** A comment in a template renders into every generated repository,
  and the receiver cannot fix it: any correction they make is overwritten at the next render.
  There is no return loop, so a wrong sentence there is not a defect someone reports — it is a
  permanent one replicated across every consumer. The rules do not change; the tolerance for an
  unverified sentence does. What decides this is the missing return loop, not the fact that a
  template has two readers.

## Reviewing: what to hunt, and in what order

Read manually first, sweep mechanically second. A mechanical sweep finds identical sequences, and
its blind spot is copies that have ALREADY diverged — which is where the real defects live. The
sharpest finding of one field batch, a `curl` without `-f` that made the body of a 404 executable
and exited 0, was invisible to the sweep precisely because the two sites were no longer identical.
Use the mechanical pass to confirm and to measure, never to discover.

Nine defect signatures, measured across fifteen modules and two batches of CI and client-facing
files. The first is the dominant one, present in four of every five modules:

1. One rationale living in several places.
2. A client or environment identifier inside a reusable module.
3. Temporal references — a phase, a status, "currently", a date.
4. A pointer naming the wrong direction.
5. An argument built against something that has already been deleted.
6. A module describing itself through its single current tenant.
7. Circular remission — two sites pointing at each other, each carrying the whole argument.
8. Internal strings printed to the end user.
9. A pointer resolving to a site that does not hold the content.

A negative finding needs evidence too: "found nothing" that does not say what was read or run
exhaustively is indistinguishable from "did not look".
