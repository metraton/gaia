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
by its `type` — `box` (default), `separator`, `rail`, or `spacer`. Merges run on **two
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
`npm run build`, `npm run gate`, and the arithmetic proves the rectangle closes
while the render proves the stylesheet drew it.

### Fill geometry (what replaced the width math)

There is no fixed cell width anymore — the old `--cell-w` (232px) is gone, and
the guardrail's retired **W** invariant (`superseded: 'U'` in
`tools/validate-layout.cjs`) records the supersession.

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
│   ├── document.yaml     manifest: title/subtitle/version/palette + pages, in order
│   ├── pages/            one YAML per page
│   └── data.generated.js build output (committed; `window.__DOC__ = {...}`)
└── tools/
    ├── check-layout.mjs     the MODELLED GATE, mandatory — arithmetic, no browser,
    │                        js-yaml only. RECT/HOLE/TRACK/ROW/LANE/BAND/TIER/
    │                        CHIP/ORDER/CSS + the CENSUS pre-flight (npm run model)
    ├── static-census.cjs    the browser-free foundation BOTH gates import:
    │                        loadAuthoredDeck, staticCensus, cssBreakpoints,
    │                        BREAKPOINTS, DEFAULT_FORM, GRID_DENSE — one parse
    │                        path, so the two gates cannot disagree about the data
    ├── validate-layout.cjs  the MEASURED GATE, mandatory (skips with no browser,
    │                        exit 0) — headless render at
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
tokens:                      # optional — design tokens over the defaults (see "Tokens")
  viewport: { w: 1920, h: 1080 }  # the PRESENTATION viewport, in px
filters:                     # optional — the CORE chips, inherited by every page
  - { key: gate, label: "Which boxes are the gates?" }
harmony: false               # optional — true: HARMONY fails a box/rail in no chip
pages:
  - id: overview             # required — must match page.id in the file
    name: "Overview"         # required — visible label (rename without breaking refs)
    order: 1                 # required — decknav position
    visible: true            # required — false omits from build without deleting
    file: pages/overview.yaml   # required — path relative to data/
    omit_filters: [gate]     # optional — core chips this page does not carry
```

**Core chips.** `filters:` here are validated like a page's chips, then
`engine/chips.cjs` (`resolvePageFilters`, shared by the build and the census)
writes each page's list as the core chips first, in their order, minus the
entry's `omit_filters`, then the page's own chips. The build refuses an
`omit_filters` key that is not a core chip, a page chip that redeclares a core
key with another label or steps, and a page chip that reuses an omitted core
key; an identical redeclaration is dropped. An inherited chip with no member on
the page fails CHIP, so omission is always explicit. `harmony` is a boolean;
anything else is refused.

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

`tokens.viewport` is the size the deck is SHOWN at. The build validates it and
writes the resolved value onto `window.__DOC__.tokens.viewport`; CENSUS fails if
the bundle does not carry what the YAML authored. It decides two things: the tier where a
title, description or section header that overflows its lines FAILS the static
gate (every other tier only advises — see TEXT below), and the height each page
is compared against (HEIGHT statically, VH on the render). Set it to the screen
the deck is presented on.

## The page file (`data/pages/<id>.yaml`)

```yaml
id: overview            # required — matches the manifest entry
layout: grid            # DEPRECATED — `grid` is its only value; the build warns
columns: 2              # ROOT grid width (default 2) — the page is a section
form: dashboard         # optional — scopes the guardrail's invariants:
                        # dashboard (default) | timeline | flow | comparison |
                        # mindmap | planner (see the invariant table below)
filters: [ … ]          # optional — the relation chips for this page
text_fit: strict        # optional — strict (default) | advisory
sections: [ … ]         # required, ≥ 1 — the root section's children
```

`text_fit` is the one opt-out from text-fit FAILURES. On a `strict` page a line
overflow at the presentation viewport fails `check` (TEXT, INK) and the ratchet
rows FILL / SLICE / TXT run on the render. `advisory` keeps every finding as
`[INFO]` and skips the ratchet rows: use it for a page that is honestly
scrolled or read in the detail panel, never to silence a cut sentence. Any
other value is refused by the build.

### section (any node with `children`)

```yaml
- id: system            # required, stable slug
  title: "Example system"
  subtitle: "…"         # optional
  variant: neutral      # THE COLOUR AXIS — one value: neutral | good | bad
  treatment: [envelope] # THE STRUCTURAL AXIS — a list:
                        #   envelope | plain | middle | compact
  order: 3              # position among its siblings + collapse order
  span: 2               # occupy M of the PARENT's columns (default 1)
  rowspan: 1            # accepted by the schema; the vertical merge renders on
                        # cells inside LEAF grids (see the rowspan gotcha)
  columns: 2            # this section's OWN grid width (default 2)
  children: [ … ]       # sections and/or components, mixed freely
