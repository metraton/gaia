---
name: diagram-builder
description: Use when the user wants to build, design, or extend a diagram — an architecture overview, a timeline, a planner board, a flow diagram, a presentation, a comparison, or a mind-map — as a portable, data-driven deck rendered from plain YAML. Triggers — "build a diagram", "architecture diagram", "diagram deck", "timeline", "flow diagram", "planner board", "add a page/section/component to the diagram".
---

# Diagram Builder

Diagram-builder is a **canvas that draws any kind of diagram** — a system
architecture, a timeline, a slide-style presentation, a process flow, a
comparison, a mind-map, a planner board — rendered from plain YAML by a generic
engine, no framework, no server, opens under `file://`. Architecture is one form
among many; the skill's job is to find the *form that teaches THIS idea best* and
express it in two primitives — a recursive **section** (a grid) and a
**component** (a leaf). Everything domain-specific lives in the data; on top of
the two-primitive core sits a full palette of expressive tools (component types,
colour-roles, kickers, flow filters — see "The vocabulary & palette" below) that
you hold in mind while you design. Everything on the canvas invites the reader
toward the centre: the layout centres its content, a click opens a bottom-centre
panel, a chip spotlights a flow.

Each idea wants its own form. A timeline is one row of wide spans; a presentation
is a sequence of pages; a flow is components tied together by a highlight
`filter`; a comparison is two sections side by side; a mind-map is nested
sections radiating from a centre; a planner is a grid of idea cards. Same
machinery, different form — reach for the shape that makes *this* idea click.

## Understanding vs authoring

Two modes of engagement draw on different depths of knowledge. While you
**design and discuss**, hold the *capability catalog* in mind — what exists: the
two structural terms and the full palette of component types, colour-roles,
kickers, and flow filters (below). That is what lets you take an idea or a
document and reason in shared terms — "two sections, these boxes, this status, a
separator labelled 'division', in these columns and spans" — and validate the
shape with the user before touching anything. When you **author, build, or
verify**, look up the field-by-field detail — how to author each field, its
defaults, its validations — in `reference.md` and `GLOSSARY.md`. You carry the
catalog; you open the detail when you build.

**Data-first.** `data/` is the source of truth for a deck's content — the
analysis is born from `data/`. For a **modification**, read `data/` FIRST to
understand the diagram's actual state (its pages, sections, components — the
knowledge, not just how the engine works) before proposing anything. For a
**new** idea, the idea is captured *into* `data/`. On loading this skill:
validate the environment via the engine's own verify (below), understand the
current diagram (read `data/` if one exists), then analyse and discuss.

Be **proactive and pedagogical** — the palette encodes capabilities the user may
not know. When a shape fits, suggest it on the spot: "these can sit two-up with
`columns: 2`", "we can trace that path with a `filter` chip", "that band can span
the full width", "a separator can divide those groups with a label", "that lane
wants a `rail`".

## The vocabulary & palette

The **structural core is two terms** — the recursive `section` and the leaf
`component`. Learn them so that when the user says "component" or "section" you
already know what they mean; on top of them the engine gives a full palette of
expressive tools you carry into every discussion. Name the capabilities here so
you hold the map; the field-by-field definitions, defaults, and full enums live
in `GLOSSARY.md` — do not restate them. The rendered app documents this same
vocabulary in its help HUD (the "?" button, or the `H` key), so a viewer can
learn it too.

| Term | What it is, in one line |
|------|-------------------------|
| `section` | a recursive grid: `columns` + `span` + `children`; its children auto-flow and wrap down; a child may itself be a section (nesting) |
| `component` | a leaf in a grid cell, chosen by `type`: `box` · `separator` · `rail` |
| `cell` | the base unit: every leaf is one fixed cell; cells never resize, only merge and cascade |
| `columns` | how many columns THIS section's grid has (default 2) — the count that cascades 3→2→1 as width tightens |
| `span` | an Excel-style **merge**: occupy N of the parent's columns (default 1); `span == columns` = a full-width **band** that takes its own row; same at every level |
| `band` / `inline` | a band (`span == columns`) takes its own full row; an inline section (`span: 1`) sits side by side with its neighbours |
| `status` / `variant` | the kicker badge word / the colour-role of a box or section |
| `filter` | a chip that spotlights every component declaring its key — how a flow is traced |

