---
name: diagram-builder
description: Use when the user wants to build, design, or extend a diagram — an architecture overview, a timeline, a planner, or a flow diagram — as a portable, data-driven deck; when decomposing an idea into pages/sections/components; or when the orchestrator needs the diagram vocabulary to propose a decomposition. Triggers — "build a diagram", "architecture diagram", "diagrama", "diagram deck", "add a page/section/component to the diagram", "timeline", "flow diagram", "planner board".
metadata:
  user-invocable: true
  type: domain
---

# Diagram Builder

Diagram-builder is a **canvas for turning any idea into a creative, usable,
pedagogical layout** — rendered from plain YAML by a generic engine, no
framework, no server, opens under `file://`. The idea can be anything: a system
architecture, a timeline, a slide-style presentation, a process flow, a
comparison, a mind-map, a planner board. The engine knows only two primitives —
a recursive **section** (a grid) and a **component** (a leaf) — and everything
domain-specific lives in the data. Everything on the canvas invites the reader
toward the centre: the layout centres its content, a click opens a
bottom-centre panel, a chip spotlights a flow.

The common misread is to treat this as "draw an architecture diagram." It is
not. Architecture is one form among many, and defaulting to boxes-in-a-grid for
every idea is the failure that makes a diagram forgettable. The skill's job is
to find the *form that teaches this idea best* and express it in the two
primitives. A timeline is one row of wide spans; a presentation is a sequence of
pages; a flow is components tied together by a highlight `filter`; a comparison
is two sections side by side; a mind-map is nested sections radiating from a
centre. Same machinery, different form.

## Two consumers, one vocabulary

- **The orchestrator reads the jerga to propose.** Before anyone builds, it
  converses with the user in these terms — "we split it into two sections, these
  components, this status/variant, laid out in these columns and spans" — and
  lands a decomposition the user agreed to. It proposes structure; it does not
  author YAML.
- **The agent (developer) builds with the machinery.** It takes the agreed
  decomposition, writes the YAML dialect, runs the build, and confirms the
  render. The field schema and engine behavior it needs are in `reference.md`.

Be **proactive and pedagogical**: the vocabulary encodes capabilities the user
may not know exist. When a shape fits, suggest it — "these can sit two-up with
`columns: 2`", "we can trace that path across boxes with a `filter` chip", "that
band can span the full width", "a separator can divide those groups".

## The vocabulary (the jerga)

The whole model is **two terms**. Full definitions and enums are in
`GLOSSARY.md` — do not restate them here.

| Term | What it is, in one line |
|------|-------------------------|
| `section` | a recursive grid: `columns` + `span` + `children`; its children auto-flow and wrap down; a child may itself be a section (nesting) |
| `component` | a leaf in a grid cell, chosen by `type`: `box` (status/title/description/detail) · `separator` (a divider line) · `rail` (a swimlane label) |
| `columns` | a section's grid width (default 2) — authoritative; width never collapses it |
| `span` | occupy N of the parent's columns (default 1; `span == columns` = a full-width band); same at every level |
| `status` / `variant` | the kicker badge word / the colour-role of a box or section (see GLOSSARY enums) |
| `filter` | a chip that spotlights every component declaring its key — how a flow is traced |
| `version` | optional deck version, shown in the header |

**The root/canvas is itself the invisible base section.** `page.columns` is the
root grid width and `page.sections` are its children — the page is section depth
0, with no frame of its own. Everything else is the same section, nested.

## The decomposition model

```
idea
 └─ document        the deck: title, subtitle, version, filters, pages
     └─ page        one act/view (also the ROOT section: its columns + sections)
         └─ section     a grid zone; nests other sections freely (a grid of grids)
             └─ component   a leaf: box | separator | rail
   filters (document- or page-level) trace a highlighted flow across components
```

## The method: idea → canvas (the brain)

This is the core of the skill. The **order is load-bearing**: understand the
input and choose the *form* BEFORE you synthesize components. Synthesizing blind
— reaching for boxes-in-a-grid before you know what the idea is or which shape
teaches it — is the single failure that makes a diagram unusable. Follow the
thought-order:

1. **Understand the input first.** What is the idea or document, and what is its
   *intent*? Read what you were given — a prose idea, a markdown doc, a spec, a
   conversation — and name the entities, how they group, and what the reader is
   meant to walk away understanding. You cannot choose a form for an idea you
   have not yet grasped.

