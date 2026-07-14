# Diagram Builder — reference

Deep mechanics for authoring a diagram deck on the recursive-section model: the
field-by-field schema, the engine behaviors that surprise you, the authoring
modes, and the build → verify loop. For the vocabulary and enums, see
`GLOSSARY.md`; for the thinking method and the two-layer framing (Layer 1
structure / Layer 2 construction), see `SKILL.md`.

The portable engine is bundled under `assets/` (see `assets/README.md`):
`index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`,
`tools/validate-layout.cjs`, `tools/verify.mjs`, and a domain-free seed `data/`.
Scaffold from there.

## The layout model in one paragraph

There are exactly **two primitives**. A **section** is a node with a `children`
array; it renders as a CSS-Grid `columns` wide, and its children auto-flow
left→right and wrap down. A **component** is a leaf (no `children`); it renders
by its `type` — `box` (default), `separator`, or `rail`. Merges run on **two
axes**: any child may set `span: M` to merge across M of the parent's columns,
and a leaf cell may set `rowspan: K` to merge down K rows. The page/root is
itself a section (`page.columns` = its grid width, `page.sections` = its
children). Nesting is just a section whose children include other sections — a
grid of grids, as deep as the idea needs. There is no envelope primitive, no
subsection, no mosaic, no `wraps`, no `layout.row`, no layout modes.

**It is a filled, capped grid of uniform-height cells.** A leaf grid divides its
section's width into `columns` EQUAL `fr` tracks
(`repeat(N, minmax(var(--cell-min-w),1fr))` in `index.html`): cells STRETCH to
fill, so a row spans the section edge-to-edge with no right gap and cells within
one grid are always equal width (width varies by section, never within a grid).
Rows are a fixed `--cell-h` (130px). The root plane FILLS the canvas up to a
**1280px cap**, then centers (`.sec-plane { max-width:1280px;
margin-inline:auto }`) — no sideways sprawl at any width. The gutter is one
token everywhere (`gap: var(--s-2)` = 8px). The column count cascades **…→2→1**
as width tightens (2-column "two-table" intermediate; 1-column single-stack
endpoint), and nothing scrolls sideways at the stacked tiers. Positioning is a
**known operation** — change `columns`/`span`/`rowspan`/`order`,
`npm run build`, `npm run validate`, and the guardrail proves the grid adds up.

### Fill geometry (what replaced the width math)

There is no fixed cell width anymore — `--cell-w` (232px) survives only as a
documented readability reference, and the guardrail's retired **W** invariant
(`superseded: 'U'` in `tools/validate-layout.cjs`) records the supersession.

- **Leaf grid** — `repeat(columns, minmax(var(--cell-min-w),1fr))` tracks,
  `gap: 8px`, fixed `--cell-h: 130px` rows.
- **Readable minimum** — `--cell-min-w: 120px` is a hard floor on every leaf
  track (kept in sync with `MIN_LEGIBLE` in the validator; the **M** invariant
  asserts it on the real render). Because the floor survives intrinsic sizing,
  a compound parent WRAPS/STACKS its sections when it cannot give each child
  the floor — **columns collapse before a cell degrades to illegible text**.
- **Compound grid** — a flex-wrap row whose children `flex:1 1 0` GROW EQUALLY:
  N sections on a row are N equal-width slices, `align-items:stretch` makes
  them equal height — each row a clean filled rectangle.
- **Fill-to-cap** — `.sec-plane` is `width:100%; max-width:1280px;
  margin-inline:auto`: at ≤1280 it fills edge-to-edge; above, the surplus
  becomes equal side margins (**B** asserts centering at the wide tiers).
