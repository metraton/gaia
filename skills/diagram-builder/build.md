# Diagram Builder — build

This file is for the subagent that BUILDS the deck. The orchestrator has
already agreed a sketch with the person and chosen the pieces
([SKILL.md](SKILL.md), "The map"); it hands you that sketch and the values each
piece carries. Your job is the part the person never sees: lower the sketch
into YAML, run the checks, and return three things:

1. the YAML you wrote under `data/`;
2. the check results, verbatim;
3. the census as JSON (`npm run census --prefix <assets> -- --json`), which the
   orchestrator holds against the sketch.

You own the schema; the orchestrator owns the meaning. When the sketch asks for
something no field can say, report it as a finding — do not invent a field
(the build refuses an unknown key anyway) and do not quietly pick another
piece.

`<assets>` below is the deck's root: the directory holding `package.json`,
`index.html`, `engine/`, `data/` and `tools/`. In the skill itself it is
`assets/`.

## The build lane

The deck has no dependencies: Node alone runs every script, with no
`npm install` (the YAML is read by `engine/yaml.cjs`, the skill's own reader of
its dialect). Always spell a script as `npm run <script> --prefix <assets>` —
the script first, the prefix after. That is the spelling Gaia's command lane
was measured on; do not move `--prefix` in front of `run`.

| Script | Runs | What it proves | In Gaia |
|--------|------|----------------|---------|
| `npm run build --prefix <assets>` | `engine/build-data.mjs` | Regenerates `data/data.generated.js` and `data/breakpoints.generated.css` from the YAML. It is also the strict-schema gate: an unknown key, an out-of-set value, a broken lead band or an unpaired `half` fails here, by name. | writes files: request it for signature |
| `npm run model --prefix <assets>` | `tools/check-layout.mjs` | The layout, as arithmetic over the authored YAML at five container tiers: the rectangle closes, no interior hole, no dead track, chips have two ends, text fits at the presentation viewport. A run that asserted nothing is red. | reads: runs free |
| `npm run census --prefix <assets> -- --json` | `tools/census.mjs` | What each page IS, in reading order: its sections and how they nest, the width the grid really gives every node, the variants, and every chip with its members — each named by the id the YAML authored. It exits non-zero when the last build no longer matches the YAML. | reads: runs free |
| `npm run contrast --prefix <assets>` | `tools/contrast-audit.cjs` | Every foreground/background pair of every palette, and of any `palette_overrides`, against WCAG 2.1. | reads: runs free |
| `npm run test --prefix <assets>` | `tools/test-guards.mjs` | The negative suite: it fabricates broken decks in the system temp dir and proves each guard fails on the defect it claims to catch. | writes temp files: request it for signature |

**The census is the summary the orchestrator reads back.** It is not a check
you pass or fail: it states the deck the way the sketch states it — how many
sections, which sits inside which, how wide each is, which colour, which chip
lights what. Hand it over whole; the orchestrator names every difference from
the sketch before the person sees the deck.

**The loop.**

1. Edit the YAML under `data/`.
2. `build`. Re-run it after every YAML change: the bundle is what the browser
   draws, and a stale bundle renders the old deck without complaint.
3. `model`. Never return a layout change on red. Findings are grouped per
   check; `[FAIL]` exits non-zero, `[INFO]` advises and never fails, and
   `[NOT ASSERTED]` is counted in the headline — a non-zero count is not green.
4. `census --json`. Its `problems` list must be empty.
5. `contrast`, when the palette, an override or a hue changed.
6. `test`, when you touched a guard, the placement model or the engine.
7. On a failure, read the finding (it names the page, the grid, the tier and
   the measured value), fix the YAML, and go back to step 2.

**What you return as the verdict.** Name what each check established and what
none of them can: "MODELLED: 8708 assertions, 0 not asserted · CENSUS: attached,
no problems · CONTRAST: pass · SEEN: pending, the person looks." The layout
checks reach the geometry and the data; whether a section is the right section,
whether a colour says one thing, whether the page reads — no script reaches
that. The person decides it by opening `index.html` and looking. Do not open a
browser for the diagram, do not take screenshots of it and do not write a
browser probe: the diagram has no browser check, on purpose.

**Explain before you execute.** Before running a script, say in one plain
sentence what it does and which file it runs — *"I'll run
`npm run build --prefix <assets>`, which runs `engine/build-data.mjs`, to
regenerate the deck from your YAML"* — so whoever reads the transcript can open
the script first.

**The RATCHET rule.** A defect the person's eye catches becomes a check before
the change closes, in `tools/check-layout.mjs` when it is a statement about the
data, with a case in `tools/test-guards.mjs`. A new check is not landed until it
has been SEEN TO FAIL: break the rule it guards, watch `test` go red, restore
it. A check that has never failed is a comment with a pass count.

## Vocabulary

The canonical terms of the dialect. The engine (`engine/engine.js`) reads
exactly these; every domain string lives in `data/`, and the deck's help panel
documents the same terms.