2. **Decide the approach by input type.** Two cases:
   - **A specific, structured document** (a spec, a system description, an
     itemized doc) already carries its own structure — mirror it: its sections
     become sections, its items become components. The decomposition is largely
     given; your job is faithful translation.
   - **An open idea** ("show how our onboarding works", "a roadmap for the
     year") has no given structure. *Explore which pedagogical FORM fits before
     drawing anything.* Do **not** default to squares-in-a-grid. Consider a
     timeline (one row of wide spans, left→right), a presentation (a page per
     beat), a flow (boxes tied by a `filter`), a comparison (two sections side
     by side), a mind-map (nested sections radiating from a centre), a planner
     (a grid of idea cards). Be **creative AND effective**: pick the form that
     makes the idea *click* fastest, not the one that is easiest to type.

3. **Synthesize into sections + components.** Only now, with the form chosen,
   decompose. Choose `columns`, `span`, and component `type` to tell the story
   **compactly, semantically, and pedagogically**: a full-width `span` for the
   spine of the story, a nested section for a group-within-a-group, a
   `separator` to divide phases, a `rail` to label a lane, a `status`/`variant`
   to carry meaning by colour, a `filter` to make a path traceable. Every choice
   should earn its place by teaching something.

4. **Discuss with the user.** Be **direct**, speak the jerga ("I made onboarding
   a 4-column timeline; step 3 is a full-width band because it's the pivot"),
   and keep adjustments **concise**. Iterate: propose, hear the correction,
   adjust one thing at a time. The cheapest place to get the shape right is the
   conversation, before and between builds.

5. **Show before/after.** When you change an existing canvas, show what changed
   — the section or component before, and after — so the user can see the edit
   rather than re-reading the whole deck. A small, legible diff of the structure
   keeps the iteration honest and fast.

## The build → verify loop

Editing the data is the **fast path**; the diagram is decided in the YAML, not
in the pixels.

1. **Edit** the YAML under `data/`.
2. **Build** — regenerate the render data (`npm run build`, which runs
   `engine/build-data.mjs` → `data/data.generated.js`). Re-run after every YAML
   change. Explain the command in one plain sentence before running it.
3. **Spot-check by looking — optional, not a gate.** A quick visual read
   (load `Skill('visual-verify')`, render `index.html`, glance across widths and
   both themes) catches overflow or a wrong wrap, because the engine scrolls
   horizontally rather than collapsing columns. It is a spot-check, not a
   required step — a correct data model is what matters. Screenshots go to a
   **system temp dir, never into the project**. Detail in `reference.md`.

## Feasibility, transparency, capability

- **Validate feasibility first, step by step** — read what the environment
  offers and reason one thing at a time (*"can I run the build? how far can I
  get?"*) **before** investing in a full synthesis. Order: **feasibility →
  understand → choose form → synthesize → discuss → build.**
- **Explain before you execute** — before running any script, say in one plain
  sentence what it does and which file to open to inspect it first.
- **Degrade gracefully** — with only a browser you can *view* (the committed
  `data.generated.js` renders with zero tooling); Node adds *rebuild*;
  Playwright adds *auto-verify*. The engine carries **no baked-in data** — every
  domain string lives in `data/`, which is what keeps a scaffold generic and
  leak-free. Detail in `reference.md`.

## The authoring modes

Each mode's step-by-step is in `reference.md`. Before scaffolding, **confirm the
destination — never assume a path.**

1. **New repo** — scaffold the engine + a seed document.
2. **Add the engine to an existing repo** — drop the engine layer into a subfolder.
3. **New page** — add a page YAML and register it in the manifest.
4. **New section** — add a section to a parent's `children`; nest by giving it
   its own section children.
5. **Add/edit components** — populate a section with boxes, separators, rails.

## Versioning

The deck has an optional `version` (`document.version`). Bump it on a
significant change — a new page, a reworked layout, a section added or removed —
so a viewer glancing at the header can tell which cut they are looking at. If a
pipeline injects the version at build/deploy time, let the pipeline own it. It
renders after the subtitle; omit the field and nothing shows.

## Where the rest lives

- `GLOSSARY.md` — the canonical dialect terms + the `status` and `variant`
  enums; the shared vocabulary both consumers speak.
- `reference.md` — field-by-field schema, engine behaviors, the authoring modes,
  and the build/verify loop in detail.
- `assets/` — the vendored portable engine, ready to scaffold into any repo:
  `index.html`, `engine/engine.js`, `engine/build-data.mjs`, `package.json`,
  `tools/verify.mjs`, and a domain-free seed `data/` that showcases sections,
  nesting, a separator, and a rail. See `assets/README.md`.
