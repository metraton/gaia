---
name: diagram-builder
description: Use when the user wants to build, design, or extend a diagram — an architecture overview, a timeline, a planner, or a flow diagram — as a portable, data-driven deck; when decomposing an idea into pages/sections/components; or when the orchestrator needs the diagram vocabulary to propose a decomposition. Triggers — "build a diagram", "architecture diagram", "diagrama", "diagram deck", "add a page/section/component to the diagram", "timeline", "flow diagram", "planner board".
metadata:
  user-invocable: true
  type: domain
---

# Diagram Builder

Diagram-builder turns an idea into a portable, data-driven diagram deck: themeable
pages rendered from plain YAML by a generic engine — no framework, no server, opens
under `file://`. The engine knows only a small **dialect**
(`document / page / section / subsection / component`); every domain string lives
in the data. The same primitives serve any diagram, not only architecture: a
**timeline** is a one-row mosaic of wide spans, a **planner** is a grid of idea
components, a **flow diagram** is components tied together by highlight `filters`.
Learning this skill is learning to *use that tool*.

## Two consumers, one vocabulary

The shared vocabulary below is what lets two readers meet:

- **The orchestrator reads the jerga to propose.** Before anyone builds, it
  converses with the user in these terms — "we split it into two sections; these
  components; this status/variant; laid out in these columns and rows" — and lands
  a decomposition the user agreed to. It proposes structure; it does not author YAML.
- **The agent (developer) builds with the machinery.** It takes the agreed
  decomposition, writes the YAML dialect, runs the build, and verifies the render.
  The field reference and engine behavior it needs is in `reference.md`.

Be **proactive and pedagogical**: the vocabulary encodes capabilities the user may
not know exist. When a shape fits, suggest it — "these can sit two-up with
`columns: 2`", "we can trace that path across boxes with a `filter` chip", "that
band can span the full width".

## The vocabulary (the jerga)

The terms the orchestrator uses to decompose and the agent uses to build. Full
canonical definitions and enums are in `GLOSSARY.md` — do not restate them here.

| Term | What it is, in one line |
|------|-------------------------|
| `document` | the whole deck: title, subtitle, filters, pages |
| `page` | one act/slide; owns the top-level `columns` (the mosaic) and its sections |
| `section` | a zone/cell of the mosaic; placed with `layout: {row, span}` |
| `subsection` | a labeled grid of components inside a section |
| `component` | the box unit: `status`, `title`, `description`, `detail`, `variant`, `span`, `filters` |
| `status` | the kicker badge (ENTRY, EXPOSED, NEW, HARDENED, RISK … — open enum) |
| `variant` | the box/zone color role (component: normal/crit/warn/ok/strong/ext/store; section: normal/danger/safe/envelope) |
| `columns` | grid width, default 2; on an `envelope` section it activates BANDED mode |
| `span` | occupy N of the parent's columns — same meaning at every level |
| `row` / `order` | the mosaic band a cell sits in / its left-to-right and collapse order |
| `envelope` | a section that wraps other sections in a dashed border (a container) |
| `filters` | chips that light up the components/flow that declare them |

## The decomposition model

Read an idea top-down into the dialect:

```
idea
 └─ pages       one per act/state/view (Current vs Target; Q1..Q4; phases)
     └─ sections    the zones of that view, placed row × span across `columns`
         └─ subsections   optional labeled grids
             └─ components   the boxes, each a status + variant + text
   filters (document- or page-level) cut a highlighted flow across components
```

Propose in this order: pages → sections and their layout (columns/rows/spans) →
components with status/variant → filters for the flows worth tracing.

## The facilitation method: idea → diagram

The part people get wrong is skipping the conversation and jumping straight to
YAML. Converge on the shape *first*, on the cheap:

0. **Feasibility + destination first** — before investing in a sketch, validate
   step by step what you can actually run, and *ask where to scaffold* (never
   assume a path). See *Feasibility, transparency, capability* below.
1. **Capture** the idea (or "read this markdown") and extract the entities and
   how they group.
