# Diagram Builder — reference

Deep mechanics for authoring a diagram deck: the field-by-field schema, the
engine behaviors that surprise you, the five authoring modes, and the
build → verify loop. For the shared vocabulary and enums, see `GLOSSARY.md`; for
the decomposition model and the two-consumer framing, see `SKILL.md`.

The portable engine is vendored under `assets/` (see `assets/README.md`):
`index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`,
`tools/verify.mjs`, and a domain-free seed `data/`. Scaffold from there. The
reference framework this skill was modeled on
(`/home/jorge/ws/century-inc/branchkinect-architecture-overview`) remains a
fuller worked example — its `engine/DIALECT.md` §0–§1 is the original canonical
contract, kept in sync with this skill's `GLOSSARY.md`.

## The facilitation method: idea → diagram

The hard part of diagramming is not the YAML syntax — it is deciding the shape.
The failure mode is jumping from a vague idea straight to authoring, then
fighting the render. This method keeps the expensive step (build + render) last
and does all the convergence on the cheap, in text.

### Why ASCII first

An ASCII sketch drawn with the dialect's own concepts — a page as a grid of
columns, sections as bordered boxes placed by row/span, components as lines
inside — is a 1:1 preview of the YAML. It costs nothing (no build, no render, no
T3), it is instantly editable mid-conversation, and because it uses the same
vocabulary, translating an agreed sketch to YAML is mechanical. The orchestrator
can run this whole loop in chat before any agent is dispatched.

### The loop

1. **Capture the idea.** Take the user's description, or read the markdown/notes
   they point at. Extract the entities and the groups they fall into.
2. **First sketch.** Draw the whole thing in ASCII: choose a column count, place
   the big groups as sections (bordered boxes) across rows, drop the components
   inside as lines. Annotate each box with its dialect intent —
   `(variant, row, span, columns)`.
3. **Ask the sharpening questions, redraw on each answer:**
   - Where does it start / what is the entry point? → the first section, often a
     `danger` zone with an `ENTRY` component.
   - What are the natural groups? → sections, and whether one *contains* others
     (an `envelope`).
   - What connects to / flows through what? → a `filter` chip tracing the path.
   - What is risky, new, or hardened? → `status` + `variant` per component.
   - How wide is it — how many columns, what sits beside what? → `columns` and
     each section's `span`/`row`.
4. **Confirm.** When the user says "that's it", the ASCII is the spec.
5. **Translate & build.** Convert the agreed sketch to YAML (the five modes),
   run the build, and verify by looking (the build → verify loop below).
6. **Fine-tune only.** From here, changes should be cheap parameter tweaks —
   `columns`, `span`, `order`, wording — not structural rework, because the
   structure was already agreed as ASCII.

### Worked example

**Idea (prose):** "Show our request path. A user hits the load balancer, which
goes to the API; the API reads the database and calls an external payments
provider. The payments call is the risky bit."

**Round 1 — a first cut (one row, everything inline):**

```
cols: [ 1 ][ 1 ][ 1 ]
┌─ Request path (row 1, span 3, columns 3) ───────────────────┐
│ [LB] ENTRY   [API] INTERNAL   [DB] INTERNAL   [Pay] EXTERNAL │
└─────────────────────────────────────────────────────────────┘
```

*Question:* "Is the database internal and payments outside your perimeter?
Should we group the internal parts and set payments apart?"
*Answer:* "Yes — DB and API are ours; payments is external and risky."

**Round 2 — group internal vs external, mark the risk:**

```
cols: [      1      ][      1      ]
┌─ Our system (row 1, span 1, columns 1) ─┐  ┌─ External (danger, row 1, span 1) ─┐
│ [LB]  ENTRY                              │  │ [Pay] EXTERNAL   variant: crit     │
│ [API] INTERNAL                          │  │  raw card data · no vault          │
│ [DB]  INTERNAL   variant: store         │  └────────────────────────────────────┘
└──────────────────────────────────────────┘
```

*Question:* "Want a chip that lights the whole request path end to end?"
*Answer:* "Yes, call it 'Checkout'."

**Round 3 — add the flow filter, confirm.** Every box on the path gets
`filters: [checkout]`; the chip spotlights the path. User: "That's it." The
sketch is now the spec.

**Final YAML (Modes 3–5):**

