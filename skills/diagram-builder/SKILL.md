---
name: diagram-builder
description: Use when the user wants to build or extend a diagram deck of nested sections and components authored in plain YAML — an architecture map, a timeline diagram, a planner board, a process-flow diagram, a slide-style presentation, a side-by-side comparison, or a mind-map. Not for charts, plots, or numeric/data visualization — route those to the dataviz skill. Triggers — "build a diagram", "architecture diagram", "diagram deck", "timeline diagram", "flow diagram", "planner board", "comparison diagram", "add a page/section/component to the diagram", "arma un diagrama", "diagrama de arquitectura", "haz un timeline", "diagrama de flujo", "tablero de planificación".
---

# Diagram Builder

This skill teaches a PERSON what they have and how to draw with it. It gives
them one standard, easy way to make a diagram: they fill in data — what the
parts are, what goes together, what comes first, what crosses everything — and
the look follows from that data. Nobody places a box by hand.

Everything starts from a diagram. A deck of pages is the unit of work. A video
can only come from a deck and animates that deck's structure, never a structure
of its own.

This file is for you, the orchestrator. You understand the idea with the
person, choose the pieces with them, and read the result back. A subagent
builds; its instructions are in [build.md](build.md). How the idea is explained
before it is drawn — the intent, the fixed order, the two registers — is
`technical-explanation`; this skill takes that explanation and turns it into a
deck.

```
idea
 └─ document        the deck: title, palette, tokens, core chips, pages
     └─ page        one act (also the ROOT section)
         └─ section     a frame that groups; nests other sections freely
             └─ component   a leaf: a box, a separator, a rail, a spacer
   chips light a relation: core chips on the document, page chips after them
```

## What the person has

Two primitives: a **section** ARRANGES and a **component** CARRIES. A
component is a **box** (the card), a **separator** (a line inside a section), a
**rail** (a title-only label for a lane or one level of a tree) or a **spacer**
(a hole left on purpose). A **chip** is a question at the top of the page;
clicking it lights every box that shares the answer.

Each box has four slots, and each slot has a character: the **kicker** is small
(a qualifier, a code, a step), the **title** is loud (a number goes here when
the number is the message), the **description** is short and clamps, and the
**detail** has no limit and sits behind a click.

The look has three dials, and each says one thing:

- **variant = colour.** Six roles carry a verdict (`neutral`, `good`, `warn`,
  `bad`, `accent`, `muted`). Four hues (`blue`, `violet`, `gold`, `clay`) carry
  none: they only tell up to four peers apart.
- **treatment = structure.** It covers a dashed frame (`outside`), centred
  text, a vertical label, a frameless wrapper, and a short-row staircase. It
  changes how something is drawn, never what it means.
- **palette = skin.** It sets how the whole deck looks (`neutral`, `rose-pine`,
  `rose-pine-moon`, `contrast`) and never changes what anything means.

## The map: from an idea to a piece

When the person says something about their idea, look it up here. Every row
names the piece, why it is that piece, the neighbour piece they might confuse it
with, and when the neighbour is the right choice instead. Name the neighbour to
the person only where the choice was close.

