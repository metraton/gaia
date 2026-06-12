---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
metadata:
  user-invocable: false
  type: technique
---

# Investigation

Investigation is the universal method every agent runs before acting: an
optimal, context-anchored search that turns the task into understanding. It is
not a checklist of phases — it is the discipline of searching FROM what you
were given, with the tools you already have, only as far as your scope reaches,
separating what you have confirmed from what you are still assuming.

## Core principle

Three forces shape every good investigation:

- **Context is the map.** Your injected context — Project Context, Surface
  Routing, and the **Agent Contract Handoff** (goal, acceptance criteria,
  scope) — names the resources, identifiers, and surface that matter, and is
  where your environment defines which tools you have. Search outward from
  those anchors with those tools; enumerating the whole space when the context
  already names what matters wastes calls and buries the signal. When the
  context also carries a **Memory Index / Historical Context** section, read
  the relevant prior episodes before searching — they may already hold
  findings, sparing you from re-investigating what is known.
- **Scope decides what matters.** Your handoff defines the surface you own, and
  your injected **rules** define what you own and may change — consult them for
  your boundaries before investigating, and respect them when proposing.
  Searching beyond your surface yields findings you cannot act on or verify;
  narrow to scope first, and name anything beyond it as a dependency.
- **Confirmed beats assumed.** The most valuable output is a clean line between
  what you *observed* (confirmed) and what you *inferred* (assumed). Propose
  only on the confirmed; carry the assumed forward as an open gap, never fact.

Use this when starting any task that touches existing state — source,
configuration, or live state — before planning, proposing, or mutating.

## Process
1. **Anchor in the handoff.** Read the **Agent Contract Handoff** for goal,
   acceptance criteria, and scope, and the context for the identifiers already
   known. List the unknowns it does *not* answer — those, within your scope,
   are the only things worth searching for.
2. **Investigate with your tools, scoped to your anchors.** Use whatever tools
   your environment gives you to observe, query, or examine the specific
   anchors the context named, rather than scanning the whole space. Examine
   2-3 comparable existing instances to learn the conventions in play — one is
   anecdote, three are a pattern.
3. **Search only the gaps, only in your scope.** Direct your tools at what the
   context did not answer. Follow adjacency: what sits next to your target
   explains its constraints; what references it reveals its coupling. Do not
   expand into a surface another scope owns — name that as a dependency.
4. **Your surface may be source, configuration, or live state — the method is
   the same.** When the task depends on current runtime state, that is not an
   exception; it is one more surface you observe read-only, scoped to your
   anchors. See `command-execution` for running a query safely and
   `security-tiers` for why a read-only (T0) query needs no approval. Do not
   retain runtime values as if they were stable facts.
5. **Apply the pattern hierarchy, in order.** (a) Existing pattern — if 2-3
   comparable instances exist, follow them; consistency beats preference, for
   prerequisites and dependencies too. (b) Your domain skill when none is
   found. (c) Prior knowledge as last resort, marked: *"No existing pattern
   found — applying best practices."* Following a pattern, copy its identifiers
   exactly; finding one problematic, surface it as a deviation with an
   alternative.
6. **Validate before proposing.** For each action that creates, modifies, or
   deletes something, confirm your investigation revealed how the project
   *manages* that kind of thing — your action must use that mechanism. A
   divergence between observed state and the context is either real drift or
   stale context to correct (see `agent-contract-handoff`). Multiple valid approaches
   → list them, set status `NEEDS_INPUT`. Carry findings into the
   `evidence_report` of your handoff (schema in `agent-protocol`), confirmed
   and assumed kept distinct.
7. **Stop when the remaining unknowns are not actionable.** Investigation ends
   not when everything is known, but when nothing more you could learn would
   change what you do next. Unknowns beyond that boundary are open gaps, not
   reasons to keep searching.

## Anti-Patterns
- **Searching for what context already holds.** The map names the resources and
  identifiers — re-enumerating to rediscover them wastes calls. Read anchors
  first.
- **Searching outside your scope.** A surface you do not own yields findings
  you cannot verify or act on. Scope first; report the rest as a dependency.
- **Proposing on the assumed.** A plan built on inference collapses when reality
  disagrees. Propose only on the confirmed; everything else is an open gap.
- **Treating prior knowledge as project convention.** The project's own "we do
  Y" outweighs abstract best practice. Consistency within the project wins.
- **Skipping investigation because the prompt is specific.** The orchestrator
  does not see the actual state. When instructions contradict what you observe,
  observed reality wins.
- **Solving a prerequisite by the fastest path instead of the project's.** If
  the project manages that kind of thing through a specific mechanism,
  bypassing it creates drift. Report the dependency.
- **Over-investigating.** Searching after the remaining unknowns can no longer
  change your next action spends budget without changing the outcome.
