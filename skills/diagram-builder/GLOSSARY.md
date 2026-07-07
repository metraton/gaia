# Diagram dialect — canonical glossary

The source of truth for every term the diagram deck uses. Distilled from the
reference framework's `engine/DIALECT.md` §0 (the canonical, English glossary) —
the §1.1–§4 legacy narrative there is history, not contract. Both the
orchestrator (to propose a decomposition) and the agent (to author it) speak
these terms.

## Structural terms

| Term | Where | Meaning |
|------|-------|---------|
| `document` | top level | The whole deck: `title`, `subtitle`, `filters`, `pages`. |
| `page` | `document.pages[]` | One act/slide. Owns `layout` (engine), `columns` (the mosaic), `filters`, `sections`. |
| `section` | `page.sections[]` | A top-level cell of the mosaic (a zone): `title`, `subtitle`, `variant`, `order`, `layout`, and `subsections`/`components`/`wraps`. |
| `subsection` | `section.subsections[]` | A labeled grid inside a section: `label`, `sublabel`, `columns`, `components`. |
| `component` | `…components[]` | The box unit: `id`, `order`, `status`, `title`, `description`, `detail`, `note`, `variant`, `span`, `filters`. |
| `filter` | `document`/`page` `filters[]` | A highlight chip: `key`, `label`, `steps` (flow text). Components/sections that declare the `key` in their own `filters` light up when the chip is clicked. |

## Layout terms

| Term | Where | Meaning |
|------|-------|---------|
| `columns` | page · section · subsection · envelope | Grid width, **default 2**. `page.columns` = the top-level MOSAIC column count (its presence activates mosaic mode). On a content section/subsection = how many component columns the grid renders. On an **envelope** section = activates BANDED mode. Effective columns = `min(columns ?? 2, item count)` — never reserves an empty track. |
| `span` | component · section.layout · envelope child | Occupy N of the parent's columns — **same semantics at every nesting level**. Component default 1. A top-level `section.layout.span` **defaults to the section's own effective columns** when omitted; an explicit value overrides. Clamped to the parent's column count. Horizontal only. |
| `layout.row` | section.layout · envelope child | Which row (1-indexed) the cell belongs to, in the top-level mosaic or inside a banded envelope. Cells with a larger row stack below. |
| `order` | section · component | Explicit position within its row/subsection AND the single-column collapse order. Falls back to list order. |
| `wraps` | `section.wraps` | On an `envelope` section, the section ids it contains. Flat list = main + side-stack; list-of-lists = N equal columns; flat list + `columns` = banded mosaic (each child carries `layout: {row, span}`). |
| `layout` (page) | `page.layout` | The render **engine** selector: `grid` (recommended) or `svg` (legacy). Distinct object from `section.layout` — no clash. |
| `position` | `section.position` | LEGACY placement (`left`/`right`/`center`), used only when `page.columns` is absent. |

## Content terms

| Term | Where | Meaning |
|------|-------|---------|
| `key` / `id` | identity | A stable kebab-case slug for a page/section/subsection/component. Rendered as `data-zone` (zones) / `data-k` (components); the anchor for `wraps`, `filters`, and future edit mode. Reuse a value where it already exists. |
| `description` | `component.description` | Short text shown in the box: a string, or a list where each item is a line/bullet. |
| `detail` | `component.detail` | Long, HTML-allowed text for the click-through detail panel. Falls back to `description`. |
| `note` | `component.note` | A warning-style note (`⚠ …`) shown separately in the panel. |
| `steps` | `filter.steps` | The flow explanation shown when a filter chip is clicked. |
| `kicker` | presentation | The small uppercase eyebrow that renders a component's `status`. `status` is the data; `kicker` is the rendered role. |
| `version` | `document.version` | Optional free-form string on the manifest (semver recommended, e.g. `"0.1.0"`). Rendered in the header after the subtitle; omitted entirely when absent. See the versioning rule in `SKILL.md`. |

## The `status` enum (the kicker badge)

Open vocabulary — free strings are accepted — but these are the agreed values
that map to known styles:

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

**Section / zone** `variant`:

| value | role |
|-------|------|
| `normal` | neutral zone, dotted border |
| `danger` | red fill/border — high-risk zone |
| `safe` | green fill/border — hardened zone |
| `envelope` | no fill, dashed border — a container that `wraps` other zones |

> Author variant values in **English** (`crit`, `warn`, `ok`, `strong`, `ext`,
> `store`, `danger`, `safe`, `envelope`). The Spanish names in the reference
> framework's DIALECT §1.3 tables (`crítico`, `advertencia`, `seguro`…) are
> legacy narrative and are not what the engine reads.
