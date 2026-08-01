# Diagram dialect — canonical glossary

The source of truth for every term the diagram deck uses. The engine
(`assets/engine/engine.js`) reads exactly these terms; every domain string lives
in the data. This is the shared vocabulary for both phases — discussing and
decomposing a diagram idea with the user, and authoring the YAML — and the
rendered app documents the same terms in its help HUD.

The whole layout model is **two primitives**: a recursive **section** (a grid
with `columns`, `span`, and `children`) and a **component** (a leaf with a
`type`). Nothing else. There is no envelope primitive, no subsection, no mosaic,
no `wraps`, no `layout.row`, no layout "modes" — those are gone.

The grid is a **filled plane of uniform-height cells** you position by merging
cells on **two axes** — `span` (columns) and `rowspan` (rows), exactly like
merging cells in a spreadsheet. This glossary NAMES the terms and their
concrete values (the 130px cell height, the 40px separator row, the 120px
readable floor, the 1280px cap, the 232px reference width); the fill-geometry
MODEL and its derivation (equal `fr` tracks, why the cap centers, why the floor
collapses columns before cells shrink, the collapse cascade) is told once,
canonically, in `reference.md`, proved arithmetically by `npm run check`.

## The three gates

| script | tool | role |
|--------|------|------|
| `npm run check` | `tools/check-layout.mjs` | **MANDATORY.** Arithmetic, no browser, js-yaml only. Proves the rectangle closes: `Σ(spanCols × rowspanRows) === tracks × rowCount`. |
| `npm run validate` | `tools/validate-layout.cjs` | **OPTIONAL REINFORCEMENT.** Renders one width (2560) in Chromium and asserts only what needs pixels. `loadChromium()` resolves Playwright lazily and `main()` prints `SKIPPED (no browser)` and **exits 0** when it is absent. |
| `npm test` | `tools/test-guards.mjs` | The negative-test suite: fabricates broken decks in `os.tmpdir()` and asserts each guard FAILS as claimed. |

`npm run build` (`engine/build-data.mjs`) generates `data/data.generated.js` and
is the strict-schema gate; `npm run verify` / `npm run contrast` are screenshot
and WCAG QA, not layout gates.

## Structural terms