```

A child of `children` is a **section** if it has its own `children`, otherwise a
**component**. Mix them freely in one list.

The two section treatments added after `envelope` and `plain`, and why each exists:

- **`middle`** centres the section's grid vertically inside the height its
  compound row stretches it to (`.zone.middle`). A compound row is
  `align-items:stretch`, so a short section beside a taller neighbour otherwise
  keeps its content at the top and leaves the gap below it. `middle` is how a
  short cell sits balanced instead.
- **`compact`** gives ONE leaf grid a shorter row: `--cell-h: 74px` and a 4px
  row gap (`.zone.compact`), and its boxes drop the description clamp because
  their height comes from `rowspan`. It exists for a staircase of rowspans that
  must end level with a shorter neighbour. Rules: a `compact` section's children
  must all be components (the build refuses a nested section, since the
  treatment shortens the rows of one leaf grid); a single-row box inside it must
  carry at most a one-line description, or `validate` C reports the clip; and
  the render gate's U accepts a row other than 130px ONLY in a grid that
  declares `compact`, reporting any other short row as
  `--cell-h=…px without the compact treatment`. The static gate models it too:
  the grid's height uses the 74px row and 4px gap, TEXT does not apply the
  description clamp there (there is none), and INK measures the WHOLE
  description plus the 4px box padding against the short slot — so a
  description that does not fit a compact row fails `check` before the render.

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
                         #   | blue | violet | gold | clay   (categorical)
  variant_extra: [muted] # DEPRECATED — a second colour role on one frame; the
                         #   build warns. Say the second claim in text instead.
  lead: true             # optional — this box is the page's LEAD band (below)
  treatment: [centered]  # THE STRUCTURAL AXIS — a list, composable:
                         #   centered | half | vertical | outside
  span: 2               # occupy M of the section's columns (default 1);
                        # 1 < M < columns is a real PARTIAL merge
  rowspan: 2            # occupy K rows (default 1) — a vertical merge, K× the
                        # cell height (height as magnitude)
  filters: [flow]       # keys of the filters this component belongs to
  copy: true            # optional copy-to-clipboard button: `true` copies the
                        #   title, a string copies that string
```

**The four categorical hues — `blue`, `violet`, `gold`, `clay`.** Every other
variant carries a meaning of risk or state (`bad` is danger, `good` is safe).
These four carry NONE: they exist for a page that must tell up to four peer
groups apart, where borrowing `good`/`bad` would assert a verdict the content
does not make. So the page that uses them must say, in its own content, what
each hue means. Each hue is a tint, a border and a kicker colour
(`.box.<hue>`), with its description on `--body` because `--muted` loses AA on
a tint in the dark skins. Every palette block in `index.html` declares
`--hue-<name>` and `--hue-<name>-soft`, and `npm run contrast` measures four
pairs per hue (title, description, kicker, border).

**`lead: true`** marks the page's lead band, the full-width first box whose
title is the page's claim. `checkLead` in the build refuses it unless it is a
box, a direct child of the page root, first in effective `order`, with `span`
equal to the root's `columns`, and neither `half` nor `vertical`. HARMONY
exempts it. Skeleton:

```yaml
columns: 2
sections:
  - { id: lead, order: 1, span: 2, lead: true, kicker: "PART 2 OF 5",
      title: "The claim this page makes", description: ["one line of context"] }
  - { id: first-zone, order: 2, span: 2, title: "…", columns: 4, children: [ … ] }
```

**`copy`** puts a small corner button on the box that copies text to the
clipboard (engine.js `buildCopyButton`): `copy: true` copies the title verbatim,
`copy: "<string>"` copies that string. It exists for a box whose title IS
something the reader will paste: a command, a path, an identifier. The click
stops at the button, so it never opens the detail card. Rules the build
enforces: only a box may carry it (not a separator, rail or spacer), the value
is `true` or a non-empty string, and `copy: true` needs a title. The Clipboard
API needs a secure context, so under `file://` the engine falls back to a hidden
textarea and `execCommand('copy')`. The glyph's contrast pair is `copy-icon`.

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
  treatment: [vertical]   # rotates the text; omit for a horizontal banner.
                          #   [centered] centres the title; its absence is start-aligned
  variant: blue           # optional: blue | violet | gold | clay — nothing else
  indent: 1               # optional: 0..3 — inset the drawn frame one step per level
  filters: [flow]         # optional: chip membership — a chip lights the rail
  span: 1
