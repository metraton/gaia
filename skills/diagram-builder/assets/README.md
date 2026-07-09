# assets/ — the portable diagram engine

A drop-in, zero-runtime-dependency diagram deck. Copy this directory into a
target repo (whole, or into a subfolder like `diagram/`) and you have a working,
themeable, data-driven diagram that opens under `file://`. Everything domain-
specific lives in `data/`; the engine layer knows only the dialect.

## The layout model — a spreadsheet-style grid of uniform cells

Two primitives, nothing else. A **section** is a node with a `children` array;
it renders as a CSS-Grid `columns` wide, and its children auto-flow left→right
and wrap down. A **component** is a leaf; it renders by its `type` — `box`
(default) · `separator` · `rail`. Any child may set `span: N` to merge across N
of the parent's columns. The page/root is itself a section. Nesting is a section
whose children are sections — a grid of grids. There is no envelope primitive,
no subsection, no mosaic, no `wraps` — the engine is one recursive
`buildSection` / `buildGrid` pair, with no JS layout or measurement pass.

Every leaf is one **fixed uniform cell** (`--cell-w × --cell-h = 232 × 130px`);
a section is always an integer number of those cells wide. Cells never resize;
they merge (`span`) and the column count **cascades 3→2→1** as width tightens
(2-column intermediate, 1-column single-stack endpoint), so nothing scrolls
sideways at the stacked tiers. `span == columns` makes a full-width **band** that
takes its own row and spans the block; the whole block is centered on the canvas.
Positioning is a known operation: `merge of M cells = M×232 + (M−1)×8`;
`C-column section = C×232 + (C−1)×8 + 32`.

## Layout

```
assets/
├── index.html            entry + template (design-system CSS inline, help HUD)
├── engine/
│   ├── engine.js         render engine — dialect only, no domain knowledge (@version 2.0.0)
│   └── build-data.mjs    build step: data/*.yaml → data/data.generated.js
├── tools/
│   ├── validate-layout.cjs  the LAYOUT GUARDRAIL — renders every page at five
│   │                        widths (5 reloads each) and asserts the layout
│   │                        invariants against the real geometry; PASS/FAIL,
│   │                        exit≠0 on failure; shots to a system temp dir
│   └── verify.mjs        lighter render QA (root grid renders, no top-level cell
│                         collisions, screenshots widths × themes)
├── package.json          build / validate / verify scripts + js-yaml + playwright devDeps
└── data/                 ── the only part you edit ──
    ├── document.yaml     manifest: title/subtitle/version + which pages, in order
    ├── pages/overview.yaml   one starter page: two inline sections side by side,
    │                         a base band, a nested section, a separator, and a rail
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
  meaningful change. The engine also
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

## Deploy: cache-busting & no-cache

`index.html`, `engine/engine.js`, and `data/data.generated.js` are a **coupled
contract**: they change together on every meaningful deck change (a new page,
a reworked layout, a rebuilt `data.generated.js`). A browser that caches any
one of the three independently can pair a stale `engine.js`/`data.generated.js`
with a fresh `index.html` after a release — the classic stale-assets bug.
This seed ships **with no server** (it is copied whole into a target repo and
opens under `file://` with zero tooling), so it cannot bundle a fix that lives
in a server layer the target may not have. Two things are already in place for
whatever the target repo's deploy layer turns out to be:

- **A cache-busting placeholder is already on the two coupled `<script>` tags**
  in `index.html`: `data/data.generated.js?v={{DIAGRAM_DECK_VERSION}}` and
  `engine/engine.js?v={{DIAGRAM_DECK_VERSION}}`. Left unsubstituted, the query
  string is harmless — a static file server or `file://` resolves the path and
  ignores it, so "view immediately" still works with no visible change.
- **Wire up the placeholder when you deploy behind anything that serves these
  files** (a server that templates the HTML, a CI build step, a static-site
  generator): substitute `{{DIAGRAM_DECK_VERSION}}` with the real deck version
  (`data/document.yaml`'s `version`, or the repo's release tag) so every
  release produces distinct asset URLs and the browser is forced to fetch the
  new pair instead of reusing the old one.
- **Add a no-cache safety net as a second layer**, in whatever serves the
  three coupled paths (`/`, `/index.html`, `/engine/engine.js`,
  `/data/data.generated.js`): send `Cache-Control: no-cache, must-revalidate`
  on those responses (relying on ETag for revalidation, which most static
  file servers already set). This covers the case the placeholder is left
  unsubstituted or a proxy ignores the query string — the browser may keep a
  cached copy but must always revalidate with the server before using it, so
  it can never serve a stale copy silently. A worked example (a FastAPI app
  serving this exact seed) sets this via one `@app.middleware("http")` that
  checks `request.url.path` against the four coupled paths and sets the
  header on the response — the same pattern applies to any framework or
  static-file layer (nginx `add_header`, an S3/CloudFront cache policy, etc).
- **If the target has no server at all** (pure static hosting, `file://`,
  opened straight from the repo), there is nothing to wire up — the
  placeholder degrades to a no-op query string and the deck still renders
  correctly; you are simply not protected from the stale-pair bug until a
  serving layer exists.

## Genericized from the reference artifact

Vendored from a frozen reference architecture-diagram artifact (HUD included)
and made domain-free: neutral title/subtitle placeholders (the engine overwrites
them from `document.yaml`), domain names stripped from comments, a generic
`package.json` name, a `verify.mjs` with generic collision assertions (no
diagram-specific zone names), and a domain-free seed `data/`. No absolute paths;
`js-yaml` is a bare import resolved from `node_modules`.
