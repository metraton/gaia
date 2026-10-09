# Diagram Builder — the toolbox

Every piece a deck is made of and every property each piece accepts, with what
that property changes on screen. Each property is a visual channel; a page that
uses a channel says what it means there. The values below are the ones the
build accepts, read from the code that enforces them (`file::symbol`); a value
outside them fails `build` by name, with a near-miss hint.

The pieces go from the outside in: document, page, section, box, separator,
rail, spacer, chip, look. After them: the forms a page declares, the limits
measured on real decks, and where the seed shows each piece live.

Paths are relative to the skill directory; `<deck>` is a deck's root, the
directory holding `index.html`, `engine/`, `data/` and `tools/`.

## Document

The deck as a whole: one file, `data/document.yaml`, that names the deck, sets
its look and its core chips, and lists the pages in order. Its keys are closed by
`assets/engine/build-data.mjs::MANIFEST_FIELDS`.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `title` | yes | text | — | The deck's name in the header. |
| `subtitle` | no | text | none | The muted line under the title. |
| `version` | no | free text | none | Printed after the subtitle; bump it when the deck changes meaningfully. |
| `look` | no | `projector`, `report`, `brand` (`assets/engine/tokens.mjs::LOOKS`) | none | Picks the palette and the text sizes of every page at once; see Look. |
| `palette` | no | `neutral`, `rose-pine`, `rose-pine-moon`, `contrast` (`assets/engine/build-data.mjs::PALETTES`) | `neutral` | The colour skin of every page; refused when `look` is set. |
| `palette_overrides` | no | `light` and/or `dark`, each a map of colour token to colour | none | Replaces one colour of the palette, per theme; see Look. |
| `tokens` | no | a map of token to value (`assets/engine/tokens.mjs::TOKEN_SCHEMA`) | the defaults in Look | Tunes sizes, rows, spacing, breakpoints and the presentation screen; see Look. |
| `filters` | no | a list of chips | none | The core chips: shown first, in order, on every page; see Chip. |
| `harmony` | no | `true`, `false` | `false` | With `true`, `model` fails any box or rail that belongs to no chip (the lead band exempt). |
| `pages` | yes | a list of page entries | — | Which pages exist, in which order, and whether they show; see Page. |

Limits:

- `look` and `palette` answer the same question, so a document carries one or
  the other; both together is a build error.
- An authored token passes the same range check as a look's token, so neither
  can push text below the legibility floors.

## Page

One claim, told on one tab. A page is the root section: it owns a grid like any
section, and its entry in `document.yaml` decides its tab name, its position
and whether it shows. Its keys are closed by
`assets/engine/build-data.mjs::PAGE_FIELDS` (the page file) and
`assets/engine/build-data.mjs::MANIFEST_PAGE_FIELDS` (its entry in `pages`).

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `id` | yes, in both files | kebab-case slug | — | Nothing visible; joins the entry to its file and names the page in every check finding. |
| `name` | yes, in the entry | text | — | The tab label. |
| `order` | no, in the entry | number | 0 | The tab's position; tabs sort by it, lowest first. |
| `visible` | no, in the entry | `true`, `false` | `true` | `false` leaves the page out of the build. |
| `file` | yes, in the entry | path under `data/` | — | Which page file is read. |
| `omit_filters` | no, in the entry | keys of core chips | none | Drops those core chips from this page's chip bar. |
| `columns` | no | integer ≥ 1 | `default_columns` (2) | How many equal tracks the root grid has. |
| `form` | no | `dashboard`, `timeline`, `flow`, `comparison`, `mindmap`, `planner` (`assets/tools/static-census.cjs::FORMS`) | `dashboard` | Nothing drawn; decides which layout checks judge the page (see Forms). |
| `filters` | no | a list of chips | none | The page chips, after the core chips. |
| `sections` | yes | a list of sections and components | — | The root's children, the content of the page. |
| `text_fit` | no | `strict`, `advisory` (`assets/engine/build-data.mjs::TEXT_FIT`) | `strict` | Nothing drawn; `advisory` turns text-overflow failures into notes, for a page honestly read in its detail panel. |
| `layout` | no | `grid` (`assets/engine/build-data.mjs::LAYOUTS`) | `grid` | Nothing; kept for old decks, and the build warns where it appears. |

