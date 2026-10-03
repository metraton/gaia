# The review

For you, the orchestrator, when the subagent comes back with the YAML, the
check results and the census JSON (`npm run census -- --json`, fields defined in
[assets/README.md](assets/README.md)). You compare the census with the sketch
the person approved in [conversation.md](conversation.md), and you name every
difference before the person sees the deck. A difference nobody named reaches
the person as a surprise; a named one reaches them as a decision.

## First: the checks

build, model and contrast must pass, and the census's top-level `problems` must
be empty. A red check goes back to the subagent, never to the person. Passing
checks mean "not broken"; whether the deck says what was agreed is this review.

## Then: four classes, every one of them

Read each page's entry in `pages[]` by authored `id`. Go through all four
classes even when the first looks right; "no difference" is a finding you state,
not one you assume.

| Class | In the sketch | In the census | It is a difference when |
|-------|---------------|---------------|-------------------------|
| **Sections**: count and nesting | each frame, a frame inside a frame, frames side by side or stacked, each band, each dashed frame | nodes with `kind: "section"` and their `children`; `start {row, col}`, 1-based within the parent; the parent's `grid`: `stack` puts one child per row, `tracks`, `row` and `root` place children side by side by `start`; a band is a node with `width: "1/1"` in a parent of more than one column; a dashed frame is `outside` in the node's `treatment` | a frame is missing or extra, sits in the wrong parent, frames the sketch puts side by side do not share a `start.row` (or share it when stacked), a band or a dashed frame is missing |
| **Widths** | how wide each frame and box is drawn against its parent | `width`, the ONE normal form: a reduced fraction of the parent (`1/1`, `1/2`, `2/3`), or `content` for a component sitting directly in a flex row; `span.authored` against `span.resolved` | a fraction differs from the drawn proportion, or `span.resolved` differs from `span.authored` (the engine clamped what was written) |
| **Colours** | the colour lines under the sketch; everything else is plain | `variant` and `variant_source` per node, and the page's `variants` map from colour to ids; `variant_source` is `authored`, `default` (unset, so neutral; a section never passes its colour down) or `colourless` (separator, spacer) | a named colour is missing or on other boxes, a box the sketch left plain is coloured, or a named colour shows `default`: it was never set, and a section's colour did not reach its boxes |
| **Chips**: members | each `question (n)` line and the boxes marked `(n)` | the page's `chips[]`: `key`, `label`, `core`, `members`, `scope` (`members`, `all` for the reserved reset, or `none`); each node's own `chips` | members differ from the marked boxes in either direction, a question is missing or extra, the label no longer asks the agreed question, or `scope` is `none` (the chip lights nothing) |

A `core: true` chip is inherited from the document and appears on every page;
check its members on each page it reaches.

Two things the census cannot tell you, so do not read a difference into them:

- **`width: "content"` has no share.** A vertical separator, a rail inside a
  row, or a leaf directly in a flex row takes its natural size, which the JSON
  does not give. Compare only the siblings that carry a fraction.
- **`start` is the top tier only.** It is the placement at the census viewport
  (1920 px), the tier the sketch is drawn at. Narrower tiers repack, `model`
  proves that collapse, and the review never compares narrow-tier positions.

## Naming a difference

One line each: the class, what the sketch says, what the census says, the
authored id, and what happens next. There are two outcomes, and each difference
takes one:

- **Send back.** The build missed the sketch: the subagent fixes it with the
  sketch's value, and you read the new census the same way.
- **Tell the person.** The difference was deliberate or forced (the engine
  clamped a span; a page needed a hole). You say it, and why, before they open
  the deck.

When nothing differs, say so per class: "sections, widths, colours and chips
match the sketch".

## One worked comparison

The agreed sketch:

```
question (1): Which way does the flow run?

[ The flow runs in three phases, left to right                           ]

+-- Phase one ---------+ +-- Phase two ---------+ +-- Phase three -------+
| [ phase        (1) ] | | [ path         (1) ] | | [ level        (1) ] |
| [ step         (1) ] | | [ kicker       (1) ] | | [ no arrow     (1) ] |
+----------------------+ +----------------------+ +----------------------+
```

Colour: none named, so everything is plain.

The census for that page (the seed's `p10-flow-phases`), cut to what the four
classes read:

```json
{
  "id": "p10-flow-phases", "grid": "root",
  "variants": { "neutral": ["p10-phase-1", "p10-s1", "p10-s2", "..."],
                "accent": ["p10-s3"] },
  "chips": [{ "key": "path", "label": "Which way does the flow run?",
              "core": false, "scope": "members",
              "members": ["p10-s1", "p10-s2", "p10-s3",
                          "p10-s4", "p10-s5", "p10-s6"] }],
  "sections": [
    { "id": "p10-phase-1", "kind": "section", "grid": "tracks",
      "start": { "row": 1, "col": 1 }, "width": "1/3",
      "children": [
        { "id": "p10-s1", "kind": "box", "width": "1/1",
          "start": { "row": 1, "col": 1 } },
        { "id": "p10-s2", "kind": "box", "width": "1/1",
          "start": { "row": 2, "col": 1 } } ] },
    { "id": "p10-phase-2", "start": { "row": 1, "col": 2 }, "width": "1/3",
      "children": [
        { "id": "p10-s3", "kind": "box", "title": "The path is a chip",
          "variant": "accent", "variant_source": "authored" },
        "..." ] },
    { "id": "p10-phase-3", "start": { "row": 1, "col": 3 }, "width": "1/3" }
  ]
}
```

The differences, named:

1. **Sections.** The sketch has a band and three frames; the census has the
   three sections and no node with `width: "1/1"` at the page's level. The
   claim row is missing. Send back.
2. **Widths.** The three sections are `1/3` each and share `start.row` 1 at
   `col` 1, 2 and 3, as drawn; every box is `1/1` of its section. No difference.
3. **Colours.** `p10-s3` ("The path is a chip") is `accent`, `authored`; the
   sketch named no colour. Ask whether it was meant; if it was, the page must
   also say what accent means.
4. **Chips.** `path` asks the agreed question and its `members` are the six
   marked boxes, `scope` `members`. No difference.

Two differences named, one sent back and one asked, and only then does the
person open the deck.
