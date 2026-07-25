---
name: diagram-builder
description: Use when the user wants to build or extend a diagram deck of nested sections and components authored in plain YAML — an architecture map, a timeline diagram, a planner board, a process-flow diagram, a slide-style presentation, a side-by-side comparison, or a mind-map. Not for charts, plots, or numeric/data visualization — route those to the dataviz skill. Triggers — "build a diagram", "architecture diagram", "diagram deck", "timeline diagram", "flow diagram", "planner board", "comparison diagram", "add a page/section/component to the diagram".
---

# Diagram Builder

Diagram-builder draws a diagram of any kind — a system architecture, a timeline,
a slide-style presentation, a process flow, a comparison, a mind-map, a planner
board — as nested boxes authored in plain YAML and rendered by a generic engine:
no framework, no server, opens under `file://`. Its whole material is two
primitives — a recursive **section** that ARRANGES and a **component** that
CARRIES — and its work is to find the form that teaches THIS idea best and lower
it into that geometry. Everything domain-specific lives in the data; nothing
about a domain lives in the engine. Everything on the canvas invites the reader
toward the centre: the layout centres its content, a click opens a bottom-centre
panel, a chip spotlights a relation.

```
idea
 └─ document        the deck: title, subtitle, version, filters, pages
     └─ page        one act/view (also the ROOT section: its columns + sections)
         └─ section     a grid zone; nests other sections freely (a grid of grids)
             └─ component   a leaf that carries: a card, a divider, a lane label
   filters (document- or page-level) light a relation across components
```

## The governing definition (the anchor)

Internalize this before anything else. Every design decision is judged against
it, and the adversarial critique at the end of the cycle is run against it:

> A diagram is a **semantic design tool: nested boxes, one inside another**.
> Some boxes are **sections** — they group other sections or groups of
> components. Sections divide into **columns**, vertically and horizontally.
> **Components expand horizontally and vertically** — a merge on **two axes**:
> a span of columns plus a row-span of rows — and they sit in columns, or in
> cells flowing downward. The objective is **compaction and symmetry**: the
> canvas **fills** inside a **centered width cap** (max-width ≈ 1280px — a
> medium resolution, no horizontal scroll). It neither expands to arbitrary
> width nor leaves holes — **full rectangles**.

The engine implements this model: both merge axes are real, the canvas fills to
the centered cap, cells keep a readable minimum width (columns collapse before a
cell degrades), a guardrail asserts form-scoped invariants against the real
rendered geometry, and a strict schema rejects any unknown field loudly at build
time. So design, discuss, and critique against the definition knowing the engine
renders it — the exact geometry and the field-by-field schema live in
`reference.md`, the dialect's terms and value sets in `GLOSSARY.md`.

## The nine principles

Nine principles, not a menu of features. Every capability of the engine is a
consequence of one of them, so hold the principles and the possibilities open by
themselves.

### How the canvas works

**1 · Everything you see is a merged cell.** There is no other geometry: one
uniform cell, and two axes to merge it on. Width is REACH; height is MAGNITUDE.
A merge consumes rows or columns that must already exist — something has to sit
beside it creating them. Ask: what do I want bigger, in which direction, and
what holds it up?

**2 · A grid holds cells or zones, and mixing them changes what every dial
means.** One nested section among components turns the whole level into a row of
zones: `columns` stops creating tracks, `span` becomes a relative weight,
`rowspan` ceases to exist, and the cell invariants stop measuring that level.
You will mix sometimes — a timeline of sections divided by separators is a good
reason. Mix knowing what you give up.

**3 · You author a sequence, not positions.** There is no cell coordinate, only
`order`. That same order is the packing order and the stacking order when
everything collapses to one column. And filling runs forward: nothing goes back
to fill the hole a tall cell left. Whatever belongs beside something tall goes
before it.

### How you speak through it

**4 · Every field is a slot with a character, not a meaning.** The field decides
SIZE and PROMINENCE; you decide the meaning. The kicker is small — a qualifier,
a code, a step. The title is the loud one, and that is where a number goes when
the number is the message. The description is brief and clamps. The detail is
unbounded, behind a click. Read a slot's character wrong and the layout fights
you: a lane label pressed into service as a section heading grows, steals space,
and distorts the grid — a section that needs a heading has one.

**5 · Every visual channel carries one claim.** Position, size, colour, border
style, kicker — independent of each other. Double them to reinforce (a bar that
grows and turns red says magnitude twice) or split them to say two things. A
channel's meaning is PER PAGE, and it has to be declared.

