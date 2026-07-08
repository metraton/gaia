# assets/ — the portable diagram engine

A drop-in, zero-runtime-dependency diagram deck. Copy this directory into a
target repo (whole, or into a subfolder like `diagram/`) and you have a working,
themeable, data-driven diagram that opens under `file://`. Everything domain-
specific lives in `data/`; the engine layer knows only the dialect.

## The layout model

Two primitives, nothing else. A **section** is a node with a `children` array;
it renders as a CSS-Grid `columns` wide, and its children auto-flow left→right
and wrap down. A **component** is a leaf; it renders by its `type` — `box`
(default) · `separator` · `rail`. Any child may set `span: N` to occupy N of the
parent's columns. The page/root is itself a section. Nesting is a section whose
children are sections — a grid of grids. There is no envelope primitive, no
subsection, no mosaic, no `wraps` — the engine is one recursive `buildSection` /
`buildGrid` pair, with no JS layout or measurement pass.

## Layout

```
assets/
├── index.html            entry + template (design-system CSS inline, help HUD)
├── engine/
│   ├── engine.js         render engine — dialect only, no domain knowledge (@version 2.0.0)
│   └── build-data.mjs    build step: data/*.yaml → data/data.generated.js
├── tools/
│   └── verify.mjs        generic headless render QA (renders every page, asserts
│                         the root grid renders with no top-level cell collisions,
│                         screenshots widths × themes to a system temp dir)
├── package.json          build / verify scripts + js-yaml + playwright devDeps
└── data/                 ── the only part you edit ──
    ├── document.yaml     manifest: title/subtitle/version + which pages, in order
    ├── pages/overview.yaml   one starter page: a section with boxes, a labeled
    │                         separator, a NESTED section, and a rail
    └── data.generated.js committed build output (window.__DOC__) — renders with zero tooling
```

## Use

- **View immediately:** open `index.html` in any browser. The committed
  `data/data.generated.js` means it renders with no tooling.
- **Author:** edit the YAML under `data/`, then `npm install` once and
  `npm run build` to regenerate `data/data.generated.js`. `npm run verify` runs
  the headless QA; its screenshots go to a **system temp dir** (`os.tmpdir()`,
  override with `DIAGRAM_SHOTS_DIR`), not into the project — the repo stays clean.
- **The dialect** (every field + the `status`/`variant` enums) is documented in
  the diagram-builder skill: `../GLOSSARY.md` and `../reference.md`.
- **`document.yaml`'s optional `version`** renders in the header — bump it on a
  meaningful change (see the versioning rule in `../SKILL.md`). The engine also
  supports click-and-drag panning on the canvas (grab/grabbing cursor) as a
  free interaction alongside wheel/trackpad scroll, and a help HUD (H key or the
  "?" button) that explains the whole visual vocabulary.

## Responsive behavior

The layout is pure CSS, driven by the STAGE container query (works under
split-screen / narrow panes, not just the viewport):

- **Desktop** honors the authored `columns`; when the diagram is wider than the
  viewport the canvas scrolls horizontally rather than collapsing.
- **Tablet (≤768px)** caps every grid to `min(columns, 2)`.
- **Phone (≤480px)** caps every grid to 1 — everything stacks in authored order.

A `span` renders as `min(span, effective columns)` at each tier, so a band stays
full-width and a 3-span degrades 3→2→1 without overflow. There is no JS layout
pass and no per-section breakpoint.

## Genericized from the reference artifact

Vendored from a frozen reference architecture-diagram artifact (HUD included)
and made domain-free: neutral title/subtitle placeholders (the engine overwrites
them from `document.yaml`), domain names stripped from comments, a generic
`package.json` name, a `verify.mjs` with generic collision assertions (no
diagram-specific zone names), and a domain-free seed `data/`. No absolute paths;
`js-yaml` is a bare import resolved from `node_modules`.
