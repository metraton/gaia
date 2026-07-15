---
name: diagram-builder
description: Use when the user wants to build or extend a diagram deck of nested sections and components authored in plain YAML — an architecture map, a timeline diagram, a planner board, a process-flow diagram, a slide-style presentation, a side-by-side comparison, or a mind-map. Not for charts, plots, or numeric/data visualization — route those to the dataviz skill. Triggers — "build a diagram", "architecture diagram", "diagram deck", "timeline diagram", "flow diagram", "planner board", "comparison diagram", "add a page/section/component to the diagram".
---

# Diagram Builder

Diagram-builder is a **semantic design tool that draws any kind of diagram** — a
system architecture, a timeline, a slide-style presentation, a process flow, a
comparison, a mind-map, a planner board — rendered from plain YAML by a generic
engine, no framework, no server, opens under `file://`. Architecture is one form
among many; the skill's job is to find the *form that teaches THIS idea best*
and express it in two primitives — a recursive **section** (a grid that
arranges) and a **component** (a leaf that carries). Everything domain-specific lives in the data; on top of
the two-primitive core sits a full palette of expressive tools (component types,
colour-roles, kickers, relation filters — see "The vocabulary & palette" below) that
you hold in mind while you design. Everything on the canvas invites the reader
toward the centre: the layout centres its content, a click opens a bottom-centre
panel, a chip spotlights a relation.

## The governing definition (the anchor)

Internalize this before anything else. It is what every design decision is
judged against, and the final critique of the method (below) is run
adversarially against it:

> A diagram is a **semantic design tool: nested boxes, one inside another**.
> Some boxes are **sections** — they group other sections or groups of
> components. Sections divide into **columns**, vertically and horizontally.
> **Components expand horizontally and vertically** — a merge on **two axes**:
> a span of columns plus a row-span of rows — and they sit in columns, or in
> cells flowing downward. The objective is **compaction and symmetry**: the
> canvas **fills** inside a **centered width cap** (max-width ≈ 1280px — a
> medium resolution, no horizontal scroll). It neither expands to arbitrary
> width nor leaves holes — **full rectangles**.

Each idea still wants its own form — a timeline is one row of wide spans, a
presentation a sequence of pages, a flow is components tied by a highlight
`filter`, a comparison two sections side by side, a mind-map symmetric nested
sections around a central band (the engine is a GRID — it cannot radiate; do
not present radial shapes as something it draws), a planner a grid of idea
cards — but every form is an
arrangement of the same nested boxes, judged by the same objectives: compact,
symmetric, full.

**The engine implements this governing model.** Both merge axes are real
(`span` partial column merges + `rowspan` row merges), the canvas fills to the
centered 1280px cap, cells keep a readable 120px floor (columns collapse before
a cell degrades), the guardrail asserts form-scoped invariant families, and a
strict authoring schema rejects any unknown field loudly at build time. Design,
discuss, and critique against the definition knowing the engine renders it; the
exact geometry and field-by-field schema live in `reference.md`.

## First load

On loading this skill: (1) internalize the governing definition; (2) validate
the environment via the engine's own verify (see the truth discipline below);
(3) understand the current diagram — **`data/` is the source of truth**, so for
a modification read `data/` FIRST to learn the deck's real pages, sections, and
components (the knowledge, not just how the engine works) before proposing
anything, and for a new idea the idea is captured *into* `data/`; (4) then
analyse, propose, and discuss.

## Thinking in two layers (understanding vs authoring)

Every diagram moves through two layers, and each draws on a different depth of
knowledge:

- **Layer 1 — STRUCTURE (the discussion).** The high-level conversation where
  the shape is agreed: which sections, which boxes, which merges. Hold the
  *capability catalog* in mind — the two structural terms and the full palette
  of component types, colour-roles, kickers, and relation filters (below) — and
  reason in shared terms: "two sections, these boxes, a separator labelled
  'division', in these columns and spans". At this layer NO component detail is
  decided — no title wording, no status, no positioning; deciding those here
  drowns the structural discussion in detail the sketch does not need.
- **Layer 2 — CONSTRUCTION (the build).** Lowering the agreed shape into data:
  each component's payload (status/title/description/detail) and its
  POSITIONING inside the grid. The component's expressive tools — the payload
  fields — belong to this layer: you invoke them while building, looking up the
  field-by-field detail in `reference.md` and `GLOSSARY.md`, never during the
  discussion. Realize each intended relation as a filter `key`, using
  `order`/position so a FLOW-kind relation reads directionally.

