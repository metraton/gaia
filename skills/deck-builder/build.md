# Diagram Builder — build

This file is for the subagent that builds the deck. The orchestrator has agreed
with the person, page by page, a table of sections and components and the deck's
conventions (`SKILL.md`, phases A-D); it hands you those tables, the
conventions and where the deck lives. Your job is the part the person never
sees: write the tables as YAML, run the checks, and return three things:

1. the YAML you wrote under `data/`;
2. the check results, verbatim;
3. the census as JSON (`npm run census --prefix <deck> -- --json`), which the
   orchestrator reads against the tables.

You own the schema; the orchestrator owns the meaning. Every piece and every
property, with its values and what it changes on screen, is in
[toolbox.md](toolbox.md); this file does not repeat them. When a table asks for
something no property can say, report it as a finding: do not invent a field
(the build refuses an unknown key by name) and do not quietly pick another
piece, because either way the deck stops saying what was agreed.

The detail text of each box is yours to write: the tables say only whether a
box has a detail and what it defines. Write it from the source, with the real
names, because it is the one text the person did not review line by line.

`<deck>` is the deck's root, the directory holding `package.json`,
`index.html`, `engine/`, `data/` and `tools/`. In the skill itself it is
`assets/`.

## Starting a deck

- **A new deck.** Copy `assets/` whole into the place the person named:
  `index.html`, `package.json`, `engine/`, `tools/` and the seed `data/`.
  Without `tools/` the deck cannot be checked. Then replace the seed's pages
  with the agreed ones and set the document's title and look.
- **A deck inside another repository.** Put it in its own directory (for
  example `diagram/`), so its `index.html` does not collide with the host's.
- **A new page.** Add `data/pages/<id>.yaml` and its entry in
  `document.yaml` (`id`, `name`, `order`, `visible: true`, `file`); the two
  `id`s must match.
- **A deck generated from data.** Change the generator, never its output: the
  next run overwrites any hand edit.

## The build lane

The deck has no dependencies: Node alone runs every script, with no
`npm install`. Spell a script as `npm run <script> --prefix <deck>`, the script
first and the prefix after; that is the spelling Gaia's command lane was
measured on.

| Script | Runs | What it establishes | In Gaia |
|--------|------|---------------------|---------|
| `build` | `engine/build-data.mjs` | Regenerates `data/data.generated.js` and `data/breakpoints.generated.css` from the YAML. It is also the schema check: an unknown key, a value outside its set, a broken lead band or an unpaired `half` fails here, by name. | writes files: request it for signature |
| `model` | `tools/check-layout.mjs` | The layout, as arithmetic over the YAML at every container tier: the rectangle closes, no hole, no dead track, chips have two ends, text fits at the presentation viewport. A run that asserted nothing is red. | reads: runs free |
| `census -- --json` | `tools/census.mjs` | What each page is, in reading order, by the ids the YAML authored. It exits non-zero when the last build no longer matches the YAML. | reads: runs free |
| `contrast` | `tools/contrast-audit.cjs` | Every colour pair of every palette and every override, against WCAG 2.1, in both themes. | reads: runs free |
| `test` | `tools/test-guards.mjs` | The negative suite: it fabricates broken decks in a temporary directory and proves each check fails on the defect it claims to catch. | writes temp files: request it for signature |

Before running a script, say in one plain sentence what it does and which file
it runs, so whoever reads the transcript can open the script first.

## The check loop

1. Edit the YAML under `data/`.
2. `build`. Run it after every YAML change: `index.html` draws the generated
   bundle, and a stale bundle shows the old deck without complaint.
