# Diagram Builder — reference

Deep mechanics for authoring a diagram deck on the recursive-section model: the
field-by-field schema, the engine behaviors that surprise you, the placement and
row-track mechanics, the authoring modes, and the build → check loop. For the
vocabulary and enums, see
`GLOSSARY.md`; for the thinking method and the two-layer framing (Layer 1
structure / Layer 2 construction), see `SKILL.md`.

The portable engine is bundled under `assets/` (see `assets/README.md`):
`index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`, the
`tools/` gates (`check-layout.mjs`, `static-census.cjs`, `test-guards.mjs`,
`validate-layout.cjs`, `contrast-audit.cjs`, `verify.mjs`), and a domain-free
seed `data/`. Scaffold from there.

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
`npm run build`, `npm run check`, and the arithmetic proves the rectangle closes.

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
  becomes equal side margins (**B** asserts centering at the one wide tier the
  render gate still renders, 2560px).
- **Grow-with-content** — a leaf grid's effective column count is clamped to
  what its children can actually fill (its single-cell count, or the widest
  band's span), so an over-authored `columns` never reserves a dead track. The
  clamp lives in `buildGrid` (`engine/engine.js`) and is mirrored by
  `effectiveCols` in `tools/check-layout.mjs`; **TRACK** guards it (the retired
  render invariant **E** asserted the same thing on pixels).

### Band vs inline — how a section sits in its parent row

- **Inline** (`span: 1`) — occupies one column of the parent row and stretches
  to fill it; sections sharing a row are equal-width, equal-height slices.
- **Band** (`span == parent columns`) — takes its own full row; consecutive
  bands stack top-to-bottom. A band spans the **block width** and its content
  FILLS it edge-to-edge — the inner cells stretch, leaving only the zone
  padding at each side (the **Y** invariant fails a band with a dead margin).
- **Being a band is TIER-RELATIVE, and that is why there are two questions.**
  `span == columns` is the authored declaration; owning your row is a property
  of the tier you are at. See "The placement model" below for the pair of
  functions that keep them apart.

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
    ├── check-layout.mjs     the MANDATORY STATIC GATE — arithmetic, no browser,
    │                        js-yaml only. RECT/HOLE/TRACK/ROW/LANE/BAND/TIER/
    │                        CHIP/ORDER/CSS + the CENSUS pre-flight (npm run check)
    ├── static-census.cjs    the browser-free foundation BOTH gates import:
    │                        loadAuthoredDeck, staticCensus, cssBreakpoints,
    │                        BREAKPOINTS, DEFAULT_FORM, GRID_DENSE — one parse
    │                        path, so the two gates cannot disagree about the data
    ├── validate-layout.cjs  the OPTIONAL RENDER GUARDRAIL — headless render at
    │                        ONE width + the FORM-SCOPED invariant table
    │                        (INTEGRITY A/Z/D/R/T/C/O/S/B/H/X/G · GEOMETRY
    │                        U(×2)/M/N/Y/Q · advisory V · retired L/F/E/P/K/W),
    │                        PASS/FAIL, exit≠0; pure-read (build first)
    ├── test-guards.mjs      the NEGATIVE-TEST SUITE — fabricates broken decks in
    │                        os.tmpdir() and asserts each guard FAILS as claimed;
    │                        also pins the engine↔gate placement mirror (npm test)
    ├── contrast-audit.cjs   the PALETTE CONTRAST guardrail — parses the palette
    │                        token blocks out of index.html and measures every real
    │                        fg/bg pair against WCAG 2.1 (npm run contrast)
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
palette: neutral             # optional — the deck SKIN; omitted == neutral.
                             #   neutral | rose-pine | rose-pine-moon | contrast
                             # The semantic roles are identical in every palette, so
                             # this changes how the deck LOOKS, never what it MEANS.
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
  variant: neutral      # THE COLOUR AXIS — one value: neutral | good | bad
  treatment: [envelope] # THE STRUCTURAL AXIS — a list: envelope | plain
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
  kicker: INTERNAL      # the small mark above the title — OPEN vocabulary, no
                        # enum gates it; any free string is accepted
  title: "API"
  description:          # string, or a list where each item is a line
    - "handles requests from the web app"
  detail: "Long <b>HTML-allowed</b> text for the click panel."  # falls back to description
  note: "⚠ …"           # optional warning note, shown separately
  variant: neutral       # THE COLOUR AXIS — one value:
                         #   neutral | good | warn | bad | accent | muted
  variant_extra: [muted] # optional SECOND colour role (same enum), for a box that
                         #   is both a kind and a state (bad + muted)
  treatment: [centered]  # THE STRUCTURAL AXIS — a list, composable:
                         #   centered | half | vertical | outside
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
  treatment: [vertical]     # omit for a horizontal rule (the default)
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
  treatment: [vertical]   # rotates the text; omit for a horizontal banner
  span: 1
```

A title-only swimlane LABEL banner (styled like a box but carrying only a
title). `treatment: [vertical]` rotates it for labeling a vertical lane — the
former `orientation` field is gone and is now rejected by the strict schema. Not
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
so the flow reads directionally) OR a cross-cutting **CONCEPT / state /
theme** (a chip per plan in a planner; a chip for "everything exposed" in an
architecture deck). Lighting the chip reveals that relation's membership across
the whole canvas. A chip needs at least TWO members: **CHIP** fails a one-member
chip, because an active chip dims everything it does not name.

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
      - { id: card-1, kicker: TODO, title: "Card 1", filters: [plan-1] }
      - { id: card-2, kicker: DOING, title: "Card 2", filters: [plan-1] }
      - { id: card-3, kicker: DONE, title: "Card 3" }
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

## Migrating a pre-2.1 deck (the vocabulary split)

Engine 2.1 splits the old single `variant` enum into **two orthogonal axes** —
`variant` (one semantic COLOUR role) and `treatment` (a composable list of
STRUCTURAL modifiers). This is a **clean break**: a structural value left in
`variant` is a hard build error naming the axis it belongs to, not a silent
translation. A deck vendors its own copy of the engine, so an existing deck keeps
working until it adopts a newer engine — and when it does, the build tells it
exactly what to change.

Every legacy spelling and its replacement:

| legacy | becomes | why |
|--------|---------|-----|
| `variant: plain` (section) | `treatment: [plain]` | removes frame + padding + min-height — structure, not colour |
| `variant: envelope` (section) | `treatment: [envelope]` | frame styling, no colour claim |
| `variant: ext` (component) | `treatment: [outside]` | its CSS is `border-style:dashed` and **nothing else** — zero colour. The value was ALSO renamed: `ext` is not a treatment either |
| `variant_extra: [centered]` | `treatment: [centered]` | `text-align` only; this smuggling is the evidence the axis was missing |
| `variant_extra: [ext]` | `treatment: [outside]` | same as `ext` above |
| `orientation: vertical` (rail/separator) | `treatment: [vertical]` | the same switch under a second name; a parallel field is the duplication the split removes |
| `orientation: horizontal` | **delete it** | absence of the `vertical` treatment IS horizontal |
| `status: <word>` | `kicker: <word>` | the field was renamed; `status` is now an unknown key and a hard build error. The vocabulary stayed OPEN — no enum replaced it |

**The colour ROLES were renamed too, and none of the old spellings survive.**
`crit` → `bad`, `ok` → `good`, `strong` → `accent`, `store` → `muted`;
`danger` → `bad` and `safe` → `good` on a section. Each is now an unknown
variant value and a hard build error with a near-miss hint
(`checkVariantValue`, `engine/build-data.mjs`). What did NOT change is the CSS
TOKENS the old names came from: role `bad` still paints with `--crit`, `good`
with `--olive`, `accent` with `--strong`, `muted` with `--surface2`. A role and
its token disagreeing is expected — author the ROLE, read the TOKEN only as CSS
evidence. `variant_extra: [store]` becomes `variant_extra: [muted]`, which is
the one case the rename does not collapse: it is still a legitimate SECOND
colour role and is validated against the same enum as `variant`.

Two notes on what did **not** move:

- **`variant_extra` survives, deliberately.** It is the second COLOUR role,
  validated by `checkVariantValue` against the same colour enum as `variant`. It
  was NOT renamed to `treatment` (nor deleted) because they are different axes: a
  box that is a KIND and a STATE at once (`variant: bad` +
  `variant_extra: [muted]`, exercised in `data/pages/p8-does-not-fit.yaml`) is a
  genuine second colour role that `treatment` must not absorb without
  re-conflating exactly what the split separated. Only its structural values
  (`[centered]`, `[ext]`) moved.
- **`cell` was never a field.** `GLOSSARY.md` lists it under "Layout terms" as
  "(engine behavior)" — the base grid UNIT, not something authorable (and
  distinct from `slot`, the rectangle the grid actually places, which is what
  **U** measures). So `half` belongs on `treatment`; there was no existing field
  for it to follow.

## Engine gotchas

Behaviors that bite if you author against intuition instead of the engine:

- **Cells fill; every section is a filled rectangle.** A leaf grid's tracks are
  EQUAL `fr` shares of the section width — cells stretch, so width varies by
  section but is always equal within a grid, and every row reaches the right
  edge (**U** asserts the equal widths on the render; that the row CLOSES is
  arithmetic — **RECT**/**TRACK** in `npm run check`, which superseded the
  retired render invariants **L**/**E**). Rows stay a fixed `--cell-h` so cells
  are uniform in height — except a separator-only row, below.
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
  by the horizontal cascade. `rowspan` is mutually exclusive with `half`
  (`checkTreatmentCombinations`): one grows a cell by WHOLE slots, the other
  divides ONE. The render gate excludes the taller cell from the uniform-height
  check (**U**); the static gate exempts every row the cell touches from
  **RECT**'s closure and from **ROW** — a tapered bar-chart row is by design, and
  it is reported as an `[INFO] rowspan taper` naming the taper's own area.
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
  its own frame (per its `variant`). Use `treatment: [plain]` for a frameless
  structural wrapper and `treatment: [envelope]` for a borderless dashed
  container — both are TREATMENTS; writing either into `variant` is a hard build
  error that names the axis.
- **`separator` and `rail` are structural leaves.** They occupy a grid cell and
  honor `span` like any component, but carry no detail and are not clickable. A
  row whose ONLY occupants are HORIZONTAL separators is the one row that is not
  `--cell-h` — see "The separator row" below.
- **`data.generated.js` is generated and committed.** A plain
  `window.__DOC__ = {…}` assignment loaded by a normal `<script src>`, so the
  deck works under `file://` with zero fetch/CORS. Never hand-edit it; regenerate
  after any YAML change.
- **Only `layout: grid` renders.** Any other `page.layout` is skipped with a
  console warning (the deck degrades instead of throwing).

## The deep mechanics (what the glossary only names)

### The placement model, and why it exists twice

Where a slot lands is DERIVED, never measured: `grid-auto-flow` is the default
`row`/**SPARSE**, so the placement cursor never moves backwards. An item that
does not fit in the tracks left on its row moves DOWN and abandons the rest of
that row — which is exactly how an interior hole is born, and why the hole can
be found in the data instead of in a screenshot. Three functions carry the whole
model:

| function | question | answer |
|----------|----------|--------|
| `widthAtTier(span, cols, tracks)` | how many TRACKS does this slot occupy at this tier? | `span` at the authored tier; `tracks` when `span >= cols` (a `.msp` is `grid-column:1/-1` at every tier); otherwise `--span2 = round(span/cols·tracks)` clamped `[1, tracks]` |
| `isBandAtTier(w, tracks)` | does it OWN its row here? | `w >= tracks` — the RESOLVED width, never the authored span |
| `rowOccupants(items, tracks)` | which row does each slot land on? | the sparse-flow simulation; a rowspan slot occupies every row it covers, so a row a taller cell passes through is never seen as empty |

**`isBandAtTier` and `isBandClass` are two questions, not a copy.** Band-ness is
TIER-RELATIVE because the CSS makes it so: at the 640px endpoint
`.sec-grid:not(.sec-compound) > .mspan` becomes `grid-column:1/-1`, so a partial
merge IS a band there, and a merge that fills both tracks of the 2-track tier
already spans its whole row. That is `isBandAtTier`, and it is what placement
must ask. `isBandClass(span, cols)` (`span >= cols`) is a different question —
the AUTHORED declaration, which is what makes the engine emit the `.msp` class
and therefore what the root's `:has(> .msp)` grid rule keys on. It is answered at
the authored tier only. Conflating them was a real divergence: a span-3-of-4 at
the 2-track tier is a band by tier and not by class.

**The engine and the static gate each implement this, mirrored on purpose.**
`engine/engine.js` runs it in the browser; `tools/check-layout.mjs` runs it in
Node (`place`, the coordinate form of `rowOccupants`). They are a MIRROR and not
a shared import because the engine is a plain browser script under `file://`,
where an ES module is CORS-blocked (origin `null`) — the deck's contract is that
it opens with a double click, so there is no module system to share one through.
**The mirror is not held by a comment.** `tools/test-guards.mjs` lifts the
engine's own functions out of its real source by brace-matching (`liftFromEngine`)
and asserts they agree with the gate's copy over a corpus of widths and grid
shapes; a rename makes the extraction throw, which FAILS the case rather than
skipping it. One further case feeds the comparator a deliberately wrong width
function and requires it to report the divergence — so the agreement test is
proven able to fail. Never write "keep in sync" here: change the model in the
engine and that suite tells you the gate drifted.

### The separator row (the third row-height family)

A `separator` is a leaf COMPONENT, so it occupies a whole cell and was charged a
full `--cell-h` for one pixel of ink. The fix is not to stop it being a cell —
everything visible is a merged cell — but to give its row a reduced track
height. The derivation:

1. `rowOccupants(items, tracks)` returns the occupant nodes per row.
2. `rowTrackList(items, tracks)` walks those rows and emits ONE entry each:
   `var(--sep-row-h)` when the row has occupants and EVERY one is a horizontal
   separator (`isThinRowLeaf`), `var(--cell-h)` otherwise. It returns `null` when
   no row qualifies, so a grid without a separator row gets no inline style at
   all and stays on the plain fixed-row default.
3. `applyRowTracks(grid, slots, cols)` emits that list once PER COLLAPSE TIER —
   `--row-tracks` (authored `cols`), `--row-tracks-2`, `--row-tracks-1` — skipping
   any tier that would widen the grid. The CSS consumes them as
   `grid-auto-rows: var(--row-tracks, var(--cell-h))`, switching to
   `--row-tracks-2` inside the 1000px container query and `--row-tracks-1` inside
   the 640px one (a `.sec-c1` grid keeps its base list). Each tier is computed
   independently, because placement is a function of the track count: a separator
   that shares its row with boxes at the authored width but ends up alone at 2
   tracks is thin only in that tier's list.

Two exclusions are deliberate. A **VERTICAL** separator is never thin — its ink
IS the row height, so thinning its row would shorten the drawing rather than fit
it. And a row with NO occupant at all (an interior hole) keeps `--cell-h`: a hole
is not a thin row, and `RECT`/`HOLE` own that defect.

### Half-slot pairing

`half` does not shrink a cell — it DIVIDES a slot. `buildGrid` wraps two
CONSECUTIVE half leaves (in render order) in ONE `.half-slot`, and that wrapper
is what occupies the grid cell: it keeps the full `--cell-h` and the two
components split it. The grid's row geometry is untouched, which is why the pair
leaves no half-empty cell and no hole. Pairing runs BEFORE the grow-with-content
clamp, in both the engine and `slotsOf` in the gate — counting the two halves as
two fillable cells would let an over-authored `columns` reserve a dead track.

The build enforces the pairing rules (`checkHalfPairing`, `engine/build-data.mjs`):

- **A run of consecutive halves must be EVEN.** An odd one out would fill half a
  slot and leave the rest empty — the hole the model forbids. The error names the
  unpaired component.
- **A pair must AGREE on `span`.** Two halves stacking inside one slot cannot
  resolve two different widths.
- **Title-only, and mutually exclusive with `rowspan`** — a `description` on a
  half is a build error (which is what guards **C** by construction: a ~63px box
  cannot clip what it is forbidden to carry), and `rowspan` grows a cell by whole
  slots while `half` divides one.

The consequence for the guardrail is that **U** asserts the height of the SLOT,
not of the component: half components are excluded from the component-height set
(as rowspan cells already were) and every `.half-slot` is asserted at exactly
`--cell-h`.

## The authoring modes

**Before scaffolding, confirm the destination.** For a new repo, adding to an
existing repo, or a new page, *ask the user where the project (or file) should
live* and write there — never assume a path.

### Mode 1 — New repo

1. Copy the portable engine layer from `assets/` (`index.html`, `engine/`,
   `package.json`, `tools/`, and the seed `data/`) into the new repo. Take the
   whole `tools/` directory: `check-layout.mjs` is the mandatory gate and it
   imports `static-census.cjs`.
2. Set `data/document.yaml` `title`/`subtitle` and one page entry.
3. Write `data/pages/<id>.yaml` with `id`, `layout: grid`, `columns`, and a
   first section.
4. Run the build → check loop below.

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
3. Build → check.

### Mode 4 — New section

1. Add a section to a parent's `children` (the page's `sections`, or a deeper
   section's `children`), with a stable `id`, a `variant`, and its own
   `columns`.
2. Set `span` to widen it (up to the parent's columns); a full-width band is
   `span == parent columns`.
3. To nest, give the section its own `children` that are themselves sections.
4. Build → check.

### Mode 5 — Add / edit components

1. Add component entries to a section's `children`. Default `type` is `box`; set
   `type: separator` or `type: rail` for structural leaves.
2. Give each box a stable `id`, `kicker`, `title`, `description`, and `variant`;
   set `span` only to widen it, `filters` to tie it to a relation.
3. Build → check.

## The build → check loop

A diagram is not done until the data is right AND the layout gate passes.
Editing the data is the fast path; the model is decided in the YAML, not the
pixels.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data:
   ```
   npm run build      # node engine/build-data.mjs → data/data.generated.js
   ```
   This is a local file write (reads the manifest, skips `visible: false`, sorts
   by `order`, merges each page). Re-run after every YAML change. It is also the
   strict-schema gate: an unknown key or an out-of-enum value fails here.
3. **Check — MANDATORY, arithmetic, no browser.**
   ```
   npm run check      # node tools/check-layout.mjs [deckRoot]
   ```
   This is the gate. It is arithmetic over the AUTHORED YAML, needs nothing but
   `js-yaml`, and runs in milliseconds — which matters because Gaia is installed
   in places where no browser exists, and a guardrail that needs Chromium is
   absent precisely where a deck is authored blind. **Never declare a layout
   change done until this is green.** Two structural rules keep it honest: a run
   that asserted NOTHING is RED (the `asserted === 0` gate), and the deck root is
   taken from `argv`/`DIAGRAM_DECK_ROOT` so the gate can be pointed at a broken
   fixture and be SHOWN to fail. Findings are grouped per check; `[FAIL]` exits
   non-zero, `[INFO]` is advisory and never fails.
4. **Validate — OPTIONAL REINFORCEMENT, needs a browser.**
   ```
   npm run validate   # node tools/validate-layout.cjs — PURE-READ, build first
   ```
   Validate is **DECOUPLED from build**: it renders and asserts the EXISTING
   `data/data.generated.js` and never regenerates it. It is genuinely read-only:
   no child build process, no project file writes; PNGs go to a **system temp
   dir** (`os.tmpdir()`, override with `DIAGRAM_SHOTS_DIR`). Playwright is
   resolved LAZILY (`loadChromium`): where it is absent, `main()` prints
   `SKIPPED (no browser)` and **exits 0** — so its absence is not a failure and
   never blocks a deck. It renders ONE width (2560, `WIDTHS = { ultra: 2560 }`),
   `PASSES = 3` reloads each, and asserts only what genuinely needs PIXELS.
5. **Test the guards — `npm test`.**
   ```
   npm test           # node tools/test-guards.mjs
   ```
   The negative-test suite: **nine cases** today. Each fabricates one broken deck
   in `os.tmpdir()` (never inside the repo), runs the real guard against it, and
   asserts the guard FAILS as claimed — a guard that goes quiet on a real defect
   is the silent false negative this suite exists to catch. Its cases cover RECT,
   invariant A at both layers (build-data and `runInvariants`), CHIP, a positive
   control on the intact seed, the three engine↔gate agreement cases, and the
   teeth case that proves the comparator can fail. Run it after touching a guard
   or the placement model.
6. **Spot-check by looking (optional).** `npm run verify` renders the deck and
   writes per-page screenshots across a spread of widths and BOTH themes to the
   temp dir (or render `index.html` under `file://`). A pixel read catches
   contrast or a wrong wrap the invariants do not name; this is the lighter
   collision-only QA that complements the layout gates.
7. **Loop on any FAIL** — read the failing check's detail (it names the grid, the
   tier, and the measured value), fix the YAML/CSS, rebuild, re-check.

### The static checks (`npm run check`)

The static layer asserts what is true of the DATA. Its mechanics, not just its
names:

| check | mechanic |
|-------|----------|
| **RECT** | the closure IDENTITY, per grid per tier: `Σ(spanCols × rowspanRows) === tracks × rowCount`. A hole leaves the sum SHORT BY EXACTLY ITS OWN AREA, so the defect is not merely detected but MEASURED. Where a `rowspan` taper is present the identity is restated PER ROW over the non-exempt rows only — the honest restriction, since a tapering chart row legitimately does not close. A short LAST row at a COLLAPSED tier is `[INFO]`, not a failure: that is the cascade |
| **HOLE** | the empty cells enumerated BY COORDINATE (`r2c3`) and split in two. A TRAILING hole is the tail of the last row (legitimate); an INTERIOR hole — a gap with content after it — is always a defect: a merge did not fit in the tracks left on its row and dropped down, abandoning the rest. Interior holes are asserted at EVERY tier, collapsed ones included |
| **TRACK** | a DEAD track: a column no slot ever reaches. The grow-with-content clamp is what prevents this, so TRACK guards the clamp — if it fires, the clamp and the placement have diverged |
| **ROW** | an ORPHAN row: a lone single-track cell on its own row while a sibling row holds 2+. Scoped exactly as the retired **P** was — grid-dense forms, `tracks > 1`, rowspan rows exempt |
| **LANE** | swimlanes of unequal length. A row LED by a `rail` at column 0 is a lane; two lanes in one grid that do not reach the same track are a ragged diagram, and unlike a short last row it is AUTHORED, not a cascade artefact — so it FAILS. Parallel single-column stacks of unequal depth are the advisory half (`[INFO]`): a tall block beside a short one is often a deliberate composition |
| **BAND** | (a) a declared `span` that EXCEEDS the columns it sits in — the engine clamps it and nothing looks wrong, but the declaration is unsatisfiable as written, so it fails at the door; (b) a band owns its whole row (structurally guaranteed by the placement model, so a failure means the model and the data disagree about what a band is). An effective column count below the authored one is `[INFO]`, naming any partial merge the clamp PROMOTED to a full band |
| **TIER** | the derived tracks-per-tier table, plus monotonicity: tracks may only GROW as the container grows. A violation means the breakpoint rules disagree with each other |
| **CHIP** | referential integrity BOTH ways — every declared chip has a member, every referenced key is declared — plus **ARITY**, the half the retired **K** could never see: a chip with exactly ONE member closes the join and is still broken, because a relation needs two ends and an active chip dims everything it does not name. The reset key `all` is exempt |
| **ORDER** | a duplicate EFFECTIVE order among siblings (`order ?? index+1`). The engine resolves the tie by list position, so the render is correct today and can flip under an unrelated edit that only MOVES a node in the file |
| **CSS** | the mirror itself: the breakpoints this gate computes with, against the `@container stage` queries `index.html` actually declares (`cssBreakpoints` vs `BREAKPOINTS`). Without it a stylesheet edit that moved a cut would leave every tracks-per-tier number describing a cascade the browser no longer renders — green, and wrong. A deck with no `index.html` says `[INFO] not asserted` rather than claiming a pass |
| **CENSUS** | pre-flight, and printed FIRST: `data/*.yaml` vs `data/data.generated.js`. A stale build means everything below still describes the YAML correctly while the deck someone is LOOKING at is a different one. Shared with `validate` through `tools/static-census.cjs`, so the two gates cannot disagree about what the data says |

### The render invariants (`npm run validate`)

A **FORM-SCOPED flat table** (`INVARIANTS` in `tools/validate-layout.cjs`): the
page declares its `form` (default `dashboard`); each row names the forms it
applies to, its class (`integrity` / `design` — `design` measures rendered
geometry, not visual taste), its severity (`dura` fails the build, `consejo` only
advises), the tiers it runs at (`when`), and an optional retirement clause
(`superseded`). The scopes: **all** = every form; **gridded** = every form but
`timeline`; **grid-dense** = `dashboard` / `comparison` / `planner`; **wordfit** =
`dashboard` / `flow` (the narrative forms whose cells carry a real, human-language
title).

| id | family | forms | sev | invariant |
|----|--------|-------|-----|-----------|
| **A** | integrity | — | dura | the page declares a VALID form. Synthetic and fail-closed: `runInvariants` returns this single failing row INSTEAD of an empty set, because an undeclared form matched no row and the page used to report "ALL PASS — 0 checks" with exit 0 |
| **Z** | integrity | all | dura | census — authored == rendered. Counts `.half-slot` wrappers as the slots they are |
| **D** | integrity | all | dura | determinism — `PASSES` (3) reloads, byte-identical geometry |
| **R** | integrity | all | dura | scrollbar-robust — −17px doesn't flip the column/wrap structure (wide tier) |
| **T** | integrity | all | dura | full-page capture not truncated |
| **C** | integrity | all | dura | description clamp — no box clips its content |
| **O** | integrity | all | dura | no h-overflow |
| **S** | integrity | all | dura | inline fit / band spans the block |
| **B** | integrity | all | dura | centered block (leftPad ≈ rightPad, `CENTER_TOL` 10px) |
| **H** | integrity | all | dura | section headers/subtitles stay inside their section |
| **X** | integrity | all | dura | no sibling-section collision — catches a column-stack overflowing onto its neighbour |
| **G** | integrity | all | dura | no compound-leaf balloon / no stacked-section content overflow — a compound-row leaf never balloons past its content size, and a stacked (`sec-c1`) section keeps its content height |
| **U** | design | all | dura | TWO rows: cells equal width per grid, and uniform **SLOT** height. SLOT, not component: `rowspan` makes a component a MULTIPLE of the slot and `half` a FRACTION of it, so both are excluded from the component-height set and every `.half-slot` is asserted at `--cell-h` directly |
| **M** | design | gridded | dura | cells legible — no cell below `MIN_LEGIBLE` (kept in sync with `--cell-min-w`); collapse columns first |
| **N** | design | wordfit | dura | word-fit — a leaf title's longest indivisible token never exceeds its cell's available width. This is BELOW the M floor's reach: a cell can clear `MIN_LEGIBLE` and still be narrower than a 12-char title. **Applicability clause:** a `treatment: [vertical]` leaf is EXEMPT — its title runs down the BLOCK axis, so horizontal token width is not the fit constraint |
| **Y** | design | all | dura | band content fills the band — no dead margin (≥1200px) |
| **Q** | design | all | dura | compound section widths follow authored span — a compound row's sections stay proportional to their authored `span` weight (`SPAN_TOL_PCT` 15%), not stretched or shrunk by an inherited parent band (≥1200px) |
| **V** | design | grid-dense | **consejo** | horizontal composition — the deck earns its canvas (ultra tier; advises, never fails) |

**Retired rows** carry `superseded` and are printed once in a `[RETIRED]` list,
never evaluated. Each moved to the static gate because it was a statement about
the DATA rather than about pixels: **L** (cells fill width) → `RECT`/`HOLE`;
**F** (the cascade, two rows: min + medium) → `TIER`; **E** (no empty column) →
`TRACK`; **P** (no orphan cell) → `ROW`; **K** (filter integrity) → `CHIP`,
which adds ARITY. **W** (fixed 232px cell width) is the one retired to another
RENDER invariant, `U`, because cells now stretch to equal `fr`.

Each new layout requirement becomes a new row in whichever layer can prove it —
the RATCHET rule (`SKILL.md`, "The verdict"). Prefer the static gate when the
requirement is a statement about the data: it runs everywhere, and the trap is
trusting a metric that measures the wrong thing.

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
| **Rebuild** after editing `data/` | Node + `npm install` + `npm run build` | Regenerates `data/data.generated.js` from the YAML; also the strict-schema gate. |
| **Check** the layout | Node + `js-yaml` (already a build dependency) | `npm run check` is the MANDATORY gate: arithmetic over the authored YAML at five container tiers, no browser. This is the check that must be green before declaring done. |
| **Test** the guards | Node + `js-yaml` | `npm test` proves each guard still detects its defect, and pins the engine↔gate placement mirror. No browser. |
| **Validate** the render | Playwright (+ a Chromium) | `npm run validate` is OPTIONAL REINFORCEMENT for what only pixels answer. Absent Playwright it prints `SKIPPED (no browser)` and exits 0. |
| **Verify-UI** (lighter visual QA) | Playwright (+ a Chromium) | `npm run verify` is a lighter collision-only check + screenshots to review by eye. |

**No tool here installs a browser. Installing one is the USER's action.**
`npm run verify` launches the headless browser it needs, and if the default
Chromium will not launch it falls back to a Chromium ALREADY on disk
(`PLAYWRIGHT_BROWSERS_PATH` or `~/.cache/ms-playwright`, via
`resolveCachedChrome`) — a lookup, never a download. With neither available it
throws. Do not promise the environment heals itself: what actually happens when
the browser is missing is (a) `npm run check` and `npm test` run normally and the
mandatory gate is unaffected, (b) `npm run validate` prints
`SKIPPED (no browser)` and exits 0, and (c) getting the pixel checks requires the
user to run `npx playwright install chromium` themselves — a state-mutating
install, so in Gaia it needs T3 consent. Report the gap; do not assume it closes.

Degradation is graceful in one direction only. The mandatory gate needs no
browser, so it is available everywhere: with only a browser you can view the
already-generated diagram; rebuilding after edits adds Node; `npm run check` and
`npm test` need Node and `js-yaml`; only the pixel checks add Playwright. A
layout change is not done until `npm run check` is green — and where Playwright
IS present, running `npm run validate` too is the reinforcement, not the verdict.

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