| The person says | Use | Why | Neighbour | Choose the neighbour when |
|-----------------|-----|-----|-----------|---------------------------|
| "These go together" | a **section** — si agrupo, uso una sección | The frame says "these are one thing" and its boxes are its parts. The structure is the claim. | a **separator** inside one section | Both sides are parts of the SAME thing and you only want a pause. If they are different things, a line understates it. If the group runs across several sections, use a chip. |
| "This goes in order" | **order**. Phases are sections side by side in reading order, steps are boxes inside them, and the kicker says `STEP n OF m`. | Order is the only thing that moves boxes, and it is also the stacking order when the screen narrows, so the sequence survives on any screen. | a **chip** whose `steps` narrate the path | The path goes back or jumps between sections or pages. Order cannot run backwards, and a chip's narration can carry direction. |
| "This runs across everything" | a **chip**, or a core chip when it crosses pages | A relation is shared membership, lit on click. Turning it into structure would break up the groups it crosses. | a full-width **band**, a section or box spanning every column | The thing that crosses has content of its own and needs boxes, like a layer under everything. Width says reach. |
| "These are options, pick one" | boxes side by side in **one section** with the same variant. The kicker says `OPTION A`, `OPTION B`, and the section title says the choice. | One section is one decision, and the same colour says they are peers. Order alone would suggest a sequence, so the kicker and the title say there is none. Never `STEP n OF m`. | a **comparison**: one section per option, side by side | Each option has parts of its own that the reader compares row against row. |
| "This is measured, that is estimated" | the **kicker**: `MEASURED` or `ESTIMATE`, with the number in the title | The kicker is the qualifier slot. The word travels with its box on every screen and needs no legend. | the dashed frame (`outside`) on the estimates, declared in a legend band | A whole page must separate them at a glance, and nothing else in the deck uses the dashed frame. Never use a verdict colour: amber and red say risk, not certainty. |
| "This one is bigger" | height (`rowspan`) for magnitude, width (`span`) for reach, with spacers under the shorter bars so all bars start from one floor | Magnitudes are only comparable from a shared floor, and the eye sees the proportion before it reads a number. | the number in the **title** | The number is the message, not the proportion. |
| "This page says X" | the **lead band**: the first full-width box, whose title is the claim and whose kicker says `PART n OF m` | The page states its claim before its parts. | a **section title** | You are naming one zone, not the whole page. |
| "Label this lane" or "this level of the tree" | a **rail** | A title-only label fills its track, can be indented one level per step, and can join a chip. | a **section title** | The label heads a framed group. A rail used as a heading takes space from the grid. |
| "These are different kinds" | a **hue** per kind, declared once in a legend band after the lead band | Hues carry no verdict, and the legend makes the colour mean the same on every page. | a **verdict variant** (`good`, `warn`, `bad`) | The colour must say safe or dangerous. |
| "This is outside the scope" | the **`outside`** treatment (dashed frame, no colour) | Border style is its own channel, so colour stays free. | a separate **section** | The outside things are several and have parts. |
| "Nothing goes here, on purpose" | a **spacer** | A declared hole closes the rectangle and says the gap is meant. | close the gap (move or merge the neighbour) | The gap was not meant. An undeclared hole is a defect. |
| "There is too much to say" | the **detail** behind a click | A cell never grows. What does not fit moves; it does not shrink. | split it into a **section** of boxes | The text has parts the reader must see at once. |

No case in this map lacks a piece. The two cases older versions left open,
options and measured versus estimated, are answered with slots that already
exist (kicker, section title, legend band). No new field was needed.

## The rules the pieces follow

1. **Everything you see is a merged cell.** Width is reach; height is magnitude.
   A merge uses rows or columns that something beside it creates.
2. **A grid holds cells or zones; mixing them changes every dial.** One section
   among boxes turns the level into a row of zones, where `span` becomes a
   weight.
3. **You write a sequence, not positions.** Filling runs forward: whatever
   belongs beside something tall goes before it.
4. **Every slot has a character** (above). If you read it wrong, the layout
   fights you.
5. **Every visual channel carries one claim**, and the page declares it (in a
   legend band, a section title, or a kicker that spells it out). A channel
   nobody declares is decoration.
6. **The grid draws no arrows; it lights relations.** A chip needs two ends.
   One key on two pages means one thing.
7. **Structure is the claim.** Distinct things are distinct sections, and the
   parts of one thing are boxes in its section. No machine can check this.
8. **What does not fit does not shrink; it moves**: into the detail, into a
   merge, or into a nested section.
9. **The hole speaks.** Close it, or declare it with a spacer.

The meaning rules over the geometry. A hole or an asymmetry is fine only when it
says something. Never fold two distinct things together to make a rectangle
come out full.

**Choosing the form.** Two questions decide it. Does the idea MOVE or STAND?
(A process is a timeline or a flow; a structure is a dashboard, a comparison, a
mind-map or a planner.) Does the reading CONVERGE on one thing or DIVERGE into
many? The grid cannot radiate, so a mind-map is sections placed symmetrically
around a central band. When the input already has its own structure (a spec, an
existing deck), MIRROR it: its parts become sections and its items become boxes.
Read an existing deck's `data/` first.

## Telling it across a deck

- **Each page opens with its claim**, in the lead band, and the kicker places it
  in the arc. The count in `PART n OF m` is text you keep true.
- **The deck's recurring actors are core chips**, declared once and inherited by
  every page. A page adds a few chips of its own. Write each chip as the
  question it answers ("Which boxes are the gates?"): clicking it is the reader
  asking.
