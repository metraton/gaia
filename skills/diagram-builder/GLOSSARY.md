# Diagram dialect — canonical glossary

The source of truth for every term the diagram deck uses. The engine
(`assets/engine/engine.js`) reads exactly these terms; every domain string lives
in the data. Both the orchestrator (to propose a decomposition) and the agent
(to author it) speak this vocabulary.

The whole layout model is **two primitives**: a recursive **section** (a grid
with `columns`, `span`, and `children`) and a **component** (a leaf with a
`type`). Nothing else. There is no envelope primitive, no subsection, no mosaic,
no `wraps`, no `layout.row`, no layout "modes" — those are gone.

The grid is a **spreadsheet of uniform cells**: every leaf component is one
fixed cell (`--cell-w × --cell-h`), and a section is always an integer number of
those cells wide. You position things by changing two values — `columns` and
`span` — exactly like merging cells in a spreadsheet. It is a known operation,
not trial-and-error, and every change is checked by `npm run validate` (see
`reference.md`).

## Structural terms

| Term | Where | Meaning |
|------|-------|---------|
| `document` | top level | The whole deck: `title`, `subtitle`, `version`, `filters`, `pages`. |
| `page` | `document.pages[]` | One act/slide. It IS the root section: owns `columns` (root grid width), `filters`, and `sections` (the root's children). `layout: grid` selects the engine. |
| `section` | any node with `children` | A grid zone: `id`, `title`, `subtitle`, `variant`, `order`, `span`, `columns`, and `children`. Its children auto-flow across `columns` and wrap down. A child may itself be a section — this is how nesting happens (a grid of grids). |
| `component` | any leaf (no `children`) | The unit inside a grid cell. Chooses a `type`: `box` (default) · `separator` · `rail`. A `box` carries `status`/`title`/`description`/`detail`/`variant`/`filters`; `separator` and `rail` are structural. |
| `filter` | `document`/`page` `filters[]` | A highlight chip: `key`, `label`, `steps` (flow text). Components that declare the `key` in their own `filters` light up when the chip is clicked. |

**The root/canvas is itself the invisible base section.** `page.columns` is the
root section's column count and `page.sections` are its children — the engine
renders the page by running the exact same `buildGrid` it uses at every deeper
level. There is no special "page layout": the page is section depth 0, with no
frame of its own.

## Component types (the `type` of a leaf)

| `type` | Renders | Props |
|--------|---------|-------|
| `box` (default) | The standard clickable card | `status`, `title`, `description`, `detail`, `note`, `variant`, `variant_extra`, `span`, `filters`. Omit `type` and you get a box. |
| `separator` | A thin divider LINE (not a card) | `orientation` (`horizontal` default · `vertical`), `style` (`solid` default · `dotted`), optional `text` (an inline centered label). Honors `span`. Not clickable. |
| `rail` | A title-only swimlane LABEL banner | `title`, `orientation` (`horizontal` default · `vertical`, which rotates the text). Honors `span`. Not clickable. |

## Layout terms

| Term | Where | Meaning |
|------|-------|---------|
| `cell` | (engine behavior) | The base unit: every leaf component is exactly `--cell-w × --cell-h` (232 × 130 px, design tokens in `index.html`). Cells **never grow** — a title + up to 3 clamped description lines always fits, the rest lives in the click panel. A cell grows only **horizontally, by merge** (`span`). Uniform cells are what make the layout deterministic and math-checkable. |
| `columns` | page · section | How many columns **this section's** grid has, **default 2**. A leaf grid renders exactly this many fixed `--cell-w` tracks. This column count is what **cascades 3→2→1** as width tightens (below). |
| `span` | any child (section or component) | An Excel-style **merge**: occupy N columns of the parent — **same semantics at every level**. Default 1, clamped to the parent's `columns`. `span == columns` makes the child a full-width **band** that takes its own row. In a leaf grid a merged child spans the full row (`grid-column: 1 / -1`) so it never overflows on collapse; a genuinely-narrower multi-cell arrangement is done by nesting sections with different `columns` (a compound grid). |
| `band` vs `inline` | section as a child of a compound grid | An **inline** section (`span: 1`) is sized to its own content and sits side by side with its neighbours on the same row. A **band** (`span == parent columns`) takes its own full row; consecutive bands stack top-to-bottom. A band spans the **block width** while its uniform cells inside stay `--cell-w` and left-align (banda ancha, componente uniforme). |
| `order` | section · component | Explicit position of a child within its parent's grid AND the single-column collapse order at the narrowest tier. Falls back to list order (stable). Children flow in `order`, packing side by side until a band forces a new full row. |
| the collapse cascade | (engine behavior) | Driven by the STAGE container query (works under split-screen / narrow panes, not the viewport): a leaf grid's `columns` step **down 3→2→1** as width tightens. **3** at full width, a **2-column "two-table"** intermediate at medium widths (≤1000px for a 3-col grid), a **1-column endpoint** at the narrowest tier (≤640px), where every leaf grid drops to a single cell and the whole page becomes one vertical stack. Below ~1440px compound rows fold from side-by-side into a centered vertical stack. A `columns: 1` section stays 1 at every tier. Cells stay `--cell-w` through every step — collapse changes how many columns show, never the cell size — so nothing scrolls sideways at the stacked tiers. |
| width math | (geometry) | Leaf grid, gap `--s-2 = 8px`, zone padding `--s-3 = 16px` per side: a merge/band of M cells = `M×232 + (M−1)×8`; a C-column leaf section = `C×232 + (C−1)×8 + 32`. E.g. a `columns:3` section is `3×232 + 2×8 + 32 = 760px`. |

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
| `steps` | `filter.steps` | The flow explanation shown when a filter chip is clicked. |
| `version` | `document.version` | Optional free-form string on the manifest (semver recommended). Rendered in the header after the subtitle; the node is `:empty`-collapsed when absent, so an older deck degrades with zero visible change. See the versioning rule in `SKILL.md`. |

## The `status` enum (the kicker badge)

Open vocabulary — free strings are accepted — but these are the agreed values:

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