You carry the catalog; you open the detail when you build.

## The semantic doctrine (the layout mirrors the idea)

The structure of the layout **is a mirror of the structure of the idea**. Every
structural choice encodes a semantic claim, so the mapping is not stylistic —
it is the meaning:

- **Distinct things are distinct sections.** An idea made of four things is
  four sections, each with its own components inside. Do not fold two distinct
  things into one section for visual convenience.
- **Parts of one thing are components in one section.** If they are aspects,
  steps, or members of the same thing, they are siblings inside its section.
- **Cross-cutting relations are FILTERS, not structure.** Beyond "what
  are the sections and components?", ask **"what relations should the reader
  be able to spotlight?"** — a chip RELATES components that share either a
  directional FLOW (the substitute for an arrow, since a grid cannot draw
  edges) or a cross-cutting CONCEPT/status/theme/plan that cuts across
  sections. Each becomes a filter chip; lighting it reveals membership
  across the whole canvas.
- **A separator is a WEAK divider.** A line only divides *within* a section —
  sub-phases, internal groupings. If the two sides are distinct things, they
  are sections, not a line. Reaching for a separator where a section boundary
  belongs understates the distinction the idea is making.
- **Every element is justified by MEANING — nothing by decoration.** For each
  placement you must be able to say *why*: why this is a section, why this
  column count, why this merge, why it sits on the right, why it is the base
  band — in terms of priority, reading order, grouping, or foundation.
- **That "why" IS the design critique.** If you cannot justify a placement
  against the idea, it is misplaced — not "fine for now". The adversarial
  critique in the method below is exactly this interrogation, run element by
  element.
- **The semantic doctrine RULES over the geometric objectives.** Compaction,
  symmetry, and full rectangles are targets, not the meaning: a hole or an
  asymmetry is justified only when it ENCODES an intention of the idea; when it
  does not, it is a defect. Never fold, drop, or distort a semantic distinction
  to make a rectangle come out full — the geometry serves the idea, never the
  reverse.

## The vocabulary & palette

The **structural core is two terms**, split by one clean rule: **sections
ARRANGE, components CARRY**. A `section` groups — other sections, or groups of
components — and its content IS the arrangement: columns, nesting, merges. A
`component` is the leaf that carries the information — its payload of
`status` / `title` / `description` / `detail`; when a box needs to say
"something more", that something lives in its payload, never in structure.
Learn the two terms so that when the user says "component" or "section" you
already know what they mean; on top of them the engine gives a full palette of
expressive tools you carry into every discussion. Name the capabilities here so
you hold the map; the field-by-field definitions, defaults, and full enums live
in `GLOSSARY.md` — do not restate them. The rendered app documents this same
vocabulary in its help HUD (the "?" button, or the `H` key), so a viewer can
learn it too.

The Layer-1 sketch needs only a handful of these terms in shared speech — a
`section` (arranges) and a `component` (carries), merged on two axes by `span`
(columns; `span == columns` is a full-width **band**, an inline `span: 1` sits
side by side) and `rowspan` (rows), scoped by a `form`, and traced by a
`filter`. That is the whole sketch vocabulary; **the full definitions, defaults,
and enums (`cell`, `columns`, `status`, `variant`, every value set) live in
`GLOSSARY.md`** — reach for it, do not restate it here.

**The root/canvas is itself the invisible base section.** `page.columns` is the
root grid width and `page.sections` are its children — the page is section depth
0, with no frame of its own. Everything else is the same section, nested.

The palette is an inviting toolkit, not a fixed menu:

- **Three component types** (the leaf `type`): `box` — the standard clickable
  card (a `status` kicker, `title`, `description`, and a click-through `detail`);
  `separator` — a divider LINE, with an optional `text` label; `rail` — a
  swimlane LABEL banner, horizontal or vertical.
- **Payload** — a box's payload is fixed (`status` / `title` / `description` /
  `detail`); everything the box "says" lives in those four fields — the
  field-by-field Layer-2 detail is in `reference.md` and `GLOSSARY.md`.
- **`status`** — the kicker badge word on a box: **open vocabulary**, any string
  the idea needs.
- **`variant`** — the colour/frame role: a component `variant` tints a box, a
  section `variant` frames a zone. Named roles, not a closed set.