```

A title-only swimlane LABEL banner (styled like a box but carrying only a
title). `treatment: [vertical]` rotates it for labeling a vertical lane — the
former `orientation` field is gone and is now rejected by the strict schema. Not
clickable. A rail has its OWN whitelist (`RAIL_FIELDS`: id, type, order, span,
rowspan, title, treatment, variant, filters, indent), so a payload key is
refused by name. Its fields beyond the title, and why each exists:

- **`treatment: [centered]`** centres the title (`.rail.centered`, mirroring
  `.box.centered`); without it the title is start-aligned, as on a box. A
  vertical rail keeps both centrings regardless, because a rotated lane label has
  no start or centre text axis to choose.
- **`filters`** makes the rail a chip member. `buildRail` stamps it as
  `data-filters`, so a chip lights the rail exactly as it lights a box, and a
  rail that is not a member dims like a section header while a chip is active.
  Without it a relation that runs through a lane label could not show the label.
- **`variant`** colours the rail with one of the four categorical hues, the only
  variants `.rail.<hue>` draws; the build refuses any other value (`good`,
  `bad`, ...) as `unknown rail variant`. A hue rail is a word rather than a lane
  label: it keeps its authored case and is set tighter (10.5px, no tracking,
  4px sides) so one word fits a narrow cell. The static gate's RAILT measures it
  at those metrics.
- **`indent`** (integer 0 to 3) moves the drawn frame onto the title and insets
  it by `indent × --indent-step` (32px), so a column of rails reads as a tree by
  indentation. The CELL still fills its track, so every cell gate measures it
  unchanged; the build refuses a value outside 0 to 3. The TITLE, though, wraps
  in less: RAILT measures it in the cell minus `indent × 32px`, the rail's right
  padding, and the title frame's own 16px padding and 1px border on each side —
  a title that fits two lines flat can need three at indent 3.

**A horizontal rail without `rowspan` is a THIN leaf.** Its row renders at the
rail's own content height (`auto`, 33px for one title line) instead of 130px,
the same reason a separator row is thin: one banner line should not cost a full
cell. A vertical rail and a rail with `rowspan` stay full height, because their
height IS what they draw. Because `.rail-title` has no clamp, a third title line
would grow the row: the static gate's RAILT fails a thin rail whose title wraps
past two lines, and the render gate's U asserts a rail row inside 33 to 48px.

### component — spacer (`type: spacer`)

```yaml
- id: gap-1
  type: spacer
  order: 1
  span: 1               # the two merge dials are the ONLY payload-free fields it
  rowspan: 1            # keeps — they belong to the CELL, not to its content
```

The declared hole: it occupies its cell and renders an empty `.spacer`
(`buildSpacer`), so a rectangle closes without inventing content. `SPACER_FIELDS`
is the entire whitelist and `checkSpacer` runs BEFORE the two vocabulary axes, so
a `variant`/`treatment`/`filters`/payload key is reported as "a `spacer` carries
no X" instead of as a near-miss inside an enum it cannot reach. Two consequences
downstream: `nodeCensus` counts `spacers` on its own arm, and `textBudget` skips
it BY TYPE — budgeting a cell that carries no text by schema would compare
nothing against a capacity and count as evidence.

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

**flow — phases as sections** — the shape an explanation with phases lowers
into (`SKILL.md`, "When the idea MOVES, the layout is the path"): phases are
sibling sections in `order`, steps are ordered components inside their phase,
the path is ONE chip declared by a step in every phase, and the kicker carries
`STEP n OF m` as a convention the engine neither validates nor renders. The root
holds only sections, so it is a compound row of equal-weight zones that stacks
in the same `order` below the 1440px breakpoint. Seed:
`data/pages/p10-flow-phases.yaml`.

```yaml
form: flow
columns: 3
filters:
  - { key: path, label: "The path", steps: ["Phase 1 → Phase 2 → Phase 3, one chip across every phase-section"] }