Limits:

- `name`, `order` and `visible` are read from the entry. The page file accepts
  them too (`assets/engine/build-data.mjs::PAGE_FIELDS`) and the build ignores
  them there. `id` lives in both files and the build checks they match.
- A page grid closes as a rectangle at every tier; a cell that is empty and not
  declared fails `model` (HOLE, RECT).
- An inherited core chip with no member on the page fails CHIP; drop it
  explicitly with `omit_filters`.

## Section

A frame that groups: its children are the parts of one thing. A section is any
node with `children`, and its children may be sections again, to any depth. Its
keys are closed by `assets/engine/build-data.mjs::SECTION_FIELDS`.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `id` | yes | kebab-case slug | — | Nothing visible; names the frame in the census and the checks. |
| `children` | yes | a list of sections and components | — | What the frame holds; a child with `children` is a section, otherwise a component. |
| `title` | no | text | none | The header on the frame's top edge; no title and no subtitle draws no header. |
| `subtitle` | no | text | none | The muted line under the title. |
| `variant` | no | `neutral`, `good`, `bad` (`assets/engine/build-data.mjs::SECTION_VARIANTS`) | `neutral` | `good` tints the zone green, `bad` red, title included; `neutral` is the plain zone. |
| `treatment` | no | list of `envelope`, `plain`, `middle`, `compact` (`assets/engine/build-data.mjs::SECTION_TREATMENTS`) | none | `envelope`: no fill, dashed border, a container around nested frames. `plain`: no frame and no padding, a pure wrapper. `middle`: centres the grid vertically in the height its row gives it. `compact`: shorter rows (`row.compact_h`) for one leaf grid. |
| `order` | no | number | position in the list | Where the frame sits among its siblings, and its place when the page folds to one column. |
| `span` | no | integer ≥ 1 | 1 | Among sibling sections, the frame's width weight; `span` equal to the parent's columns makes it a band that owns its row. |
| `rowspan` | no | integer ≥ 1 | 1 | Nothing: a section always sits in a row of sections, where rows do not merge (see limits). |
| `columns` | no | integer ≥ 1 | `default_columns` (2) | How many equal tracks the frame's own grid has. |
| `tokens` | no | `row.cell_h`, `type.title.lines`, `type.desc.lines` (`assets/engine/tokens.mjs::NODE_TOKEN_KEYS`) | the deck's | The row height and the text clamps inside this frame only. |

Limits:

- A section's colour does not reach its boxes; a box is coloured by its own
  `variant`. The four hues exist only on components.
- A grid that holds at least one section is a row of frames that share the
  width by `span`; a grid that holds only components is a grid of equal tracks.
  Putting one section among boxes turns the whole level into the first kind.
- A leaf grid never keeps an empty track: its columns shrink to what its
  children fill (`assets/engine/engine.js::buildGrid`), so an over-written
  `columns` does not leave room on the right.
- `compact` takes components only, never nested sections.
- `order` is also the one-column reading order, so a sequence that returns,
  written with reversed `order`, reads backwards when the page folds. The
  return keeps its natural `order` in its own row or section, and its direction
  goes in the text: a kicker, a chip's steps.
- `rowspan` is accepted and draws nothing: `index.html` merges rows only for
  children of a grid of components (`.sec-grid:not(.sec-compound) > .mrsp`).

## Box