- **`filter`** — the relation tracer: a chip (`key`, `label`, `steps`) that
  spotlights every component sharing its key — a directional FLOW or a
  cross-cutting CONCEPT — so the relation traces across the whole canvas.
- **`columns` / `span` / `order`** — the layout dials: how wide a grid is, how
  many columns a child merges, and where it sits in its row.

The exact `status` and `variant` value sets live in `GLOSSARY.md` — reach for
them when you author.

## The decomposition model

```
idea
 └─ document        the deck: title, subtitle, version, filters, pages
     └─ page        one act/view (also the ROOT section: its columns + sections)
         └─ section     a grid zone; nests other sections freely (a grid of grids)
             └─ component   a leaf: box | separator | rail
   filters (document- or page-level) trace a highlighted relation across components
```

## The layout model (how position works)

Position is a **known operation, not trial-and-error** — you reason it from the
governing definition and a small set of dials, then recalculate with intent
instead of nudging values until they look right.

- **A section declares its own `columns`** and its children flow left→right,
  wrapping down into cells.
- **A merge is the unit of emphasis, on two axes.** `span: M` occupies exactly
  M columns (a partial merge when M < columns; `span == columns` is a
  full-width **band** that takes its own row), and `rowspan: K` grows a cell
  downward K rows — height as magnitude. On collapse a partial span keeps its
  proportion; see `reference.md` for the exact behaviour.
- **Band vs inline.** A band takes its own full row; an inline section
  (`span: 1`) sits side by side with its neighbours.
- **Compaction and symmetry are the target.** Pack the grid so rectangles come
  out full — no orphan holes, no ragged rows the idea does not justify — and
  balanced inside the centered width cap (≈1280px). Nothing scrolls sideways;
  the collapse cascades …→2→1 as width tightens and the block stays centered.
- **Cells have a MINIMUM READABLE width.** A cell exists to be read. The engine
  enforces a 120px floor (`--cell-min-w`): the grid COLLAPSES COLUMNS — fewer,
  wider cells — before any cell shrinks into unreadable text, and the
  guardrail's **M** invariant asserts the floor on the real render. Still
  choose a column count the content survives — the floor is a guard, not a
  design.

The engine's exact fill geometry — the cap, the floor, the merge axes, the
strict schema — is in `reference.md`.

**Translate the idea into the dials + order.** When the user expresses a
placement, map it to concrete `columns`/`span`/`order` values — the idea→dials
recipes live in `reference.md`, "Positioning recipes".

## The method: the conversational cycle

The method is generic — it holds for a person or an agent, whoever **holds the
idea**. The idea-holder drives; the cycle turns a vague idea into a validated
canvas.

1. **Be explanatory, not verbose.** Do not assume the other side knows the
   jargon or the app. Name what a section, a band, a filter *does for their
   idea* as you propose it — one plain sentence each, when it earns its place.
   Explanatory is a default posture, not a word count.

2. **The idea-holder makes the initial proposal.** From a vague idea — whether
   it arrives as a suggestion or as a direct mandate — *develop* it and
   propose a concrete shape. Never wait to be told "two sections, four
   components": naming the entities, how they group, and which form teaches
   them is your move, made from the semantic doctrine above.

