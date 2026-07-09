# Diagram Builder — reference

Deep mechanics for authoring a diagram deck on the recursive-section model: the
field-by-field schema, the engine behaviors that surprise you, the authoring
modes, and the build → verify loop. For the vocabulary and enums, see
`GLOSSARY.md`; for the thinking method and the understanding-vs-authoring
framing, see `SKILL.md`.

The portable engine is bundled under `assets/` (see `assets/README.md`):
`index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`,
`tools/validate-layout.cjs`, `tools/verify.mjs`, and a domain-free seed `data/`.
Scaffold from there.

## The layout model in one paragraph

There are exactly **two primitives**. A **section** is a node with a `children`
array; it renders as a CSS-Grid `columns` wide, and its children auto-flow
left→right and wrap down. A **component** is a leaf (no `children`); it renders
by its `type` — `box` (default), `separator`, or `rail`. Any child, section or
component, may set `span: N` to merge across N of the parent's columns. The
page/root is itself a section (`page.columns` = its grid width, `page.sections`
= its children). Nesting is just a section whose children include other sections
— a grid of grids, as deep as the idea needs. There is no envelope primitive, no
subsection, no mosaic, no `wraps`, no `layout.row`, no layout modes.

**It is a spreadsheet of uniform cells.** Every leaf component is one fixed cell
(`--cell-w × --cell-h = 232 × 130px`, design tokens in `index.html`); a section
is always an integer number of those cells wide. Cells never resize; they merge
(`span`) horizontally and the column count cascades **3→2→1** as width tightens
(2-column "two-table" intermediate; 1-column single-stack endpoint), so nothing
scrolls sideways at the stacked tiers. The whole block is centered on the canvas.
This is what makes positioning a **known operation** — change `columns`/`span`,
`npm run build`, `npm run validate`, and the guardrail proves the grid adds up.

### Width math

Leaf grid, gap `--s-2 = 8px`, zone padding `--s-3 = 16px` per side:

```
cell / merge of M cells   = M×232 + (M−1)×8
leaf section, C columns   = C×232 + (C−1)×8 + 32   (grid + zone padding)
```

A `columns:3` section is `3×232 + 2×8 + 32 = 760px`; a `span:2` merge is
`2×232 + 8 = 472px` wide and still one `--cell-h` row tall.

### Band vs inline — how a section sits in its parent row

- **Inline** (`span: 1`) — sized to its own content (fit-content), sits side by
  side with its neighbours on the same row.
- **Band** (`span == parent columns`) — takes its own full row; consecutive
  bands stack top-to-bottom. A band spans the **block width** while the uniform
  cells inside it stay `--cell-w` and left-align — a one-cell band is a
  full-width bar with a single cell at its left (a wide band with uniform
  cells), never a ballooned cell.

### Positioning recipes (idea → columns/span/order)

- **A base band at the bottom:** give the section `span: <parent columns>` (a
  band) and place it **last** in order. It renders as a full-width row beneath
  everything else.
- **Two sections side by side on a row:** give each `span: 1` and place them
  consecutively; they pack left-to-right until the row is full.
- **A wide container over a narrow sidecar:** nest them in a parent section
  whose `columns` = (wide cols + 1); give the wide child its own `columns: N`
  and the sidecar `columns: 1`, so they sit side by side at their natural
  integer-cell widths (this is how a 3-col group sits beside a 1-col stack).
- **Full-width divider / lane label inside a band:** a `separator` or `rail`
  with `span == the section's columns` spans the whole band on its own row.

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
    ├── validate-layout.cjs  the LAYOUT GUARDRAIL — headless render + hard layout
    │                        invariants (D/R/T/U/C/O/F/S/B/H), PASS/FAIL, exit≠0
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
filters: [ … ]          # optional — the flow chips for this page
sections: [ … ]         # required, ≥ 1 — the root section's children
```

### section (any node with `children`)

```yaml
- id: system            # required, stable slug
  title: "Example system"
  subtitle: "…"         # optional
  variant: envelope     # normal | danger | safe | envelope | plain
  order: 3              # position among its siblings + collapse order
  span: 2               # occupy N of the PARENT's columns (default 1)
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
  span: 2               # occupy N of the section's columns (default 1)
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
  steps:                # optional flow text shown when the chip is clicked
    - "Chips are flows: click one to spotlight the components that declare it."