**The root/canvas is itself the invisible base section.** `page.columns` is the
root grid width and `page.sections` are its children — the page is section depth
0, with no frame of its own. Everything else is the same section, nested.

The palette is an inviting toolkit, not a fixed menu:

- **Three component types** (the leaf `type`): `box` — the standard clickable
  card (a `status` kicker, `title`, `description`, and a click-through `detail`);
  `separator` — a divider LINE, with an optional `text` label; `rail` — a
  swimlane LABEL banner, horizontal or vertical.
- **`status`** — the kicker badge word on a box: **open vocabulary**, any string
  the idea needs.
- **`variant`** — the colour/frame role: a component `variant` tints a box, a
  section `variant` frames a zone. Named roles, not a closed set.
- **`filter`** — the flow-tracer: a chip (`key`, `label`, `steps`) that
  spotlights every component declaring its key, so a path traces across the whole
  canvas.
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
   filters (document- or page-level) trace a highlighted flow across components
```

## The spreadsheet model (how position works)

The layout is a **spreadsheet of uniform cells**, and this is what makes
positioning a *known operation, not trial-and-error*. Internalize it — it is what
lets you recalculate a diagram with intent instead of nudging values until they
look right.

- **Every leaf is one fixed cell**, the same size at every depth. Cells never
  resize. A title plus up to 3 clamped description lines always fits; longer copy
  lives in the click-through `detail` panel. A cell grows only **horizontally, by
  merge**.
- **A section declares its own `columns`** and its children flow left→right,
  wrapping down. A leaf section is always an integer number of cells wide.
- **`span` is an Excel merge.** `span: M` occupies M columns; `span == columns`
  makes a child a full-width **band** that takes its own row. In a leaf grid a
  merged child spans the whole row, so it never overflows on collapse.
- **Band vs inline.** A band takes its own full row; an inline section (`span:
  1`) sits side by side with its neighbours.
- **Collapse cascades 3→2→1** as width tightens (a 2-column "two-table"
  intermediate, a 1-column single-stack endpoint); the whole block stays
  centered. Cells never change size — only the column count does — so nothing
  scrolls sideways.

You can compute the layout before you render it — the exact cell dimensions and
the width formulas are in `reference.md`.

**Translate the idea into the two dials + order.** When the user expresses a
placement, map it to concrete values:

| The user says… | You change… |
|----------------|-------------|
| "put PROVISIONING as a base band at the bottom" | give it `span: <parent columns>` (a band) and the **last** `order` → a full-width row beneath everything |
| "these two sit side by side" | give each `span: 1`, place them consecutively → they pack onto one row |
| "make this a 3-column section" | set the section's `columns: 3` |
| "this whole thing is one band / a full row" | `span == columns` on it |
| "a wide group beside a narrow sidecar" | nest both in a parent with `columns: (wide+1)`; wide child `columns: N`, sidecar `columns: 1` |
| "a divider / lane label across the band" | a `separator`/`rail` with `span == the section's columns` |
| "move this above/below that" | change `order` — there is no row/column coordinate to set |

## The method: idea → canvas

The order matters: grasp the idea and choose the *form* before you synthesize
components. Choose the form only after you understand the idea — the shape is
what makes it click, so explore which form fits first. Follow the thought-order:

1. **Understand the input first.** What is the idea or document, and what is its
   *intent*? Read what you were given — a prose idea, a markdown doc, a spec, a
   conversation — and name the entities, how they group, and what the reader is
   meant to walk away understanding. **When modifying an existing deck this
   starts in `data/`:** read it first to learn the diagram's real current state
   before proposing a change. You cannot choose a form for an idea you have not
   yet grasped.

