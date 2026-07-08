# Diagram Builder — reference

Deep mechanics for authoring a diagram deck on the recursive-section model: the
field-by-field schema, the engine behaviors that surprise you, the authoring
modes, and the build → verify loop. For the vocabulary and enums, see
`GLOSSARY.md`; for the thinking method and the two-consumer framing, see
`SKILL.md`.

The portable engine is vendored under `assets/` (see `assets/README.md`):
`index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`,
`tools/verify.mjs`, and a domain-free seed `data/`. Scaffold from there.

## The layout model in one paragraph

There are exactly **two primitives**. A **section** is a node with a `children`
array; it renders as a CSS-Grid `columns` wide, and its children auto-flow
left→right and wrap down. A **component** is a leaf (no `children`); it renders
by its `type` — `box` (default), `separator`, or `rail`. Any child, section or
component, may set `span: N` to occupy N of the parent's columns. The page/root
is itself a section (`page.columns` = its grid width, `page.sections` = its
children). Nesting is just a section whose children include other sections — a
grid of grids, as deep as the idea needs. There is no envelope primitive, no
subsection, no mosaic, no `wraps`, no `layout.row`, no layout modes.

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
└── tools/verify.mjs      headless render QA (collision assertions + screenshots)
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
`version` degrades with zero visible change. See the versioning rule in
`SKILL.md`.

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

- **`columns` is authoritative for width; width never collapses it.** A section
  is rendered wide enough to sustain its `columns` (≈ columns × `--box-min`);
  when the diagram is wider than the viewport the canvas **scrolls
  horizontally** rather than dropping columns. "Go vertical" is the author's
  explicit `columns: 1`, not something the width forces.
- **Tracks size to content, not to fill (`minmax(--box-min, max-content)`).** A
  narrow one-column section does NOT balloon to a dense neighbour's width. This
  is why the root grid lives in a `width:max-content` plane that centers when it
  fits and scrolls when it does not.
- **`span` is the same at every level, and is clamped to the parent's
  `columns`.** `span == columns` is a full-width band. A `span` larger than the
  parent's columns is clamped down — it never overflows the grid.
- **Order is `order`, else list order.** DOM order (after the stable sort by
  `order`) IS the single-column collapse order at the phone breakpoint. To move
  a cell, change its `order` — there is no column/row coordinate to set.
- **The responsive rule is coarse and applied per-grid.** Below `--bp-tablet`
  (768px) every grid caps to `min(columns, 2)`; below `--bp-phone` (480px) every
  grid caps to 1. A `columns: 1` section (`.sec-c1`) is exempt from the tablet
  cap. A multi-span child degrades 3→2→1 in lockstep so nothing overflows. There
  is no per-section custom breakpoint.
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

1. Copy the vendored engine layer from `assets/` (`index.html`, `engine/`,
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

## The build → verify loop

A diagram is not done until the data is right; a visual look is a spot-check,
not a gate. Editing the data is the fast path.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data:
   ```
   npm run build      # node engine/build-data.mjs → data/data.generated.js
   ```
   This is a local file write (reads the manifest, skips `visible: false`, sorts
   by `order`, merges each page). Re-run after every YAML change.
3. **Spot-check by looking (optional).** Load `Skill('visual-verify')` and
   render `index.html` as a `file://` URL, capturing across a spread of widths
   (desktop down to ~380px) and both themes. Because the engine scrolls
   horizontally instead of collapsing columns, a wide layout can overflow — a
   quick pixel read catches it. The project's `npm run verify` (Playwright:
   asserts the root grid renders and its top-level cells do not collide, then
   screenshots widths × themes) is a useful T1 gate. Both write PNGs to a
   **system temp dir**, never inside the project (`tools/verify.mjs` uses
   `os.tmpdir()`, override with `DIAGRAM_SHOTS_DIR`).
4. **Loop on any real defect** — overflow, clipping, a wrong wrap, contrast
   failing in one theme. Fix the YAML and rebuild.

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
| **Auto-verify** the render | Playwright (+ a Chromium) | `npm run verify` renders every page and screenshots widths × themes to a system temp dir. Optional — its absence never blocks; a browser still lets you look. |

Degradation is graceful: with only a browser you can always view the
already-generated diagram; rebuilding after edits adds Node, auto-verification
adds Playwright.

### Explain before you execute

Before running ANY script, say in one short, plain sentence what it does and
which file to open to inspect it first — e.g. *"I'll run `npm run build` (which
runs `engine/build-data.mjs`) to regenerate the diagram from your YAML — you can
read that script first."* Informed consent by a human who can see what will run.

### Why the engine stays minimal and data-driven

The engine and template carry **no baked-in data** — every domain string lives
in `data/`. That is what keeps a scaffold generic and leak-free: nothing from
one diagram bleeds into the next; it only scales to new content. Keep it that
way — content in `data/`, never in the engine or template.