The card: one part of a thing, with a face read at a glance and a detail behind
a click. A box is any component without `type`, or with `type: box`. Its keys
are closed by `assets/engine/build-data.mjs::COMPONENT_FIELDS`, shared with the
separator.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `id` | yes | kebab-case slug | — | Nothing visible; chips, the census and the video name the box by it. |
| `type` | no | `box`, `separator`, `rail`, `spacer` (`assets/engine/build-data.mjs::COMPONENT_TYPES`) | `box` | Which piece the component is. |
| `kicker` | no | free text | none | The small uppercase mark above the title: a step, a code, a category, `MEASURED`. |
| `title` | no | text | empty | The loud line, clamped to `type.title.lines` (2); where a number goes when the number is the message. |
| `description` | no | text or a list of lines | none | The short line under the title, clamped to `type.desc.lines` (3). |
| `detail` | no | text, HTML allowed | the description | The body of the card a click opens; it has no length limit. |
| `note` | no | text | none | A warning shown apart in the same card. |
| `variant` | no | `neutral`, `good`, `warn`, `bad`, `accent`, `muted`, `blue`, `violet`, `gold`, `clay` (`assets/engine/build-data.mjs::COMPONENT_VARIANTS`) | `neutral` | The fill and border. `good` green, `warn` amber, `bad` red carry a verdict; `accent` a 2 px border; `muted` a secondary fill; the four hues tell peers apart and carry no verdict. |
| `variant_extra` | no | list of the same values | none | A second colour role on the same frame; deprecated, it builds with a warning because two roles compete for one fill. |
| `treatment` | no | list of `centered`, `half`, `vertical`, `outside` (`assets/engine/build-data.mjs::COMPONENT_TREATMENTS`) | none | `centered`: centres the text. `half`: two consecutive halves share one cell, stacked. `vertical`: the text runs down the cell. `outside`: a dashed border and no colour. |
| `lead` | no | `true` | absent | Marks the lead band, the page's claim (see limits). |
| `order` | no | number | position in the list | Where the box sits among its siblings and in the one-column reading order. |
| `span` | no | integer ≥ 1 | 1 | How many tracks of its grid the box merges across; equal to the grid's columns, it is a band on its own row. |
| `rowspan` | no | integer ≥ 1 | 1 | How many rows the box merges down: height as magnitude. |
| `filters` | no | list of chip keys | none | The chips that light this box. |
| `copy` | no | `true` or text | none | A copy button on the box: `true` copies the title, text copies that text. |
| `tokens` | no | `type.title.lines`, `type.desc.lines` (`assets/engine/tokens.mjs::NODE_TOKEN_KEYS`) | the deck's | This box's two clamps. |

Limits:

- A cell never grows to fit its text: what does not fit moves to `detail`, to
  a `rowspan`, or to a nested section. `model` fails text past its clamp at the
  presentation viewport (TEXT, INK) on a strict page.
- Heights drawn with `rowspan` compare as magnitudes only from a shared floor.
  Cells flow from the top, so a shorter box starts at the top row unless
  spacers fill the rows above it and bring its bottom down to the tallest
  one's. Where the tallest must end level with a shorter neighbour section,
  the section holding them is `compact`, with shorter rows.
- `half` and `vertical` are title-only: a `description` on them is a build
  error. `half` and a `rowspan` above 1 exclude each other, halves come in
  consecutive pairs (an unpaired half is a build error), and the two halves of a
  pair declare the same `span`
  (`assets/engine/build-data.mjs::checkHalfPairing`).
- The lead band is a box, a direct child of the page, first in `order`, with
  `span` equal to the page's `columns`, and neither `half` nor `vertical`
  (`assets/engine/build-data.mjs::checkLead`).
- `copy` belongs to a box only, and `copy: true` needs a title.
- A `span` larger than the grid's columns fails BAND; the engine would cut it
  to the columns, and the census shows both values (`span.authored`,
  `span.resolved`).
- `kicker` is open text: nothing counts or checks a `STEP n OF m` for you.

## Separator

A thin line inside a section: a pause between parts of the same thing, with an
optional label on it. It is `type: separator`, and it shares the box's
whitelist.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `id` | yes | kebab-case slug | — | Nothing visible. |
| `type` | yes | `separator` | — | Makes the component a line instead of a card. |
| `style` | no | `solid`, `dotted` (`assets/engine/build-data.mjs::SEPARATOR_STYLES`) | `solid` | The line's stroke. |
| `text` | no | text | none | A small label centred on a horizontal line. |
| `treatment` | no | `vertical` | horizontal | A vertical rule instead of a horizontal one. |
| `order` | no | number | position in the list | Its place among siblings. |
| `span` | no | integer ≥ 1 | 1 | How many tracks the line crosses; the section's columns make it full width. |
| `rowspan` | no | integer ≥ 1 | 1 | How many rows a vertical rule runs down. |