2. **Decide the approach by input type.** Two cases:
   - **A specific, structured document** (a spec, a system description, an
     itemized doc) already carries its own structure — mirror it: its sections
     become sections, its items become components. Your job is faithful
     translation.
   - **An open idea** ("show how our onboarding works", "a roadmap for the year")
     has no given structure. *Explore which pedagogical FORM fits before drawing
     anything.* Consider a timeline (one row of wide spans, left→right), a
     presentation (a page per beat), a flow (boxes tied by a `filter`), a
     comparison (two sections side by side), a mind-map (nested sections
     radiating from a centre), a planner (a grid of idea cards). Be **creative
     AND effective**: pick the form that makes the idea *click*.

3. **Synthesize by RECALCULATING the grid.** Now, with the form chosen, decompose
   into the spreadsheet model. For each piece translate the intent into concrete
   `columns` / `span` / `order` (see "The spreadsheet model" above) and
   **recalculate the whole page** — think in cells: which sections are inline vs
   bands, how they pack and compact, where the collapse cascade lands. A
   full-width band for the spine, a nested section for a group-within-a-group, a
   `separator` to divide phases, a `rail` to label a lane, a `status`/`variant`
   to carry meaning by colour, a `filter` to make a path traceable. Every choice
   earns its place by teaching something AND by adding up in the grid.

4. **Discuss with the user.** Be **direct**, speak the vocabulary ("I made
   onboarding a 4-column timeline; step 3 is a full-width band because it's the
   pivot"), and keep adjustments **concise**. Iterate: propose, hear the
   correction, adjust one thing at a time. The cheapest place to get the shape
   right is the conversation, before and between builds.

5. **On every new idea or change, RECALCULATE — don't nudge.** When the user
   proposes a placement ("put this at the base", "make these a band", "this is a
   3-column section"), name the exact dials it touches (`columns`, `span`,
   `order`), reason about how the row packs and where it collapses, and show the
   before/after of the changed section so the user sees the edit rather than
   re-reading the whole deck. Positioning is a known operation — treat it like
   recomputing a spreadsheet.

## The build → validate loop

Editing the data is the **fast path** — the diagram is decided in the YAML, not
in the pixels. A change is **not done until the layout guardrail passes**:
asserting the real rendered geometry, and looking, is what proves a layout,
never a metric that measures the wrong thing.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data (`npm run build`, which runs
   `engine/build-data.mjs` → `data/data.generated.js`). Re-run after every YAML
   change. Explain the command in one plain sentence before running it.
3. **Validate — mandatory.** Run the engine's own **verify** (`npm run validate`,
   which runs `tools/validate-layout.cjs` and re-runs the build itself). It
   renders every page in headless Chromium at five widths, five reloads each, and
   asserts the layout invariants against the real rendered geometry, exiting
   non-zero on any failure. **Assert the invariants passed before you declare
   done.** Each new layout requirement should become a new invariant. Screenshots
   go to a **system temp dir, never into the project**. The full invariant table
   is in `reference.md`.
4. **Look (optional, complementary).** Use the engine's **verify-UI** capability
   (`npm run verify`) — it renders the deck and writes full-page PNGs across
   widths and both themes; read them for what the invariants don't name
   (contrast, a semantically-wrong wrap). This is the lighter collision-only QA
   that complements the layout verify, and it produces the images you review by
   eye.
5. **Loop on any FAIL** — the failing invariant names the zone and the measured
   value; fix the YAML/CSS, rebuild, re-validate. Never declare done on red.

## Feasibility, transparency, capability

- **Validate feasibility first, step by step** — read what the environment offers
  and reason one thing at a time (*"can I run the build? the verify? how far can I
  get?"*) **before** investing in a full synthesis. Order: **feasibility →
  understand → choose form → synthesize → discuss → build → validate.**
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
- `reference.md` — field-by-field schema, engine behaviors, the exact cell
  dimensions and width math, the authoring modes, and the build → validate loop
  with the full invariant table.
- `assets/` — the portable engine, ready to scaffold into any repo: `index.html`
  (the uniform-cell CSS model), `engine/engine.js`, `engine/build-data.mjs`,
  `package.json`, `tools/validate-layout.cjs` (the layout guardrail),
  `tools/verify.mjs`, and a domain-free seed `data/` that showcases two inline
  sections side by side, a base band, nesting, a separator, and a rail.