3. `model`. Never return a layout change on red. Findings are grouped per
   check; `[FAIL]` exits non-zero, `[INFO]` advises and never fails, and
   `[NOT ASSERTED]` is counted in the headline: a non-zero count is not green.
   A finding names the page, the grid, the tier and the measured value, which
   is usually enough to know which property to change (toolbox.md, each
   piece's limits).
4. `census -- --json`. Its top-level `problems` list must be empty.
5. `contrast`, when the look, the palette, an override or a hue changed.
6. `test`, when you changed a check, the placement model or the engine.
7. On a failure, fix the YAML and go back to step 2.

These four checks are the whole check of a diagram. Do not open a
browser for it, do not take screenshots of it and do not write a browser probe:
whether the page says what was meant is judged by the person's eye when they
open `index.html`, and no script reaches that judgement.

Return the verdict in that shape: what each check established and what none of
them can. For example: "MODEL: 8708 assertions, 0 not asserted · CENSUS:
attached, no problems · CONTRAST: pass · the person's eye: pending".

**A defect the person's eye catches becomes a check** before the change closes:
in `tools/check-layout.mjs` when it is a statement about the data, with a case
in `tools/test-guards.mjs`. A new check is not landed until it has been seen to
fail: break the rule it guards, watch `test` go red, restore it. A check that
has never failed proves nothing.

## Reading the census against the tables

The census states the deck the way the tables state it. The orchestrator reads
it; you hand it over whole. Each page is an entry of `pages[]`, found by its
authored `id`, and every class below is read even when the first one matches,
because "no difference" is a finding stated, not assumed.

| Class | In the agreed table | In the census | A difference when |
|-------|---------------------|---------------|-------------------|
| Sections | each section, which one sits inside which, which sit side by side | nodes with `kind: "section"` and their `children`; `start {row, col}`, 1-based within the parent; the parent's `grid`: `stack` puts one child per row, `tracks`, `row` and `root` place children side by side by `start` | a section is missing or extra, sits in the wrong parent, or sections meant side by side do not share `start.row` |
| Sizes | the widths and heights that carry meaning | `width`, a reduced fraction of the parent (`1/1`, `1/2`, `2/3`) or `content`; `span.authored` against `span.resolved`; `span.rows` for height | a fraction differs from the agreed proportion, the engine cut a `span`, or a height differs |
| Colours | the colour legend and the boxes it names | `variant` and `variant_source` per node, and the page's `variants` map from colour to ids; `variant_source` is `authored`, `default` (unset, so neutral) or `colourless` (separator, spacer) | a legend colour is missing or on other boxes, a plain box is coloured, or a named colour shows `default` |
| Chips | each chip, its label and its members | the page's `chips[]`: `key`, `label`, `core`, `members`, `scope` (`members`, `all` for the reset, or `none`) | members differ in either direction, a label no longer says the agreed words, or `scope` is `none` |
| Text | titles, kickers, and whether each box opens a detail | `title` per node; kickers and details are read from the returned YAML | a title, a kicker or a detail's presence differs from the table |

A `core: true` chip is inherited by every page; read its members on each page
it reaches. Two things the census cannot tell, so no difference is read into
them: `width: "content"` has no share (a vertical separator, a rail or a box
directly in a row of sections takes its natural size), and `start` is the
placement at the presentation viewport only; narrower tiers repack, and `model`
proves that collapse.

Each difference is named in one line (the class, what the table says, what the
census says, the id) and takes one of two outcomes: sent back, when the build
missed the table, or told to the person, when the difference was forced (the
engine cut a span, a page needed a hole) and the reason is said before they
open the deck.

## The engine and the bundle move together

`index.html`, `engine/engine.js` and `data/data.generated.js` are one coupled
trio: a new engine beside an old bundle does not fail, it shows the stale deck.
`build` re-pairs them, and `model` catches a skipped build (CENSUS, WORDS).
`data/data.generated.js` is generated and committed so the deck opens under
`file://`; never edit it by hand. Serving the trio without a stale pair is in
[assets/README.md](assets/README.md).

## The strict schema, and migrating an old deck

Each node kind has a whitelist in `assets/engine/build-data.mjs`; any other key
is a build error naming the page, the node and the key, with a near-miss hint
(`colummns`: did you mean `columns`?). A deck written before the two colour and
structure axes fails the build with the change to make:

| Old spelling | New spelling |
|--------------|--------------|
| `variant: plain` / `envelope` on a section | `treatment: [plain]` / `[envelope]` |
| `variant: ext`, `variant_extra: [ext]` | `treatment: [outside]` |
| `variant_extra: [centered]` | `treatment: [centered]` |
| `orientation: vertical` | `treatment: [vertical]`; delete `orientation: horizontal` |
| `status:` | `kicker:` |
| `crit`, `ok`, `strong`, `store` on a component | `bad`, `good`, `accent`, `muted` |
| `danger`, `safe` on a section | `bad`, `good` |
</content>
</invoke>