- **Grow-with-content** — a leaf grid's effective column count is clamped to
  what its children can actually fill (its single-cell count, or the widest
  band's span), so an over-authored `columns` never reserves a dead track
  (**E** asserts it; see `buildGrid` in `engine/engine.js`).

### Band vs inline — how a section sits in its parent row

- **Inline** (`span: 1`) — occupies one column of the parent row and stretches
  to fill it; sections sharing a row are equal-width, equal-height slices.
- **Band** (`span == parent columns`) — takes its own full row; consecutive
  bands stack top-to-bottom. A band spans the **block width** and its content
  FILLS it edge-to-edge — the inner cells stretch, leaving only the zone
  padding at each side (the **Y** invariant fails a band with a dead margin).

### Positioning recipes (idea → columns/span/order)

- **A base band at the bottom:** give the section `span: <parent columns>` (a
  band) and place it **last** in order. It renders as a full-width row beneath
  everything else.
- **Two sections side by side on a row:** give each `span: 1` and place them
  consecutively; they render as equal-width, equal-height halves of the row.
- **A cell wider than one column but NOT the whole row:** `span: M` with
  `1 < M < columns` — a real PARTIAL merge that occupies exactly M of the N
  tracks (Excel-style) and keeps its proportion as the grid collapses.
- **A cell whose height encodes magnitude / a label spanning rows:**
  `rowspan: K` — the cell grows to K rows tall (a cell-graph bar, a lane cell);
  its column position is untouched.
- **Full-width divider / lane label inside a band:** a `separator` or `rail`
  with `span == the section's columns` spans the whole band on its own row; a
  vertical rail with `rowspan` labels a lane down several rows.
- **"Make this a 3-column section":** set the section's own `columns: 3`.
- **"This whole thing is one band / a full row":** give it
  `span == parent columns`.
- **"Move this above/below that":** change its `order` — there is no row/column
  coordinate to set.

## Repository layout

```
<repo>/
├── index.html            entry + template (design-system CSS inline, help HUD)
├── engine/
│   ├── engine.js         render engine — knows only the dialect (@version 2.0.0)
│   └── build-data.mjs    build step: YAML → data/data.generated.js
├── data/
│   ├── document.yaml     manifest: title/subtitle/version + which pages, in order
│   ├── pages/            one YAML per page
│   └── data.generated.js build output (committed; `window.__DOC__ = {...}`)
└── tools/
    ├── validate-layout.cjs  the LAYOUT GUARDRAIL — headless render + the
    │                        FORM-SCOPED invariant table (INTEGRITY D/R/T/C/O/F/S/B/H/X
    │                        · DESIGN U/E/P/L/M/Y · advisory V · retired W),
    │                        PASS/FAIL, exit≠0; pure-read (build first)
    └── verify.mjs           lightweight render QA (collision assertions + shots)
```

The engine layer (`engine/` + `index.html`) is generic and knows nothing about
the diagram's domain; every domain string lives in `data/`. `js-yaml` (build)
and `playwright` (QA) are devDependencies — the shipped artifact has zero
runtime dependencies.

## The manifest (`data/document.yaml`)

```yaml
title: "Deck title"          # required
subtitle: "…"                # optional
version: "0.1.0"             # optional — free-form (semver recommended)
pages:
  - id: overview             # required — must match page.id in the file
    name: "Overview"         # required — visible label (rename without breaking refs)
    order: 1                 # required — decknav position
    visible: true            # required — false omits from build without deleting
    file: pages/overview.yaml   # required — path relative to data/
```

The manifest is the single source of **which** pages exist, in what order, and
whether they show. `name` / `order` / `visible` live **only** here — the page
file must not repeat them. `page.id` lives in both (a cross-reference the build
validates). The build discards `visible: false` pages, sorts the rest by
`order`, then merges each file.

`version` is a plain passthrough: `build-data.mjs` copies `manifest.version`
onto `window.__DOC__.version` with no default, and `engine.js` renders it in the
header (`if (barVer && doc.version)`) after the subtitle. Omit it and the `.ver`
node stays empty; `:empty` collapses it in index.html, so a deck with no
`version` degrades with zero visible change.

## The page file (`data/pages/<id>.yaml`)

```yaml
id: overview            # required — matches the manifest entry
layout: grid            # engine selector — only `grid` is supported
columns: 2              # ROOT grid width (default 2) — the page is a section
form: dashboard         # optional — scopes the guardrail's invariants:
                        # dashboard (default) | timeline | flow | comparison |
                        # mindmap | planner (see the invariant table below)
filters: [ … ]          # optional — the relation chips for this page
sections: [ … ]         # required, ≥ 1 — the root section's children
```

### section (any node with `children`)

```yaml
- id: system            # required, stable slug
  title: "Example system"
  subtitle: "…"         # optional
  variant: envelope     # normal | danger | safe | envelope | plain
  order: 3              # position among its siblings + collapse order
  span: 2               # occupy M of the PARENT's columns (default 1)
  rowspan: 1            # accepted by the schema; the vertical merge renders on
                        # cells inside LEAF grids (see the rowspan gotcha)
  columns: 2            # this section's OWN grid width (default 2)
  children: [ … ]       # sections and/or components, mixed freely
```

A child of `children` is a **section** if it has its own `children`, otherwise a
**component**. Mix them freely in one list.

### component — box (default `type`)

```yaml
- id: api               # required, STABLE slug (data-k / edit-mode key)
  order: 1              # explicit position; falls back to list order
  status: INTERNAL      # kicker badge — see GLOSSARY enum
  title: "API"
  description:          # string, or a list where each item is a line
    - "handles requests from the web app"
  detail: "Long <b>HTML-allowed</b> text for the click panel."  # falls back to description
  note: "⚠ …"           # optional warning note, shown separately
  variant: normal       # normal | crit | warn | ok | strong | ext | store
  variant_extra: [ext]  # optional second class, composed (e.g. box ext)
  span: 2               # occupy M of the section's columns (default 1);
                        # 1 < M < columns is a real PARTIAL merge
  rowspan: 2            # occupy K rows (default 1) — a vertical merge, K× the
                        # cell height (height as magnitude)
  filters: [flow]       # keys of the filters this component belongs to
```

### component — separator (`type: separator`)

```yaml
- id: sep-1
  type: separator
  span: 2               # usually a full-width band
  orientation: horizontal   # horizontal (default) | vertical
  style: dotted             # solid (default) | dotted
  text: "An example system" # optional inline label centered on the line
```

A thin divider LINE, not a card. Horizontal = a rule across its cell(s);
vertical = a rule down its cell. With `text`, the label sits centered on the
line. Not clickable, no detail.

### component — rail (`type: rail`)

```yaml
- id: lane
  type: rail
  title: "CI/CD"
  orientation: vertical   # horizontal (default) | vertical (rotates the text)
  span: 1
```

A title-only swimlane LABEL banner (styled like a box but carrying only a
title). `orientation: vertical` rotates it for labeling a vertical lane. Not
clickable.

### filter

```yaml
- key: flow             # slug referenced by component.filters and the chip
  label: "Example flow"
  steps:                # optional explanation shown when the chip is clicked
    - "Chips express relations: click one to spotlight the components that share it."
```

A filter chip expresses a **RELATION**: it groups every component that
declares its `key` — a shared thing that can be a directional **FLOW** (the
substitute for an arrow, since a grid cannot draw edges — use `order`/position
so the flow reads directionally) OR a cross-cutting **CONCEPT / status /
theme** (a chip per plan in a planner; a chip for "everything exposed"
grouping by status in an architecture deck). Lighting the chip reveals that
relation's membership across the whole canvas.

Highlight is **component-owns-its-tags**: the engine builds an inverse index by
walking the tree, so you never maintain a central node list. A component lights
up because IT declares the filter key; its enclosing section lights with it.

## Per-form seed skeletons (idea → form)

Every page declares a `form` that scopes the guardrail (`dashboard` default ·
`timeline` · `flow` · `comparison` · `mindmap` · `planner`). These are tight
copyable skeletons — the minimum shape each form wants, not full decks. Pick the
form that teaches the idea, paste, then fill the payload.

**dashboard** — a grid of peer zones (the default form).

```yaml
form: dashboard
columns: 2
sections:
  - { id: zone-a, title: "Zone A", columns: 2, children: [ {id: a1, title: "…"}, {id: a2, title: "…"} ] }
  - { id: zone-b, title: "Zone B", columns: 2, children: [ {id: b1, title: "…"}, {id: b2, title: "…"} ] }
```

**timeline** — one long row of wide spans; V does not apply.

```yaml
form: timeline
columns: 4
sections:
  - id: line
    columns: 4
    children:
      - { id: t1, title: "Phase 1" }
      - { id: t2, title: "Phase 2" }
      - { id: t3, title: "Phase 3" }
      - { id: t4, title: "Phase 4" }
```

**flow** — components tied by a highlight `filter`; `order` reads directionally.

```yaml
form: flow
columns: 3
filters:
  - { key: path, label: "Main flow", steps: ["A → B → C"] }
sections:
  - id: pipeline
    columns: 3
    children:
      - { id: a, title: "A", order: 1, filters: [path] }
      - { id: b, title: "B", order: 2, filters: [path] }
      - { id: c, title: "C", order: 3, filters: [path] }
```

**comparison** — two inline sections side by side (span 1 each).

```yaml
form: comparison
columns: 2
sections:
  - { id: left,  title: "Option A", span: 1, columns: 1, children: [ {id: la, title: "…"} ] }
  - { id: right, title: "Option B", span: 1, columns: 1, children: [ {id: rb, title: "…"} ] }
```

**mindmap** — a central band with symmetric nested sections around it (the
engine is a GRID, not radial — do not present a radial shape).

```yaml
form: mindmap
columns: 2
sections:
  - { id: core, title: "Central idea", span: 2, columns: 1, children: [ {id: c0, title: "…"} ] }
  - { id: branch-l, title: "Branch L", span: 1, columns: 1, children: [ {id: bl, title: "…"} ] }
  - { id: branch-r, title: "Branch R", span: 1, columns: 1, children: [ {id: br, title: "…"} ] }
```

**planner** — a grid of idea cards, optionally one `filter` chip per plan.

```yaml
form: planner
columns: 3
filters:
  - { key: plan-1, label: "Plan 1" }
sections:
  - id: board
    columns: 3
    children:
      - { id: card-1, status: TODO, title: "Card 1", filters: [plan-1] }
      - { id: card-2, status: DOING, title: "Card 2", filters: [plan-1] }
      - { id: card-3, status: DONE, title: "Card 3" }
```

## The strict authoring schema

The build (`engine/build-data.mjs`) is the single gate every YAML edit passes
through, so it is where a typo or an invented field is caught — **loudly** —
instead of being silently dropped by the engine at render time. Each node kind
has a WHITELIST of exactly the fields the engine consumes (`PAGE_FIELDS`,
`SECTION_FIELDS`, `COMPONENT_FIELDS` in `build-data.mjs`); any key outside it
is a hard build error that names the page, the node, and the offending key,
and suggests the intended field on a near-miss (`colummns` → *did you mean
"columns"?*). Do not invent fields: authoring an unknown key fails the build.

## Engine gotchas

Behaviors that bite if you author against intuition instead of the engine:

- **Cells fill; every section is a filled rectangle.** A leaf grid's tracks are
  EQUAL `fr` shares of the section width — cells stretch, so width varies by
  section but is always equal within a grid, and every row reaches the right
  edge (the **U/L/E** invariants assert this). Rows stay a fixed `--cell-h`
  (130px) so cells are uniform in height.
- **A cell never grows vertically by content.** The title clamps to 2 lines and
  the whole description to 3 lines (`.box .desc` line-clamp), so every box is
  `--cell-h` tall regardless of how many description lines the data carries; a
  cell grows in height only by whole rows, via `rowspan`. The full text always
  lives in the click-through detail panel. Put long copy in `detail`.
- **`span` is a real partial merge.** `span == columns` is the full-width band
  (`.msp`, `grid-column: 1/-1`, its own row). `1 < span < columns` is `.mspan`:
  it occupies EXACTLY M of the N tracks (`grid-column: span var(--span)`). On
  collapse it keeps its PROPORTION — at the 2-track tier it becomes
  `--span2 = round(M/N·2)` (clamped [1,2], emitted by the engine) and only at
  the 1-column endpoint does it become a full band.
- **`rowspan` is the vertical merge.** `rowspan: K` (`.mrsp`,
  `grid-row: span var(--rowspan)`) makes a leaf cell K rows tall — the base for
  a cell-graph where height encodes magnitude. The column position is untouched
  by the horizontal cascade. The validator excludes the taller cell from the
  uniform-height check (**U**) and exempts the rows it touches from the orphan
  and edge-fill checks (**P**/**L**) — a tapered bar-chart row is by design.
- **Two grid shapes, tagged by the engine.** A grid whose children are all
  components is a **leaf grid** (equal `fr` tracks, fixed `--cell-h` rows). A
  grid that holds at least one nested section is a **compound grid** — a
  flex-wrap row whose children `flex:1 1 0` GROW EQUALLY into equal-width,
  equal-height slices. The engine adds `sec-c{N}` (effective column count) and
  `sec-compound` so the CSS steps each grid by its real width need.
- **Order is `order`, else list order.** DOM order (after the stable sort by
  `order`) IS the single-column collapse order at the narrowest tier, and the
  packing order on every row. To move a cell, change its `order` — there is no
  column/row coordinate to set.
- **The collapse cascade is …→2→1, per-grid, no horizontal scroll.** A 3-, 4-,
  or 5-column leaf grid steps to 2 at ≤1000px and to 1 at ≤640px; a 2-col grid
  steps to 1 at ≤640px; a `columns:1` grid stays 1. Below 1440px compound rows
  fold from side-by-side into a full-width vertical stack. At the 1-column
  endpoint the whole page is a single vertical stack. Cells re-divide the width
  at each tier (equal `fr`); partial spans re-proportion via `--span2`.
- **A band spans the block at EVERY tier — and its content fills it.** A band
  (`span == columns`) fills the block width from the widest tier down to the
  single-column endpoint — it never shrinks to its one cell on the first
  collapse — and its inner cells stretch edge-to-edge, leaving only the zone
  padding at each side (**S** and **Y** assert this).
- **Nesting is free and has no depth limit.** A section can hold sections which
  hold sections. Each level runs the same `buildGrid`; each nested section draws
  its own frame (per its `variant`). Use `variant: plain` for a frameless
  structural wrapper and `variant: envelope` for a borderless dashed container.
- **`separator` and `rail` are structural leaves.** They occupy a grid cell and
  honor `span` like any component, but carry no detail and are not clickable.
- **`data.generated.js` is generated and committed.** A plain
  `window.__DOC__ = {…}` assignment loaded by a normal `<script src>`, so the
  deck works under `file://` with zero fetch/CORS. Never hand-edit it; regenerate
  after any YAML change.
- **Only `layout: grid` renders.** Any other `page.layout` is skipped with a
  console warning (the deck degrades instead of throwing).

## The authoring modes

**Before scaffolding, confirm the destination.** For a new repo, adding to an
existing repo, or a new page, *ask the user where the project (or file) should
live* and write there — never assume a path.

### Mode 1 — New repo

1. Copy the portable engine layer from `assets/` (`index.html`, `engine/`,
   `package.json`, `tools/verify.mjs`, and the seed `data/`) into the new repo.
2. Set `data/document.yaml` `title`/`subtitle` and one page entry.
3. Write `data/pages/<id>.yaml` with `id`, `layout: grid`, `columns`, and a
   first section.
4. Run the build → verify loop below.

### Mode 2 — Add the engine to an existing repo

1. Drop the engine layer into a subdirectory (e.g. `diagram/`) so it stays
   self-contained; its only footprint is `engine/`, `index.html`, and the two
   devDependencies.
2. Create `data/document.yaml` + a first page as in Mode 1.
3. Confirm nothing in the host repo already claims `index.html`; if so, nest the
   whole diagram under its own folder.

### Mode 3 — New page

1. Add a `data/pages/<id>.yaml` with a unique `id`.
2. Register it in `data/document.yaml` with `name`, `order`, `visible: true`,
   and `file`. The `id` **must** match, or the build throws.
3. Build → verify.

### Mode 4 — New section

1. Add a section to a parent's `children` (the page's `sections`, or a deeper
   section's `children`), with a stable `id`, a `variant`, and its own
   `columns`.
2. Set `span` to widen it (up to the parent's columns); a full-width band is
   `span == parent columns`.
3. To nest, give the section its own `children` that are themselves sections.
4. Build → verify.

### Mode 5 — Add / edit components

1. Add component entries to a section's `children`. Default `type` is `box`; set
   `type: separator` or `type: rail` for structural leaves.
2. Give each box a stable `id`, `status`, `title`, `description`, and `variant`;
   set `span` only to widen it, `filters` to tie it to a relation.
3. Build → verify.

## The build → validate loop

A diagram is not done until the data is right AND the layout guardrail passes.
Editing the data is the fast path; the model is decided in the YAML, not the
pixels.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data:
   ```
   npm run build      # node engine/build-data.mjs → data/data.generated.js
   ```
   This is a local file write (reads the manifest, skips `visible: false`, sorts
   by `order`, merges each page). Re-run after every YAML change.
3. **Validate — mandatory (the engine's own verify).**
   ```
   npm run validate   # node tools/validate-layout.cjs — PURE-READ, build first
   ```
   Validate is **DECOUPLED from build**: it renders and asserts the EXISTING
   `data/data.generated.js` and never regenerates it — run `npm run build`
   first. It is genuinely read-only: no child build process, no project file
   writes. It renders every page in headless Chromium at five widths (min 600 /
   medium 900 / large 1200 / huge 1920 / ultra 2560), each **5× with a real
   reload**, and ASSERTS every applicable invariant against the *real* rendered
   geometry (`getBoundingClientRect`), printing a per-(page,width) PASS/FAIL
   table and exiting non-zero on any `dura` failure. Full-page PNGs go to a
   **system temp dir** (`os.tmpdir()`, override with `DIAGRAM_SHOTS_DIR`),
   never into the project. **Never declare a change done until this is green.**

   The invariants are a **FORM-SCOPED flat table** (`INVARIANTS` in
   `tools/validate-layout.cjs`): the page declares its `form` (default
   `dashboard`); each invariant declares the forms it applies to, its class
   (integrity vs design), its severity (`dura` fails the build; `consejo` only
   advises), and an optional retirement clause (`superseded` — a retired row is
   listed, never evaluated). The scopes: **all** = every form; **gridded** =
   every form but `timeline`; **grid-dense** = `dashboard` / `comparison` /
   `planner`.

   | id | family | forms | sev | invariant |
   |----|--------|-------|-----|-----------|
   | **D** | integrity | all | dura | determinism — 5 reloads, byte-identical geometry |
   | **R** | integrity | all | dura | scrollbar-robust — −17px doesn't flip the column/wrap structure (wide tiers) |
   | **T** | integrity | all | dura | full-page capture not truncated |
   | **C** | integrity | all | dura | description clamp — no box clips its content |
   | **O** | integrity | all | dura | no h-overflow at the stacked tiers (tolerated only at wide) |
   | **F** | integrity | all | dura | collapse cascade — 1-column single stack at min, 2-column intermediate at medium |
   | **S** | integrity | all | dura | inline fit / band spans the block at every tier |
   | **B** | integrity | all | dura | centered block at the wide tiers (leftPad ≈ rightPad) |
   | **H** | integrity | all | dura | section headers/subtitles stay inside their section |
   | **X** | integrity | all | dura | no sibling-section collision — no two sibling sections overlap (catches a column-stack overflowing onto its neighbour) |
   | **G** | integrity | all | dura | no compound-leaf balloon / no stacked-section content overflow — a compound-row leaf never ballons past its content size, and a stacked section keeps its content height (the compound-row leaf balloon / sec-c1 overflow guard) |
   | **U** | design | all | dura | cells equal width per grid + uniform `--cell-h` height (row-span cells exempt from the height set) |
   | **L** | design | gridded | dura | cells fill the grid edge-to-edge, no right/left gap (≥1200px; rows a row-span touches exempt) |
   | **E** | design | gridded | dura | no empty grid column — every declared track is filled |
   | **P** | design | grid-dense | dura | no orphan cell — no lone cell beside grouped sibling rows (>1000px; row-span rows exempt) |
   | **M** | design | gridded | dura | cells legible — no 1-column cell below `MIN_LEGIBLE` 120px (collapse columns first) |
   | **Y** | design | all | dura | band content fills the band — no dead margin (≥1200px) |
   | **Q** | design | all | dura | compound section widths follow authored span — a compound row's sections are proportional to their authored `span` weight, not stretched or shrunk by an inherited parent band (≥1200px) |
   | **V** | design | grid-dense | **consejo** | horizontal composition — the deck earns its canvas (ultra tier; advises, never fails) |
   | **W** | design | — | retired | fixed 232px cell width — `superseded: 'U'`; listed in the report, never evaluated |

   Each new layout requirement should become a new row here — the RATCHET rule
   (see `SKILL.md`, "The truth discipline"): the trap is trusting a metric that
   measures the wrong thing; assert the real geometry.
4. **Spot-check by looking (optional).** Use the engine's **verify-UI**
   capability — `npm run verify` renders the deck and writes the `-full.png`
   shots across widths and both themes (or render `index.html` under `file://`).
   A pixel read catches contrast or a wrong wrap the invariants don't name; this
   is the lighter collision-only QA that complements the layout verify.
5. **Loop on any FAIL** — read the failing invariant's detail (it names the zone
   and the measured value), fix the YAML/CSS, rebuild, re-validate.

## Feasibility, transparency, capability

Two disciplines frame every run, both universal: validate feasibility before
investing, and be transparent about what you run.

### Feasibility first (validate step by step, early)

Detect what the environment offers and reason about it **one thing at a time**,
early — *"can I run this script? how far can I get?"* — and say it **before**
investing in a full sketch or build. The order is **feasibility → understand →
choose form → synthesize → discuss → build**, not the reverse.

| Goal | Needs | Notes |
|------|-------|-------|
| **View** the diagram | A browser | `data/data.generated.js` is committed, so it renders with zero tooling, even under `file://`. |
| **Rebuild** after editing `data/` | Node + `npm install` + `npm run build` | Regenerates `data/data.generated.js` from the YAML. |
| **Validate** the layout (verify) | Playwright (+ a Chromium) | `npm run validate` renders every page at five widths, asserts the layout invariants against the real geometry, and exits non-zero on any failure. This is the mandatory check before declaring done. |
| **Verify-UI** (lighter visual QA) | Playwright (+ a Chromium) | `npm run verify` is a lighter collision-only check + screenshots to review by eye. |

The engine's verify launches the headless browser it needs — and can install
the browser/Playwright when it is missing — so running it is also how you
confirm the OS and dependencies the render requires are present.

Degradation is graceful: with only a browser you can always view the
already-generated diagram; rebuilding after edits adds Node; validation adds
Playwright. When Playwright is present, the layout verify is not optional — a
layout change is not done until `npm run validate` is green.

### Explain before you execute

Before running ANY script, say in one short, plain sentence what it does and
which file to open to inspect it first — e.g. *"I'll run `npm run build` (which
runs `engine/build-data.mjs`) to regenerate the diagram from your YAML — you can
read that script first."* The user can see what will run before it runs.

### Why the engine stays minimal and data-driven

The engine and template carry **no baked-in data** — every domain string lives
in `data/`. That is what keeps a scaffold generic and leak-free: nothing from
one diagram bleeds into the next; it only scales to new content. Keep it that
way — content in `data/`, never in the engine or template.