sections:
  - id: phase-1
    title: "Phase 1"
    order: 1
    span: 1
    columns: 1
    children:
      - { id: s1, kicker: "STEP 1 OF 4", title: "…", order: 1, filters: [path] }
      - { id: s2, kicker: "STEP 2 OF 4", title: "…", order: 2, filters: [path] }
  - id: phase-2
    title: "Phase 2"
    order: 2
    span: 1
    columns: 1
    children:
      - { id: s3, kicker: "STEP 3 OF 4", title: "…", order: 1, filters: [path] }
      - { id: s4, kicker: "STEP 4 OF 4", title: "…", order: 2, filters: [path] }
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

## Tokens

Every visual number a deck may tune is a **token**. `engine/tokens.mjs` holds
the defaults (`DEFAULT_TOKENS`) and the schema; `document.yaml` `tokens:` is
merged over them by the build, which refuses an unknown key (with a near-miss
hint), a value outside its range, and a broken relation (`breakpoints` must be
`one < two < stack`; a `min_px` must not exceed its `max_px`). The resolved set
is written to `window.__DOC__.tokens`, and its CSS projection to
`window.__DOC__.css_vars`, which `engine.js` sets on `<html>`. Both gates read
`__DOC__.tokens`; none keeps a copy. Change a value in the YAML, rebuild, and
the engine, `check`, `validate` and `test` all follow.

```yaml
tokens:
  row: { cell_h: 120 }            # document-wide
  type: { desc: { lines: 2 } }
```

A **section** may override `row.cell_h`, `type.title.lines` and
`type.desc.lines`; a **box** may override the two `lines`. Nothing else is
per-node: a per-cell font or spacing would let one cell stop matching its
neighbours. An override inherits down the subtree, like the CSS property it
becomes, and a section that sets `row.cell_h` is held to that row by validate U.
`treatment: [compact]` is sugar for the section override
`row.cell_h: <row.compact_h>` plus its structural rules (a `--s-1` row gap and
box padding, no description clamp).

The breakpoints are the one exception to "every rule reads var()": a container
query cannot, so the build writes the three `@container` tiers into
`data/breakpoints.generated.css` (committed, like `data.generated.js`), which
`index.html` links. The `:root` declarations and `var()` fallbacks in
`index.html` are the defaults a deck opened without its bundle draws; `check`
fails if any of them differs from `DEFAULT_TOKENS`.

| Key | Default | Range | Where | Why it is a token |
|-----|---------|-------|-------|-------------------|
| `row.cell_h` | 130 | 60..400 px | doc, section | the fixed row every cell fills: title + clamped description |
| `row.sep_h` | 40 | 16..120 px | doc | a divider row: a break, not a missing cell |
| `row.zone_min_h` | 180 | 0..600 px | doc | a framed zone's floor, so a short zone is not a sliver |
| `row.compact_h` | 74 | 40..400 px | doc | the row `compact` presets, for a staircase that ends level |
| `space.base` | 8 | 2..16 px | doc | the grid step every gap and padding is a multiple of |
| `space.scale` | [0.5,1,2,3,4,6,8] | 7 increasing, 0.25..16 | doc | `--s-1`..`--s-7` = base × scale |
| `frame.v` / `frame.h` / `frame.top` | 28 / 40 / 35 | 0..200 px | doc | the stage's breathing room; `top` matches the header→chips rhythm |
| `frame.narrow` | 8 | 0..64 px | doc | the frame and canvas padding at the one-track tier |
| `plane_max` | 1280 | 640..7680 px | doc | the content block's cap; wider screens get side margins |
| `cell_min_w` | 120 | 100..400 px | doc | the legibility floor: columns collapse before a cell goes below it |
| `type.title.min_px` / `vw` / `max_px` | 15 / 1 / 17 | 11..32 px / 0..5 vw / 11..40 px | doc | box title `clamp()` |
| `type.title.lines` | 2 | 1..4 | doc, section, box | the title clamp |
| `type.desc.px` / `lh` | 12 / 1.4 | 10..24 px / 1..2.4 | doc | description line size |
| `type.desc.lines` | 3 | 1..8 | doc, section, box | the description clamp (a fixed box height) |
| `type.kicker.px` / `track_em` | 10.5 / 0.09 | 9..20 px / 0..0.3 em | doc | the machine name above the title; tracking counts in the budget |
| `type.section_title.*` | 13 / 0.85 / 14.5 px, 0.1 em, 2 lines | as title, lines 1..4 | doc | section header `clamp()` and clamp |
| `type.section_sub.px` / `lines` | 12 / 3 | 10..24 px / 1..6 | doc | section subtitle |
| `type.rail.px` / `track_em` | 13 / 0.09 | 10..24 px / 0..0.3 em | doc | a lane label |
| `type.rail_hue.px` / `track_em` / `pad_y` | 10.5 / 0 / 10 | 9..24 px / 0..0.3 em / 0..24 px | doc | a hue rail is a word, set tight to fit a narrow cell |
| `type.panel.*` | title 19, summary 15, kicker 13 px, 0.08 em | 12..48 / 11..32 / 9..24 px | doc | the detail card |
| `indent_step` | 32 | 8..96 px | doc | one rail tree level (depth stays 0..3) |
| `dim.box` / `dim.label` | 0.18 / 0.34 | 0.05..0.9 | doc | how far a chip dims the rest |
| `panel.dock` | bottom-left | 4 corners | doc | where the detail card docks |
| `panel.inset` / `aspect` / `width_cols` | 24 / 1.25 / 2 | 0..96 px / 0.5..3 / 1..4 | doc | card inset, height:width, width in narrowest-root-section units |
| `breakpoints.stack` / `two` / `one` | 1440 / 1000 / 640 | 320..7680 px | doc | the collapse tiers (generated CSS) |
| `viewport.w` / `h` | 1920 / 1080 | 320..7680 / 240..4320 px | doc | the presentation tier: text fit fails there; page height is compared with it |
| `default_columns` | 2 | 1..12 | doc | a section's columns when it declares none |

