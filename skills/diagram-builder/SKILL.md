---
name: diagram-builder
description: Use when the user wants to build, design, or extend a diagram — an architecture overview, a timeline, a planner, or a flow diagram — as a portable, data-driven deck; when decomposing an idea into pages/sections/components; or when the orchestrator needs the diagram vocabulary to propose a decomposition. Triggers — "build a diagram", "architecture diagram", "diagrama", "diagram deck", "add a page/section/component to the diagram", "timeline", "flow diagram", "planner board".
metadata:
  user-invocable: true
  type: domain
---

# Diagram Builder

Diagram-builder is a **canvas for turning any idea into a creative, usable,
pedagogical layout** — rendered from plain YAML by a generic engine, no
framework, no server, opens under `file://`. The idea can be anything: a system
architecture, a timeline, a slide-style presentation, a process flow, a
comparison, a mind-map, a planner board. The engine knows only two primitives —
a recursive **section** (a grid) and a **component** (a leaf) — and everything
domain-specific lives in the data. Everything on the canvas invites the reader
toward the centre: the layout centres its content, a click opens a
bottom-centre panel, a chip spotlights a flow.

The common misread is to treat this as "draw an architecture diagram." It is
not. Architecture is one form among many, and defaulting to boxes-in-a-grid for
every idea is the failure that makes a diagram forgettable. The skill's job is
to find the *form that teaches this idea best* and express it in the two
primitives. A timeline is one row of wide spans; a presentation is a sequence of
pages; a flow is components tied together by a highlight `filter`; a comparison
is two sections side by side; a mind-map is nested sections radiating from a
centre. Same machinery, different form.

## Two consumers, one vocabulary

- **The orchestrator reads the jerga to propose.** Before anyone builds, it
  converses with the user in these terms — "we split it into two sections, these
  components, this status/variant, laid out in these columns and spans" — and
  lands a decomposition the user agreed to. It proposes structure; it does not
  author YAML.
- **The agent (developer) builds with the machinery.** It takes the agreed
  decomposition, writes the YAML dialect, runs the build, and confirms the
  render. The field schema and engine behavior it needs are in `reference.md`.

Be **proactive and pedagogical**: the vocabulary encodes capabilities the user
may not know exist. When a shape fits, suggest it — "these can sit two-up with
`columns: 2`", "we can trace that path across boxes with a `filter` chip", "that
band can span the full width", "a separator can divide those groups".

## The vocabulary (the jerga)

The whole model is **two terms**. Full definitions and enums are in
`GLOSSARY.md` — do not restate them here.

| Term | What it is, in one line |
|------|-------------------------|
| `section` | a recursive grid: `columns` + `span` + `children`; its children auto-flow and wrap down; a child may itself be a section (nesting) |
| `component` | a leaf in a grid cell, chosen by `type`: `box` (status/title/description/detail) · `separator` (a divider line) · `rail` (a swimlane label) |
| `cell` | the base unit: every leaf is one fixed `--cell-w × --cell-h` (232×130) cell; cells never resize, only merge and cascade |
| `columns` | how many columns THIS section's grid has (default 2) — the count that cascades 3→2→1 as width tightens |
| `span` | an Excel-style **merge**: occupy N of the parent's columns (default 1); `span == columns` = a full-width **band** that takes its own row; same at every level |
| `band` / `inline` | a band (`span == columns`) takes its own full row and spans the block; an inline section (`span: 1`) sits side by side with its neighbours |
| `status` / `variant` | the kicker badge word / the colour-role of a box or section (see GLOSSARY enums) |
| `filter` | a chip that spotlights every component declaring its key — how a flow is traced |
| `version` | optional deck version, shown in the header |

**The root/canvas is itself the invisible base section.** `page.columns` is the
root grid width and `page.sections` are its children — the page is section depth
0, with no frame of its own. Everything else is the same section, nested.

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
positioning a *known operation, not trial-and-error*. Internalize it — it is the
difference between recalculating a diagram with intent and nudging values until
it looks right.

- **Every leaf is one fixed cell** — `--cell-w × --cell-h = 232 × 130px`. Cells
  never resize. A title plus up to 3 clamped description lines always fits the
  cell height; longer copy lives in the click-through `detail` panel. A cell
  grows only **horizontally, by merge**.