Limits:

- A row holding only horizontal separators and spacers is a thin row,
  `row.sep_h` (40 px) instead of `row.cell_h`.
- The build accepts the box's other fields on a separator and the engine draws
  none of them (`assets/engine/engine.js::buildSeparator`); `filters` on one
  fails LIT, `tokens` and `copy` fail the build.
- A separator is not clickable and joins no chip.

## Rail

A title-only label: the name at the start of a lane, or one level of a tree.
It is `type: rail`, and its keys are closed by
`assets/engine/build-data.mjs::RAIL_FIELDS`.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `id` | yes | kebab-case slug | — | Nothing visible. |
| `type` | yes | `rail` | — | Makes the component a label. |
| `title` | no | text | empty | The label's word. |
| `variant` | no | `blue`, `violet`, `gold`, `clay` (`assets/engine/build-data.mjs::RAIL_VARIANTS`) | none | The label's hue. |
| `treatment` | no | list of `centered`, `half`, `vertical`, `outside` (the box's set, `assets/engine/build-data.mjs::COMPONENT_TREATMENTS`) | none | `centered` centres the label; `vertical` rotates it down the cell. `half` pairs the rail with the next half leaf and draws both as half boxes, so it stops being a label (`assets/engine/engine.js::buildGrid`). `outside` builds and draws nothing on a rail (`assets/engine/engine.js::buildRail`). |
| `indent` | no | integer 0..3 (`assets/engine/build-data.mjs::RAIL_MAX_INDENT`) | 0 | Moves the drawn frame right one step (`indent_step`, 32 px) per tree level while the cell still fills its track. |
| `filters` | no | list of chip keys | none | The chips that light this rail. |
| `order` | no | number | position in the list | Its place among siblings. |
| `span` | no | integer ≥ 1 | 1 | How many tracks it crosses. |
| `rowspan` | no | integer ≥ 1 | 1 | How many rows it runs down; without it a horizontal rail is a thin row. |

Limits:

- A rail opens no detail: it has no `kicker`, `description` or `detail`.
- A thin rail whose title wraps past two lines fails RAILT, measured at the
  width its `indent` leaves.
- A row led by a rail is a lane, and every lane led by a rail in one grid
  must reach the same track (LANE).
- A heading over a group is a section title or the lead band; a rail used as a
  heading takes a cell from the grid.

## Spacer

The declared hole: a cell that is empty on purpose. It occupies its place and
draws nothing. It is `type: spacer`, and its keys are closed by
`assets/engine/build-data.mjs::SPACER_FIELDS`.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `id` | yes | kebab-case slug | — | Nothing visible. |
| `type` | yes | `spacer` | — | Makes the component a hole. |
| `order` | no | number | position in the list | Where the hole sits. |
| `span` | no | integer ≥ 1 | 1 | How many tracks the hole is wide. |
| `rowspan` | no | integer ≥ 1 | 1 | How many rows the hole is tall. |

Limits:

- Any other key is refused by name
  (`assets/engine/build-data.mjs::checkSpacer`): a cell meant to carry
  something is a box.
- Spacers in the rows above a shorter `rowspan` box bring it down to the
  floor its taller siblings stand on, so their heights compare.
- A hole nobody declared fails HOLE or RECT; a hole under a `vertical` box whose
  row a taller sibling sets fails FROZEN.

## Chip

A relation: a question in the chip bar that, clicked, lights every box and rail
that declares its key and dims the rest. The engine draws no arrows; a chip plus
order is how a deck says that things are connected. Its keys are closed by
`assets/engine/build-data.mjs::FILTER_FIELDS`.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `key` | yes | slug | — | Nothing visible; the name boxes and rails list in their `filters`. |
| `label` | yes | text | — | The words on the chip: the question it answers, or a numbered moment. |
| `steps` | no | list of lines, HTML allowed | none | A numbered narration shown in the card while the chip is lit. |

Limits:

- A chip needs at least two members; one member, none, or a key nobody declared
  fails CHIP.
- One key keeps one label across pages; two labels fail CHIP-X.
- `all` is the reset chip: the engine adds it when a page does not declare it,
  and it lights everything.
- Core chips (`document.filters`) come first on every page, in order; a page
  chip may not redeclare a core key with another label or steps.
- How far the rest dims is `dim.box` and `dim.label` (Look).

## Look

How the whole deck is drawn: the colours and the sizes, chosen once for where
the deck will be seen. The look never changes what anything means; a risk stays
a risk in every look.

Choose it by asking where people will see the deck:

- On a projector, in a room, read from the back: `projector`, which picks the
  `contrast` palette, taller rows (`row.cell_h` 160) and larger text with more
  clamp lines.
- In a document read up close: `report`, which picks `neutral` and smaller text.
- As the owner's or a client's deck: `brand`, which picks `rose-pine` and the
  house sizes; the brand's own colours go on top as `palette_overrides`.

If two places apply, ask which comes first; the same pages can be built twice
with another `look`. Without `look`, `palette` picks the colours alone and the
sizes stay the defaults below.

| Property | Required | Values | Default | What it changes on screen |
|----------|----------|--------|---------|---------------------------|
| `look` | no | `projector`, `report`, `brand` (`assets/engine/tokens.mjs::LOOKS`) | none | Palette and token set together. |
| `palette` | no | `neutral`, `rose-pine`, `rose-pine-moon`, `contrast` (`assets/engine/build-data.mjs::PALETTES`) | `neutral` | The colour skin; `rose-pine` uses Dawn on light and Main on dark, `rose-pine-moon` Moon on dark, `contrast` is high contrast. |
| `palette_overrides` | no | per theme: `bg`, `surface`, `surface2`, `zone`, `ink`, `body`, `muted`, `line`, `zone-line`, `crit`, `crit-soft`, `warn`, `warn-soft`, `olive`, `olive-soft`, `strong`, `strong-soft`, `clay`, `clay-soft`, `hue-blue`, `hue-violet`, `hue-gold`, `hue-clay` and their `-soft` tints (`assets/engine/build-data.mjs::PALETTE_OVERRIDE_KEYS`); a value is `#rgb`, `#rrggbb`, `rgb()` or `rgba()` | none | That one colour, in that theme. `bad` paints with `crit`, `good` with `olive`, `accent` with `strong`. |
| `row.cell_h` | no | 60..400 px | 130 | The height of every row of boxes. |
| `row.sep_h` | no | 16..120 px | 40 | The height of a row of horizontal separators. |
| `row.zone_min_h` | no | 0..600 px | 180 | The minimum height of a framed zone. |
| `row.compact_h` | no | 40..400 px | 74 | The row a `compact` section uses. |
| `space.base` | no | 2..16 px | 8 | The step every gap and padding multiplies. |
| `space.scale` | no | 7 increasing multipliers, 0.25..16 | [0.5, 1, 2, 3, 4, 6, 8] | The seven spacing sizes. |
| `frame.v` | no | 0..200 px | 28 | Vertical breathing room around the stage. |
| `frame.h` | no | 0..200 px | 40 | Horizontal breathing room around the stage. |
| `frame.top` | no | 0..200 px | 35 | Room above the stage. |
| `frame.narrow` | no | 0..64 px | 8 | Stage padding at the one-column tier. |
| `plane_max` | no | 640..7680 px | 1280 | The widest the content grows before it centres. |
| `cell_min_w` | no | 100..400 px | 120 | The narrowest a track may be before a grid folds. |
| `type.title.min_px` | no | 11..32 px | 15 | Box title size, lower bound. |
| `type.title.vw` | no | 0..5 vw | 1 | How the box title grows with the screen. |
| `type.title.max_px` | no | 11..40 px | 17 | Box title size, upper bound. |
| `type.title.lines` | no | 1..4 | 2 | Lines a box title shows before it is cut. |
| `type.desc.px` | no | 10..24 px | 12 | Description size. |
| `type.desc.lh` | no | 1..2.4 × size | 1.4 | Description line height. |
| `type.desc.lines` | no | 1..8 | 3 | Lines a description shows before it is cut. |
| `type.kicker.px` | no | 9..20 px | 10.5 | Kicker size. |
| `type.kicker.track_em` | no | 0..0.3 em | 0.09 | Kicker letter spacing. |
| `type.section_title.min_px` | no | 10..32 px | 13 | Section title size, lower bound. |
| `type.section_title.vw` | no | 0..5 vw | 0.85 | How the section title grows with the screen. |
| `type.section_title.max_px` | no | 10..40 px | 14.5 | Section title size, upper bound. |
| `type.section_title.track_em` | no | 0..0.3 em | 0.1 | Section title letter spacing. |
| `type.section_title.lines` | no | 1..4 | 2 | Lines a section title shows. |
| `type.section_sub.px` | no | 10..24 px | 12 | Section subtitle size. |
| `type.section_sub.lines` | no | 1..6 | 3 | Lines a section subtitle shows. |
| `type.rail.px` | no | 10..24 px | 13 | Rail label size. |
| `type.rail.track_em` | no | 0..0.3 em | 0.09 | Rail label letter spacing. |
| `type.rail_hue.px` | no | 9..24 px | 10.5 | Size of a hued rail's label. |
| `type.rail_hue.track_em` | no | 0..0.3 em | 0 | Letter spacing of a hued rail's label. |
| `type.rail_hue.pad_y` | no | 0..24 px | 10 | Vertical padding of a hued rail. |
| `type.panel.title_px` | no | 12..48 px | 19 | Title size in the detail card. |
| `type.panel.summary_px` | no | 11..32 px | 15 | Body size in the detail card. |
| `type.panel.kicker_px` | no | 9..24 px | 13 | Kicker size in the detail card. |
| `type.panel.kicker_track_em` | no | 0..0.3 em | 0.08 | Kicker letter spacing in the detail card. |
| `indent_step` | no | 8..96 px | 32 | One tree level of a rail's `indent`. |
| `dim.box` | no | 0.05..0.9 opacity | 0.18 | How faint an unlit box is while a chip is on. |
| `dim.label` | no | 0.05..0.9 opacity | 0.34 | How faint an unlit label is while a chip is on. |
| `panel.dock` | no | `bottom-left`, `bottom-right`, `top-left`, `top-right` | `bottom-left` | The corner the detail card docks to. |
| `panel.inset` | no | 0..96 px | 24 | The card's distance from that corner. |
| `panel.width_cols` | no | 1..4 root columns | 2 | The card's width; its height follows its text up to 65 % of the screen. |
| `breakpoints.stack` | no | 320..7680 px | 1440 | Below it, frames side by side stack. |
| `breakpoints.two` | no | 320..7680 px | 1000 | Below it, a leaf grid of three or more tracks steps to two. |
| `breakpoints.one` | no | 320..7680 px | 640 | Below it, every grid is one track and the page reads top to bottom. |
| `viewport.w` | no | 320..7680 px | 1920 | The screen the deck is shown on: the width where text-fit findings fail. |
| `viewport.h` | no | 240..4320 px | 1080 | The height a page is compared against (HEIGHT, advisory). |
| `default_columns` | no | 1..12 | 2 | Columns of any grid that declares none. |

Defaults are `assets/engine/tokens.mjs::DEFAULT_TOKENS`; ranges are
`assets/engine/tokens.mjs::TOKEN_SCHEMA`. Token keys are written as nested
maps in `document.yaml` (`tokens: { row: { cell_h: 120 } }`).

Limits:

- A look's tokens apply first and the document's `tokens` on top of them.
- The breakpoints keep `one < two < stack`, and each `min_px` stays at or below
  its `max_px` (`assets/engine/tokens.mjs::resolveDocTokens`).
- Only three tokens exist per node (Section, Box); every other size is
  deck-wide, so no cell can quietly stop matching its neighbours.
- Fixed and not tunable: the box border and radius, the half-title clamp, the
  rail indent depth and the contrast thresholds.
- `contrast` measures every colour pair of the palette and every pair an
  override enters, in both themes; an override is checked, and can fail, even
  on `neutral`.

## Forms

A page declares its form in `form`. The form draws nothing; it tells `model`
which checks judge the page, so the declared form is a promise about the shape.
The sets are `assets/tools/static-census.cjs::GRIDDED` (cells kept legible),
`assets/tools/static-census.cjs::GRID_DENSE` (a lone cell on its own row fails
ROW) and `assets/tools/static-census.cjs::WORDFIT` (a word too long for its
cell fails).

| Form | The properties that make it | Checks it adds |
|------|-----------------------------|----------------|
| `dashboard` | Peer sections side by side, each a leaf grid of boxes; `span` weights the zones. | legible, dense, word fit |
| `timeline` | One section whose `columns` equal its number of boxes, read left to right by `order`. | none of the three: one long row is legitimate |
| `flow` | Sections in `order` as phases, boxes in `order` as steps inside them, one chip whose members cross every phase. | legible, word fit |
| `comparison` | One section per option, `span` 1 each, the same inner grammar in each so rows compare across. | legible, dense |
| `mindmap` | A centre band (`span` equal to the page's columns) with sections placed symmetrically around it: the grid cannot radiate. | legible |
| `planner` | One grid of cards whose `kicker` carries the state, with one chip per plan. | legible, dense |

## Measured limits

These were measured on real decks, not derived from the schema. Each says where
it was measured; a deck with other tokens may land elsewhere.

- A box title of about 23 characters fits one line in a four-column grid at the
  presentation viewport (deck gaia-architecture-overview v8, 2026-10-05,
  default tokens).
- A box title of about 20 characters fits one line in a five-column grid, and
  a box's line under the title (its `description`, which that deck called its
  subtitle) holds
  about 33 characters with four columns and 28 with five (deck
  gaia-architecture-overview v8, 2026-10-02, default tokens).
- A four-column grid folds to two columns at about 900 px of screen width in
  that deck; at that fold a band with an odd number of labels leaves an empty
  cell that `model` refuses, closed there with a spacer.
- Rows were kept at 60 px or taller in that deck; 60 px is also the schema
  floor of `row.cell_h`. Something that lasts longer was drawn taller with
  `rowspan`, not with a larger row.
- In that deck a page's content ended at about 1050 px of a 1080 px screen,
  because the deck's own bar takes the rest; HEIGHT compares against
  `viewport.h` and only advises.
- In the video a chip cue fits only if at least one of its members is on
  screen when the chip fires: the member and every cued section above it
  revealed at or before that moment. An earlier chip is reported as a problem
  and the plan does not fit (`assets/tools/video/driver.js::load`); in that
  deck this sometimes put the chip one sentence after its question.

## Seen in the seed

The seed deck under `assets/data/` is a tour in four steps, named in its tabs:
Story, Ideas, Pieces, Data. Its Pieces step is a catalogue: one entry per piece
below, each showing the piece live, its YAML, what it says and the neighbour
piece it is confused with. The seed's `test` holds this table and the catalogue
to each other in both directions.

| Piece | Seed page |
|-------|-----------|
| **section** | `pieces-1-frame-and-leaves` |
| **component** | `pieces-1-frame-and-leaves` |
| **box** | `pieces-1-frame-and-leaves` |
| **separator** | `pieces-1-frame-and-leaves` |
| **rail** | `pieces-1-frame-and-leaves` |
| **spacer** | `pieces-1-frame-and-leaves` |
| **kicker** | `pieces-2-slots` |
| **title** | `pieces-2-slots` |
| **description** | `pieces-2-slots` |
| **detail** | `pieces-2-slots` |
| **section title** | `pieces-2-slots` |
| **span** | `pieces-3-size-and-order` |
| **rowspan** | `pieces-3-size-and-order` |
| **band** | `pieces-3-size-and-order` |
| **lead band** | `pieces-3-size-and-order` |
| **order** | `pieces-3-size-and-order` |
| **comparison** | `pieces-3-size-and-order` |
| **variant** | `pieces-4-colour-and-relation` |
| **verdict variant** | `pieces-4-colour-and-relation` |
| **hue** | `pieces-4-colour-and-relation` |
| **treatment** | `pieces-4-colour-and-relation` |
| **outside** | `pieces-4-colour-and-relation` |
| **palette** | `pieces-4-colour-and-relation` |
| **chip** | `pieces-4-colour-and-relation` |
</content>
</invoke>