**Fixed, not tokens:** box chrome (border 1.5, radius, inner gap 2, title margin
1), the half-title clamp of 1, the rail indent depth, the monospace advance and
line factors the gates assume, WCAG thresholds, measurement tolerances, and the
pan threshold. They are craft values, structural definitions or facts about the
instrument; `check` still reads the chrome back out of `index.html`.

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

### A migration touches THREE coupled files, not two

`index.html`, `engine/engine.js` and `data/data.generated.js` are a **coupled
contract** — they change together, always. `data.generated.js` is a COMMITTED
build output (the `window.__DOC__` assignment that is the ONLY thing `engine.js`
reads), so a migration planned as two steps — drop in the new engine, rewrite
the fields — leaves a NEW engine paired with the bundle the OLD one generated.
That does not fail: the browser renders the stale deck silently. `npm run build`
is the third step, not a formality, and `check`'s CENSUS line (`data/*.yaml` vs
`data/data.generated.js`) is what catches you when you skip it. The serving-side
half of the same coupling — the `{{DIAGRAM_DECK_VERSION}}` cache-busting
placeholder on both `<script>` tags and the no-cache safety net that stop a
BROWSER from pairing them stale — is in `assets/README.md`.

## Engine gotchas

Behaviors that bite if you author against intuition instead of the engine:

