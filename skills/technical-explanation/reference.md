# Technical Explanation -- reference

The field-level correspondence from an explanation into the two render lanes,
what each engine accepts and rejects, and the form catalogue with the criterion
each form answers. `SKILL.md` carries the decisions and the rules; this file is
opened when an explanation is being lowered into a rendered artifact, or when a
form has to be checked against the criterion that produced it.

## Lane 1 -- `diagram-builder` (a YAML deck)

The deck's material is two primitives -- a **section** that arranges and a
**component** that carries -- plus page-level **filters** whose chips light a
relation across components. The correspondence from an explanation is fixed:

| Explanation | Deck | Where the field lives |
|-------------|------|-----------------------|
| a phase | a section, placed by its `order` among its siblings | `skills/diagram-builder/assets/engine/build-data.mjs` (`SECTION_FIELDS`) |
| a step | a component inside its phase-section, placed by its `order` | `skills/diagram-builder/assets/engine/build-data.mjs` (`COMPONENT_FIELDS`) |
| the path | one filter declared on the page, whose `key` every step component lists in its `filters` | `skills/diagram-builder/assets/engine/build-data.mjs` (`FILTER_FIELDS`, page `filters`) |
| a disclosure level | a page; a filter `key` reused on two pages projects one onto the other | `skills/diagram-builder/assets/engine/build-data.mjs` (`MANIFEST_PAGE_FIELDS`) |
| the step index | the component's `kicker`, by convention `STEP n OF m` -- open vocabulary, no validation, no rendered progress affordance | `skills/diagram-builder/assets/engine/build-data.mjs` (`COMPONENT_FIELDS`) |

What the crossing loses, and how each loss is carried:

- **Arrows.** The engine draws no edges and its component types are closed to
  `box`, `separator`, `rail` and `spacer`
  (`skills/diagram-builder/assets/engine/build-data.mjs`, `COMPONENT_TYPES`).
  A directed relation becomes chip membership plus `order`; the direction is
  read from the packing order and from the kicker's step index.
- **An arrow label.** A labelled arrow of the inline picture ("pushes image")
  has no field; the fact moves into the description or the detail of the
  component the arrow left from.
- **A declared parent/zoom relation between two pages.** The manifest's page
  fields are closed to `id`, `name`, `order`, `visible`, `file`; there is no
  field naming one page as the disclosure of another. The convention is the
  reused filter `key`, which projects a relation across pages without declaring
  the pages related.

The copyable skeletons live in the deck skill's reference ("Per-form seed
skeletons"): the flat `flow` skeleton is one section of ordered steps; the
"flow -- phases as sections" skeleton is the shape this correspondence produces
when the explanation has phases. The deck's own gate (`npm run gate` inside the
deck) is the verdict on the result, and it does not judge whether the phases
were the right sections -- that remains the writer's assertion, exactly as it was
in the text.

## Lane 2 -- `artifact-diagramming` (inline SVG in an HTML page)

A host-provided skill, not a Gaia one, so nothing here anchors to a file in this
repository. It renders a single picture, not a deck, and it keeps the drawing
rules of `SKILL.md` Step 4 intact: the mechanism is depicted rather than named,
every edge is labelled, one edge is one relation. What it adds is the SVG
mechanics -- legibility in both themes, text that does not overflow its shape,
edges that stay attached when the picture reflows. An explanation enters that
lane with its level-1 or level-2 picture already decided; the lane changes the
medium, not the shape.

## The form catalogue, by the criterion that produces it

Each form is what the two questions of Step 1 produce for one combination of
answers. Read the criterion first; the name is only what the shape is called.

| Form | MOVE or STAND | CONVERGE or DIVERGE | It teaches | Inline notation |
|------|---------------|---------------------|------------|-----------------|
| flow | moves | diverges | a path through stages, in one direction | boxes left to right or top to bottom, `──►` between them |
| swimlane | moves | diverges | the same path, with each stage under its owner | one row per owner, the path crossing rows |
| timeline | moves | diverges | a path across time, with what changed when | one row, ordered, each box a moment |
| sequence | moves | diverges | who sends what to whom, in message order | one column per party, arrows between columns top to bottom |
| decision | moves | converges | branches that end in one choice each | a tree of questions with `├──` / `└──` branches |
| state | moves | converges | a thing moving among a fixed set of conditions | boxes for the conditions, labelled arrows for the transitions |
| architecture map | stands | diverges | the parts and the boundaries between them | nested boxes, one per boundary |
| before/after | stands | diverges | two arrangements of the same parts | two maps side by side, same shapes |
| tree | stands | converges | a hierarchy under one root | `└──` / `├──` indentation |
| dependency | stands | converges | what rests on what, down to one base | boxes stacked, `▼` from dependent to dependency |

Two forms with the same two answers differ only in what the reader is meant to
notice (a swimlane notices the owner, a flow does not), so the choice between
them is the intent from Step 1, not a further criterion.