- **A section declares its own `columns`** and its children flow left→right,
  wrapping down. A leaf section is always an integer number of cells wide.
- **`span` is an Excel merge.** `span: M` occupies M columns; `span == columns`
  makes a child a full-width **band** that takes its own row. In a leaf grid a
  merged child spans the whole row (so it never overflows on collapse).
- **The width math is exact** (gap 8px, zone padding 16px/side):
  `merge of M cells = M×232 + (M−1)×8`; `C-column section = C×232 + (C−1)×8 + 32`.
  A `columns:3` section is 760px wide. You can compute the layout before you
  render it.
- **Collapse cascades 3→2→1** as width tightens (2-column "two-table"
  intermediate, 1-column single-stack endpoint); the whole block stays centered.
  Cells never change size — only the column count does — so nothing scrolls
  sideways.

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

## The method: idea → canvas (the brain)

This is the core of the skill. The **order is load-bearing**: understand the
input and choose the *form* BEFORE you synthesize components. Synthesizing blind
— reaching for boxes-in-a-grid before you know what the idea is or which shape
teaches it — is the single failure that makes a diagram unusable. Follow the
thought-order:

1. **Understand the input first.** What is the idea or document, and what is its
   *intent*? Read what you were given — a prose idea, a markdown doc, a spec, a
   conversation — and name the entities, how they group, and what the reader is
   meant to walk away understanding. You cannot choose a form for an idea you
   have not yet grasped.