```

Highlight is **component-owns-its-tags**: the engine builds an inverse index by
walking the tree, so you never maintain a central node list. A component lights
up because IT declares the filter key; its enclosing section lights with it.

## Engine gotchas

Behaviors that bite if you author against intuition instead of the engine:

- **Cells are fixed and uniform; sections adapt to the cells, not the reverse.**
  Every leaf cell is exactly `--cell-w × --cell-h` (232 × 130) at every depth. A
  section is an integer number of cells wide; a cell never stretches to fill
  leftover width. If a section is narrower than the container, the leftover is
  accepted right-hand margin — uniformity ranks above "fill the width".
- **A cell never grows vertically.** The title clamps to 2 lines and the whole
  description to 3 lines (`.box .desc` line-clamp), so every box is `--cell-h`
  tall regardless of how many description lines the data carries. The full text
  always lives in the click-through detail panel. Put long copy in `detail`.
- **`span` merges; in a leaf grid it spans the full row.** `.sec-grid:not(.sec-compound) > .msp { grid-column: 1 / -1 }` — a merged leaf child (span > 1)
  occupies the whole row so it never overflows when columns cascade down.
  `span == columns` is the canonical band. For a multi-cell group that is
  genuinely narrower than full-width and sits beside a neighbour, nest sections
  with different `columns` in a compound grid (the "wide container over narrow
  sidecar" recipe), don't rely on a partial leaf span.
- **Two grid shapes, tagged by the engine.** A grid whose children are all
  components is a **leaf grid** (fixed `--cell-w` tracks, `--cell-h` rows). A
  grid that holds at least one nested section is a **compound grid** (a
  flex-wrap row of sections at their natural integer-cell widths). The engine
  adds `sec-c{N}` (authored column count) and `sec-compound` so the CSS steps
  each grid by its real width need.
- **Order is `order`, else list order.** DOM order (after the stable sort by
  `order`) IS the single-column collapse order at the narrowest tier, and the
  packing order on every row. To move a cell, change its `order` — there is no
  column/row coordinate to set.
- **The collapse cascade is 3→2→1, per-grid, no horizontal scroll.** A 3-col
  leaf grid steps to 2 at ≤1000px and to 1 at ≤640px; a 2-col grid steps to 1 at
  ≤640px; a `columns:1` grid stays 1. Below ~1440px compound rows fold from
  side-by-side into a centered vertical stack. At the 1-column endpoint the whole
  page is a single vertical stack. Cells stay `--cell-w` through every step.
- **A band spans the block at EVERY tier.** A band (`span == columns`) fills the
  block width from the widest tier down to the single-column endpoint — it never
  shrinks to its one cell on the first collapse. The uniform cells inside stay
  `--cell-w` and left-align.
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
   set `span` only to widen it, `filters` to tie it to a flow.
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
   npm run validate   # node tools/validate-layout.cjs (re-runs the build itself)
   ```
   It renders every page in headless Chromium at five widths (min 600 / medium
   900 / large 1200 / huge 1920 / ultra 2560), each **5× with a real reload**,
   and ASSERTS every layout invariant against the *real* rendered geometry
   (`getBoundingClientRect`), printing a per-(page,width) PASS/FAIL table and
   exiting non-zero on any failure. Full-page PNGs go to a **system temp dir**
   (`os.tmpdir()`, override with `DIAGRAM_SHOTS_DIR`), never into the project.
   **Never declare a change done until this is green.** The invariants:

   | id | invariant |
   |----|-----------|
   | **D** | determinism — 5 reloads produce byte-identical geometry (catches an F5 column/wrap flip) |
   | **R** | scrollbar-robust — shaving a scrollbar's width off the wide tiers doesn't flip the column/wrap structure |
   | **T** | full-page capture not truncated — the `-full.png` shows the whole deck |
   | **U** | uniform leaf cells — every non-span box is exactly `--cell-w × --cell-h` |
   | **C** | description clamp — no box clips (desc clamped to 3 lines) |
   | **O** | no h-overflow at the stacked tiers (min/medium/large) |
   | **F** | collapse cascade 3→2→1 — 1-column single stack at min, 2-column intermediate at medium |
   | **S** | inline sections fit their content; band sections span the block at every tier |
   | **B** | centered block at the wide tiers (leftPad ≈ rightPad) |
   | **H** | section headers/subtitles stay inside their section |

   Each new layout requirement should become a new invariant here — the trap is
   trusting a metric that measures the wrong thing; assert the real geometry.
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