The whole layout model is **two primitives**: a recursive **section** (a grid
with `columns`, `span` and `children`) and a **component** (a leaf with a
`type`). There is no envelope primitive, no subsection, no mosaic, no `wraps`,
no `layout.row`, no layout modes.

| Term | Where | Meaning |
|------|-------|---------|
| `document` | `data/document.yaml` | The whole deck: `title`, `subtitle`, `version`, `look` (a key of `LOOKS` in `engine/tokens.mjs`: it picks the palette and a token set, so it excludes `palette`; see [look.md](look.md)), `palette`, `palette_overrides`, `tokens`, `filters` (the core chips), `harmony`, `pages` (`MANIFEST_FIELDS`). Each `pages[]` entry carries `id`/`name`/`order`/`visible`/`file` and `omit_filters` (`MANIFEST_PAGE_FIELDS`). |
| `page` | `document.pages[]` | One act. It IS the root section: owns `columns`, `filters` (its page chips), `sections` (the root's children), `form` and `text_fit`, plus the manifest-owned `name`/`order`/`visible` (`PAGE_FIELDS`). `layout` is deprecated: `grid` is its only value and the build warns where it appears. |
| `section` | any node with `children` | A grid zone: `id`, `title`, `subtitle`, `variant`, `treatment`, `order`, `span`, `rowspan`, `columns`, `tokens`, `children` (`SECTION_FIELDS`). A child may itself be a section: a grid of grids. |
| `component` | any leaf | Chooses a `type`: `box` (default) · `separator` · `rail` · `spacer` (`COMPONENT_TYPES`). Whitelist `COMPONENT_FIELDS`; a rail has `RAIL_FIELDS`; a spacer `SPACER_FIELDS`. |
| `filter` (chip) | `document.filters[]` · `page.filters[]` | A RELATION: `key`, `label`, `steps` (`FILTER_FIELDS`). It groups every box and rail that declares its `key` — a directed path (the substitute for an arrow) or a cross-cutting concept. |
| core chip | `document.filters[]` | Inherited by every page, first, in order, with one label (`engine/chips.cjs`). A page drops one only through its manifest entry's `omit_filters`. |
| page chip | `page.filters[]` | Local to one page, after the core chips. CHIP-X fails a key carrying two labels across pages. |
| `harmony` | `document.harmony` | Opt-in: HARMONY fails any box or rail in no chip, the lead band exempt. |
| lead band | `lead: true` on a box | The page's claim: first root child, spanning every root column; title = the claim, kicker = its place in the arc. |
| `form` | page | `dashboard` (default) · `timeline` · `flow` · `comparison` · `mindmap` · `planner`. It scopes which checks apply; FORM fails an undeclared or unknown value. |
| `cell` | engine behaviour | The base unit: an equal `fr` share of its grid's width × a fixed `--cell-h` (130px), never below `--cell-min-w` (120px). A cell never grows by content, only by merge. |
| `slot` | engine behaviour | The rectangle the grid places. `rowspan` makes a component a multiple of it; a `half` pair shares one. |
| separator row | engine behaviour | The one row not `--cell-h`: a row holding only horizontal separators and spacers resolves to `--sep-row-h` (40px). |
| `band` / `inline` | section in a compound grid | Inline (`span: 1`) takes one column and stretches; a band (`span == columns`) owns its row. Band-ness is tier-relative (see "The placement model"). |
| collapse cascade | engine behaviour | A leaf grid's columns step …→2→1 as the stage narrows (≤1000px, ≤640px); below 1440px compound rows stack. Nothing scrolls sideways. |

**Content slots.** `kicker` is the small uppercase mark above the title — open
vocabulary, no enum: a step, a code, a phase, `MEASURED`, `OPTION A`. `title` is
the loud line (where a number goes when the number is the message).
`description` is a string or a list of lines, clamped. `detail` is long,
HTML-allowed text for the click panel, falling back to `description`. `note` is
a warning shown separately in the panel. `steps` is a chip's narration.
`subtitle` is the muted line under a section title. `id`/`key` are stable
kebab-case slugs. `version` is a free string shown after the subtitle.

### `variant` (the colour) and `treatment` (the structure)

Two independent axes, both closed and validated by the build: a structural
value written into `variant` is a hard error naming the axis it belongs to, and
the reverse.

| axis | question | cardinality |
|------|----------|-------------|
| `variant` | What does this MEAN? the colour role | exactly one value |
| `treatment` | How is this DRAWN? structural modifiers | a list; they compose |

**Component variants** (`COMPONENT_VARIANTS`): the six semantic roles carry a
verdict — `neutral` (no class), `bad` (red: exposed, high risk), `warn` (amber:
medium risk), `good` (green: hardened, correct), `accent` (2px green border: a
highlighted new component), `muted` (secondary fill). The four categorical hues
`blue` · `violet` · `gold` · `clay` carry none: they tell up to four peers
apart, the page declares what each means, and the deck keeps that meaning on
every page. They are also the only rail variants. Role names and CSS tokens
differ on purpose — `bad` paints with `--crit`, `good` with `--olive`, `accent`
with `--strong`: author the role, read the token only as CSS evidence. `crit`,
`ok`, `strong`, `store`, `danger`, `safe` are no longer values.

**Section variants** (`SECTION_VARIANTS`): `neutral` (dashed zone), `bad`, `good`.

`variant_extra` is deprecated: a second colour role on one frame competes for
one fill and one border. It still builds, with a warning; say the second claim
in the kicker, a treatment or a legend band.

**Component treatments** (`COMPONENT_TREATMENTS`):

| value | what it does |
|-------|--------------|
| `centered` | centres the text block |
| `outside` | dashed frame, no colour at all — border STYLE only, which is why it is a treatment |
| `half` | divides a slot: a pair of consecutive halves stacks in one full-height cell. Title-only, exclusive with `rowspan`; runs must be even and a pair must agree on `span` (`checkHalfPairing`) |
| `vertical` | runs the text down the block axis (a rotated lane label); title-only |

**Section treatments** (`SECTION_TREATMENTS`; `half` is refused on a section):

| value | what it does |
|-------|--------------|
| `envelope` | no fill, dashed border: a container frame around nested sections |
| `plain` | no frame, no padding: a pure structural wrapper |
| `middle` | centres the grid vertically in the height its compound row stretches it to |
| `compact` | shortens the rows of ONE leaf grid (`tokens.row.compact_h`) for a rowspan staircase; children must all be components; boxes lose the description clamp and INK measures the whole description against the short row |

### `palette` — the skin

`palette` selects the token set from the closed `PALETTES` set. The semantic
roles are identical in every palette, so switching it changes how the deck
LOOKS and never what it MEANS. Absent means `neutral`.

| value | skin |
|-------|------|
| `neutral` | the house palette, the default |
| `rose-pine` | Rosé Pine — Dawn on light, Main on dark |
| `rose-pine-moon` | the same, with Moon on dark |
| `contrast` | high contrast, for projecting in a room |

## The layout model

A **section** renders as a CSS grid `columns` wide; its children auto-flow
left→right and wrap down. A **component** renders by its `type`. Merges run on
two axes: `span: M` merges across M of the parent's columns, `rowspan: K` merges
a leaf cell down K rows. The page is itself a section (`page.columns`,
`page.sections`).

**It is a filled, capped grid of uniform-height cells.** A leaf grid divides
its width into `columns` EQUAL `fr` tracks
(`repeat(N, minmax(var(--cell-min-w),1fr))`): cells stretch, so a row reaches
the right edge and cells within one grid are equal width. Rows are a fixed
`--cell-h` (130px). The root plane fills up to a 1280px cap, then centres. The
gutter is one token (`gap: var(--s-2)` = 8px). Positioning is a known
operation: change `columns`/`span`/`rowspan`/`order`, build, and `model` proves
the rectangle closes.

- **Readable minimum.** `--cell-min-w: 120px` floors every leaf track, so a
  compound parent stacks its sections before a cell degrades; LEGIBLE fails a
  cell the model places below it.
- **Compound grid.** A grid holding at least one section is a flex-wrap row
  whose children grow equally: N sections are N equal-width, equal-height
  slices, weighted by `span`.
- **Grow-with-content.** A leaf grid's effective columns are clamped to what
  its children can fill, so an over-authored `columns` never reserves a dead
  track. The clamp is `buildGrid` in `engine/engine.js`, mirrored by
  `effectiveCols` in `tools/check-layout.mjs`; TRACK guards it.
- **Inline vs band.** An inline section (`span: 1`) takes one column of its row;
  a band (`span == parent columns`) takes its own row and its cells fill it edge
  to edge.

### Positioning recipes (idea → columns/span/order)

- **A base band at the bottom:** `span: <parent columns>`, placed last in `order`.
- **Two sections side by side:** `span: 1` each, consecutive in `order`.
- **Wider than one column but not the row:** `span: M`, `1 < M < columns` — a
  real partial merge that keeps its proportion as the grid collapses.
- **Height as magnitude, or a label down several rows:** `rowspan: K`.
- **A full-width divider or lane label inside a band:** a `separator` or `rail`
  with `span == the section's columns`.
- **"Make this a 3-column section":** the section's own `columns: 3`.
- **"Move this above/below that":** change `order`; there is no coordinate.

## Repository layout

```
<repo>/
├── index.html              entry + template (design-system CSS inline, help panel)
├── package.json            the five scripts; no dependencies
├── engine/
│   ├── engine.js           render engine — knows only the dialect
│   ├── build-data.mjs      YAML → data/data.generated.js; the strict schema
│   ├── yaml.cjs            the reader of the skill's YAML dialect
│   ├── tokens.mjs          DEFAULT_TOKENS and their schema
│   └── chips.cjs           core + page chip resolution, shared with the census
├── data/
│   ├── document.yaml       manifest: title, palette, tokens, core chips, pages
│   ├── pages/              one YAML per page
│   ├── data.generated.js   build output (committed; `window.__DOC__ = {...}`)
│   └── breakpoints.generated.css   build output (committed)
└── tools/
    ├── check-layout.mjs    `model`: the layout as arithmetic, per check
    ├── static-census.cjs   the one parse path every tool shares
    ├── census.mjs          `census`: what each page is, for the orchestrator
    ├── contrast-audit.cjs  `contrast`: WCAG over every palette pair
    └── test-guards.mjs     `test`: the negative suite
```

The engine layer is generic; every domain string lives in `data/`. The shipped
deck has no runtime dependency and no build dependency.

## The manifest (`data/document.yaml`)

```yaml
title: "Deck title"          # required
subtitle: "…"                # optional
version: "0.1.0"             # optional — free-form
palette: neutral             # optional — neutral | rose-pine | rose-pine-moon | contrast
palette_overrides:           # optional — the deck's own colour for a palette token
  light: { hue-blue: "#1f4e8c" }
  dark:  { hue-blue: "#8ab4f8" }
tokens:                      # optional — over the defaults (see "Tokens")
  viewport: { w: 1920, h: 1080 }
filters:                     # optional — the CORE chips, inherited by every page
  - { key: gate, label: "Which boxes are the gates?" }
harmony: false               # optional — true: HARMONY fails a box/rail in no chip
pages:
  - id: overview             # required — must match page.id in the file
    name: "Overview"         # required — visible label
    order: 1                 # required — position in the deck
    visible: true            # required — false omits it from the build
    file: pages/overview.yaml   # required — relative to data/
    omit_filters: [gate]     # optional — core chips this page does not carry
```

**Palette overrides.** `palette_overrides` replaces a colour token of the
chosen palette, per theme (`light` | `dark`). The keys are a closed set, written
without `--`: `bg`, `surface`, `surface2`, `zone`, `ink`, `body`, `muted`,
`line`, `zone-line`, `crit`, `crit-soft`, `warn`, `warn-soft`, `olive`,
`olive-soft`, `strong`, `strong-soft`, `clay`, `clay-soft`, and
`hue-{blue,violet,gold,clay}` with their `-soft` tints. A value is `#rgb`,
`#rrggbb`, `rgb()` or `rgba()`. The build refuses an unknown theme, a key
outside the set (with the nearest key as a hint) and a value that is not a
colour. `contrast` re-measures every pair an overridden token enters, and those
pairs gate even on `neutral`: an override is a new colour and inherits no
exemption.

**Core chips.** `filters:` here are validated like a page's chips; each page's
list is the core chips first, in order, minus its `omit_filters`, then its own.
The build refuses an `omit_filters` key that is not a core chip, a page chip
that redeclares a core key with another label or steps, and a page chip that
reuses an omitted core key. An inherited chip with no member on the page fails
CHIP, so omission is always explicit.

The manifest is the single source of which pages exist, in what order, and
whether they show; `name`/`order`/`visible` live only here. `page.id` lives in
both, and the build checks the match.

`tokens.viewport` is the screen the deck is SHOWN on. It decides the tier where
a title, description or section header past its clamp FAILS `model` (every
other tier advises), and the height each page is compared against (HEIGHT).

## The page file (`data/pages/<id>.yaml`)

```yaml
id: overview            # required — matches the manifest entry
columns: 2              # ROOT grid width (default 2)
form: dashboard         # optional — dashboard | timeline | flow | comparison | mindmap | planner
filters: [ … ]          # optional — the page chips
text_fit: strict        # optional — strict (default) | advisory
sections: [ … ]         # required, ≥ 1 — the root's children
```

`text_fit: advisory` is the one opt-out from text-fit failures: TEXT and INK
keep every finding as `[INFO]`. Use it for a page honestly read in the detail
panel, never to silence a cut sentence.

### section (any node with `children`)

```yaml
- id: system            # required, stable slug
  title: "Example system"
  subtitle: "…"         # optional
  variant: neutral      # colour: neutral | good | bad
  treatment: [envelope] # structure: envelope | plain | middle | compact
  order: 3              # position among siblings + collapse order
  span: 2               # M of the PARENT's columns (default 1)
  columns: 2            # this section's own grid width (default 2)
  children: [ … ]       # sections and/or components, mixed freely
```

A child is a section if it has its own `children`, otherwise a component.

### component — box (default `type`)

```yaml
- id: api               # required, stable slug
  order: 1
  kicker: INTERNAL      # open vocabulary
  title: "API"
  description: ["handles requests from the web app"]
  detail: "Long <b>HTML-allowed</b> text for the click panel."
  note: "⚠ …"
  variant: neutral      # neutral | good | warn | bad | accent | muted | blue | violet | gold | clay
  lead: true            # optional — this box is the page's lead band
  treatment: [centered] # centered | half | vertical | outside
  span: 2               # 1 < M < columns is a real partial merge
  rowspan: 2            # K rows tall: height as magnitude
  filters: [flow]       # the chips this box belongs to
  copy: true            # copy button: true copies the title, a string copies that string
```

**`lead: true`.** The build (`checkLead`) refuses it unless it is a box, a
direct child of the root, first in effective `order`, with `span` equal to the
root's `columns`, and neither `half` nor `vertical`. HARMONY exempts it.

```yaml
columns: 2
sections:
  - { id: lead, order: 1, span: 2, lead: true, kicker: "PART 2 OF 5",
      title: "The claim this page makes", description: ["one line of context"] }
  - { id: first-zone, order: 2, span: 2, title: "…", columns: 4, children: [ … ] }
```

**`copy`** is for a box whose title IS what the reader pastes: a command, a
path, an identifier. Only a box may carry it; `copy: true` needs a title.

**The four hues** carry no verdict, so the page that uses them says, in its own
content, what each means — usually a legend band right after the lead band,
one box per hue whose title says what the hue means.

### component — separator (`type: separator`)

```yaml
- { id: sep-1, type: separator, span: 2, style: dotted, text: "An example system" }
```

A thin divider LINE, not a card: horizontal by default, `treatment: [vertical]`
for a vertical rule, `style` `solid` | `dotted`, optional centred `text`. It
divides only WITHIN a section. Not clickable.

### component — rail (`type: rail`)

```yaml
- { id: lane, type: rail, title: "CI/CD", treatment: [vertical], variant: blue, indent: 1, filters: [flow], span: 1 }
```

A title-only LABEL: a lane label, or one word of a tree. `RAIL_FIELDS` is its
whole whitelist. `treatment: [centered]` centres it, `[vertical]` rotates it;
`variant` is one of the four hues only; `indent` 0..3 moves the frame onto the
title one step per tree level while the cell still fills its track; `filters`
makes a chip light it. A horizontal rail without `rowspan` is a THIN row (33px
for one line); RAILT fails a thin rail whose title wraps past two lines,
measured at the rail's own metrics and in the width its `indent` leaves. A
heading is a section title or the lead band, never a rail.

### component — spacer (`type: spacer`)

```yaml
- { id: gap-1, type: spacer, order: 1, span: 1, rowspan: 1 }
```

The declared hole: it occupies its cell and draws nothing. `SPACER_FIELDS` is
its entire whitelist and `checkSpacer` refuses any other key by name — a
payload key means the cell was meant to carry something, and then it is a box.

### filter

```yaml
- key: flow
  label: "Which steps make the flow?"   # the question the chip answers
  steps: ["Chips express relations: click one to light what shares it."]
```

A chip groups every box and rail that declares its `key`. It needs at least TWO
members: CHIP fails a one-member chip, because an active chip dims everything it
does not name. Highlight is component-owns-its-tags: you never maintain a
central list.

## Lowering the map

The map in [SKILL.md](SKILL.md) names the piece; these are its fields.

- **Grouping:** a section with its own `columns`; the parts are its children.
  A pause inside one thing is a `separator` with `span` = the section's columns.
- **Order:** siblings in `order`; for phases, one section per phase in `order`,
  steps inside, `kicker: "STEP n OF m"`, one chip across every phase.
- **Runs across everything:** a chip declared by every member; a core chip in
  `document.yaml` when it crosses pages. When it carries its own content, a
  band: `span` = the root's `columns`.
- **Parallel options:** sibling boxes in ONE section, equal `span`, one
  `variant`, `kicker: "OPTION A"` / `"OPTION B"`, and a section `title` that says
  the choice (`"Pick one: …"`). Never `STEP n OF m`. When each option has parts
  of its own, `form: comparison` with one inline section per option.
- **Measured vs estimated:** the kicker carries it — `kicker: MEASURED` or
  `kicker: ESTIMATE` — and the number goes in the `title`. When a whole page
  must separate them at a glance, `treatment: [outside]` (dashed) on the
  estimated boxes, declared in a legend band, and only if nothing else on the
  deck uses the dashed frame.

## Per-form seed skeletons (idea → form)

Every page declares a `form`. These are the minimum shape each form wants;
paste, then fill.

**dashboard** — a grid of peer zones (the default).

```yaml
form: dashboard
columns: 2
sections:
  - { id: zone-a, title: "Zone A", columns: 2, children: [ {id: a1, title: "…"}, {id: a2, title: "…"} ] }
  - { id: zone-b, title: "Zone B", columns: 2, children: [ {id: b1, title: "…"}, {id: b2, title: "…"} ] }
```

**timeline** — one long row.

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

**flow** — components tied by a chip; `order` reads directionally.

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

**flow — phases as sections** — phases are sibling sections in `order`, steps
are ordered components inside, the path is ONE chip declared by a step in every
phase, and the kicker carries `STEP n OF m` (text the author keeps true: the
engine renders no counter and no arrowhead). Seed: `data/pages/p10-flow-phases.yaml`.

```yaml
form: flow
columns: 3
filters:
  - { key: path, label: "The path", steps: ["Phase 1 → Phase 2, one chip across every phase"] }
sections:
  - id: phase-1
    title: "Phase 1"
    order: 1
    columns: 1
    children:
      - { id: s1, kicker: "STEP 1 OF 4", title: "…", order: 1, filters: [path] }
      - { id: s2, kicker: "STEP 2 OF 4", title: "…", order: 2, filters: [path] }
  - id: phase-2
    title: "Phase 2"
    order: 2
    columns: 1
    children:
      - { id: s3, kicker: "STEP 3 OF 4", title: "…", order: 1, filters: [path] }
      - { id: s4, kicker: "STEP 4 OF 4", title: "…", order: 2, filters: [path] }
```

**comparison** — inline sections side by side.

```yaml
form: comparison
columns: 2
sections:
  - { id: left,  title: "Option A", span: 1, columns: 1, children: [ {id: la, title: "…"} ] }
  - { id: right, title: "Option B", span: 1, columns: 1, children: [ {id: rb, title: "…"} ] }
```

**mindmap** — a central band with symmetric sections around it (the grid
cannot radiate).

```yaml
form: mindmap
columns: 2
sections:
  - { id: core, title: "Central idea", span: 2, columns: 1, children: [ {id: c0, title: "…"} ] }
  - { id: branch-l, title: "Branch L", span: 1, columns: 1, children: [ {id: bl, title: "…"} ] }
  - { id: branch-r, title: "Branch R", span: 1, columns: 1, children: [ {id: br, title: "…"} ] }
```

**planner** — a grid of cards, optionally one chip per plan.

```yaml
form: planner
columns: 3
filters:
  - { key: plan-1, label: "What does plan 1 need?" }
sections:
  - id: board
    columns: 3
    children:
      - { id: card-1, kicker: TODO, title: "Card 1", filters: [plan-1] }
      - { id: card-2, kicker: DOING, title: "Card 2", filters: [plan-1] }
      - { id: card-3, kicker: DONE, title: "Card 3" }
```

## Tokens

Every visual number a deck may tune is a token. `engine/tokens.mjs` holds the
defaults (`DEFAULT_TOKENS`) and the schema; `document.yaml` `tokens:` is merged
over them by the build, which refuses an unknown key (with a near-miss hint), a
value outside its range, and a broken relation (`one < two < stack`; a `min_px`
not above its `max_px`). The resolved set goes to `window.__DOC__.tokens` and
its CSS projection to `window.__DOC__.css_vars`; the engine and every tool read
it from there, none keeps a copy.

```yaml
tokens:
  row: { cell_h: 120 }
  type: { desc: { lines: 2 } }
```

A section may override `row.cell_h`, `type.title.lines` and `type.desc.lines`;
a box the two `lines`. Nothing else is per-node: a per-cell font or spacing
would let one cell stop matching its neighbours. `treatment: [compact]` is sugar
for `row.cell_h: <row.compact_h>` plus its structural rules. The breakpoints are
written to `data/breakpoints.generated.css`, because a container query cannot
read `var()`. TOKENS fails when the `:root` defaults in `index.html` differ from
`DEFAULT_TOKENS`.

| Key | Default | Range | Where | Why it is a token |
|-----|---------|-------|-------|-------------------|
| `row.cell_h` | 130 | 60..400 px | doc, section | the fixed row: title + clamped description |
| `row.sep_h` | 40 | 16..120 px | doc | a divider row |
| `row.zone_min_h` | 180 | 0..600 px | doc | a framed zone's floor |
| `row.compact_h` | 74 | 40..400 px | doc | the row `compact` presets |
| `space.base` | 8 | 2..16 px | doc | the step every gap and padding multiplies |
| `space.scale` | [0.5,1,2,3,4,6,8] | 7 increasing, 0.25..16 | doc | `--s-1`..`--s-7` |
| `frame.v` / `frame.h` / `frame.top` | 28 / 40 / 35 | 0..200 px | doc | the stage's breathing room |
| `frame.narrow` | 8 | 0..64 px | doc | frame padding at the one-track tier |
| `plane_max` | 1280 | 640..7680 px | doc | the content cap |
| `cell_min_w` | 120 | 100..400 px | doc | the legibility floor |
| `type.title.min_px` / `vw` / `max_px` | 15 / 1 / 17 | 11..32 / 0..5 / 11..40 | doc | box title `clamp()` |
| `type.title.lines` | 2 | 1..4 | doc, section, box | the title clamp |
| `type.desc.px` / `lh` | 12 / 1.4 | 10..24 px / 1..2.4 | doc | description line |
| `type.desc.lines` | 3 | 1..8 | doc, section, box | the description clamp |
| `type.kicker.px` / `track_em` | 10.5 / 0.09 | 9..20 px / 0..0.3 em | doc | the kicker |
| `type.section_title.*` | 13 / 0.85 / 14.5 px, 0.1 em, 2 lines | as title | doc | section header |
| `type.section_sub.px` / `lines` | 12 / 3 | 10..24 px / 1..6 | doc | section subtitle |
| `type.rail.px` / `track_em` | 13 / 0.09 | 10..24 px / 0..0.3 em | doc | a lane label |
| `type.rail_hue.px` / `track_em` / `pad_y` | 10.5 / 0 / 10 | 9..24 / 0..0.3 / 0..24 | doc | a hue rail, one word |
| `type.panel.*` | title 19, summary 15, kicker 13 px | 12..48 / 11..32 / 9..24 px | doc | the detail card |
| `indent_step` | 32 | 8..96 px | doc | one rail tree level |
| `dim.box` / `dim.label` | 0.18 / 0.34 | 0.05..0.9 | doc | how far a chip dims the rest |
| `panel.dock` | bottom-left | 4 corners | doc | where the detail card docks |
| `panel.inset` / `width_cols` | 24 / 2 | 0..96 / 1..4 | doc | card inset and width; its height follows its text, capped at 65vh |
| `breakpoints.stack` / `two` / `one` | 1440 / 1000 / 640 | 320..7680 px | doc | the collapse tiers |
| `viewport.w` / `h` | 1920 / 1080 | 320..7680 / 240..4320 px | doc | the presentation screen |
| `default_columns` | 2 | 1..12 | doc | columns when a section declares none |

**Fixed, not tokens:** box chrome (border, radius, inner gap, title margin), the
half-title clamp, the rail indent depth, the metrics the checks assume, WCAG
thresholds and tolerances. `model` still reads the chrome back out of
`index.html` (CSS).

## The strict schema, and migrating an old deck

The build is the one gate every YAML edit passes through. Each node kind has a
whitelist (`PAGE_FIELDS`, `SECTION_FIELDS`, `COMPONENT_FIELDS`, `RAIL_FIELDS`,
`SPACER_FIELDS` in `engine/build-data.mjs`); any other key is a hard error that
names the page, the node and the key, with a near-miss hint (`colummns` →
*did you mean "columns"?*).

A pre-2.1 deck fails the build with the change to make. The legacy spellings:
`variant: plain`/`envelope` (section) → `treatment: [plain]`/`[envelope]`;
`variant: ext` or `variant_extra: [ext]` → `treatment: [outside]`;
`variant_extra: [centered]` → `treatment: [centered]`; `orientation: vertical`
→ `treatment: [vertical]` (delete `orientation: horizontal`); `status:` →
`kicker:`. Colour roles: `crit` → `bad`, `ok` → `good`, `strong` → `accent`,
`store` → `muted`; on a section `danger` → `bad`, `safe` → `good`.

**The engine and the bundle move together.** `index.html`, `engine/engine.js`
and `data/data.generated.js` are one coupled trio: a new engine beside an old
bundle does not fail, it renders the stale deck silently. `build` is the step
that re-pairs them, and CENSUS (in `model`) and WORDS catch a skipped one. The
serving side of the same coupling — the cache-busting placeholder and the
no-cache headers — is in [assets/README.md](assets/README.md).

## Engine gotchas

- **Cells fill; every section is a filled rectangle.** Tracks are equal `fr`
  shares; rows stay `--cell-h` except a separator-only row. That a row CLOSES is
  arithmetic: RECT and TRACK.
- **A cell never grows by content.** The title clamps to 2 lines and the
  description to 3; a cell grows only by whole rows via `rowspan`. Long copy goes
  in `detail`.
- **`span` is a real partial merge.** `span == columns` is a band (`.msp`);
  `1 < span < columns` (`.mspan`) keeps its proportion on collapse
  (`--span2 = round(M/N·2)`) and becomes a band only at the one-column endpoint.
- **`rowspan` is the vertical merge**, exclusive with `half`. `model` exempts
  every row a tall cell touches from RECT's per-row closure and from ROW, and
  reports the taper as `[INFO] rowspan taper`.
- **Order is `order`, else list order**, and it is both the packing order and
  the one-column stacking order. ORDER fails a duplicate effective order among
  siblings: the tie renders by list position and flips under an unrelated move.
- **Filling runs forward.** Nothing goes back to fill the hole a tall cell
  left; whatever belongs beside something tall goes before it.
- **Nesting is free, with no depth limit.** `plain` for a frameless wrapper,
  `envelope` for a dashed container; both are treatments.
- **`data.generated.js` is generated and committed** — a plain
  `window.__DOC__ = {…}` assignment, so the deck opens under `file://` with no
  fetch. Never hand-edit it.

## The placement model

Where a slot lands is DERIVED, never measured: the grid auto-flow is sparse, so
the placement cursor never moves backwards. An item that does not fit in the
tracks left on its row moves down and abandons the rest of that row — which is
how an interior hole is born, and why `model` can find it in the data.

| function | question | answer |
|----------|----------|--------|
| `widthAtTier(span, cols, tracks)` | how many tracks does this slot occupy at this tier? | `span` at the authored tier; `tracks` when `span >= cols`; otherwise `round(span/cols·tracks)` clamped `[1, tracks]` |
| `isBandAtTier(w, tracks)` | does it own its row here? | `w >= tracks` — the resolved width, never the authored span |
| `rowOccupants(items, tracks)` | which row does each slot land on? | the sparse-flow simulation; a rowspan slot occupies every row it covers |

`isBandClass(span, cols)` (`span >= cols`) is a different question — the
authored declaration that makes the engine emit `.msp` — answered at the
authored tier only. A span-3-of-4 at the two-track tier is a band by tier and not
by class.

The engine (`engine/engine.js`) and `model` (`tools/check-layout.mjs`) each
implement this, mirrored on purpose: the engine is a plain browser script under
`file://`, where an ES module is blocked, so there is no module to share.
`test` lifts the engine's own functions out of its source and asserts they
agree with the checker's copy over a corpus of widths and grid shapes, and
proves the comparator can fail. Change the model in the engine and that suite
tells you the checker drifted.

**The separator row.** A row whose occupants are all THIN leaves — horizontal
separators or spacers — resolves to `--sep-row-h` (40px), emitted per collapse
tier (`rowTrackList`, `applyRowTracks`). A vertical separator is never thin (its
ink is the row height), and a row with no occupant keeps `--cell-h`: an
undeclared hole is a defect RECT/HOLE own.

**Half-slot pairing.** `half` DIVIDES a slot: two consecutive halves are wrapped
in one `.half-slot` that occupies the cell. Pairing runs before the
grow-with-content clamp, in both the engine and `slotsOf` in the checker. A
`description` on a half is a build error, so a half cannot clip.

## The authoring modes

**Before scaffolding, ask the person where the deck should live** and write
there — never assume a path.

1. **New repo.** Copy `assets/` whole — `index.html`, `package.json`,
   `engine/`, `tools/` and the seed `data/`. Without `tools/` the deck has no
   way to earn a verdict. Set the manifest's title and one page, write the page,
   run the lane.
2. **Add the engine to an existing repo.** Drop it into its own directory
   (e.g. `diagram/`) so `index.html` does not collide with the host's.
3. **New page.** Add `data/pages/<id>.yaml` and register it in the manifest with
   `name`, `order`, `visible: true`, `file`; the `id` must match.
4. **New section.** Add it to a parent's `children` with a stable `id` and its
   own `columns`; `span` widens it; its own sections nest it.
5. **Add or edit components.** Default `type` is `box`; give each a stable `id`.

Every mode ends with the build lane above.

## The checks `model` runs

`model` asserts what is true of the DATA, per page and per container tier.

| check | fails when |
|-------|-----------|
| **CENSUS** | printed first: `data/*.yaml` and `data/data.generated.js` disagree — the build is stale |
| **WORDS** | an authored string is missing from the bundle (a text edit without a rebuild) |
| **FORM** | a page declares no valid `form` |
| **RECT** | a grid does not close: `Σ(spanCols × rowspanRows) ≠ tracks × rowCount`, short by exactly the hole's area. A short last row at a collapsed tier is `[INFO]`: that is the cascade |
| **HOLE** | an interior empty cell, named by coordinate (`r2c3`), at any tier |
| **TRACK** | a column no slot reaches |
| **ROW** | a lone single-track cell on its own row beside rows of two or more (grid-dense forms) |
| **LANE** | rail-led lanes of unequal length (fails); parallel stacks of unequal depth (advises) |
| **BAND** | a `span` larger than its columns, or a band that does not own its row |
| **TIER** | tracks shrink as the container grows — the breakpoints disagree |
| **LEGIBLE** | a cell the model places below `cell_min_w` |
| **CHIP** | a chip with no member or one member, or a key nobody declared (`all` exempt) |
| **CHIP-X** | one chip key with two labels across pages |
| **HARMONY** | with `harmony: true`, a box or rail in no chip |
| **LIT** | `filters` on a separator or spacer, which never render a membership |
| **TEXT** | a line past its clamp at the presentation viewport on a strict page (other tiers and tokens advise) |
| **HEADER** | a section's title or subtitle past its clamp at the zone's inner width, compound grids included. Its findings print under the TEXT label and follow TEXT's rule: they fail at the presentation viewport on a strict page and advise elsewhere |
| **INK** | a box's stacked lines overflow its row at the presentation viewport; judged to 0.1px, the precision it prints, so a need equal to the row (130.0 of 130.0) passes and one more pixel fails |
| **RAILT** | a thin rail's title past two lines |
| **FROZEN** | an undeclared hole under a `vertical` box whose row a taller sibling sets |
| **HEIGHT** | advisory: a page's predicted height against `viewport.h` — a deck may mean to scroll |
| **ORDER** | a duplicate effective `order` among siblings |
| **CSS** · **SPAN** · **TOKENS** | `index.html` no longer declares the breakpoints, span rules, text metrics or token defaults the checker computes with. No `index.html` at all is `[NOT ASSERTED]`, never a pass |

## Feasibility

Say what the environment offers before investing in a sketch or a build.

| Goal | Needs |
|------|-------|
| **View** the deck | a browser, and the person looking. `data/data.generated.js` is committed, so it opens under `file://` with no tooling. |
| **Build**, **model**, **census**, **contrast**, **test** | Node, nothing else. |

A video of the deck is a separate moment with its own manifest and its own
browser; nothing in this lane needs it.

## Why the engine stays minimal and data-driven

The engine and template carry no baked-in data — every domain string lives in
`data/`. That keeps a scaffold generic: nothing from one diagram bleeds into the
next. Keep it that way.