2. **Decide the approach by input type.** Two cases:
   - **A specific, structured document** (a spec, a system description, an
     itemized doc) already carries its own structure — mirror it: its sections
     become sections, its items become components. The decomposition is largely
     given; your job is faithful translation.
   - **An open idea** ("show how our onboarding works", "a roadmap for the
     year") has no given structure. *Explore which pedagogical FORM fits before
     drawing anything.* Do **not** default to squares-in-a-grid. Consider a
     timeline (one row of wide spans, left→right), a presentation (a page per
     beat), a flow (boxes tied by a `filter`), a comparison (two sections side
     by side), a mind-map (nested sections radiating from a centre), a planner
     (a grid of idea cards). Be **creative AND effective**: pick the form that
     makes the idea *click* fastest, not the one that is easiest to type.

3. **Synthesize by RECALCULATING the grid.** Only now, with the form chosen,
   decompose into the spreadsheet model. For each piece translate the intent
   into concrete `columns` / `span` / `order` (see "The spreadsheet model"
   above) and **recalculate the whole page** — think in cells: which sections
   are inline (side by side) vs bands (own row), how they pack and compact, and
   where the collapse cascade lands. A full-width band for the spine, a nested
   section for a group-within-a-group, a `separator` to divide phases, a `rail`
   to label a lane, a `status`/`variant` to carry meaning by colour, a `filter`
   to make a path traceable. Every choice earns its place by teaching something
   AND by adding up in the grid.

4. **Discuss with the user.** Be **direct**, speak the jerga ("I made onboarding
   a 4-column timeline; step 3 is a full-width band because it's the pivot"),
   and keep adjustments **concise**. Iterate: propose, hear the correction,
   adjust one thing at a time. The cheapest place to get the shape right is the
   conversation, before and between builds.

5. **On every new idea or change, RECALCULATE — don't nudge.** When the user
   proposes a placement ("put this at the base", "make these a band", "this is a
   3-column section"), do not tweak values blindly: name the exact dials it
   touches (`columns`, `span`, `order`), reason about how the row packs and where
   it collapses, and show the before/after of the changed section so the user
   sees the edit rather than re-reading the whole deck. Positioning is a known
   operation — treat it like recomputing a spreadsheet.

## The build → validate loop

Editing the data is the **fast path**; the diagram is decided in the YAML, not
in the pixels. But a change is **not done until the layout guardrail passes** —
the hard lesson is that trusting a metric that measures the wrong thing fails;
what works is asserting the real geometry and looking.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data (`npm run build`, which runs
   `engine/build-data.mjs` → `data/data.generated.js`). Re-run after every YAML
   change. Explain the command in one plain sentence before running it.
3. **Validate — mandatory, the gate (T1).** Run `npm run validate` (which runs
   `tools/validate-layout.cjs`; it re-runs the build itself). It renders every
   page in headless Chromium at five widths, five reloads each, and asserts the
   layout invariants (**D**eterminism, scrollbar-**R**obust, capture-not-**T**runcated,
   **U**niform cells, description **C**lamp, no h-**O**verflow, collapse cascade
   **F** 3→2→1, inline-fit / band-**S**pans-block, centered **B**lock, **H**eader
   within section) against the real rendered geometry, exiting non-zero on any
   failure. **Assert the invariants passed before you declare done.** Each new
   layout requirement should become a new invariant. Screenshots go to a **system
   temp dir, never into the project**. Full detail + the invariant table in
   `reference.md`.
4. **Look (optional, complementary).** Load `Skill('visual-verify')` and read the
   full-page PNGs across widths and both themes for what the invariants don't
   name (contrast, a semantically-wrong wrap). `npm run verify` is the lighter
   collision-only QA.
5. **Loop on any FAIL** — the failing invariant names the zone and the measured
   value; fix the YAML/CSS, rebuild, re-validate. Never declare done on red.

## Feasibility, transparency, capability

- **Validate feasibility first, step by step** — read what the environment
  offers and reason one thing at a time (*"can I run the build? the validator?
  how far can I get?"*) **before** investing in a full synthesis. Order:
  **feasibility → understand → choose form → synthesize → discuss → build →
  validate.**
- **Explain before you execute** — before running any script, say in one plain
  sentence what it does and which file to open to inspect it first.
- **Degrade gracefully** — with only a browser you can *view* (the committed
  `data.generated.js` renders with zero tooling); Node adds *rebuild*;
  Playwright adds the *layout guardrail* (`npm run validate`). When Playwright is
  present the guardrail is not optional — a layout change is not done until it is
  green. The engine carries **no baked-in data** — every domain string lives in
  `data/`, which keeps a scaffold generic and leak-free. Detail in `reference.md`.

## The authoring modes

Each mode's step-by-step is in `reference.md`. Before scaffolding, **confirm the
destination — never assume a path.**

1. **New repo** — scaffold the engine + a seed document.
2. **Add the engine to an existing repo** — drop the engine layer into a subfolder.
3. **New page** — add a page YAML and register it in the manifest.
4. **New section** — add a section to a parent's `children`; nest by giving it
   its own section children.
5. **Add/edit components** — populate a section with boxes, separators, rails.

## Versioning

The deck has an optional `version` (`document.version`). Bump it on a
significant change — a new page, a reworked layout, a section added or removed —
so a viewer glancing at the header can tell which cut they are looking at. If a
pipeline injects the version at build/deploy time, let the pipeline own it. It
renders after the subtitle; omit the field and nothing shows.

**Deploy: don't let a release serve a stale asset pair.** `index.html`,
`engine/engine.js`, and `data/data.generated.js` are a coupled contract that
changes together on every release; a browser that caches one independently of
the others can pair stale JS/data with fresh HTML. `assets/index.html` already
carries a `?v={{DIAGRAM_DECK_VERSION}}` cache-busting placeholder on both
coupled `<script>` tags — when scaffolding into a target repo, wire that
placeholder up in whatever serves the deck (substitute it with the real
version at deploy time) and add `Cache-Control: no-cache, must-revalidate` on
the coupled paths as a second layer. This is a deploy-layer concern the seed
cannot own (it ships with no server) — see `assets/README.md` → "Deploy:
cache-busting & no-cache" for the full guidance and a worked example.

## Where the rest lives

- `GLOSSARY.md` — the canonical dialect terms + the `status` and `variant`
  enums; the shared vocabulary both consumers speak.
- `reference.md` — field-by-field schema, engine behaviors, the authoring modes,
  and the build/verify loop in detail.
- `assets/` — the vendored portable engine, ready to scaffold into any repo:
  `index.html` (the uniform-cell CSS model), `engine/engine.js`,
  `engine/build-data.mjs`, `package.json`, `tools/validate-layout.cjs` (the
  layout guardrail), `tools/verify.mjs`, and a domain-free seed `data/` that
  showcases two inline sections side by side, a base band, nesting, a separator,
  and a rail. See `assets/README.md`.