3. **The cycle:** vague idea → develop → **propose** → give a **sketch** (a
   drawing/boceto in shared vocabulary, Layer 1 only: which sections, which
   boxes, which bands and spans — no titles, statuses, or positions yet;
   cheap to redo) → **iterate** with the user → **implement with the
   model in mind** (translate the agreed sketch into `columns`/`span`/`order`,
   recalculating the whole page in the governing model's terms) → **validate**
   (the truth discipline below) → **adjust on new data**. The cheapest place to
   get the shape right is the conversation, before and between builds.

4. **Adjust means RECALCULATE, never nudge.** When a datum arrives — "mount it
   in that section", "this belongs at the base" — name the exact dials it
   touches, reason how the rows repack and where the collapse lands, and show
   the before/after of the changed section so the user sees the edit rather
   than re-reading the whole deck.

5. **Two input types shape step 2.** A specific, structured document (a spec,
   an itemized doc) already carries its structure — mirror it faithfully: its
   parts become sections, its items components. An open idea has no given
   structure — explore which pedagogical FORM fits (timeline, presentation,
   flow, comparison, mind-map, planner) before drawing anything.

**Headless holds the same method.** The cycle is generic; when no interactive
user exists (a headless holder, a scheduled task), "iterate with the user"
resolves as adversarial SELF-CRITIQUE against the doctrine before building —
the sketch is still made, then interrogated element by element. Class B
evidence stays available headless (the PNGs plus the `visual-verify`
discipline). An ambiguity that survives the self-critique is a REPORT FINDING,
never a guess. The method does not fork; only the counterpart changes.

## The truth discipline: build → validate → critique

Editing the data is the **fast path** — the diagram is decided in the YAML, not
in the pixels. A change is not done until its verdict is earned, and every
verdict **declares the evidence behind it**.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data (`npm run build`, which runs
   `engine/build-data.mjs` → `data/data.generated.js`). Re-run after every YAML
   change. Explain the command in one plain sentence before running it.
3. **Validate — the guardrail.** Run the engine's own verify
   (`npm run validate` → `tools/validate-layout.cjs`). Validate is **DECOUPLED
   from build and pure-read**: it renders and asserts the EXISTING
   `data/data.generated.js` and never regenerates it — build first. It renders
   every page in headless Chromium at five widths, five reloads each, and
   asserts every applicable invariant against the real rendered geometry,
   exiting non-zero on any hard failure. Screenshots go to a **system temp
   dir, never into the project**.
4. **Know what the invariants cover — two families, both implemented.** Both
   run today in `tools/validate-layout.cjs` as a FLAT **form-scoped** table:
   the page declares its `form`; each invariant declares its applicability
   set, severity (`dura` fails · `consejo` advises), and retirement clause.
   - **INTEGRITY** (the layout is not broken — every form, `dura`): **D**
     determinism · **R** scrollbar-robust · **T** untruncated capture · **C**
     description clamp · **O** no horizontal overflow · **F** collapse
     cascade · **S** inline-fit / band-span · **B** centered block · **H**
     headers inside their section · **X** no sibling-section collision · **G**
     no compound-leaf balloon / no stacked-section content overflow (the
     compound-row leaf balloon / sec-c1 overflow guard).
   - **DESIGN** (the layout serves the governing definition — form-scoped,
     `dura` where they apply): **U** equal/uniform cells (supersedes the
     retired fixed-width **W**) · **E** no empty column · **P** no orphan
     cell · **L** cells fill the width edge-to-edge · **M** the readable
     120px floor · **Y** band content fills the band · **Q** compound section
     widths follow authored span. **V** (horizontal composition) is
     `consejo` — an advisory that never fails; a timeline is deliberately one
     long row, so V does not even apply to it.
5. **Choose the evidence class — and declare it.**
   - **Class A — the guardrail suffices**: the topology is intact and the
     change's intention is fully covered by existing invariants (copy edits, a
     `status`/`variant` change, an `order` swap inside a settled grid). A green
     `npm run validate` is the verdict.
   - **Class B — guardrail + eye**: a first build, a change of form or of
     model, or an intention no invariant covers. Run the guardrail AND look at
     the rendered result — `npm run verify` writes full-page PNGs across widths
     and both themes; load the `visual-verify` skill for the looking
     discipline.
   - **Every verdict declares its class.** "Green, Class A: intention covered
     by F and S" or "Green, Class B: guardrail + shots reviewed at 3 widths". A
     green without declared scope is **not** a design verdict — this is what
     prevents the rubber-stamp.
6. **The RATCHET rule — form-scoped.** Every defect the eye catches becomes an
   invariant before the change closes — the guardrail only grows. A defect
   fixed without a new invariant will be reintroduced by the next change the
   guardrail cannot see. Design invariants are **form-scoped**, expressed as a
   FLAT lookup, never a tree of cases (the `INVARIANTS` table in
   `tools/validate-layout.cjs`): the page declares its FORM (timeline,
   dashboard, flow, comparison…); each invariant declares the set of forms it
   applies to (its applicability set), a severity (`dura` vs `consejo`), and a
   **retirement clause** — an invariant can be superseded by one that
   generalizes it, as the retired fixed-width **W** → **U** set the precedent.
   That is what lets the guardrail grow without ossifying: an invariant tuned
   to a dashboard does not fail a legitimate timeline.
7. **Final step — adversarial design critique.** Before declaring done, walk
   the rendered layout against the **governing definition** and demand the
   semantic doctrine's "why" for every element: why this section, why this
   merge, why this position — and that every intended relation has a chip. An
   element without a "why" fails the critique —
   this step is the doctrine's justification test run as verification, not a
   formality.
8. **Loop on any FAIL** — the failing invariant names the zone and the measured
   value; fix the YAML, rebuild, re-validate. Never declare done on red.