**6 · The grid does not draw relations: it lights them.** There are no arrows.
Every relation is shared membership in a chip — a directed path, which `order`
makes readable, or a concept that cuts across sections. A relation needs two
ends; a chip with a single member dims the page and lights nothing. The same key
on two pages projects one onto the other.

### What judges it

**7 · Structure is the assertion.** Distinct things are distinct sections; parts
of one thing are components inside its section. No machine can verify this, and
it is the only thing separating a diagram from decoration.

**8 · What does not fit does not shrink: it moves.** A cell never grows by
content. Every "it doesn't fit" is answered by moving text into the detail, by
merging, or by collapsing columns through nesting — never by squeezing. An
unreadable cell is a defect even when the geometry closes.

**9 · The hole speaks.** An empty cell asserts something. If you did not mean to
say it, close it; if you did, declare it. And a shared row only means something
when every lane is the same length.

## What the guardrail can and cannot see

The guardrail is not a design judge. It measures the real rendered geometry, so
it reaches exactly the principles that ARE geometry:

| Principle | Automatic check |
|-----------|-----------------|
| 1 merged cell · 2 cells-or-zones · 3 sequence | **yes** — the merge, the compound level, and the packing order are all measured on the render |
| 8 does not shrink · 9 the hole speaks | **yes** — clamped content, the readable floor, empty tracks and orphan cells are all asserted |
| 6 relations | **half** — the chip↔component join is asserted in both directions; whether the relation is the RIGHT one is unreachable |
| 4 slot character · 5 one claim per channel · 7 structure is the assertion | **no** — nothing measurable distinguishes a meaningful section from a convenient one |

Therefore: **a green guardrail means "it is not broken", never "it is right".**
Reading the render and running the adversarial critique are not an elegant
closing ritual — they are the ONLY verification that exists for four of the nine
principles (4, 5, 7, and the half of 6 no machine can reach). A verdict that
rests on green alone has verified the geometry and asserted the meaning.

## Conceptualizing the user's problem

The form is the LAST decision, not the first. Before it:

1. **Understand the problem.** What is the idea, who reads it, what should they
   walk away knowing? For a change to an existing deck, `data/` is the source of
   truth — read the real pages, sections, and components (the knowledge, not
   just how the engine works) before proposing anything.
2. **Help develop it.** A vague idea is not a blocker; developing it is the
   work. Name the entities, ask what is distinct from what, surface the
   relations the user has not named yet.
3. **Summarize and adapt the information to the components.** Each thing must
   survive as a qualifier, one loud line, a brief gloss that clamps, and
   whatever is unbounded behind a click. Information that cannot be compressed
   that way is not yet a component — it is a section, or it is detail.
4. **Only then choose the form.**

**Two kinds of input.** A specific, structured document (a spec, an itemized
doc) already carries its structure — MIRROR it: its parts become sections, its
items components; inventing a different shape discards the author's own
assertion. An open idea carries no structure — EXPLORE which form teaches it
before drawing anything.