```yaml
id: request-path
layout: grid
columns: 2
filters:
  - key: all
    label: "All"
  - key: checkout
    label: "Checkout path"
    steps:
      - "User → load balancer → API; the API reads the DB and calls payments."
sections:
  - id: oursystem
    title: "Our system"
    variant: normal
    order: 1
    layout: { row: 1, span: 1 }
    columns: 1
    components:
      - { id: lb,  order: 1, status: ENTRY,    title: "Load balancer", variant: normal, filters: [checkout] }
      - { id: api, order: 2, status: INTERNAL, title: "API",           variant: normal, filters: [checkout] }
      - { id: db,  order: 3, status: INTERNAL, title: "Database",      variant: store,  filters: [checkout] }
  - id: external
    title: "External"
    variant: danger
    order: 2
    layout: { row: 1, span: 1 }
    columns: 1
    components:
      - id: pay
        order: 1
        status: EXTERNAL
        title: "Payments provider"
        description: ["raw card data", "no vault"]
        variant: crit
        variant_extra: [ext]
        filters: [checkout]
```

The sketch and the YAML carry the same structure — that is the whole point: the
conversation, not the authoring, is where the diagram is actually decided.

## Repository layout

```
<repo>/
├── index.html            entry + template (design-system CSS inline)
├── engine/
│   ├── engine.js         render engine — knows only the dialect
│   ├── build-data.mjs     build step: YAML → data/data.generated.js
│   └── DIALECT.md         the dialect contract + glossary
├── data/
│   ├── document.yaml     manifest: title/subtitle + which pages, in what order
│   ├── pages/            one YAML per page
│   └── data.generated.js build output (committed; `window.__DOC__ = {...}`)
└── tools/verify.mjs      headless render QA (geometry assertions + screenshots)
```

The engine layer (`engine/` + `index.html`) is generic and knows nothing about
the diagram's domain; every domain string lives in `data/`. That seam is what
makes the engine reusable across diagrams. `js-yaml` (build) and `playwright`
(QA) are devDependencies — the shipped artifact has zero runtime dependencies.

## The manifest (`data/document.yaml`)

```yaml
title: "Deck title"          # required
subtitle: "…"                # optional
version: "0.1.0"              # optional — free-form (semver recommended)
pages:
  - id: current-state        # required — must match page.id in the file
    name: "Current state"    # required — visible label (rename without breaking refs)
    order: 1                 # required — decknav position
    visible: true            # required — false omits from build without deleting
    file: pages/current-state.yaml   # required — path relative to data/
```

The manifest is the single source of **which** pages exist, in what order, and
whether they show. `name` / `order` / `visible` live **only** here — the page
file must not repeat them. `page.id` lives in both (as a cross-reference the
build validates). The build discards `visible: false` pages, sorts the rest by
`order`, then merges each file.

`version` is a plain passthrough: `build-data.mjs` copies it onto
`window.__DOC__.version` with no default and no validation, and `engine.js`
renders it in the header — after the subtitle, in the same mono/muted style,
one size down — only when the field is present (`if (barVer && doc.version)`).
Omit it and the header shows nothing where it would go; the `.ver` node stays
empty and `:empty` collapses it in index.html, so an older deck with no
`version` degrades with zero visible change. See the versioning rule in
`SKILL.md` for when to bump it.

## The page file (`data/pages/<id>.yaml`)

```yaml
id: current-state       # required — matches the manifest entry
layout: grid            # engine selector: grid (recommended) | svg (legacy)
columns: 5              # top-level MOSAIC column count — presence activates mosaic mode
filters: [ … ]          # optional — overrides/extends the document's
sections: [ … ]         # required, ≥ 1
```

### section

```yaml
- id: gcpenv            # required, stable slug
  title: "GCP environment"
  subtitle: "…"         # optional
  variant: envelope     # normal | danger | safe | envelope
  order: 2              # collapse order (single-column stack)
  layout: { row: 1, span: 3 }   # placement in the mosaic (or in a banded envelope)
  columns: 2            # content grid width (default 2); on an envelope → BANDED mode
  wraps: [gke, databox] # envelope only — ids of the sections it contains
  subsections: [ … ]    # OR
  components: [ … ]      # loose components with no subsection
```

### subsection

```yaml
- id: dev-gke-standard
  label: "DEV-GKE-STANDARD"      # required
  sublabel: "v1.35 · ~9 nodes …" # optional
  columns: 3                     # grid width (default 2)
  components: [ … ]
```

### component

```yaml
- id: apiserver         # required, STABLE slug (data-k / edit-mode key)
  order: 1              # explicit position; falls back to list order
  status: EXPOSED       # kicker badge — see GLOSSARY enum
  title: "GKE control plane"
  description:          # string, or a list where each item is a line
    - "API server · public endpoint"
    - "no authorized networks"
  detail: "Long <b>HTML-allowed</b> text for the click panel."  # falls back to description
  note: "⚠ …"          # optional warning note, shown separately
  variant: crit         # normal | crit | warn | ok | strong | ext | store
  variant_extra: [ext]  # optional second class, composed (e.g. box ext)
  span: 2               # occupy N of the subsection's columns (default 1)
  filters: [expose]     # keys of the filters this component belongs to
```

### filter