- **Cells fill; every section is a filled rectangle.** A leaf grid's tracks are
  EQUAL `fr` shares of the section width — cells stretch, so width varies by
  section but is always equal within a grid, and every row reaches the right
  edge (**U** asserts the equal widths on the render; that the row CLOSES is
  arithmetic — **RECT**/**TRACK** in `npm run model`, which superseded the
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
- **`separator`, `rail` and `spacer` are structural leaves.** They occupy a grid
  cell and honor `span` like any component, but carry no detail and are not
  clickable. A
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
   `var(--sep-row-h)` when the row has occupants and EVERY one is a THIN LEAF —
   a horizontal separator or a `spacer` (`isThinRowLeaf`) — `var(--cell-h)`
   otherwise. It returns `null` when no row qualifies, so a grid without a thin
   row gets no inline style at all and stays on the plain fixed-row default.
3. `applyRowTracks(grid, slots, cols)` emits that list once PER COLLAPSE TIER —
   `--row-tracks` (authored `cols`), `--row-tracks-2`, `--row-tracks-1` — skipping
   any tier that would widen the grid. The CSS consumes them as
   `grid-auto-rows: var(--row-tracks, var(--cell-h))`, switching to
   `--row-tracks-2` inside the 1000px container query and `--row-tracks-1` inside
   the 640px one (a `.sec-c1` grid keeps its base list). Each tier is computed
   independently, because placement is a function of the track count: a separator
   that shares its row with boxes at the authored width but ends up alone at 2
   tracks is thin only in that tier's list.

A `spacer` counts as thin, for the ROW's reason and not the spacer's. A row
composed only of rules and DECLARED holes carries nothing of cell height — it is
a one-line row — and a declared hole inside it is the absence of a RULE, not the
absence of a BOX. A rule that spans 3 of 4 tracks needs the fourth declared (the
rectangle must close), and charging that declared cell a full `--cell-h` would
let the track the rule chose NOT to reach set the height of the rule's own row.
A spacer beside ORDINARY cells changes nothing: `every` still fails on those
cells, so that row keeps `--cell-h`.

Two exclusions are deliberate. A **VERTICAL** separator is never thin — its ink
IS the row height, so thinning its row would shorten the drawing rather than fit
it. And a row with NO occupant at all (an UNdeclared interior hole) keeps
`--cell-h`: an empty track is not a thin row, only a hole someone declared is,
and `RECT`/`HOLE` own that defect.

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
   `type: separator`, `type: rail` or `type: spacer` for structural leaves.
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
3. **Model — MANDATORY, arithmetic, no browser.**
   ```
   npm run model      # node tools/check-layout.mjs [deckRoot]   (alias: npm run check)
   ```
   This is the MODELLED half of the gate. It is arithmetic over the AUTHORED YAML, needs nothing but
   `js-yaml`, and runs in milliseconds — which matters because Gaia is installed
   in places where no browser exists, and a guardrail that needs Chromium is
   absent precisely where a deck is authored blind. **Never declare a layout
   change done until this is green.** Three structural rules keep it honest: a run
   that asserted NOTHING is RED (the `asserted === 0` gate), a check that could not
   be asserted is `[NOT ASSERTED]` and counted in the headline (a non-zero count is
   NOT green — nothing failed and the gate cannot say the deck holds either), and
   the deck root is taken from `argv`/`DIAGRAM_DECK_ROOT` so the gate can be
   pointed at a broken fixture and be SHOWN to fail. Findings are grouped per
   check; `[FAIL]` exits non-zero, `[INFO]` is advisory and never fails.
4. **Render — MANDATORY, and it skips cleanly where there is no browser.**
   ```
   npm run render     # node tools/validate-layout.cjs — PURE-READ, build first   (alias: npm run validate)
   ```
   The MEASURED half, and the only half that can tell whether the stylesheet
   IMPLEMENTS what step 3 computed: a span rule the CSS never carried leaves the
   arithmetic closing a rectangle the browser draws at a third of its width.
   Requiring it costs nothing, which is what makes it mandatory rather than
   advisory: Playwright is resolved LAZILY (`loadChromium`) and where it is absent
   `main()` prints `SKIPPED (no browser)` and **exits 0**, so its absence never
   blocks a deck — it obliges the verdict to say `MEASURED: unavailable` out loud
   instead of resting silently on the arithmetic. Render is **DECOUPLED from
   build**: it renders and asserts the EXISTING `data/data.generated.js` and never
   regenerates it. It is genuinely read-only: no child build process, no project
   file writes; PNGs go to a **system temp dir** (`os.tmpdir()`, override with
   `DIAGRAM_SHOTS_DIR`). It renders ONE width (2560, `WIDTHS = { ultra: 2560 }`),
   `PASSES = 3` reloads each, and asserts only what genuinely needs PIXELS.
   ```
   npm run gate       # npm run model && npm run render — the pair, and the ONLY
                      # thing a verdict may cite
   ```
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
6. **Look — the SEEN class, required on a first build, a change of form or of
   model, or any intention no invariant covers.** `DIAGRAM_SHOTS_DIR=<a readable
   path> npm run verify` renders the deck and writes per-page screenshots at 1920
   and 1440 in BOTH themes, then load `visual-verify` for the looking discipline.
   Do not write a browser probe: `verify.mjs` already resolves a Chromium that is
   on disk and handles the capture trap (`.canvas` is `position:absolute` with
   `overflow:auto`, so a naive full-page screenshot truncates to viewport height).
   A pixel read catches contrast or a wrong wrap the invariants do not name, and it
   is the ONLY evidence that reaches principles 4, 5, 7 and half of 6.
7. **Loop on any FAIL** — read the failing check's detail (it names the grid, the
   tier, and the measured value), fix the YAML/CSS, rebuild, re-check.

### The MODELLED checks (`npm run model`)

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
| **TEXT** | the character budget per box per tier: title token, kicker token, title lines vs its clamp (2, 1 for a `half`), description lines vs its clamp (3; none in a `compact` grid), and every SECTION HEADER — `.ztitle` (13–14.5px, 0.1em tracking, clamp 2) and `.zsub` (12px, clamp 3) at the zone's inner width. A LINE overflow FAILS at the presentation tier (`document.yaml` `viewport.w`, added to the sweep when it is not one of the five) on a page not declared `text_fit: advisory`; at the other tiers, and for a token, it is `[INFO]` |
| **INK** | the ink height of a box against the slot the model gives it (a `compact` grid's 74px row included). An overflow FAILS at the presentation tier of a strict page and advises elsewhere; room for a whole statement left undeclared advises at the presentation tier |
| **RAILT** | a thin rail's title past two lines — HARD, because `.rail-title` has no clamp and a third line grows the row. Measured at the rail's own metrics and, for an `indent`, in the narrower width the indented title frame leaves |
| **HEIGHT** | ADVISORY: each page's full height predicted from the placement model (row tracks, zone frames and headers, compound rows as the tallest child above 1440px and the sum below, plus the chrome validate VH measures) against `viewport.h`, as `page X: predicted NNNNpx > 1080 (+NNN) at 1920`. A deck may mean to scroll; this says by how much |
| **ORDER** | a duplicate EFFECTIVE order among siblings (`order ?? index+1`). The engine resolves the tie by list position, so the render is correct today and can flip under an unrelated edit that only MOVES a node in the file |
| **CSS** | the mirror itself: the breakpoints and text metrics this gate computes with, against the `@container stage` queries and `.box`/`.zone`/`.canvas` declarations `index.html` actually declares (`cssBreakpoints` / `cssTextTokens` vs `BREAKPOINTS` / `CSS_TEXT`). Without it a stylesheet edit that moved a cut would leave every tracks-per-tier and character number describing a deck the browser no longer draws — green, and wrong. The two failures to read are OPPOSITE: no `index.html` at all is `[NOT ASSERTED]` (recorded, counted in the headline, never a pass), while an `index.html` that IS present and whose probe missed is a `[FAIL]` — the declaration moved past the probe, so every number derived from it is unverified, and "not asserted" there is exactly the silence that certifies the drift |
| **CENSUS** | pre-flight, and printed FIRST: `data/*.yaml` vs `data/data.generated.js`. A stale build means everything below still describes the YAML correctly while the deck someone is LOOKING at is a different one. Shared with `validate` through `tools/static-census.cjs`, so the two gates cannot disagree about what the data says |

### The MEASURED invariants (`npm run render`)

A **FORM-SCOPED flat table** (`INVARIANTS` in `tools/validate-layout.cjs`): the
page declares its `form` (default `dashboard`); each row names the forms it
applies to, its class (`integrity` / `geometry` — `geometry` is measured pixels,
not visual taste), its severity (`dura` fails the build, `consejo` only
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
| **U** | geometry | all | dura | TWO rows: cells equal width per grid, and uniform **SLOT** height. SLOT, not component: `rowspan` makes a component a MULTIPLE of the slot and `half` a FRACTION of it, so both are excluded from the component-height set and every `.half-slot` is asserted at `--cell-h` directly |
| **M** | geometry | gridded | dura | cells legible — no cell below `MIN_LEGIBLE` (kept in sync with `--cell-min-w`); collapse columns first |
| **N** | geometry | wordfit | dura | word-fit — a leaf title's longest indivisible token never exceeds its cell's available width. This is BELOW the M floor's reach: a cell can clear `MIN_LEGIBLE` and still be narrower than a 12-char title. **Applicability clause:** a `treatment: [vertical]` leaf is EXEMPT — its title runs down the BLOCK axis, so horizontal token width is not the fit constraint |
| **Y** | geometry | all | dura | band content fills the band — no dead margin (≥1200px) |
| **Q** | geometry | all | dura | compound section widths follow authored span — a compound row's sections stay proportional to their authored `span` weight (`SPAN_TOL_PCT` 15%), not stretched or shrunk by an inherited parent band (≥1200px) |
| **V** | geometry | grid-dense | **consejo** | horizontal composition — the deck earns its canvas (ultra tier; advises, never fails) |

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

### Checks added with the thin-row, rail and copy port

The static gate (`npm run model`) also asserts, each with a negative case in
`npm test`:

| Check | What it fails | Why it exists |
|---|---|---|
| **FROZEN** | an undeclared hole under a `vertical` box whose flex row a taller sibling sets | a rotated bar's height is its authored rowspan, and nothing in the data ties that rowspan to the neighbour that sets the row |
| **LIT** | `filters` on a separator or spacer | only boxes and rails emit `data-filters`, so that membership closes the CHIP join and never renders |
| **RAILT** | a thin rail's title past two lines | the rail row is `auto` and `.rail-title` has no clamp, so a third line grows the row instead of clipping |
| **WORDS** | an authored string missing from `data.generated.js` | CENSUS compares ids and counts, so a text-only edit without a rebuild stays green on the old words |
| **SPAN** | a stylesheet missing one of the four span-to-tracks rules | every width the gate reports assumes a span of M occupies M tracks; without the rule the browser places the section in one track |
| **INK** | a box whose stacked lines overflow its fixed row (advises on a large undeclared void) | TEXT measures width only; INK is the height half. It runs on every page not declared `text_fit: advisory` and fails at the presentation viewport |
| **TEXT** kicker token | a kicker token wider than its cell (advisory) | the title budget never measured `.box .k`, which has its own size and tracking |

The render gate (`npm run render`) gains the rail-row band and the `compact`
row in U, the `.msp` band-leaf exemption in G (a band separator or rail may be
full width, and must fill its row), and three RATCHET rows, FILL, SLICE and
TXT, that run on every page not declared `text_fit: advisory` — the same page
field that scopes INK and the TEXT failures, so the static estimate and the
rendered ruling always cover the same pages. It also reports **VH**, an
advisory: each page rendered at the `document.yaml` viewport, its measured full
height against `viewport.h`, with the chrome (canvas offset and frame) it
measured — the value the static HEIGHT prediction assumes.

**Chip coverage is NOT a gate in the seed.** A deck may require that every box
and rail belongs to at least one chip, so that no component is left out of every
question the page answers. That rule is deck policy, not a generic invariant:
the seed's own teaching pages use chips for a subset of their components on
purpose, and the rule fails 10 of its 11 pages. A deck that wants it adds the
check to its own `tools/check-layout.mjs`.

The engine's reset chip reads `all`, and the one detail and relation card is
docked bottom-left, draggable by its header, and sized from the widest root
grid in the deck (`placeCard`).

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
| **Model** the layout | Node + `js-yaml` (already a build dependency) | `npm run model` (alias `check`) is the MODELLED half of the gate: arithmetic over the authored YAML at five container tiers, no browser. |
| **Test** the guards | Node + `js-yaml` | `npm test` proves each guard still detects its defect, and pins the engine↔gate placement mirror. No browser. |
| **Render** and measure it | Playwright (+ a Chromium) | `npm run render` (alias `validate`) is the MEASURED half — MANDATORY, for what only pixels answer. Absent Playwright it prints `SKIPPED (no browser)` and exits 0, and the verdict says `MEASURED: unavailable`. |
| **Gate** — the verdict's only citation | Node (+ Playwright when present) | `npm run gate` runs both halves in order. A verdict cites this, never one half. |
| **Verify-UI** (lighter visual QA) | Playwright (+ a Chromium) | `npm run verify` is a lighter collision-only check + screenshots to review by eye. |

**No tool here installs a browser. Installing one is the USER's action.**
`npm run verify` launches the headless browser it needs, and if the default
Chromium will not launch it falls back to a Chromium ALREADY on disk
(`PLAYWRIGHT_BROWSERS_PATH` or `~/.cache/ms-playwright`, via
`resolveCachedChrome`) — a lookup, never a download. With neither available it
throws. Do not promise the environment heals itself: what actually happens when
the browser is missing is (a) `npm run model` and `npm test` run normally, (b)
`npm run render` prints `SKIPPED (no browser)` and exits 0, so `npm run gate`
still exits 0 on a sound deck, and (c) getting the pixel checks requires the user
to run `npx playwright install chromium` themselves — a state-mutating install, so
in Gaia it needs T3 consent. Report the gap; do not assume it closes.

Degradation is graceful in one direction only, and it is a degradation the verdict
must NAME. `model` needs no browser, so it is available everywhere: with only a
browser you can view the already-generated diagram; rebuilding after edits adds
Node; `model` and `npm test` need Node and `js-yaml`; only the pixel checks add
Playwright. A layout change is not done until `npm run gate` is green — both
halves — and where the render half skipped for want of a Chromium the verdict says
`MEASURED: unavailable` rather than presenting the arithmetic as an observation.

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