- **The same hue or chip means the same thing on every page.** Keep a recurring
  anchor in the same place, and open with an overview before the drill-downs.
- **Never show a return leg with reversed order.** It reads backwards when the
  page narrows. Give the return leg its own row or section, placed after the
  outbound leg, and let the kicker (`BACK TO 1`) and the chip's `steps` carry
  the direction.
- **Four page shapes** come from the fields:
  - **ring**: two legs in natural order and one chip across them;
  - **staircase**: height by `rowspan`, with spacers so every step rests on one floor;
  - **ladder**: phases as sections in order, crossed by one chip;
  - **lanes**: a rail at the start of each row, every lane the same length.

## The flow, and who does what

1. **Understand the story with the person**, develop the idea, and name what is
   distinct, what goes together, what comes in order, and what crosses
   everything. Propose; do not wait to be told.
2. **Map each idea to a piece** with the map above.
3. **Draw the sketch before any YAML exists.** Draw it in ASCII, one mark per
   piece; do not narrate it. The person answers "yes", "like this" or "change
   X", and none of those answers needs a field name. How to draw it and how to
   teach it without overloading the person is the conversation moment, in
   [conversation.md](conversation.md).
4. **Ask where the deck lives** before anything is saved.
5. **Hand off to a subagent** with [build.md](build.md). Give it the agreed
   sketch and the values each piece carries, not field names. It returns the
   YAML, the check results and the census as JSON.
6. **Read the census against the sketch.** Check the number of sections, how
   they nest, their widths, their colours and each chip's members. Name every
   difference before the person sees the deck. How to read it closely is the
   review moment, in [review.md](review.md).
7. **The person looks.** They open the deck and judge whether it says what they
   meant.
8. **Offer the video last, as an option.** It is made from this deck only.

When the person asks for a change, **recalculate, never nudge**: name the
dials the change touches, say how the rows repack, and show the before and
after of that section.

## How a diagram is checked

- **The diagram's checks are build, model, census and contrast.** None of them
  opens a browser. The subagent runs them as the build lane in
  [build.md](build.md):
  - **build** refuses any field the schema does not know;
  - **model** proves the layout closes, as arithmetic;
  - **census** states what each page is;
  - **contrast** measures every colour pair.

  `test` is only for someone who changes the checks themselves.
- **The visual review is human.** The person opens `index.html` and looks.
  Whether a section is the right section, whether a colour says one thing, and
  whether the page reads are judged by their eye, and no script reaches them.
  Passing checks mean "not broken", never "right".
- **Never propose a browser check, a screenshot or a render for a diagram.** The
  diagram has none, on purpose.
- **Playwright is only for the video**: capturing frames and `video:check`.
  Raise it only when the person asks for a video.
- **To know whether it is present, look in the video's own manifest.** That is
  the `package.json` under the deck's `tools/video/`, which lists `playwright`.
  `npm ls playwright --prefix <deck>/tools/video` says whether it is installed.
  Chromium is present when `~/.cache/ms-playwright` holds a `chromium-<n>`
  build.
- **Install it only with the user's consent.** Both installs download and write:
  `npm install --prefix <deck>/tools/video` and then
  `npm exec --prefix <deck>/tools/video -- playwright install chromium`.
  Request them as one signed set that says what each does. Never run them on
  your own, and never as part of building a diagram.

## The seed is the showcase

`assets/data/` holds a seed deck with no domain. Its job is to exercise every
piece this skill names: sections side by side and nested, the four leaves,
height as magnitude, a partial merge, the collapse, a flow whose phases are
sections (`p10-flow-phases`), colour and rails (`p11-colour-and-rails`), and the
ring and the staircase (`p12-shapes`). Open it: a piece you can see rendered
teaches more than the same piece described. What the skill names, the seed
shows, and what the seed shows, the skill names. When either side changes,
check the other.

## Where the rest lives

- [conversation.md](conversation.md): for you, facing the person. The story in
  one sentence, the sketch and its marks, the reading rules, the handoff.
- [review.md](review.md): for you, after the build. The census read against the
  sketch in four classes, and how each difference is named.
- [build.md](build.md): for the subagent. It covers the build lane, the census,
  the vocabulary and every field, the per-form skeletons, the tokens, the
  engine's behaviour, and every check `model` runs.
- [assets/README.md](assets/README.md): the portable engine (`index.html`,
  `engine/`, `data/`, `tools/`) and how to serve it.