**Choosing the form is a criterion, not a lookup.** Two questions settle it: does
the idea MOVE or STAND? (a process wants a timeline or a flow; a structure wants
a dashboard, a comparison, a mind-map, a planner) — and does the reading
CONVERGE on one thing or DIVERGE into many? (a mind-map and a comparison
converge; a planner and a dashboard diverge). One caveat the geometry imposes:
the engine is a GRID and cannot radiate, so a mind-map is symmetric nested
sections around a central band — never present a radial burst as something it
draws. For a wider inventory of visualization forms and what each one teaches,
<https://www.visual-literacy.org/periodic_table/periodic_table.html>. The
copyable YAML skeleton for each form is in `reference.md` ("Per-form seed
skeletons") — do not rebuild it from memory.

## The semantic doctrine (the layout mirrors the idea)

The structure of the layout IS a mirror of the structure of the idea, so the
mapping is never stylistic — it is the meaning:

- **Distinct things are distinct sections; parts of one thing are components in
  one section.** Folding two distinct things together for visual convenience
  erases the distinction the idea makes.
- **A cross-cutting relation is a CHIP, not structure.** Beyond "what are the
  sections?", ask "what should the reader be able to spotlight?".
- **A separator is a WEAK divider.** A line divides only WITHIN a section. If
  the two sides are distinct things, they are sections — reaching for a line
  where a boundary belongs understates the distinction.
- **Every element is justified by MEANING, nothing by decoration.** For each
  placement you must be able to say why: why a section, why this column count,
  why this merge, why on the right, why the base band. That "why" IS the design
  critique, run element by element.
- **The doctrine RULES over the geometry.** Compaction, symmetry, and full
  rectangles are targets, not the meaning. A hole or an asymmetry is legitimate
  only when it ENCODES an intention; when it does not, it is a defect. Never
  fold, drop, or distort a semantic distinction to make a rectangle come out
  full — the geometry serves the idea, never the reverse.

## The conversational cycle and the handoff to the builder

The cycle is generic: whoever HOLDS the idea drives it, person or agent.

**vague idea → develop → propose → sketch → iterate → build → validate → adjust
by recalculating.**

- **Propose; do not wait to be told.** From a vague idea — a suggestion or a
  direct mandate — naming the entities, how they group, and which form teaches
  them is your move.
- **Be explanatory, not verbose.** Do not assume the other side knows the jargon
  or the app. Say what a section, a band, or a chip does FOR THEIR IDEA, one
  plain sentence, when it earns its place.
- **The sketch is spoken, not notated.** Describe the shape in the shared
  vocabulary — "two sections side by side, the left one three cells wide, a
  full-width base band beneath them with a labelled divider" — and decide NO
  component detail here: no title wording, no kicker, no positions. The sketch
  is cheap to redo; that is its whole value, and detail is what makes it
  expensive.
- **The handoff to the builder is the agreed FORM plus the VALUES, in the
  natural language of the dialect.** You hand over the shape and the meaning —
  which sections, which merges, which relations, and the content each component
  carries. The builder holds the schema (`reference.md`, `GLOSSARY.md`) and
  lowers it into fields; you do not need the field names to hand off well.
- **Adjust means RECALCULATE, never nudge.** When a datum arrives — "mount it in
  that section", "this belongs at the base" — name the dials it touches, reason
  how the rows repack and where the collapse lands, and show the before/after of
  the changed section instead of re-reading the whole deck.
- **Ask WHERE before saving anything.** Never assume a path; the scaffolding
  modes are in `reference.md`.

**The verdict.** Editing the data is the fast path — the diagram is decided in
the YAML, not in the pixels — and a change is not done until its verdict is
earned. Build, then run the engine's own guardrail (the loop, the commands, and
the invariant table are in `reference.md`), and never declare done on red. On top
of that:

- **Every verdict declares its evidence class.** **Class A** — the guardrail
  suffices: the topology is intact and the change's intention is fully covered by
  existing invariants (a copy edit, a colour change, an `order` swap inside a
  settled grid). **Class B** — guardrail AND eye: a first build, a change of form
  or of model, or any intention no invariant covers; look at the rendered result
  and load `visual-verify` for the looking discipline. "Green, Class A: intention
  covered by the collapse and band invariants" is a verdict; a bare green is not.
- **The RATCHET rule.** Every defect the eye catches becomes an invariant before
  the change closes — the guardrail only grows. A defect fixed without a new
  invariant will be reintroduced by the next change the guardrail cannot see.
  Invariants are form-scoped and retirable (one can supersede another), which is
  what lets the guardrail grow without ossifying: a rule tuned to a dashboard
  must not fail a legitimate timeline.
- **The adversarial critique closes the work.** Walk the rendered layout against
  the governing definition and demand the doctrine's "why" for every element,
  and that every intended relation has a chip. An element without a "why" fails
  the critique.
- **Headless changes the counterpart, not the method.** With no interactive user,
  "iterate" resolves as adversarial SELF-critique against the doctrine before
  building — the sketch is still made, then interrogated element by element. An
  ambiguity that survives the self-critique is a REPORT FINDING, never a guess.

## The seed is the showcase — open it

`assets/data/` carries a domain-free seed deck whose only job is to EXERCISE
every tool the engine offers: inline sections side by side, nesting, the
structural leaves, height-as-magnitude, a partial merge, the collapse cascade at
a wide column count, span-weighted zones, and — twice — the deliberate mixing of
cells and zones that principle 2 warns about. It is the fastest path from "is
this possible?" to seeing it rendered.

**Open it.** A capability read in a seed that renders is worth more than the same
capability described in prose, and the seed is where a claim gets falsified.

**Coherence runs both ways.** Every tool this skill names is exercised in the
seed, and every tool the seed exercises is named in this skill. A capability
present on only one side is a defect: either the seed lost its demo, or the skill
grew a claim nothing renders. Check the pair whenever either side changes.

## Where the rest lives

- `GLOSSARY.md` — the canonical dialect terms and their value sets; the shared
  vocabulary the skill and the rendered app both speak.
- `reference.md` — the field-by-field schema, the fill geometry, the per-form
  skeletons, the positioning recipes, the engine gotchas, the authoring modes,
  and the build → validate loop with the form-scoped invariant table.
- `assets/` — the portable engine, ready to scaffold into any repo, plus the
  seed `data/` above.