| Term | Where | Meaning |
|------|-------|---------|
| `document` | top level (`data/document.yaml`) | The whole deck: `title`, `subtitle`, `version`, `palette`, `pages` (`MANIFEST_FIELDS`). There is **no** document-level `filters` — chips are page-scoped. Each `pages[]` entry carries `id`/`name`/`order`/`visible`/`file` (`MANIFEST_PAGE_FIELDS`). |
| `page` | `document.pages[]` | One act/slide. It IS the root section: owns `columns` (root grid width), `filters`, `sections` (the root's children), and `form` (the layout form the guardrail scopes its invariants by) — plus the manifest-owned `name`/`order`/`visible` (`PAGE_FIELDS`). `layout: grid` is the only value the engine renders (`LAYOUTS`). |
| `section` | any node with `children` | A grid zone: `id`, `title`, `subtitle`, `variant`, `treatment`, `order`, `span`, `rowspan`, `columns`, and `children` (`SECTION_FIELDS`). Its children auto-flow across `columns` and wrap down. A child may itself be a section — this is how nesting happens (a grid of grids). |
| `component` | any leaf (no `children`) | The unit inside a grid cell. Chooses a `type`: `box` (default) · `separator` · `rail` · `spacer` (`COMPONENT_TYPES`). Whitelist (`COMPONENT_FIELDS`): `id`, `type`, `variant`, `variant_extra`, `treatment`, `kicker`, `title`, `description`, `detail`, `note`, `order`, `span`, `rowspan`, `filters`, `style`, `text`. A `spacer` is narrower: `SPACER_FIELDS` admits only `id`, `type`, `order`, `span`, `rowspan`. |
| `filter` | `page.filters[]` | A highlight chip that expresses a **RELATION**: `key`, `label`, `steps` (`FILTER_FIELDS`). It **groups** every component that declares its `key` — components that share either a directional **FLOW** (the substitute for an arrow, since a grid cannot draw edges) OR a cross-cutting **CONCEPT / state / theme**. Clicking the chip spotlights that relation's membership across the whole canvas. `validateFilters` guarantees the SHAPE at build time; the chip↔component join (both directions) plus ARITY is asserted by **CHIP** in `npm run check` — the render-time invariant **K** that only closed the join is retired. |

**The root/canvas is itself the invisible base section.** `page.columns` is the
root section's column count and `page.sections` are its children — the engine
renders the page by running the exact same `buildGrid` it uses at every deeper
level. There is no special "page layout": the page is section depth 0, with no
frame of its own.

## Component types (the `type` of a leaf)

| `type` | Renders | Props |
|--------|---------|-------|
| `box` (default) | The standard clickable card | `kicker`, `title`, `description`, `detail`, `note`, `variant`, `variant_extra`, `treatment`, `span`, `rowspan`, `filters`. Omit `type` and you get a box. |
| `separator` | A thin divider LINE (not a card) | `treatment: [vertical]` for a vertical rule (horizontal is the default), `style` (`solid` default · `dotted`), optional `text` (an inline centered label). Honors `span`/`rowspan`. Not clickable. |
| `rail` | A title-only swimlane LABEL banner | `title`, `treatment: [vertical]` (rotates the text). Honors `span`/`rowspan` (a vertical rail with `rowspan` labels a lane down several rows). Not clickable. |
| `spacer` | Nothing at all — the **declared hole**: it occupies its cell and draws no frame, no ink, no text (`buildSpacer` → `.spacer`) | `span`/`rowspan` only (plus `id`/`type`/`order`) — `SPACER_FIELDS`. Every other field is refused BY NAME by `checkSpacer`, ahead of the colour/treatment enums: a payload key means the cell was meant to CARRY something, and then it is a box. It is not an empty card. Not clickable. |

> The former `orientation: horizontal|vertical` field is **gone**: it was the same
> switch as `treatment: [vertical]` under a second name, and a parallel field is
> exactly the duplication the two-axis model exists to remove. Absence of the
> treatment IS horizontal. The treatment also works on a **box**, which
> `orientation` never did — new capability, not just a rename.

## Layout terms

| Term | Where | Meaning |
|------|-------|---------|
| `cell` | (engine behavior) | The base unit: an EQUAL `fr` share of its leaf grid's width (equal width within a grid; width varies by section) × a fixed `--cell-h` (130px) height, with a **readable floor** of `--cell-min-w` (120px) — columns collapse before a cell shrinks below it. Cells **never grow by content** — a title + up to 3 clamped description lines always fits, the rest lives in the click panel. A cell grows only **by merge**: horizontally via `span`, vertically via `rowspan`. |
| `columns` | page · section | How many columns **this section's** grid has, **default 2**. A leaf grid renders this many EQUAL `fr` tracks that fill the section width — clamped to what its children can actually fill, so an over-authored count never reserves an empty track. This column count is what **cascades …→2→1** as width tightens (below). |
| `span` | any child (section or component) | An Excel-style **horizontal merge**: occupy M columns of the parent — **same semantics at every level**. Default 1, clamped to the parent's `columns`. `span == columns` makes the child a full-width **band** that takes its own row. `1 < span < columns` is a real **PARTIAL merge** (`.mspan`): it occupies exactly M of the N tracks, keeps its proportion at the 2-track collapse tier (`--span2 = round(M/N·2)`), and becomes a full band only at the 1-column endpoint. |
| `rowspan` | any leaf cell | The **vertical merge**: occupy K rows — K× the cell height (`.mrsp`, `grid-row: span var(--rowspan)`). The base for a cell-graph where a cell's HEIGHT encodes magnitude, or a lane label spanning rows. Column position is untouched by the horizontal cascade; the guardrail exempts row-span cells/rows from the uniform-height, orphan, and edge-fill checks. |
| `form` | page | The page's declared layout FORM: `dashboard` (default) · `timeline` · `flow` · `comparison` · `mindmap` · `planner`. It scopes which guardrail invariants apply (form-scoped families, below) — an invariant tuned to a dashboard does not fail a legitimate timeline. |
| `slot` | (engine behavior) | The rectangle the grid places, which is what invariant **U** asserts at exactly `--cell-h` — **not** the component. The two height treatments legitimately differ from it in opposite directions: `rowspan` is a MULTIPLE of the slot, and a `half` **pair is ONE slot** — two consecutive `half` leaves are wrapped in a single `.half-slot` that IS the grid cell, so both components are excluded from the component-height set and the wrapper is measured instead. |
| separator row | (engine behavior) | The one row track that is **not** `--cell-h`: a row whose ONLY occupants are THIN LEAVES — a HORIZONTAL separator or a `spacer` — resolves to `--sep-row-h` (**40px**), emitted as a per-tier `grid-auto-rows` track list (`rowTrackList`, `applyRowTracks`). A row of rules and DECLARED holes carries no cell-height content, so the track a rule chose not to reach must not set the height of the rule's row; a spacer beside ordinary cells changes nothing (`every` still fails on those cells). A **vertical** separator is excluded on purpose — its ink IS the row height. Recomputed per tier, so a thin row moves with the cascade; a row with NO occupant at all (an UNdeclared interior hole) keeps `--cell-h`. |
| `band` vs `inline` | section as a child of a compound grid | An **inline** section (`span: 1`) occupies one column of the row and stretches to fill it — sections sharing a row are equal-width, equal-height slices. A **band** takes its own full row; consecutive bands stack top-to-bottom. A band spans the **block width** and its inner cells FILL it edge-to-edge (only the zone padding at each side). Being a band is **TIER-RELATIVE**: the test is the width the slot RESOLVES to at that tier, not the authored span (`isBandAtTier(w, tracks) => w >= tracks`, fed by `widthAtTier`). So a partial merge that fills both tracks of the 2-track tier — and any `.mspan` at the 1-column endpoint, where CSS gives it `grid-column:1/-1` — IS a band there. `span >= columns` is only the authored tier's answer. |
| `order` | section · component | Explicit position of a child within its parent's grid AND the single-column collapse order at the narrowest tier. Falls back to list order (stable). Children flow in `order`, packing side by side until a band forces a new full row. |
| the collapse cascade | (engine behavior) | Driven by the STAGE container query (works under split-screen / narrow panes, not the viewport): a leaf grid's `columns` step **down …→2→1** as width tightens. All authored columns at full width, a **2-column "two-table"** intermediate at medium widths (≤1000px for a 3-/4-/5-col grid), a **1-column endpoint** at the narrowest tier (≤640px), where every leaf grid drops to a single cell and the whole page becomes one vertical stack. Below 1440px compound rows fold from side-by-side into a full-width vertical stack. A `columns: 1` section stays 1 at every tier. Cells re-divide the width at each tier (equal `fr`, never below the 120px floor) — so nothing scrolls sideways at the stacked tiers. |
| fill-to-cap | (geometry) | The plane (`.sec-plane`) fills the canvas up to `max-width: 1280px`, then centers (`margin-inline:auto`) — at ≤1280 it fills edge-to-edge; above, the surplus becomes equal side margins. The gutter is one token everywhere (`gap: --s-2 = 8px`); zone padding is `--s-3 = 16px` per side. No fixed cell width: `--cell-w` (232px) is a documented readability reference only. |

## The guardrail families (invariant vocabulary)

Two vocabularies, one per gate. The render guardrail
(`assets/tools/validate-layout.cjs`, `INVARIANTS`) asserts a flat,
**form-scoped** table of single-letter ids — the page's `form` selects which
rows apply. Severity: `dura` fails the build; `consejo` advises, never fails. A
row carrying `superseded` is printed in a `[RETIRED]` list and **never
evaluated**. The static gate (`tools/check-layout.mjs`) asserts WORD-named
checks on the authored data. Full per-invariant detail lives in `reference.md`.

| Family | Ids | Scope · severity |
|--------|-----|------------------|
| **INTEGRITY** (the layout adds up) | **A** page declares a valid form (synthetic, fail-closed: returned INSTEAD of an empty set) · **Z** census (authored == rendered) · **D** determinism (3 reloads) · **R** scrollbar-robust · **T** capture not truncated · **C** no box clipping · **O** no h-overflow · **S** inline fit / band spans block · **B** centered block · **H** header within section · **X** no sibling-section collision · **G** no compound-leaf balloon / no stacked-section overflow | every form, `dura` |
| **GEOMETRY** (`cls: 'geometry'` in code — it measures rendered geometry, not visual design) | **U** cells equal width per grid **+** uniform **slot** height (two rows) · **M** readable ≥120px (`MIN_LEGIBLE`) · **N** word-fit, title token fits its cell (`WORDFIT` = dashboard+flow; `vertical` leaves exempt) · **Y** band fill (≥1200px) · **Q** compound widths follow authored span (≥1200px) | form-scoped, `dura` |
| advisory | **V** horizontal composition (the deck earns its canvas) | `GRID_DENSE`, ultra tier, `consejo` |

**Retired rows** (`superseded`, never evaluated) — each moved to the static gate
because it was a statement about the DATA, not about pixels:

| retired | was | `superseded` by |
|---------|-----|-----------------|
| **L** | cells fill width (no right gap) | `RECT`/`HOLE` (`npm run check`) |
| **F** | collapse cascade …→2→1 (two rows: min + medium) | `TIER` (`npm run check`) |
| **E** | no empty grid column | `TRACK` (`npm run check`) |
| **P** | no orphan cell | `ROW` (`npm run check`) |
| **K** | filter referential integrity | `CHIP` (`npm run check`) — adds ARITY |
| **W** | fixed 232px cell width | `U` (cells now stretch to equal `fr`) |

**The static layer** (`npm run check`, arithmetic, browser-free): `RECT` closure
identity — where a `rowspan` taper exempts rows from the per-row form, a taper
that nonetheless closes states it with the number as an `[INFO]` ("rowspan taper
CLOSES"), because an absence of deficit is indistinguishable from a check that
never ran · `HOLE` interior holes by coordinate · `TRACK` dead track · `ROW`
orphan row · `LANE` unequal rail-led swimlanes (hard) + unequal parallel stacks
(advisory) · `BAND` band placement / span never exceeds its columns · `TIER`
tracks-per-tier table + cascade monotonicity · `CHIP` filter integrity both
directions + arity · `ORDER` duplicate effective `order` among siblings · `CSS`
the mirrored breakpoints (640/1000/1440) match the `@container stage` queries in
`index.html` · `CENSUS` (pre-flight) `data/*.yaml` vs `data/data.generated.js`,
the stale-build detector shared with `validate` via `tools/static-census.cjs` —
its node counts are `sections` · `boxes` · `seps` · `rails` · `spacers` ·
`halves` (`nodeCensus`), a spacer counted on its own arm so it is never recounted
as content.

## Content terms

| Term | Where | Meaning |
|------|-------|---------|
| `id` / `key` | identity | A stable kebab-case slug for a page/section/component. Rendered as `data-zone` (sections) / `data-k` (components); the anchor for `filters` and a future edit mode. Reuse a value where it already exists. |
| `title` | section · component · rail | The heading. On a section it renders as the zone header; on a box it is the bold card title; on a rail it is the whole label. |
| `subtitle` | section | The muted line under a section's title. |
| `description` | `component.description` | Short text shown in a box: a string, or a list where each item is a line/bullet. |
| `detail` | `component.detail` | Long, HTML-allowed text for the click-through detail panel. Falls back to `description`. |
| `note` | `component.note` | A warning-style note (`⚠ …`) shown separately in the panel. |
| `kicker` | `component.kicker` | The small uppercase mark above the title. It means nothing on its own — it is just the mark, so it holds a step, a number, a phase, or a label equally well (open vocabulary, see below). |
| `steps` | `filter.steps` | The relation's explanation shown when a filter chip is clicked. |
| `version` | `document.version` | Optional free-form string on the manifest (semver recommended). Rendered in the header after the subtitle; the node is `:empty`-collapsed when absent, so an older deck degrades with zero visible change. |

## The `kicker` vocabulary (the badge above the title)

The field is `kicker` (`COMPONENT_FIELDS`); the former name `status` is **gone**
— it is now an unknown field, so the strict schema rejects it. `kicker` is
**open vocabulary**: no enum gates it, any free string is accepted, and each deck
picks the words its idea needs. The set below is **one example set** — the
kicker words a security-review deck might use — not a canonical default:

`ENTRY` · `EXPOSED` · `INTERNAL` · `EXTERNAL` · `WEAK` · `NEW` · `HARDENED` ·
`UNCHANGED` · `RISK`

## The two orthogonal axes: `variant` and `treatment`

A component's appearance is decided by **two independent fields**, because they
answer two different questions. Conflating them is what once forced `centered` to
be smuggled in through `variant_extra`:

| axis | question | cardinality |
|------|----------|-------------|
| `variant` | **What does this MEAN?** the semantic COLOUR role, in the idea's own language of risk / state / kind | exactly **one** value — a thing is not both "at risk" and "hardened" |
| `treatment` | **How is this DRAWN?** structural / presentational modifiers, orthogonal to meaning | a **list** — they compose freely, and composing two is normal |

With one closed single-valued field it was impossible to say *"this is at risk"*
**and** *"this goes without a frame"* at once. Two axes make that the default
case. Both enums are **closed** and validated at build time
(`engine/build-data.mjs`); a structural value written into `variant` is a hard
build error that names the axis it belongs to, and vice versa.

### `variant` — the colour role (one value)

> **The role names were renamed; the CSS TOKENS were not.** The enums below are
> the current, closed sets (`COMPONENT_VARIANTS`, `SECTION_VARIANTS`); `crit`,
> `ok`, `strong`, `store`, `danger`, `safe` no longer exist as values and are
> hard build errors. But the tokens they were named after are still spelled the
> old way in `index.html` — role `bad` paints with `--crit`/`--crit-soft`, `good`
> with `--olive`, `accent` with `--strong`. A role and its token disagreeing is
> expected, not a bug: author the ROLE, read the TOKEN only as CSS evidence.

**Component** (`COMPONENT_VARIANTS`: `neutral` · `good` · `warn` · `bad` ·
`accent` · `muted`):

| value | role | CSS evidence (`index.html`) |
|-------|------|------------------------------|
| `neutral` | neutral box, standard border | — (no class) |
| `bad` | red — exposed or high risk | `background:var(--crit-soft); border-color:var(--crit)` |
| `warn` | amber — medium risk / weak config | `background:var(--warn-soft); border-color:var(--warn)` |
| `good` | olive/green — hardened / correct | `background:var(--olive-soft); border-color:var(--olive)` |
| `accent` | marked green 2px border — a highlighted new component | `background:var(--strong-soft); border-color:var(--strong); border-width:2px` |
| `muted` | secondary fill — background / secondary | `background:var(--surface2)` |

**Section** (`SECTION_VARIANTS`: `neutral` · `good` · `bad` — only three; a
section has no `warn`/`accent`/`muted`):

| value | role | CSS evidence |
|-------|------|--------------|
| `neutral` | neutral zone, dashed border | — (no class; `.zone` is dashed by default) |
| `bad` | red fill/border — high-risk zone | `.zone.bad { background:var(--crit-soft); border-color:var(--crit) }` |
| `good` | green fill/border — hardened zone | `.zone.good { background:var(--olive-soft); border-color:var(--olive) }` |

`variant_extra` is a **list** carrying an optional SECOND colour role, for the one
case a single value cannot express: a box that is both a *kind* and a *state*
(`variant: bad` + `variant_extra: [muted]` — a secondary element at risk). Each
entry is validated by `checkVariantValue` against the SAME colour enum as
`variant`, which is what closes the old hole: a structural value can no longer
hide in it.

### `treatment` — structural modifiers (a list)

**Component** (`COMPONENT_TREATMENTS` — exactly four: `centered` · `half` ·
`vertical` · `outside`):

| value | what it does | CSS evidence |
|-------|--------------|--------------|
| `centered` | centres the text block | `.box.centered { text-align:center }` |
| `outside` | dashed frame — "outside the perimeter". Border STYLE only, **no colour at all**, which is why it is a treatment and not a colour role. The former name `ext` is gone — an unknown treatment, rejected at the gate | `.box.outside { border-style:dashed }` |
| `half` | DIVIDES a slot: a **pair** of consecutive halves stacks inside ONE full-height cell (never shrinks a cell). **Title-only**, and mutually exclusive with `rowspan` (`checkTreatmentCombinations`); runs of halves must be EVEN and a pair must agree on `span` (`checkHalfPairing`) | `.half-slot` flex column; `.box.half` 1-line title |
| `vertical` | runs the text down the block axis (a rotated lane label). Also title-only | `writing-mode:vertical-rl; transform:rotate(180deg)` |

**Section** (`SECTION_TREATMENTS` — exactly two; `half` on a section is rejected
by `checkTreatment`, which names the axis and the owning node kind):

| value | what it does | CSS evidence |
|-------|--------------|--------------|
| `envelope` | no fill, dashed border — a borderless container frame, useful as the wrapper around nested sections | `background:none; border-color:var(--line); border-style:dashed` |
| `plain` | no frame at all — a pure structural wrapper that stacks its children with nothing drawn around them | `background:none; border:none; padding:0; min-height:0` |

> `envelope` and `plain` are **style values**, not layout modes. Any section can
> nest other sections regardless of them. Author every value in **English**.

### Each `treatment` declares its guardrail consequence

A modifier that does not know which invariant it touches is what would break the
guardrail, so the relation is explicit:

| treatment | invariant it touches | consequence |
|-----------|----------------------|-------------|
| `centered` | none | `text-align` only; no geometry, no colour. |
| `outside` | none | `border-style` only; a dashed border occupies identical space. |
| `half` | **U** (gains a new subject) | U now asserts the **slot** height, not the component's: half cells are excluded from the component-height set (as row-span cells already were) and every `.half-slot` is asserted at exactly `--cell-h`. **C** is guarded *by construction* — a `description` on a half is a build error, so a 63px box cannot clip. **M** and the static `RECT`/`TRACK`/`ROW` are unaffected: the slot is a normal 1-track cell, and the census counts `.half-slot` wrappers (**Z**). |
| `vertical` | **N** (gains an applicability clause) | N's premise is a title flowing along the INLINE axis; `vertical` rotates it onto the BLOCK axis, where the fit constraint is the cell's height. A vertical leaf is therefore **exempt** from N. |
| `envelope` | none | Border/fill only. |
| `plain` | **Y** (still asserted, not exempted) | Removes padding and min-height, so a `plain` band's content bounds equal its zone bounds — Y's gaps go to ~0, which passes its tolerances. No clause needed. |

## `palette` — the document skin

`palette` (in `data/document.yaml`) selects the token set, validated against the
closed `PALETTES` set. **The semantic roles are identical in every palette** —
`--crit` is always high risk, `--olive` always hardened/ok, `--muted` always
secondary text — so switching it changes how the deck LOOKS and never what it
MEANS. Absent means `neutral`.

| value | skin |
|-------|------|
| `neutral` | the house palette; the default when the key is omitted, so every pre-existing deck is unaffected |
| `rose-pine` | Rosé Pine — Dawn on the light theme, Main on the dark |
| `rose-pine-moon` | the same, with Rosé Pine Moon on the dark theme |
| `contrast` | high contrast, for projecting in a room |

`npm run contrast` (`tools/contrast-audit.cjs`) parses the token blocks out of
`index.html` and measures every real foreground/background pair against WCAG 2.1.
