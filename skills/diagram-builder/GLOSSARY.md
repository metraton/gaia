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
concrete values (the 130px cell height, the 120px readable floor, the 1280px
cap, the 232px reference width); the fill-geometry MODEL and its derivation
(equal `fr` tracks, why the cap centers, why the floor collapses columns
before cells shrink, the collapse cascade) is told once, canonically, in
`reference.md`, checked by `npm run validate`.

## Structural terms

| Term | Where | Meaning |
|------|-------|---------|
| `document` | top level | The whole deck: `title`, `subtitle`, `version`, `filters`, `pages`. |
| `page` | `document.pages[]` | One act/slide. It IS the root section: owns `columns` (root grid width), `filters`, `sections` (the root's children), and `form` (the layout form the guardrail scopes its invariants by). `layout: grid` selects the engine. |
| `section` | any node with `children` | A grid zone: `id`, `title`, `subtitle`, `variant`, `order`, `span`, `rowspan`, `columns`, and `children`. Its children auto-flow across `columns` and wrap down. A child may itself be a section — this is how nesting happens (a grid of grids). |
| `component` | any leaf (no `children`) | The unit inside a grid cell. Chooses a `type`: `box` (default) · `separator` · `rail`. A `box` carries `status`/`title`/`description`/`detail`/`variant`/`filters`; `separator` and `rail` are structural. |
| `filter` | `document`/`page` `filters[]` | A highlight chip that expresses a **RELATION**: `key`, `label`, `steps`. It **groups** every component that declares its `key` — components that share either a directional **FLOW** (the substitute for an arrow, since a grid cannot draw edges) OR a cross-cutting **CONCEPT / status / theme**. Clicking the chip spotlights that relation's membership across the whole canvas. |

**The root/canvas is itself the invisible base section.** `page.columns` is the
root section's column count and `page.sections` are its children — the engine
renders the page by running the exact same `buildGrid` it uses at every deeper
level. There is no special "page layout": the page is section depth 0, with no
frame of its own.

## Component types (the `type` of a leaf)

| `type` | Renders | Props |
|--------|---------|-------|
| `box` (default) | The standard clickable card | `status`, `title`, `description`, `detail`, `note`, `variant`, `variant_extra`, `span`, `rowspan`, `filters`. Omit `type` and you get a box. |
| `separator` | A thin divider LINE (not a card) | `orientation` (`horizontal` default · `vertical`), `style` (`solid` default · `dotted`), optional `text` (an inline centered label). Honors `span`/`rowspan`. Not clickable. |
| `rail` | A title-only swimlane LABEL banner | `title`, `orientation` (`horizontal` default · `vertical`, which rotates the text). Honors `span`/`rowspan` (a vertical rail with `rowspan` labels a lane down several rows). Not clickable. |

## Layout terms

| Term | Where | Meaning |
|------|-------|---------|
| `cell` | (engine behavior) | The base unit: an EQUAL `fr` share of its leaf grid's width (equal width within a grid; width varies by section) × a fixed `--cell-h` (130px) height, with a **readable floor** of `--cell-min-w` (120px) — columns collapse before a cell shrinks below it. Cells **never grow by content** — a title + up to 3 clamped description lines always fits, the rest lives in the click panel. A cell grows only **by merge**: horizontally via `span`, vertically via `rowspan`. |
| `columns` | page · section | How many columns **this section's** grid has, **default 2**. A leaf grid renders this many EQUAL `fr` tracks that fill the section width — clamped to what its children can actually fill, so an over-authored count never reserves an empty track. This column count is what **cascades …→2→1** as width tightens (below). |
| `span` | any child (section or component) | An Excel-style **horizontal merge**: occupy M columns of the parent — **same semantics at every level**. Default 1, clamped to the parent's `columns`. `span == columns` makes the child a full-width **band** that takes its own row. `1 < span < columns` is a real **PARTIAL merge** (`.mspan`): it occupies exactly M of the N tracks, keeps its proportion at the 2-track collapse tier (`--span2 = round(M/N·2)`), and becomes a full band only at the 1-column endpoint. |
| `rowspan` | any leaf cell | The **vertical merge**: occupy K rows — K× the cell height (`.mrsp`, `grid-row: span var(--rowspan)`). The base for a cell-graph where a cell's HEIGHT encodes magnitude, or a lane label spanning rows. Column position is untouched by the horizontal cascade; the guardrail exempts row-span cells/rows from the uniform-height, orphan, and edge-fill checks. |
| `form` | page | The page's declared layout FORM: `dashboard` (default) · `timeline` · `flow` · `comparison` · `mindmap` · `planner`. It scopes which guardrail invariants apply (form-scoped families, below) — an invariant tuned to a dashboard does not fail a legitimate timeline. |
| `band` vs `inline` | section as a child of a compound grid | An **inline** section (`span: 1`) occupies one column of the row and stretches to fill it — sections sharing a row are equal-width, equal-height slices. A **band** (`span == parent columns`) takes its own full row; consecutive bands stack top-to-bottom. A band spans the **block width** and its inner cells FILL it edge-to-edge (only the zone padding at each side). |
| `order` | section · component | Explicit position of a child within its parent's grid AND the single-column collapse order at the narrowest tier. Falls back to list order (stable). Children flow in `order`, packing side by side until a band forces a new full row. |
| the collapse cascade | (engine behavior) | Driven by the STAGE container query (works under split-screen / narrow panes, not the viewport): a leaf grid's `columns` step **down …→2→1** as width tightens. All authored columns at full width, a **2-column "two-table"** intermediate at medium widths (≤1000px for a 3-/4-/5-col grid), a **1-column endpoint** at the narrowest tier (≤640px), where every leaf grid drops to a single cell and the whole page becomes one vertical stack. Below 1440px compound rows fold from side-by-side into a full-width vertical stack. A `columns: 1` section stays 1 at every tier. Cells re-divide the width at each tier (equal `fr`, never below the 120px floor) — so nothing scrolls sideways at the stacked tiers. |
| fill-to-cap | (geometry) | The plane (`.sec-plane`) fills the canvas up to `max-width: 1280px`, then centers (`margin-inline:auto`) — at ≤1280 it fills edge-to-edge; above, the surplus becomes equal side margins. The gutter is one token everywhere (`gap: --s-2 = 8px`); zone padding is `--s-3 = 16px` per side. No fixed cell width: `--cell-w` (232px) is a documented readability reference only. |

## The guardrail families (invariant vocabulary)

The layout guardrail (`assets/tools/validate-layout.cjs`) asserts a flat,
**form-scoped** invariant table — the page's `form` selects which rows apply.
Severity: `dura` fails the build; `consejo` advises, never fails. A retired row
(`superseded`) is listed in the report but never evaluated. The full table with
per-invariant detail lives in `reference.md`.

| Family | Ids | Scope · severity |
|--------|-----|------------------|
| **INTEGRITY** | **D** determinism · **R** scrollbar-robust · **T** capture · **C** clamp · **O** no h-overflow · **F** collapse cascade · **S** inline fit / band spans block · **B** centered · **H** headers contained | every form, `dura` |
| **DESIGN** | **U** equal/uniform cells · **E** no empty column · **P** no orphan cell · **L** cells fill width · **M** readable ≥120px · **Y** band fill | form-scoped, `dura` |
| advisory | **V** horizontal composition (the deck earns its canvas) | grid-dense forms, `consejo` |
| retired | **W** fixed 232px cell width | `superseded: 'U'`, never evaluated |

## Content terms

| Term | Where | Meaning |
|------|-------|---------|
| `id` / `key` | identity | A stable kebab-case slug for a page/section/component. Rendered as `data-zone` (sections) / `data-k` (components); the anchor for `filters` and a future edit mode. Reuse a value where it already exists. |
| `title` | section · component · rail | The heading. On a section it renders as the zone header; on a box it is the bold card title; on a rail it is the whole label. |
| `subtitle` | section | The muted line under a section's title. |
| `description` | `component.description` | Short text shown in a box: a string, or a list where each item is a line/bullet. |
| `detail` | `component.detail` | Long, HTML-allowed text for the click-through detail panel. Falls back to `description`. |
| `note` | `component.note` | A warning-style note (`⚠ …`) shown separately in the panel. |
| `status` | `component.status` | The kicker badge word (see enum below). |
| `kicker` | presentation | The small uppercase eyebrow that renders a component's `status`. `status` is the data; `kicker` is the rendered role. |
| `steps` | `filter.steps` | The relation's explanation shown when a filter chip is clicked. |
| `version` | `document.version` | Optional free-form string on the manifest (semver recommended). Rendered in the header after the subtitle; the node is `:empty`-collapsed when absent, so an older deck degrades with zero visible change. |

## The `status` enum (the kicker badge)

`status` is **open vocabulary** — any free string is accepted, and each deck
picks the words its idea needs. The set below is **one example set** — the
kicker words a security-review deck might use — not a canonical default:

`ENTRY` · `EXPOSED` · `INTERNAL` · `EXTERNAL` · `WEAK` · `NEW` · `HARDENED` ·
`UNCHANGED` · `RISK`

## The `variant` enums (color/style role)

**Component** `variant` (composable with a second via `variant_extra: [ext]`):

| value | role |
|-------|------|
| `normal` | neutral box, standard border |
| `crit` | red — exposed or high risk |
| `warn` | amber — medium risk / weak config |
| `ok` | olive/green — hardened / correct |
| `strong` | marked green 2px border — a highlighted new component |
| `ext` | dotted border — outside the perimeter |
| `store` | secondary fill — data stores |

**Section** `variant` (the frame/tint of a zone):

| value | role |
|-------|------|
| `normal` | neutral zone, dashed border |
| `danger` | red fill/border — high-risk zone |
| `safe` | green fill/border — hardened zone |
| `envelope` | no fill, dashed border — a borderless container frame, useful as the wrapper drawn around nested sections |
| `plain` | no frame at all (no border, no background, no min-height) — a pure structural wrapper that stacks its children with nothing drawn around them |

> `envelope` and `plain` are **style values**, not layout modes. Any section can
> nest other sections regardless of its variant; `envelope`/`plain` only change
> how (or whether) the wrapper's frame is drawn. Author variant values in
> **English** (`crit`, `warn`, `ok`, `strong`, `ext`, `store`, `danger`, `safe`,
> `envelope`, `plain`).