```yaml
- key: expose           # slug referenced by component.filters and the chip
  label: "Internet exposure"
  steps:                # optional flow text shown when the chip is clicked
    - "Only three surfaces are reachable from the internet."
```

Highlight is **component-owns-its-tags**: the engine builds an inverse index
`filterKey → [ids]` by walking the tree, so you never maintain a central node
list. A whole zone can light up either because its components declare the filter
or because the section declares `filters` of its own.

## Engine gotchas

These are the behaviors that bite if you author against intuition instead of the
engine:

- **Top-level `section.layout.span` defaults to the section's effective columns,
  not to 1.** A 3-column section placed in the mosaic without an explicit span
  occupies 3 mosaic columns so its content fits. Set an explicit `span` only to
  override that.
- **For a declared mosaic row to materialize, the spans in that row must sum to
  `page.columns`.** The engine emits only `grid-column: span N` and lets CSS
  auto-flow wrap row by row in DOM order (= `order`); `row` is authoring intent
  and an edit-mode anchor, not an emitted grid line. If a row's spans don't sum
  to the column count, cells wrap where you didn't expect.
- **The column is derived, never declared.** There is no `layout.col`. A cell's
  start column is the running sum of the spans before it in its row. To move a
  cell to the left of a lower band, give it a new `row` and make it first in that
  row's `order` — do not look for a column field.
- **Effective columns = `min(columns ?? 2, item count)`.** A section that
  declares `columns: 3` but holds one component renders one full-width column,
  not a narrow box in a third of the space.
- **A content section with direct components honors its `columns` (default 2).**
  Two loose components sit two-up unless you set `columns: 1`. (The DIALECT §2
  legacy note that direct-component sections are forced to a single column is
  superseded by the canonical §0 behavior — the framework's `verify.mjs` asserts
  the two-up layout.)
- **Envelope shapes are chosen by `wraps` + `columns`:** flat `wraps` list =
  one wide `.env-main` + a stack of `.env-side`; list-of-lists = N equal columns;
  flat list **plus `columns: N`** = BANDED — the children become a mosaic inside
  the dashed border, each carrying its own `layout: {row, span}`, rendered as
  measured flex-rows (not CSS grid, because the children are height-variable
  zones).
- **Only horizontal span exists.** Vertical span lives on the block axis and is
  deferred; do not author it.
- **Sizing is intrinsic — columns win, width never collapses them.** A section is
  rendered wide enough to sustain its columns; when content is wider than the
  viewport the diagram grows and the canvas **scrolls horizontally** rather than
  dropping columns. "Go vertical" is the author's explicit `columns: 1`, not
  something the width forces.
- **Responsive collapse is coarse and diagram-wide.** Below `--bp-tablet`
  (768px) the mosaic stacks to one column and multi-column grids cap at 2; below
  `--bp-phone` (480px) every grid drops to 1. There is no per-zone collapse.
- **`page.layout` vs `section.layout` are different objects.** The first selects
  the render engine (`grid` | `svg`); the second is `{row, span}` placement. An
  `svg` page is legacy positional and is not data-driven the same way — prefer
  `grid`.
- **`data.generated.js` is generated and committed.** It is a plain
  `window.__DOC__ = {…}` assignment, loaded by a normal `<script src>` so the
  deck works under `file://` with zero fetch/CORS. Never hand-edit it; regenerate
  after any YAML change.
- **Ids are stable kebab-case, reused where they already exist.** They are the
  `data-zone` / `data-k` the engine emits and the anchor a future edit mode would
  overlay via `localStorage` — never rewriting the YAML.

## The five authoring modes

**Before scaffolding, confirm the destination.** For a new repo, adding to an
existing repo, or a new page, *ask the user where the project (or file) should
live* and write there — never assume a path. Confirm the destination before
writing anything.

### Mode 1 — New repo

Scaffold the engine layer, then seed one document.

1. Copy the vendored engine layer from `assets/` (`index.html`, `engine/`,
   `package.json`, `tools/verify.mjs`, and the seed `data/`) into the new repo.
2. Create `data/document.yaml` with `title`/`subtitle` and one page entry.
3. Create `data/pages/<id>.yaml` with `id`, `layout: grid`, `columns`, and a
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

### Mode 4 — New section / zone

1. Add a `section` to the page's `sections`, with a stable `id` and a `variant`.
2. Place it: give it `layout: {row, span}` and pick `columns`. Remember the
   row's spans should sum to `page.columns`.
3. If it is a container, set `variant: envelope` and list `wraps` (add `columns`
   for banded mode).
4. Build → verify.

### Mode 5 — Add / edit components

1. Add `component` entries to a section's `components` or a subsection's
   `components`.
2. Give each a stable `id`, a `status`, a `title`, a `description`, and a
   `variant`; set `span` only to widen it, `filters` to tie it to a flow.