2. **Sketch in ASCII — for free.** Draw it as text using the *same dialect
   concepts* (columns, sections as bordered boxes, components inside), so the
   sketch is a 1:1 preview of the YAML — no build, no render, no T3.
3. **Converge by conversation.** Propose the decomposition and ask what sharpens
   it — where's the entry? what groups? what connects to what? what's risky
   (→ status/variant)? how many columns? Redraw the ASCII on each agreement.
4. **Translate on "looks good."** Only once the user accepts the sketch, turn it
   into real YAML and build/render (the five modes).
5. **Fine-tune** with cheap parameters — `columns`, `span`, wording.

This serves the orchestrator (to converse and propose) as much as the builder
(to author). A worked example — prose idea → three ASCII rounds → final YAML —
is in `reference.md`.

## The build → verify loop

After writing YAML, the diagram is not done until it has been *seen*:

1. Edit the YAML under `data/`.
2. Regenerate the render data (the project's build step; see `reference.md`).
3. **Verify by looking** — load `Skill('visual-verify')` to render the page and
   read the screenshots across widths and both themes. The engine grows and
   scrolls horizontally rather than collapsing columns, so a layout that reads at
   1440px can still overflow — reading the pixels is the honest check, not a
   clean build exit. Loop on any defect. Screenshots go to a **system temp dir,
   not into the project** — the scaffolded repo stays clean (engine + template +
   data only, no images folder).

## Feasibility, transparency, capability

- **Validate feasibility first, step by step.** Good method, always: read what
  the environment offers and reason one thing at a time — *"can I run this script?
  how far can I get?"* — and say it, **before** investing in a full sketch or
  build. (An orchestrator that already knows its environment answers this
  trivially; the skill still asks first, so you never draw the whole diagram and
  then discover you can't execute.) Order: **feasibility → define → invite →
  agree → build.** Name where the data lives (`data/`) and the script that loads
  it (`engine/build-data.mjs`).
- **Explain before you execute.** Before running any script, say in one short,
  plain sentence what it does and which file to open to inspect it first — e.g.
  *"I'll run `npm run build` (which runs `engine/build-data.mjs`) to regenerate the
  diagram from your YAML — you can read that script first."*
- **Capability (facts, not warnings), degrading gracefully:**
  - **View** — a browser; `data.generated.js` is committed, so it renders with
    zero tooling.
  - **Rebuild** after editing data — Node + `npm install` + `npm run build`.
  - **Auto-verify** — Playwright renders + screenshots (to a temp dir).
  - With only a browser you can still view; rebuilding is what needs Node.
- **Minimal, data-driven engine — no baked-in data.** Every domain string lives
  in `data/`; the engine and template carry none. That is what keeps a scaffold
  generic and leak-free — nothing from one diagram bleeds into the next; it only
  scales.

Detail in `reference.md`.

## The five authoring modes

Each mode's step-by-step procedure is in `reference.md`:

1. **New repo** — scaffold the engine + a seed document.
2. **Add the engine to an existing repo** — drop the engine layer into a project.
3. **New page** — add a page YAML and register it in the manifest.
4. **New section/zone** — add a section and place it in the mosaic.
5. **Add/edit components** — populate a section or subsection.

## Versioning

The diagram has a `version` (`document.version`, optional). Bump it when you
make a significant change — a new section, a reworked layout, a page added or
removed — so a viewer glancing at the header can tell what changed or which
cut of the deck they are looking at. If a pipeline provides the version
(injecting it at build or deploy time), let the pipeline own it instead of
hand-editing the seed. It renders in the header, after the subtitle; omit the
field and nothing shows.

## Where the rest lives

- `GLOSSARY.md` — the canonical dialect terms + the `status` and `variant`
  enums; the shared vocabulary both consumers speak.
- `reference.md` — field-by-field schema, engine gotchas, the five modes, and the
  build/verify loop in detail.
- `assets/` — the vendored portable engine, ready to scaffold into any repo:
  `index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`,
  `tools/verify.mjs`, and a domain-free seed `data/`. See `assets/README.md`.