## Anti-patterns

Each row is a principle of failure observed in real decks, anchored to the
doctrine bullet it violates.

| Anti-pattern | What happened | Violates |
|--------------|---------------|----------|
| **A rail as an oversized title** | a `rail` ("DEUDA TOTAL POR MES") was used as the header of the "Camino a Deuda Cero" section; it grew, stole space, and distorted the grid. A rail is a LANE label; if a section needs a heading, that is the section's `title` | "every element is justified by MEANING" / the rail's role in the palette |
| **Merging distinct things into one section** | two distinct things folded into one section for visual convenience, erasing the distinction the idea makes | "distinct things are distinct sections" |
| **A gratuitous separator** | divider lines with no meaning were added (and later removed) in "Deudas por Banco"; a line that divides nothing understates or invents structure | "a separator is a WEAK divider — it only divides *within* a section" |
| **Fill without a cap** | the block filled to any width — sprawl at 2560px, no centered max-width | "fills inside a centered width cap / full rectangles" (the governing definition) |
| **Rubber-stamping the green** | declaring done off a green guardrail without declaring the evidence class or running the design critique | "every verdict declares its class" / the truth discipline |
| **Over-subdivision below the readable minimum** | a section was split into so many columns that each cell rendered its text at one character per line; the guardrail stayed green — the geometry held — but nothing could be read | "cells have a MINIMUM READABLE width" — collapse columns instead of shrinking the cell |

## Feasibility, transparency, capability

- **Validate feasibility first, step by step** — read what the environment offers
  and reason one thing at a time (*"can I run the build? the verify? how far can I
  get?"*) **before** investing in a full synthesis. Order: **feasibility →
  understand → choose form → propose/sketch → iterate → build → validate.**
- **The environment check is the engine's own verify.** Before building a
  diagram, confirm the OS and dependencies the render needs are present by
  running the engine's verify capability rather than assuming them — it launches
  the headless browser it needs (and can install the browser/Playwright when it
  is missing), so a green verify is also proof the environment is ready. If the
  verify cannot run at all, degrade gracefully (below) instead of failing.
- **Explain before you execute** — before running any script, say in one plain
  sentence what it does and which file to open to inspect it first, so the user
  can see what will run before it runs.
- **Degrade gracefully** — with only a browser you can *view* (the committed
  `data.generated.js` renders with zero tooling); Node adds *rebuild*; Playwright
  adds the *layout guardrail* (`npm run validate`). When Playwright is present the
  guardrail is not optional — a layout change is not done until it is green. The
  engine carries **no baked-in data** — every domain string lives in `data/`,
  which keeps a scaffold generic and leak-free. Detail in `reference.md`.

## Creating & saving

**When the user wants to save, ask WHERE** — "where do you want to save this?" —
never assume a path. Creating a new deck produces a **portable repo-kit**: the
standard engine layout (`index.html`, `engine/`, `data/`, `tools/`,
`package.json`), with no baked-in domain data. It uses Git to version the repo,
but assumes **no** GitHub and **no** CI/CD or deploy pipeline — the kit can live
anywhere.

The five modes, each stepped out in `reference.md`:

1. **New repo-kit** — scaffold the engine + a seed document into a chosen path.
2. **Add the engine to an existing repo** — drop the engine layer into a subfolder.
3. **New page** — add a page YAML and register it in the manifest.
4. **New section** — add a section to a parent's `children`; nest by giving it
   its own section children.
5. **Add/edit components** — populate a section with boxes, separators, rails.

## Where the rest lives

- `GLOSSARY.md` — the canonical dialect terms + the `status` and `variant` value
  sets; the shared vocabulary the skill and the rendered app both speak.
- `reference.md` — the engine's field-by-field schema and behaviors: the fill
  geometry (the 1280px cap, the 120px floor, both merge axes), the strict
  authoring schema, the authoring modes, and the build → validate loop with the
  form-scoped invariant table.
- `assets/` — the portable engine, ready to scaffold into any repo: `index.html`
  (the fill-to-cap CSS model), `engine/engine.js`, `engine/build-data.mjs`
  (build + strict schema), `package.json`, `tools/validate-layout.cjs` (the
  layout guardrail), `tools/verify.mjs`, and a domain-free seed `data/` that
  showcases two inline sections side by side, nesting, a base band with a
  separator and a rail, a row-span bar chart, and a partial 2-of-4 merge.