3. Build → verify — check the effective column count is what you intended
   (`min(columns, items)`).

## The build → verify loop

A diagram is not done until it has been rendered and *seen*. The build exiting
cleanly is not verification.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data:
   ```
   npm run build      # node engine/build-data.mjs → data/data.generated.js
   ```
   This is a local file write (reads the manifest, skips `visible: false`, sorts
   by `order`, merges each page). Re-run after every YAML change.
3. **Verify by looking.** Load `Skill('visual-verify')` and render `index.html`
   as a `file://` URL, capturing across a spread of widths (desktop down to
   ~380px) and **both** themes, then read every screenshot. Because the engine
   scrolls horizontally instead of collapsing columns, a layout that reads at
   1440px can overflow — reading the pixels is the honest check. The project's
   own `npm run verify` (Playwright geometry assertions + screenshots) is a
   useful T1 gate for zone collisions and band placement, but it asserts
   geometry, not legibility; the visual read is what closes the loop. Both the
   visual-verify capture and `npm run verify` write their PNGs to a **system temp
   dir, never inside the project** (`tools/verify.mjs` uses `os.tmpdir()`, override
   with `DIAGRAM_SHOTS_DIR`) — nobody reuses verification shots, so the scaffolded
   repo carries no images folder.
4. **Loop on any defect** — overflow, clipping, collisions, a row that wrapped
   wrong, contrast failing in one theme. Fix the YAML and rebuild.

## Feasibility, transparency, capability

Two disciplines frame every run of this skill, both universal: validate
feasibility before investing, and be transparent about what you run.

### Feasibility first (validate step by step, early)

Detect what the environment offers and reason about it **one thing at a time**,
early and incrementally — *"can I run this script? how far can I get?"* — and say
it, **before** investing in a full ASCII sketch or a build. This is just good
method: an orchestrator that already knows its environment satisfies it trivially,
but the skill still asks first, so you never draw the whole diagram and only then
discover you cannot execute. The order is **feasibility → define → invite → agree
→ build**, not the reverse.

1. **Read what's available, reason one at a time:**
   - **A browser** — *view* the diagram.
   - **Node + npm** — *rebuild* the diagram from YAML.
   - **Playwright** — *auto-verify* the render (optional; its absence never blocks
     — a browser still lets you and the user look).
2. **Say it plainly:** which commands are possible and what each buys, WHERE the
   data lives (`data/`), and WHICH script loads it (`engine/build-data.mjs`). E.g.
   *"You have Node → I can run `npm install` + `npm run build` to regenerate the
   diagram from your YAML; your data lives in `data/`, loaded by
   `engine/build-data.mjs`. No Playwright, but you can see the result by opening
   `index.html`. Shall I go on?"*
3. **Confirm the destination before writing.** When about to scaffold (new repo /
   add to a repo / new page), ask *where* the project should live and write there
   — never assume a path.

### Explain before you execute

Before running ANY script — the build or the verify — say, in one short,
non-technical sentence: (a) what the command does, and (b) which file the user can
open to inspect it first. It is a core practice of the skill; do it every time.

- Build: *"I'll run `npm run build` (which runs `engine/build-data.mjs`) to
  regenerate the diagram data from your YAML — you can read that script at
  `engine/build-data.mjs` first."*
- Verify: *"I'll run `npm run verify` (which runs `tools/verify.mjs`) to render
  the pages headlessly and screenshot them — you can read that script at
  `tools/verify.mjs` first."*

Keep it short and plain. The point is informed consent by a human who can see
exactly what will run and where to check it.

### Capability and graceful degradation

State what each capability needs as a fact, so "I just want to look at it" never
gets blocked on a toolchain:

| Goal | Needs | Notes |
|------|-------|-------|
| **View** the diagram | A browser | `data/data.generated.js` is committed, so it renders with zero tooling, even under `file://`. |
| **Rebuild** after editing `data/` | Node + `npm install` + `npm run build` | Regenerates `data/data.generated.js` from the YAML. |
| **Auto-verify** the render | Playwright (+ a Chromium) | `npm run verify` renders every page and screenshots widths × themes **to a system temp dir** (`os.tmpdir()`, override `DIAGRAM_SHOTS_DIR`) — never into the project. |

Degradation is graceful: with only a browser you can always view the
already-generated diagram; rebuilding after edits is what adds Node, and
auto-verification is what adds Playwright.

### Why the engine stays minimal and data-driven

The engine and template are **minimal and carry no baked-in data** — every domain
string lives in `data/`. That is the real point behind the capability list: a
scaffold that leaks nothing from the diagram it came from, stays generic, and only
*scales* to new content. Keep it that way — put content in `data/`, never in the
engine or template.
