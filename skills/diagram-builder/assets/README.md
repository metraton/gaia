# assets/ — the portable diagram engine

A drop-in, zero-runtime-dependency diagram deck. Copy this directory into a
target repo (whole, or into a subfolder like `diagram/`) and you have a working,
themeable, data-driven diagram that opens under `file://`. Everything domain-
specific lives in `data/`; the engine layer knows only the dialect.

## Layout

```
assets/
├── index.html            entry + template (design-system CSS inline, help HUD)
├── engine/
│   ├── engine.js         render engine — dialect only, no domain knowledge (@version 1.0.0)
│   └── build-data.mjs    build step: data/*.yaml → data/data.generated.js
├── tools/
│   └── verify.mjs        generic headless render QA (renders every page, asserts
│                         no cell/zone collisions, screenshots widths × themes
│                         to a system temp dir — never into the project)
├── package.json          build / verify scripts + js-yaml + playwright devDeps
└── data/                 ── the only part you edit ──
    ├── document.yaml     manifest: title/subtitle + which pages, in what order
    ├── pages/overview.yaml   one starter page (1 section, 2 components, 1 filter)
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
  free interaction alongside wheel/trackpad scroll.

## Genericized from the reference artifact

Vendored from a frozen reference architecture-diagram artifact (HUD included)
and made domain-free: neutral title/subtitle placeholders (the engine overwrites
them from `document.yaml`), domain names stripped from comments, a generic
`package.json` name, a `verify.mjs` with generic collision assertions (no
diagram-specific zone names), and a domain-free seed `data/`. No absolute paths;
`js-yaml` is a bare import resolved from `node_modules`.
