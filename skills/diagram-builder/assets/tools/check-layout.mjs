// ─────────────────────────────────────────────────────────────────────────
// check-layout.mjs — the MANDATORY, PROGRAMMATIC layout gate. No browser.
// @version 1.0.0  (part of the diagram-builder skill; keep the placement model in
//                  sync with engine/engine.js buildGrid + the container-query
//                  tiers in index.html)
//
// Run: npm run model   (or: node tools/check-layout.mjs [deckRoot])
//
// WHY THIS EXISTS, AND WHY IT IS THE GATE
// The layout of this deck is a SPREADSHEET: a filled rectangle of uniform cells.
// "Filled rectangle" is not an aesthetic judgement — it is an ARITHMETIC IDENTITY,
// and an identity can be tested like a unit test:
//
//        Σ (spanCols × rowspanRows)  ===  tracks × rowCount
//
// The left side is what the DATA authors; the right side is the rectangle the grid
// draws. They are equal exactly when the rectangle CLOSES. Any hole — a track no
// cell reaches, a short last row, a merge that did not fit and pushed itself down —
// leaves the sum SHORT BY EXACTLY ITS OWN AREA. So the defect is not merely
// detected, it is MEASURED, and it is measured without pixels, without a render,
// and without Playwright.
//
// That matters beyond speed. Gaia is installed in places where no browser exists.
// A guardrail that needs Chromium is a guardrail that is ABSENT precisely where a
// deck is most likely to be authored blind. So the division of labour is:
//
//   npm run model     MANDATORY, this file. Static, arithmetic, js-yaml only
//                     (already a build dependency). Proves the layout CLOSES and
//                     the data is sound. It has never seen a pixel.
//   npm run render    MANDATORY too. Renders one width in Chromium and asserts
//                     only what genuinely needs PIXELS (legibility, word fit, text
//                     truncation, flex wrap points, real geometry) — the only half
//                     that can tell whether the stylesheet IMPLEMENTS what this
//                     file assumed. It skips cleanly and exits 0 where no browser
//                     exists, which is why requiring it costs nothing.
//   npm run gate      Both, in order. The only thing a verdict may cite.
//
// WHAT THIS REPLACES: the browser width SWEEP. `validate` used to render 5 widths ×
// 2 themes × 5 reloads to prove the …→2→1 collapse cascade. But the collapse is a
// CONTAINER QUERY: the cuts at 640 / 1000 / 1440 px depend on NOTHING except the
// stage container's width, so "a 4-column grid renders 2 tracks at 900px" is a PURE
// FUNCTION of (authoredColumns, containerWidth) — `tracksFor` below. Once that is
// arithmetic, re-measuring it in a browser five times is re-confirming a
// multiplication table. The sweep is gone; the identity is checked at all five of
// its former widths here, in milliseconds.
//
// WHAT IT ASSERTS
//   RECT   the closure identity above, per grid, per tier. Reports any deficit as
//          an exact cell area. Supersedes the render-time L (cells fill width).
//   HOLE   interior holes enumerated by coordinate — a merge that did not fit in
//          the tracks left on its row and dropped down, leaving a gap ABOVE it.
//   TRACK  a dead track: a column the content never reaches. Supersedes E.
//   ROW    an orphan row: a lone single cell on its own row while a sibling row
//          holds two or more. Supersedes P.
//   LANE   rail-led swimlanes of unequal length within one grid (hard), and
//          parallel single-column stacks of unequal depth (advisory).
//   FROZEN an undeclared hole UNDER an element that cannot grow: a `vertical`
//          box (its height IS its authored rowspan) inside a stretched flex row
//          that a taller sibling sets. Heights are floors, so it under-states.
//   BAND   band placement: a band owns its whole row, and a declared span never
//          exceeds the columns it is placed in.
//   TIER   the derived tracks-per-tier table, and the monotonicity of the cascade
//          (tracks never grow as the container narrows). Supersedes F.
//   CHIP   filter referential integrity in BOTH directions, plus ARITY: a chip
//          with a single member does not express a relation, and since an active
//          chip dims everything it does not name, a one-member chip switches the
//          deck off. Supersedes K, which only closed the join.
//   LIT    a filter declared on a leaf type the engine never lights (separator,
//          spacer): the CHIP join closes and the render cannot show it.
//   RAILT  a thin rail's title past its two-line ceiling: the rail row is `auto`
//          and `.rail-title` has no clamp, so a third line GROWS the row.
//   ORDER  a duplicate effective `order` among siblings — today resolved silently
//          by the index tie-break, so the author's intended sequence is a
//          coin flip that can change under an unrelated edit.
//   TEXT   the character budget (ADVISORY): title token, kicker token, title
//          clamp, description clamp.
//   WORDS  every authored string is present verbatim in the generated bundle —
//          the text-only staleness CENSUS cannot see.
//   INK    the height budget of a box against its row (a `compact` grid's own
//          shorter row included), on every page not declared `text_fit: advisory`.
//   HEADER a section title/subtitle against its clamp at the zone's inner width.
//   HEIGHT the page height predicted from the placement model against the
//          `document.yaml` viewport (ADVISORY; validate VH measures it).
//   SPAN   the stylesheet implements the span→tracks rules every width here
//          assumes.
//   CENSUS data/*.yaml vs data/data.generated.js (shared with validate, via
//          tools/static-census.cjs — one parse path, so the two gates cannot
//          disagree about what the data says).
//
// WHAT IT CANNOT SEE, ON PURPOSE
// Arithmetic knows the rectangle closes; it does not know the text fits inside it.
// Pixel legibility, mid-word wrapping, description clamping, the flex wrap point,
// real rendered proportions, and sibling collision are RENDER truths and stay in
// `validate`. This file never claims them, and never pretends a green run here is
// a verdict on how the deck LOOKS.
//
// NO FALSE GREEN. Two structural rules, mirroring validate's own:
//   • a run that asserted NOTHING is RED, never green (the `total === 0` gate).
//   • the deck root is taken from argv/env so this gate can be pointed at a BROKEN
//     FIXTURE outside the repo and be SHOWN to fail. A guardrail only ever run
//     against the deck that is supposed to pass has never been shown to work.
// ─────────────────────────────────────────────────────────────────────────
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import censusLib from './static-census.cjs';
import { DEFAULT_TOKENS, mergeTokens, resolveNodeTokens, cssVars, space } from '../engine/tokens.mjs';

const { loadAuthoredDeck, staticCensus, cssBreakpoints, loadGenerated,
  DEFAULT_ROOT, DEFAULT_FORM, GRID_DENSE, WORDFIT, isTextFitStrict } = censusLib;

const HERE = path.dirname(fileURLToPath(import.meta.url));
// The deck root: argv wins, then the env override, then the normal location.
// Both overrides exist for the negative tests (a broken fixture in a temp dir).
const ROOT = process.argv[2] ? path.resolve(process.argv[2])
  : process.env.DIAGRAM_DECK_ROOT ? path.resolve(process.env.DIAGRAM_DECK_ROOT)
  : (DEFAULT_ROOT || path.join(HERE, '..'));

// ── THE COLLAPSE CASCADE, AS ARITHMETIC ───────────────────────────────────
// The three breakpoints come from static-census.cjs, which mirrors the
// `@container stage` queries in index.html and can read the real declarations
// back out — the CSS line below ASSERTS the mirror against them, so this gate
// cannot quietly drift from the stylesheet the browser actually obeys.
// They are CONTAINER queries, not media queries, and the container is `.stage`
// (`width:100%` of the deck) — so ONE width governs EVERY grid at EVERY nesting
// depth. That is what makes the track count a pure function instead of a layout
// negotiation, and therefore what makes the browser sweep redundant.
// The breakpoints, the sample tiers, the presentation viewport and the default
// column count are all TOKENS: applyTokens() (called by main() with the
// bundle's `__DOC__.tokens`) sets them before any page is checked; importers
// get DEFAULT_TOKENS.
let BP_STACK, BP_TWO, BP_ONE, TIERS, PRESENT, SWEEP, DEFAULT_SECTION_COLUMNS, TOKENS;

// One sample width inside each collapse band (the 1-track endpoint, the 2-track
// intermediate, the first fully-authored tier) and two side-by-side tiers. The
// preferred widths are the ones the retired browser sweep used; a width that a
// moved breakpoint pushes out of its band is replaced by the band's midpoint.
function tiersFor(bp) {
  const inBand = (pref, lo, hi) => (pref > lo && pref <= hi ? pref : Math.round((lo + hi) / 2));
  const huge = Math.max(1920, bp.stack + 1);
  return [
    { name: 'min', w: inBand(600, 0, bp.one) }, { name: 'medium', w: inBand(900, bp.one, bp.two) },
    { name: 'large', w: inBand(1200, bp.two, bp.stack) },
    { name: 'huge', w: huge }, { name: 'ultra', w: Math.max(2560, huge + 1) },
  ];
}
// The presentation tier (`tokens.viewport`), where a title, a description or a
// section header that overflows its lines FAILS instead of advising; the sweep
// gains its width as a sixth tier when it is not one of the five above.
const sweepFor = w => (TIERS.some(t => t.w === w) ? TIERS
  : [...TIERS, { name: 'present', w }].sort((a, b) => a.w - b.w));

function applyTokens(tokens) {
  TOKENS = tokens;
  ({ stack: BP_STACK, two: BP_TWO, one: BP_ONE } = tokens.breakpoints);
  TIERS = tiersFor(tokens.breakpoints);
  PRESENT = { ...tokens.viewport };
  SWEEP = sweepFor(PRESENT.w);
  DEFAULT_SECTION_COLUMNS = tokens.default_columns;
  Object.assign(CSS_TEXT, metricsFrom(tokens));
}

// A node's override delta, resolved exactly as the build resolves it (same
// function), so a `compact` preset or a section `tokens:` moves this model too.
function nodeDelta(node, kind) {
  try { return resolveNodeTokens(node, kind, TOKENS, `node "${node && node.id}"`); }
  catch (e) { fail('TOKENS', `node ${node && node.id}`, e.message); return null; }
}
// The engine's reserved "show everything" chip: legitimately has no members.
const RESET_CHIP = 'all';

// How many tracks a leaf grid renders at a given container width. PURE: this is
// the whole responsive behaviour of the deck, and the reason the width sweep is
// redundant. `cols` is the EFFECTIVE (clamped) authored count, never the raw one.
function tracksFor(cols, cw) {
  if (cw <= BP_ONE) return 1;                 // the …→2→1 endpoint
  if (cw <= BP_TWO) return cols >= 2 ? 2 : 1; // the 2-track intermediate
  return cols;                                // fully authored
}

// ── THE PLACEMENT MODEL, MIRRORED FROM engine.js ───────────────────────────
// widthAtTier + isBandAtTier + place mirror the engine's widthAtTier /
// isBandAtTier / rowOccupants. They are a MIRROR and not an import because the
// engine is a plain browser script under `file://`, where an ES module is
// CORS-blocked (origin 'null') — the deck's contract is that it opens with a
// double click, so it has no module system to share one through.
// The mirror is not trusted on a comment: tools/test-guards.mjs extracts the
// engine's own functions from its real source and asserts they agree with these
// over a corpus of grid shapes, including a span-3-of-4 at the 2-track tier.

// How many TRACKS one slot occupies at a tier. Mirrors the CSS rules that govern
// a merge as the grid collapses:
//   .msp   (span >= cols)      grid-column: 1 / -1  — full width at EVERY tier
//   .mspan (1 < span < cols)   grid-column: span var(--span)  at the authored tier,
//                              span var(--span2) at the 2-track tier (--span2 =
//                              round(span/cols·2) clamped [1,2], emitted by the
//                              engine so a partial merge keeps its PROPORTION),
//                              and 1 / -1 at the 1-track endpoint.
function widthAtTier(span, cols, tracks) {
  if (tracks === cols) return span;                // authored tier: exact tracks
  if (span >= cols) return tracks;                 // band: 1 / -1
  return Math.max(1, Math.min(tracks, Math.round(span / cols * tracks)));  // --span2
}

// Whether a slot OWNS ITS ROW at this tier — the width it resolves to, never the
// authored span, because band-ness is TIER-RELATIVE in the CSS: at the 640px
// endpoint `.sec-grid:not(.sec-compound) > .mspan` becomes grid-column:1/-1, so a
// partial merge IS a band there, and a merge that fills both tracks of the
// 2-track tier already spans its whole row.
const isBandAtTier = (w, tracks) => w >= tracks;

// Whether a slot carries the .msp CLASS. This is the AUTHORED declaration —
// `span == columns` — and it is a different question from isBandAtTier: it is
// what makes the engine emit .msp (and therefore what the root's
// `:has(> .msp)` grid rule keys on), so it is answered at the authored tier
// only. Both are needed; conflating them is the divergence this pair replaces.
const isBandClass = (span, cols) => span >= cols;

// ── THE DATA MODEL, MIRRORED FROM engine.js ───────────────────────────────
const isSection = n => !!(n && Array.isArray(n.children));
const treatmentsOf = n => (Array.isArray(n && n.treatment) ? n.treatment : []);
const isHalfLeaf = c => c && !isSection(c) && treatmentsOf(c).includes('half');

// engine.js orderedChildren: explicit `order` wins, else the 1-based list index;
// ties keep declared order (stable). Returns the children WITH their effective
// order, because ORDER (below) needs to see the collision the tie-break hides.
function orderedChildren(list) {
  return [...(list || [])]
    .map((c, i) => ({ c, i, eff: c && c.order != null ? c.order : i + 1 }))
    .sort((a, b) => (a.eff === b.eff ? a.i - b.i : a.eff - b.eff));
}

// engine.js buildGrid: consecutive half leaves pair into ONE `.half-slot`, which
// is what actually occupies the grid cell. A pair is ONE slot — counting the two
// halves as two fillable cells would let an over-authored `columns` reserve a dead
// track, which is why the engine pairs BEFORE clamping and so does this.
function slotsOf(children) {
  const ordered = orderedChildren(children).map(x => x.c);
  const slots = [];
  for (let i = 0; i < ordered.length; i++) {
    if (isHalfLeaf(ordered[i]) && isHalfLeaf(ordered[i + 1])) {
      slots.push({ node: ordered[i], pair: [ordered[i], ordered[i + 1]], half: true });
      i++;
    } else {
      slots.push({ node: ordered[i], half: false });
    }
  }
  return slots;
}

// engine.js buildGrid's GROW-WITH-CONTENT clamp: a LEAF grid's effective column
// count is capped at what its children can actually FILL — the number of
// single-cell slots, or the widest band's span, whichever is larger. A compound
// grid is a flex row (no fixed tracks) and keeps its authored count.
function effectiveCols(authored, slots, compound) {
  let cols = Math.max(1, Number.isInteger(authored) && authored > 0 ? authored : DEFAULT_SECTION_COLUMNS);
  if (compound || !slots.length) return cols;
  let singleCells = 0, maxSpan = 1;
  for (const slot of slots) {
    const s = Math.max(1, Math.min(slot.node.span || 1, cols));
    if (s === 1) singleCells++; else if (s > maxSpan) maxSpan = s;
  }
  return Math.min(cols, Math.max(1, singleCells, maxSpan));
}

const rowspanOf = n => Math.max(1, Math.floor(Number(n && n.rowspan) || 1));

// ── THE TEXT BUDGET: CHARACTERS, NOT PIXELS ───────────────────────────────
// Everything above is geometry, and geometry is where arithmetic is EXACT. TEXT
// is the one irreducible thing left — the defect the eye kept catching with all
// three gates green: a description cut mid-sentence, a title wrapped past its
// clamp, a rotated label clipped with no ellipsis. So this is the one
// APPROXIMATE check in the file, and it is declared as such:
//
//        IT MARKS WHAT IS RISKY. IT NEVER BLESSES WHAT IS GOOD.
//
// The direction is chosen, not incidental. The DEMAND is over-estimated (the
// monospace advance below is the CEILING of the families the CSS names) and the
// CELL is derived from the stylesheet's own chrome, so a text flagged here is
// genuinely near its limit, and a text that fails the render gate's N is
// necessarily flagged here first. The converse does not hold — which is exactly
// why every finding is an ADVISORY ([INFO], never a failure). `validate`'s N
// measures the real font with real font metrics and is the VERDICT. The two
// COMPOSE: this is the fast warning where no browser exists, N is the ruling.
// This file never claims an authority the arithmetic does not have.
//
// THE KICKER TOKEN IS THE ONE ROLE WITH NO RULING ABOVE IT, AND IT IS STILL AN
// ADVISORY. `kicker-token` (see textBudget) budgets `.box .k` exactly as `token`
// budgets `.box .t`, and it was added because the eye caught a fracture the
// budget could not see: it measured titles only. There is no render invariant
// for the kicker — N covers the title alone — so unlike every other kind here,
// this one has no verdict downstream of it. That is an argument for making it
// [FAIL], and it is outweighed by the argument against: the measurement is the
// SAME approximation as the others (one assumed constant, MONO_ADVANCE_EM, and
// a chain of mirrored stylesheet numbers), and a check calibrated to
// over-estimate demand must not be the thing that stops a build. Two severities
// over one arithmetic would say the estimate is trustworthy for the kicker and
// merely indicative for the title, which is false. So it reports as [INFO] with
// the others, and the gap it leaves — no pixel ruling for the kicker — is a
// stated limitation rather than one papered over with a harder verdict.
//
// WHY CHARACTERS AND NOT PIXELS. Both text roles are MONOSPACE (`--mono`), so a
// token's width is its LENGTH times one advance — which means the cell can be
// expressed as "how many characters fit". That is the author's own unit, and it
// is what lets every finding name the number MEASURED, the number AVAILABLE and
// the exact CELL instead of offering generic advice.
//
// WHAT IT DOES NOT SEE. A `vertical` treatment rotates the title onto the block
// axis, where the constraint is the cell's HEIGHT, not its width — exempt here
// for the same reason N exempts it. A box sitting DIRECTLY in a nested compound
// grid is content-sized (`.sec-grid.sec-compound > .box { flex:0 0 auto }`), so
// it has no track to be budgeted against and is not visited: only grids with a
// real track model are.

// THE FIXED CHROME: the craft values tokens.mjs deliberately does not expose.
// Each is still read back out of index.html by the CSS check (CSS_FIXED_PROBES),
// so a stylesheet edit that moves one fails the gate instead of skewing it.
const FIXED = {
  zoneBorder: 1, boxBorder: 1.5, railBorder: 1, railIndentTitleBorder: 1,
  boxGap: 2, titleMarginPx: 1, halfTitleLines: 1,
  // RAILT's ceiling: .rail-title has NO clamp, so a third line silently grows the
  // auto row and the stack it lives in — the gate owns that limit.
  railTitleLines: 2,
};
// The rendered line box of one mono rail-title line, as a multiple of its font
// size: 13px renders a 15px line, which is what makes a one-line rail 33px.
// validate-layout.cjs derives its rail-row band from the same factor.
const RAIL_LINE_EM = 1.15;

// The model's metrics, derived from a resolved token set: the width chain
// (canvas, plane, zone and box insets), the character budget (font sizes,
// tracking, clamps), the height chain (rows, floors) and the ink chain.
// Kicker tracking is not optional slack: at 10.5px 0.09em adds ~15% to every
// character, so a budget that ignored it would overstate the cell.
function metricsFrom(t) {
  const s = n => space(t, n);
  const ty = t.type;
  return { ...FIXED,
    planeMax: t.plane_max, frameH: t.frame.h, canvasPad: s(3),
    frameHNarrow: t.frame.narrow, canvasPadNarrow: t.frame.narrow,
    gap: s(2), zonePad: s(3), boxPad: s(3),
    titleMinPx: ty.title.min_px, titleVw: ty.title.vw, titleMaxPx: ty.title.max_px,
    kickerPx: ty.kicker.px, kickerTrackEm: ty.kicker.track_em,
    descPx: ty.desc.px, descLineEm: ty.desc.lh, titleLines: ty.title.lines, descLines: ty.desc.lines,
    cellH: t.row.cell_h, sepRowH: t.row.sep_h, zoneMinH: t.row.zone_min_h,
    // The FLOOR of a rail's `auto` row: one title line, 2 × --s-2 padding, 2 borders.
    railRowH: Math.ceil(ty.rail.px * RAIL_LINE_EM) + 2 * s(2) + 2 * FIXED.railBorder,
    railTitlePx: ty.rail.px, railTrackEm: ty.rail.track_em,
    railHueTitlePx: ty.rail_hue.px, railHueTrackEm: ty.rail_hue.track_em, railHuePadX: s(1),
    boxPadY: s(2), halfPadY: s(1),
    // THE COMPACT ROW: the preset row height, a --s-1 row gap and box padding,
    // and NO description clamp — its text is judged by INK against the slot.
    compactCellH: t.row.compact_h, compactRowGap: s(1), compactBoxPadY: s(1),
    indentStep: t.indent_step, railIndentTitlePadX: s(3),
    ztitleMinPx: ty.section_title.min_px, ztitleVw: ty.section_title.vw,
    ztitleMaxPx: ty.section_title.max_px, ztitleTrackEm: ty.section_title.track_em,
    ztitleLines: ty.section_title.lines, zsubPx: ty.section_sub.px, zsubLines: ty.section_sub.lines,
    zheaderGap: s(1),
  };
}
const CSS_TEXT = {};
applyTokens(DEFAULT_TOKENS);

// THE ONE ASSUMED CONSTANT. The stylesheet gives every width and every font
// size; what it cannot give is how wide a CHARACTER is. Every family `--mono`
// names (ui-monospace, SF Mono, Menlo, Consolas, and the metric-compatible Linux
// defaults DejaVu/Liberation Mono) has a fixed advance between 0.55em (Consolas)
// and 0.6023em (Menlo). 0.6 is the CEILING of that range, so a character is
// never assumed NARROWER than it renders and the estimated demand stays an upper
// bound — which is what keeps this check on the "marks the risky" side. That
// ceiling also carries the slack for what the width chain below deliberately
// omits: the `scrollbar-gutter:stable` reserve at the tiers below the plane cap,
// and the extra 0.5px per side an `.accent` box's 2px border costs.
const MONO_ADVANCE_EM = 0.6;

// THE STYLESHEET, READ BACK. Three contracts, all against index.html:
//   • the FIXED chrome values, as numbers (they are not tokens);
//   • SHAPES: every rule the model computes with still spends its token through
//     var() — "clamps to var(--desc-lines)", never "clamps to 3" — so the model
//     and the browser read the same number from the bundle;
//   • DEFAULTS: every var() fallback and :root default of a token equals the
//     projection of DEFAULT_TOKENS, so a deck opened without its bundle draws the
//     same defaults the gates assume.
const CSS_FIXED_PROBES = [
  ['zoneBorder', /\.zone\s*\{[^}]*?border:\s*(\d+(?:\.\d+)?)px/],
  ['boxBorder', /\.box\s*\{[^}]*?border:\s*(\d+(?:\.\d+)?)px/],
  ['halfTitleLines', /\.box\.half \.t\s*\{[^}]*?-webkit-line-clamp:\s*(\d+)/],
  ['boxGap', /\.box\s*\{[^}]*?gap:\s*(\d+(?:\.\d+)?)px/],
  ['titleMarginPx', /\.box \.t\s*\{[^}]*?margin-bottom:\s*(\d+(?:\.\d+)?)px/],
  ['railIndentTitleBorder', /\.rail\.indented \.rail-title\s*\{[^}]*?border:\s*(\d+(?:\.\d+)?)px/],
];
const CSS_TEXT_SHAPES = [
  ['.sec-plane caps at var(--plane-max)', /\.sec-plane\s*\{[^}]*?max-width:\s*var\(--plane-max/],
  ['.canvas insets by var(--frame-h) and pads var(--s-3)',
    /\.canvas\s*\{[^}]*?right:\s*var\(--frame-h\)[^}]*?padding:\s*var\(--s-3\)/],
  ['.box .t sizes clamp(var(--title-min), var(--title-vw), var(--title-max))',
    /\.box \.t\s*\{[^}]*?font-size:\s*clamp\(var\(--title-min[^)]*\),\s*var\(--title-vw[^)]*\),\s*var\(--title-max/],
  ['.box .t clamps to var(--title-lines)', /\.box \.t\s*\{[^}]*?-webkit-line-clamp:\s*var\(--title-lines/],
  ['.box .desc clamps to var(--desc-lines)', /\.box \.desc\s*\{[^}]*?-webkit-line-clamp:\s*var\(--desc-lines/],
  ['.box .m sizes var(--desc-px) at var(--desc-lh)',
    /\.box \.m\s*\{[^}]*?font-size:\s*var\(--desc-px[^}]*?line-height:\s*var\(--desc-lh/],
  ['.box .k sizes var(--kicker-px) tracked var(--kicker-track)',
    /\.box \.k\s*\{[^}]*?font-size:\s*var\(--kicker-px[^}]*?letter-spacing:\s*var\(--kicker-track/],
  ['.ztitle sizes clamp(var(--ztitle-min), var(--ztitle-vw), var(--ztitle-max))',
    /\.zone-header \.ztitle\s*\{[^}]*?font-size:\s*clamp\(var\(--ztitle-min[^)]*\),\s*var\(--ztitle-vw[^)]*\),\s*var\(--ztitle-max/],
  ['.ztitle tracks var(--ztitle-track) and clamps to var(--ztitle-lines)',
    /\.zone-header \.ztitle\s*\{[^}]*?letter-spacing:\s*var\(--ztitle-track[^}]*?-webkit-line-clamp:\s*var\(--ztitle-lines/],
  ['.zsub sizes var(--zsub-px) and clamps to var(--zsub-lines)',
    /\.zone-header \.zsub\s*\{[^}]*?font-size:\s*var\(--zsub-px[^}]*?-webkit-line-clamp:\s*var\(--zsub-lines/],
  ['.rail-title sizes var(--rail-px) tracked var(--rail-track)',
    /\.rail-title\s*\{[^}]*?font-size:\s*var\(--rail-px[^}]*?letter-spacing:\s*var\(--rail-track/],
  ['a hue rail pads var(--rail-hue-pad-y) var(--s-1)', /\.rail\.clay\s*\{\s*padding:\s*var\(--rail-hue-pad-y[^)]*\)\s+var\(--s-1\)/],
  ['a hue rail title sizes var(--rail-hue-px)', /\.rail\.clay \.rail-title\s*\{[^}]*?font-size:\s*var\(--rail-hue-px/],
  ['.zone floors at var(--zone-min-h)', /\.zone\s*\{[^}]*?min-height:\s*var\(--zone-min-h\)/],
  ['.zone.compact > .sec-grid row gap is var(--s-1)', /\.zone\.compact > \.sec-grid\s*\{\s*row-gap:\s*var\(--s-1\)/],
  ['.zone.compact .box pads var(--s-1) vertically', /\.zone\.compact \.box\s*\{\s*padding:\s*var\(--s-1\)\s+var\(--s-3\)/],
  ['.zone padding is var(--s-3)', /\.zone\s*\{[^}]*?padding:\s*var\(--s-3\)/],
  ['.box lateral padding is var(--s-3)', /\.box\s*\{[^}]*?padding:\s*var\(--s-2\)\s+var\(--s-3\)/],
  ['leaf grid gap is var(--s-2)', /\.sec-grid:not\(\.sec-compound\)\s*\{[^}]*?gap:\s*var\(--s-2\)/],
  // The three the HEIGHT chain rests on: a leaf grid's rows are the engine's
  // track list falling back to --cell-h; a zone stacks its header above its grid
  // with one --s-2 gap; and a `plain` zone pays no frame and no floor.
  ['leaf grid rows are the --row-tracks list over --cell-h',
    /\.sec-grid:not\(\.sec-compound\)\s*\{[^}]*?grid-auto-rows:\s*var\(--row-tracks,\s*var\(--cell-h\)\)/],
  ['.zone stacks header over grid with a var(--s-2) gap',
    /\.zone\s*\{[^}]*?display:\s*flex;\s*flex-direction:\s*column;\s*gap:\s*var\(--s-2\)/],
  // The INK chain's two var()-spent numbers: a box pays --s-2 vertically (the
  // same token as its gap) and a half pays --s-1.
  ['.box.half vertical padding is var(--s-1)',
    /\.box\.half\s*\{[^}]*?padding:\s*var\(--s-1\)\s+var\(--s-3\)/],
  ['.box.half .t clamps to one line with no title margin',
    /\.box\.half \.t\s*\{[^}]*?-webkit-line-clamp:\s*1;[^}]*?margin-bottom:\s*0/],
  ['.zone.plain pays no frame and no min-height',
    /\.zone\.plain\s*\{[^}]*?border:\s*none;\s*padding:\s*0;\s*min-height:\s*0/],
  ['.zone.compact .box .desc drops the description clamp',
    /\.zone\.compact \.box \.desc\s*\{[^}]*?line-clamp:\s*none/],
  ['.rail.indented insets by --indent x --indent-step',
    /\.rail\.indented\s*\{[^}]*?padding-left:\s*calc\(var\(--indent\)\s*\*\s*var\(--indent-step\)\)/],
  ['.rail.indented .rail-title pads var(--s-3) laterally',
    /\.rail\.indented \.rail-title\s*\{[^}]*?padding:\s*\d+px\s+var\(--s-3\)/],
  ['.zone-header stacks title over subtitle with a var(--s-1) gap',
    /\.zone-header\s*\{[^}]*?gap:\s*var\(--s-1\)/],
];

// THE SPAN->TRACKS MAPPING, in BOTH grid shapes, and it is a HARD check of its
// own rather than another entry in the mirror above. Everything in this file is
// arithmetic over the authored data, and every width it reports assumes a span of
// M occupies M tracks. That assumption is not self-evident — it is these four CSS
// rules — and when one is ABSENT the model stays internally consistent and
// reports a geometry the browser never draws. Measured: the root band grid
// (display:grid, where the compound flex weighting is inert) carried a rule for
// .msp and NONE for .mspan, so a section declared span 5 of 6 was auto-placed
// into ONE track and rendered at 35% of the canvas while every assertion here
// passed.
//
// SEPARATE because the mirror's arithmetic has a mirrored constant to compute
// with even when a declaration moved; there is no fallback for a rule that does
// not exist. A missing rule FAILS; an absent stylesheet is NOT ASSERTED.
const CSS_SPAN_SHAPES = [
  ['leaf grid: a full band spans every track',
    /\.sec-grid:not\(\.sec-compound\)\s*>\s*\.msp\s*\{[^}]*?grid-column:\s*1\s*\/\s*-1/],
  ['leaf grid: a partial span occupies --span tracks',
    /\.sec-grid:not\(\.sec-compound\)\s*>\s*\.mspan\s*\{[^}]*?grid-column:\s*span\s*var\(--span/],
  ['root band grid: a full band spans every track',
    /:has\(>\s*\.msp\)\s*>\s*\.msp\s*\{[^}]*?grid-column:\s*1\s*\/\s*-1/],
  ['root band grid: a partial span occupies --span tracks',
    /:has\(>\s*\.msp\)\s*>\s*\.mspan\s*\{[^}]*?grid-column:\s*span\s*var\(--span/],
];

function cssSpanShapes(root) {
  const file = path.join(root, 'index.html');
  if (!fs.existsSync(file)) return { noFile: true, missing: [], present: [] };
  const src = fs.readFileSync(file, 'utf8');
  const missing = [], present = [];
  for (const [name, re] of CSS_SPAN_SHAPES) (re.test(src) ? present : missing).push(name);
  return { noFile: false, missing, present };
}

// Returns { ok, noFile, fixed, drift, problem }. `drift` lists fixed values and
// token defaults whose stylesheet copy differs from the gate's.
function cssTextTokens(root) {
  const file = path.join(root, 'index.html');
  if (!fs.existsSync(file))
    return { ok: false, noFile: true, fixed: {}, drift: [], problem: `index.html does not exist under "${root}"` };
  const src = fs.readFileSync(file, 'utf8');
  const genCss = path.join(root, 'data', 'breakpoints.generated.css');
  const all = src + (fs.existsSync(genCss) ? fs.readFileSync(genCss, 'utf8') : '');
  const fixed = {}, missing = [], drift = [];
  for (const [key, re] of CSS_FIXED_PROBES) {
    const m = src.match(re);
    if (m) fixed[key] = Number(m[1]); else missing.push(key);
  }
  for (const [name, re] of CSS_TEXT_SHAPES) if (!re.test(src)) missing.push(name);
  for (const [k, v] of Object.entries(fixed)) if (FIXED[k] !== v) drift.push(`${k}: stylesheet ${v} vs gate ${FIXED[k]}`);
  for (const [name, want] of Object.entries(cssVars(DEFAULT_TOKENS))) {
    const esc = name.replace(/-/g, '\\-');
    // A declaration's value starts like a CSS value; prose that happens to put a
    // colon after the name ("--zone-min-h: a section floor") is not one.
    const seen = [...all.matchAll(new RegExp(`(?:${esc}:\\s*([0-9.][^;}\\s]*|auto)|var\\(${esc},\\s*([^)]+)\\))`, 'g'))]
      .map(m => (m[1] ?? m[2]).trim());
    if (!seen.length) missing.push(`default of ${name}`);
    for (const got of new Set(seen))
      if (got !== want) drift.push(`${name}: stylesheet default ${got} vs DEFAULT_TOKENS ${want}`);
  }
  if (missing.length)
    return { ok: false, fixed, drift, problem: `index.html declares no readable [${missing.join(', ')}]` };
  return { ok: true, fixed, drift };
}

// ── THE WIDTH CHAIN, MIRRORED FROM THE STYLESHEET ──────────────────────────
// container width -> canvas insets+padding -> the plane's cap -> (per nesting
// level) a section's share of its parent, minus its own zone frame -> the leaf
// grid's tracks and gaps -> the box's border and padding. Every subtraction is a
// declaration in index.html, which is why the numbers above are mirrored and
// asserted rather than tuned: the chain reproduces `.zone` 636px -> grid 602px
// and a 6-track band cell of 201px, the widths the render gate reports.

// What the canvas leaves for the content plane at a container width.
function planeWidth(cw) {
  const narrow = cw <= BP_ONE;
  const inset = narrow ? CSS_TEXT.frameHNarrow : CSS_TEXT.frameH;
  const pad = narrow ? CSS_TEXT.canvasPadNarrow : CSS_TEXT.canvasPad;
  return Math.min(CSS_TEXT.planeMax, cw - 2 * (inset + pad));
}

// The OUTER width of a section child of grid `g`. Below the stack breakpoint
// every compound grid is a full-width column (`flex-direction:column` +
// align-items:stretch), so a section takes the whole parent. Above it the root
// with bands is a real fr grid (track-proportional) and a nested compound is a
// flex row where ONLY a nested section grows: `.sec-grid.sec-compound > .zone`
// carries `flex: var(--span,1) 1 0`, while a `.box` / `.sep` / `.rail` sibling is
// `flex:0 0 auto` — content-sized. So the row's width is divided among the
// SECTION children by their spans, and every child still costs a gap.
// ACCEPTED LIMITATION: a content-sized sibling's own width is not knowable
// without a render, so it is counted as zero. That over-states its section
// siblings' width, which can only make this budget quieter — never a false alarm.
function sectionOuterWidth(g, child, cw) {
  const gw = g.widthAt(cw);
  if (cw <= BP_STACK) return gw;
  const spanOf = n => Math.max(1, Math.min(Number(n && n.span) || 1, g.cols));
  const span = spanOf(child);
  if (span >= g.cols) return gw;                        // a band owns its row
  if (g.isRoot)
    return (gw - (g.cols - 1) * CSS_TEXT.gap) * span / g.cols + (span - 1) * CSS_TEXT.gap;
  const kids = (g.children || []).filter(n => spanOf(n) < g.cols);
  const growers = kids.filter(isSection);
  const total = growers.reduce((n, x) => n + spanOf(x), 0) || 1;
  return (gw - Math.max(0, kids.length - 1) * CSS_TEXT.gap) * span / total;
}

// The TRACK AREA of a nested section's own grid: its outer width less its zone
// frame. A `plain` zone is a bare wrapper (`padding:0; border:none`), so it
// costs nothing.
function nestedTrackArea(g, child, cw) {
  const outer = sectionOuterWidth(g, child, cw);
  if (treatmentsOf(child).includes('plain')) return outer;
  return outer - 2 * (CSS_TEXT.zonePad + CSS_TEXT.zoneBorder);
}

// The width a box's TEXT gets: its cell (w of the grid's tracks, plus the gaps
// it swallows) less the box's own border and lateral padding. `.t` carries no
// padding of its own, so this is what the render gate measures as availW.
function cellTextWidth(gridW, tracks, w) {
  const track = (gridW - (tracks - 1) * CSS_TEXT.gap) / tracks;
  return track * w + (w - 1) * CSS_TEXT.gap - 2 * (CSS_TEXT.boxBorder + CSS_TEXT.boxPad);
}

// `.box .t` is `clamp(15px, 1vw, 17px)`. 1vw is the VIEWPORT, and the stage is
// full-width (`width:100%`), so the tier width is that viewport.
const titlePx = cw => Math.min(CSS_TEXT.titleMaxPx, Math.max(CSS_TEXT.titleMinPx, CSS_TEXT.titleVw * cw / 100));

// How many monospace characters fit in `px` at `fontPx`. Floor, never round: a
// partial character is not a character. `trackingEm` is the role's own
// `letter-spacing` in em (0 for a role that declares none): CSS adds it after
// every character, so it widens the advance rather than sitting between words,
// and a role that carries it (`.box .k`) is measurably narrower per character
// than its font size alone suggests.
const capacityFor = (px, fontPx, trackingEm = 0) =>
  Math.floor(px / (fontPx * (MONO_ADVANCE_EM + trackingEm)));

const tokensOf = text => String(text ?? '').trim().split(/\s+/).filter(Boolean);
const longestToken = text => tokensOf(text).reduce((a, w) => (w.length > a.length ? w : a), '');

// Greedy wrap into lines of `cap` characters, mirroring `.box`'s
// `overflow-wrap:break-word; word-break:normal`: lines break between words, and
// a token too long for a whole line FRACTURES mid-word (which is the defect N
// names). Returns the visual line count.
function wrapLines(text, cap) {
  const words = tokensOf(text);
  if (!words.length) return 0;
  if (cap < 1) return Infinity;
  let lines = 1, used = 0;
  for (const word of words) {
    let w = word.length;
    if (used > 0) {
      if (used + 1 + w <= cap) { used += 1 + w; continue; }
      lines++; used = 0;
    }
    while (w > cap) { lines++; w -= cap; }
    used = w;
  }
  return lines;
}

// One box's budget: the longest TITLE TOKEN against the cell, the TITLE against
// its clamp, the KICKER TOKEN against the cell, and the DESCRIPTION against its
// own clamp. Returns how many assertions ran, any advisory findings, and every
// MARGIN it measured — so a passing run can report its tightest margin instead of
// a bare "holds", the way the render gate's M reports its narrowest cell. Each
// finding carries the number MEASURED, the number AVAILABLE and the exact CELL: a
// budget whose message is generic advice teaches nothing and gets silenced by
// writing a structural value at random.
// A separator, a rail and a spacer render no `.box` title; a `vertical` box is
// exempt. The spacer is skipped BY TYPE rather than left to fall out of an empty
// payload: it carries no text by schema, so budgeting it would only ever compare
// nothing against a capacity — an assertion that cannot fail is counted in
// `asserted` as if it had been evidence, and a deficit reported against a cell
// that holds no text at all is a false positive by construction.
function textBudget(leaf, ctx) {
  const findings = [], margins = [];
  const nil = { asserted: 0, findings, margins };
  if (!leaf || leaf.type === 'separator' || leaf.type === 'rail' || leaf.type === 'spacer') return nil;
  if (treatmentsOf(leaf).includes('vertical')) return nil;

  const titleCap = capacityFor(ctx.availPx, ctx.fontPx);
  const descCap = capacityFor(ctx.availPx, CSS_TEXT.descPx);
  const measure = (kind, measured, available, detail) => {
    margins.push({ kind, measured, available, slack: available - measured });
    if (measured > available) findings.push({ kind, deficit: measured - available, detail });
  };

  const title = String(leaf.title ?? '').trim();
  if (title) {
    const token = longestToken(title);
    measure('token', token.length, titleCap,
      `title token "${token}" is ${token.length} char(s) and the cell holds ${titleCap} ` +
      `at ${ctx.fontPx}px mono — it fractures mid-word. ${ctx.cell}. ` +
      `Shorten the token or widen the cell.`);

    const clamp = ctx.half ? CSS_TEXT.halfTitleLines : (ctx.titleLines ?? CSS_TEXT.titleLines);
    const lines = wrapLines(title, titleCap);
    measure('title-lines', lines, clamp,
      `title wraps to ${lines} line(s) of ${titleCap} char(s) and ` +
      `${ctx.half ? '.box.half .t' : '.box .t'} clamps to ${clamp} — ${lines - clamp} line(s) cut. ` +
      `${ctx.cell}. Shorten the title or widen the cell.`);
  }

  // THE KICKER TOKEN. The exact analogue of the title-token check above, against
  // `.box .k` instead of `.box .t`, and it exists because the eye caught what the
  // budget could not: a machine-name kicker fracturing mid-word while every check
  // ran green, because the budget only ever measured titles.
  //
  // Three things make it a DIFFERENT measurement rather than the same one reused:
  //   • the font is smaller (10.5px vs 15-17px), so the cell holds MORE kicker
  //     characters than title characters — a shared capacity would be wrong in
  //     both directions;
  //   • `.box .k` carries `letter-spacing: 0.09em`, which `.box .t` does not, and
  //     that tracking is ~15% of the advance — ignoring it over-states the cell;
  //   • `text-transform: uppercase` changes the GLYPHS and not the ADVANCE, since
  //     every family behind `--mono` is fixed-pitch. So the rendered uppercase and
  //     the authored lowercase measure identically and no case fold is needed.
  //
  // Only the TOKEN is budgeted, not the line count. `.box .k` declares no
  // line-clamp, so there is no authored capacity a wrap could be measured
  // against — a two-line kicker is a layout judgement the arithmetic cannot make,
  // while a token wider than the cell is an unambiguous mid-word fracture.
  //
  // FORM-SCOPED to WORDFIT (dashboard / flow) for the same reason the render
  // gate's N is: those are the forms whose kicker carries a real symbol name. A
  // planner's `TODO` or a timeline's phase code is short by construction, and
  // failing them would be judging a form on a constraint it does not have.
  const kicker = String(leaf.kicker ?? '').trim();
  if (kicker && WORDFIT.has(ctx.form)) {
    const kickerCap = capacityFor(ctx.availPx, CSS_TEXT.kickerPx, CSS_TEXT.kickerTrackEm);
    const token = longestToken(kicker);
    measure('kicker-token', token.length, kickerCap,
      `kicker token "${token}" is ${token.length} char(s) and the cell holds ${kickerCap} ` +
      `at ${CSS_TEXT.kickerPx}px mono + ${CSS_TEXT.kickerTrackEm}em tracking — it fractures ` +
      `mid-word. ${ctx.cell}. Use the SHORT form of the symbol (the function name ` +
      `without its module) and put the full name in the detail, or widen the cell.`);
  }

  const raw = leaf.description;
  const authored = Array.isArray(raw) ? raw : (raw === null || raw === undefined ? [] : [raw]);
  // A `compact` grid releases the description clamp, so there is no line count
  // to exceed: its text is judged by INK against the short slot instead.
  if (authored.length && !ctx.compact) {
    // Each authored line is its own `.m` block, so it costs AT LEAST one visual
    // line and more when it wraps; the clamp counts the visual lines of the whole
    // `.desc`. That is the arithmetic behind "three lines of few words".
    const per = authored.map(l => wrapLines(l, descCap));
    const total = per.reduce((n, x) => n + x, 0);
    const wrapped = per.map((n, i) => ({ n, i })).filter(x => x.n > 1)
      .map(x => `line ${x.i + 1} ("${String(authored[x.i]).slice(0, 24)}…") wraps to ${x.n}`);
    measure('desc-lines', total, (ctx.descLines ?? CSS_TEXT.descLines),
      `description needs ${total} visual line(s) of ${descCap} char(s) across ` +
      `${authored.length} authored line(s) and .box .desc clamps to ${(ctx.descLines ?? CSS_TEXT.descLines)} — ` +
      `the last ${total - (ctx.descLines ?? CSS_TEXT.descLines)} is cut mid-sentence` +
      (wrapped.length ? ` (${wrapped.join('; ')})` : '') + `. ${ctx.cell}. ` +
      `Three lines of FEW WORDS, not a paragraph — or widen the cell.`);
  }
  return { asserted: margins.length, findings, margins };
}

// The tightest margin the TEXT budget measured, per kind, across the whole run —
// the number the check reports when it passes. A gate that prints only "holds"
// cannot be told apart from a gate that measured nothing.
const textTightest = new Map();
const TEXT_KIND = { token: 'title token', 'kicker-token': 'kicker token',
  'title-lines': 'title line(s)', 'desc-lines': 'description line(s)' };
function textHeadline() {
  if (!textTightest.size) return 'no leaf carried a title or a description to budget';
  return 'tightest margin — ' + [...textTightest.entries()].map(([kind, m]) =>
    `${TEXT_KIND[kind]} ${m.measured} of ${m.available} (${m.where} @${m.cw}px)`).join('; ');
}

// ── WORDS — the census's blind spot ────────────────────────────────────────
// CENSUS compares page ids and NODE COUNTS, so a text-only edit — rewording a
// description, decoding a piece of jargon, fixing a title — leaves the counts
// identical and the census green while data.generated.js still holds the OLD
// words. MEASURED: after nine wording edits the gate printed ALL PASS and the
// bundle still carried the old sentences. That is the coupled-trio trap in its
// quietest form: the browser renders the stale text and nothing fails.
//
// Every authored string must appear VERBATIM in the bundle, because the bundle
// is generated FROM these strings — the comparison needs no model of the
// generator beyond its JSON escaping.
const TEXT_FIELDS = ['title', 'subtitle', 'kicker', 'detail', 'note', 'text', 'label'];

function authoredStrings(node, out) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { for (const n of node) authoredStrings(n, out); return out; }
  for (const f of TEXT_FIELDS) if (typeof node[f] === 'string' && node[f].trim()) out.push(node[f]);
  const lists = [node.description, node.steps];
  for (const l of lists)
    for (const line of (Array.isArray(l) ? l : l == null ? [] : [l]))
      if (typeof line === 'string' && line.trim()) out.push(line);
  for (const key of ['children', 'sections', 'filters']) authoredStrings(node[key], out);
  return out;
}

// A string is looked for in its JSON-escaped form: that is what the generator
// wrote, so a quote or a backslash in the copy does not read as a drift.
const escapeForBundle = s => JSON.stringify(s).slice(1, -1);

// ── INK — the height half of the budget ────────────────────────────────────
// TEXT asks whether a line of characters fits the cell's WIDTH. INK asks the
// question one axis over: does the STACK of those lines fit the fixed row the
// grid gives the box? Nothing else in either gate measures it, which is how a
// `half` pair whose two titles each hold on one line can still overflow the 63px
// it actually gets, and how a box left far shorter than its row leaves an
// undeclared hole no hole check can see (the cell IS occupied).
//
// WHAT INK CANNOT SEE, stated because it was read as more than it is. The slot
// is DERIVED from the authored spans through this file's width chain, so INK is a
// claim about the cell the stylesheet OWES the box, never about the cell the
// browser drew. When the two disagree INK reports the model and stays green:
// measured, it passed a title at 47.4px of a 63.0px slot while Chromium clamped
// that very title, because the real cell was 179.5px wide instead of the modelled
// 236.6px. The arithmetic was right and the render was wrong. Real clamping is
// `validate` TXT (scrollHeight vs clientHeight in the browser); the divergence
// that produced it is now caught by the CSS span->tracks shapes above.
//
// OPT-OUT, not opt-in: every page is asserted unless it declares
// `text_fit: advisory`, which demotes its overflows to [INFO]. An overflow FAILS
// only at the presentation tier (`document.yaml` `viewport.w`) — the width the
// deck is shown at — and advises at the others. The three-line description clamp
// puts a full box at ~128 of 130px, so a box that fails here is one whose text
// the author should move into the detail. The render-side ruling on the same text
// is validate's TXT row, scoped by the same page field.

// The one assumed constant on this axis, and the analogue of MONO_ADVANCE_EM:
// `.box .k` and `.box .t` declare no line-height, so they render at the family's
// `normal`, which for every family --mono names sits near 1.2. 1.25 is the
// CEILING of that range, so a line is never assumed SHORTER than it renders and
// the estimated demand stays an upper bound.
const NORMAL_LINE_EM = 1.25;

// The void that stops being slack and starts being an undeclared hole: the
// smallest CARD's worth of statement the cell could still have carried — a title
// line and a two-line gloss, with the gaps around them. A single spare line is
// the fixed row's own breathing room and flagging it would fire on the most
// ordinary box there is; room for a whole statement the cell does not make is
// the defect, and a `centered` treatment is what declares it on purpose.
const inkVoidPx = () => CSS_TEXT.titleMinPx * NORMAL_LINE_EM
  + 2 * CSS_TEXT.descPx * CSS_TEXT.descLineEm + 2 * CSS_TEXT.boxGap;

// The slot a leaf is really given: K rows plus the row gaps it swallows, or —
// for a `half` — its share of the ONE slot the pair occupies, less the pair's
// own inner gap.
function slotHeightPx(rowspan, half, rows = { cellH: CSS_TEXT.cellH, rowGap: CSS_TEXT.gap }) {
  const full = rows.cellH * rowspan + (rowspan - 1) * rows.rowGap;
  return half ? (full - CSS_TEXT.boxGap * 2) / 2 : full;
}

// One box's ink: every block it renders, at the line count the width budget
// already computed for it, plus the chrome between them. Returns the measured
// demand and the slack against the slot, so a pass can report a number.
function inkBudget(leaf, ctx) {
  if (!leaf || leaf.type === 'separator' || leaf.type === 'rail' || leaf.type === 'spacer') return null;
  // A SECTION is not a slot: its zone grows with its own grid and its header is
  // not competing with a fixed row, so budgeting one compares ink against a
  // height nothing enforces — a false positive by construction, the same reason
  // the width budget only visits grids with a real track model.
  if (isSection(leaf)) return null;
  if (treatmentsOf(leaf).includes('vertical')) return null;   // rotated: width is the constraint

  const titleCap = capacityFor(ctx.availPx, ctx.fontPx);
  const descCap = capacityFor(ctx.availPx, CSS_TEXT.descPx);
  const blocks = [];

  const kicker = String(leaf.kicker ?? '').trim();
  if (kicker) {
    const cap = capacityFor(ctx.availPx, CSS_TEXT.kickerPx, CSS_TEXT.kickerTrackEm);
    blocks.push(wrapLines(kicker, cap) * CSS_TEXT.kickerPx * NORMAL_LINE_EM);
  }

  const title = String(leaf.title ?? '').trim();
  if (title) {
    const clamp = ctx.half ? CSS_TEXT.halfTitleLines : (ctx.titleLines ?? CSS_TEXT.titleLines);
    const lines = Math.min(wrapLines(title, titleCap), clamp);
    blocks.push(lines * ctx.fontPx * NORMAL_LINE_EM + (ctx.half ? 0 : CSS_TEXT.titleMarginPx));
  }

  const raw = leaf.description;
  const authored = Array.isArray(raw) ? raw : (raw === null || raw === undefined ? [] : [raw]);
  if (authored.length) {
    const total = authored.reduce((n, l) => n + wrapLines(l, descCap), 0);
    const lines = ctx.compact ? total : Math.min(total, (ctx.descLines ?? CSS_TEXT.descLines));
    blocks.push(lines * CSS_TEXT.descPx * CSS_TEXT.descLineEm);
  }
  if (!blocks.length) return null;

  const padY = ctx.half ? CSS_TEXT.halfPadY : ctx.compact ? CSS_TEXT.compactBoxPadY : CSS_TEXT.boxPadY;
  const ink = blocks.reduce((n, x) => n + x, 0)
    + 2 * padY + 2 * CSS_TEXT.boxBorder
    + Math.max(0, blocks.length - 1) * CSS_TEXT.boxGap;
  const rows = { cellH: ctx.cellH ?? (ctx.compact ? CSS_TEXT.compactCellH : CSS_TEXT.cellH),
    rowGap: ctx.compact ? CSS_TEXT.compactRowGap : CSS_TEXT.gap };
  const slot = slotHeightPx(rowspanOf(leaf), !!ctx.half, rows);
  return { ink, slot, slack: slot - ink, blocks: blocks.length };
}

// The tightest and the loosest ink gap the run measured — a check that prints
// only "holds" cannot be told apart from one that measured nothing, and on this
// axis BOTH ends are the finding: no room left, or a room nobody claimed.
const inkTightest = { slack: Infinity }, inkLoosest = { slack: -Infinity };
function inkHeadline() {
  if (!Number.isFinite(inkTightest.slack)) return 'no strict page carried a box to budget';
  const fmt = m => `${m.ink.toFixed(1)}px of ${m.slot.toFixed(1)}px (${m.slack.toFixed(1)}px free) ` +
    `at ${m.where} @${m.cw}px`;
  return `every strict page — tightest ${fmt(inkTightest)}; loosest ${fmt(inkLoosest)}`;
}

// ── CSS GRID SPARSE AUTO-PLACEMENT, SIMULATED ─────────────────────────────
// `grid-auto-flow` is the default (row, SPARSE), so the placement cursor never
// moves backwards: an item that does not fit in the tracks left on the current
// row moves DOWN and leaves the remainder of that row EMPTY. That is precisely
// how an interior hole is born, and simulating it is what lets the hole be found
// in the data instead of in a screenshot.
// A band carries a DEFINITE column position (grid-column: 1 / -1), so it cannot
// share a row: it takes the first row where the full width is free. Band-ness is
// decided HERE, per tier, by isBandAtTier — the caller supplies widths, not a
// verdict, because the same slot is a band at one tier and not at another.
function place(items, tracks) {
  const occ = new Set();
  const key = (r, c) => `${r},${c}`;
  const free = (r, c, w, h) => {
    if (c + w > tracks) return false;
    for (let i = 0; i < h; i++) for (let j = 0; j < w; j++) if (occ.has(key(r + i, c + j))) return false;
    return true;
  };
  const fill = (r, c, w, h) => { for (let i = 0; i < h; i++) for (let j = 0; j < w; j++) occ.add(key(r + i, c + j)); };

  let cr = 0, cc = 0;
  const placed = [];
  for (const it of items) {
    const w = Math.max(1, Math.min(it.w, tracks)), h = Math.max(1, it.h);
    if (isBandAtTier(w, tracks)) {
      let r = cc > 0 ? cr + 1 : cr;   // a partially filled row cannot host a band
      let guard = 0;
      while (!free(r, 0, tracks, h) && guard++ < 10000) r++;
      fill(r, 0, tracks, h);
      placed.push({ ...it, r, c: 0, w: tracks, h, band: true });
      cr = r; cc = tracks;            // the row is full: the next item wraps
      continue;
    }
    if (cc + w > tracks) { cr++; cc = 0; }
    let guard = 0;
    while (!free(cr, cc, w, h) && guard++ < 10000) {
      cc++;
      if (cc + w > tracks) { cr++; cc = 0; }
    }
    fill(cr, cc, w, h);
    placed.push({ ...it, r: cr, c: cc, w, h, band: false });
    cc += w;
  }
  const rowCount = placed.reduce((n, p) => Math.max(n, p.r + p.h), 0);
  return { placed, occ, rowCount };
}

// ── GRID DISCOVERY ────────────────────────────────────────────────────────
// Walk a page into the grids that are REAL CSS GRIDS, because only those have a
// track model the identity can be asserted against:
//   • a LEAF grid (`.sec-grid:not(.sec-compound)`) — equal fr tracks. Always.
//   • the ROOT when it holds at least one band (`.sec-plane > .sec-grid.sec-compound:has(> .msp)`)
//     — the authored grid that FILLS the canvas, but ONLY above the 1440 stack
//     breakpoint; below it the root becomes a vertical flex stack.
// A NESTED compound grid is a flex-wrap row of sections: it has no tracks, so no
// rectangle to close. It is still walked into (its children may be leaf grids)
// and still checked for sibling-level defects (ORDER, LANE, FROZEN).
// `widthAt(containerWidth)` is threaded down the walk: the root's track area is
// the content plane, and each nested section's is its share of its parent minus
// its own zone frame (see THE WIDTH CHAIN). It is a FUNCTION, not a number,
// because the same grid is a different width at every tier.
function discoverGrids(page) {
  const grids = [];
  const walk = (node, label, isRoot, widthAt, tok) => {
    const children = isRoot ? (node.sections || []) : (node.children || []);
    const slots = slotsOf(children);
    const compound = children.some(isSection);
    const authored = node.columns;
    const cols = effectiveCols(authored, slots, compound);
    const hasBand = slots.some(s => isBandClass(Math.max(1, Math.min(s.node.span || 1, cols)), cols));
    const grid = {
      // `node` is the section (or page) this grid belongs to, so a check that
      // starts from a CHILD — FROZEN walks a compound's children — can find the
      // grid that child renders without re-deriving it.
      node, label, isRoot, compound, children, slots, widthAt,
      // `.zone.compact` gives this leaf grid its own shorter row and row gap.
      compactRows: !isRoot && !compound && treatmentsOf(node).includes('compact'),
      // The tokens in force here (document ⊕ every ancestor override ⊕ this
      // section's, `compact` preset included) and the row height they give.
      tok, cellH: tok.row.cell_h,
      authoredCols: Math.max(1, Number.isInteger(authored) && authored > 0 ? authored : DEFAULT_SECTION_COLUMNS),
      cols, hasBand,
      // Placeable = it is laid out as a CSS grid with tracks.
      placeable: !compound || (isRoot && hasBand),
      // The root-with-bands grid only exists above the stack breakpoint.
      minWidth: (isRoot && compound) ? BP_STACK + 1 : 0,
    };
    grids.push(grid);
    for (const c of children) if (isSection(c))
      walk(c, `${label} > ${c.id ?? '(no id)'}`, false, cw => nestedTrackArea(grid, c, cw),
        mergeTokens(tok, nodeDelta(c, 'section')));
  };
  walk(page, page.id != null ? `${page.id}:root` : 'root', true, planeWidth, TOKENS);
  return grids;
}

// Every leaf component under a page, for the CHIP checks.
function leavesOf(page) {
  const out = [];
  (function walk(list) {
    for (const n of list || []) {
      if (isSection(n)) { walk(n.children); continue; }
      out.push(n);
    }
  })(page.sections);
  return out;
}

// ── THE HEIGHT CHAIN (the other axis, and it is arithmetic too) ───────────
// Width is a negotiation between tracks; HEIGHT is not. A leaf grid's rows are
// FIXED tracks (`grid-auto-rows: var(--row-tracks, var(--cell-h))`), so a grid's
// height is a sum over rows the placement already knows: --cell-h each, or
// --sep-row-h for a THIN row. Every number is a mirrored declaration the CSS
// check asserts against index.html — none of it is estimated.
//
// THE ONE THING THIS CHAIN REFUSES TO GUESS is a zone HEADER's height: a clamped
// mono title plus an optional three-line subtitle needs a line-height the
// stylesheet never declares, and guessing it would drag the TEXT budget's
// approximation into a geometric verdict. So the header is EXCLUDED and only the
// gap it costs is counted. That makes every height below a FLOOR — the zone is
// at LEAST this tall, never at most — and that direction is the whole
// calibration of FROZEN.

// Mirrors engine.js `isThinRowLeaf`: a row whose occupants are ALL thin leaves
// — horizontal separators, DECLARED HOLES, or horizontal no-rowspan RAILS —
// renders reduced: --sep-row-h for separators and holes, content height
// (`auto`) for a row that holds a rail. Mirrored rather than imported for the
// same reason the placement model is (an ES module is CORS-blocked from a
// `file://` script), and ASSERTED against the engine's copy by the AGREE/thin
// case in tools/test-guards.mjs. A wrong `false` here is the safe direction:
// it only ever makes a reported hole smaller.
// A thin separator row on the LAST row is emitted as minmax(--sep-row-h, 1fr)
// so a declared hole absorbs the slack a stretched section leaves, which keeps
// --sep-row-h a FLOOR there rather than the height — consistent with every
// height in this chain already being a floor. A rail's exclusions mirror the
// engine's: a VERTICAL rail's ink IS the row height, and a rail with `rowspan`
// labels a lane down several rows.
const isThinRowLeaf = c => c && !isSection(c) &&
  (c.type === 'spacer'
    || ((c.type === 'separator' || c.type === 'rail') && !treatmentsOf(c).includes('vertical')
        && (c.type === 'separator' || Math.floor(Number(c.rowspan) || 1) <= 1)));
const isRailLeaf = c => c && !isSection(c) && c.type === 'rail';
const RAIL_HUES = new Set(['blue', 'violet', 'gold', 'clay']);

// The height of one LEAF grid at a container width: each row at its own track
// height, plus the row gaps. A COMPOUND grid is a flex row of sections with no
// track model, so it has no arithmetic height and says null instead of guessing.
function gridHeightPx(g, cw, m = CSS_TEXT) {
  if (!g || g.compound) return null;
  const cellH = g.cellH ?? m.cellH;
  const rowGap = g.compactRows ? m.compactRowGap : m.gap;
  const tracks = tracksFor(g.cols, cw);
  const items = g.slots.map(s => {
    const span = Math.max(1, Math.min(s.node.span || 1, g.cols));
    return { node: s.node, w: widthAtTier(span, g.cols, tracks), h: rowspanOf(s.node) };
  });
  if (!items.length) return 0;
  const { placed, rowCount } = place(items, tracks);
  let h = 0;
  for (let r = 0; r < rowCount; r++) {
    const occupants = placed.filter(p => r >= p.r && r < p.r + p.h);
    const thin = occupants.length > 0 && occupants.every(p => isThinRowLeaf(p.node));
    // A rail row's track is `auto` (content height), so its FLOOR is the
    // one-line rail: title line + 2×pad + 2×border (railRowH). Never sepRowH
    // here — 40 would OVERSTATE a 33px row and break the floor direction.
    h += !thin ? cellH
      : occupants.some(p => isRailLeaf(p.node)) ? m.railRowH : m.sepRowH;
  }
  return h + Math.max(0, rowCount - 1) * rowGap;
}

// A section's rendered height, and whether that number is EXACT or a floor.
// `plain` is a bare wrapper (no border, no padding, no min-height); every other
// treatment pays the zone frame and the --zone-min-h floor. A section WITH a
// header costs one more gap and an unknown header, so it is a floor only.
function zoneHeightPx(sec, gridH) {
  if (gridH === null) return null;
  const plain = treatmentsOf(sec).includes('plain');
  const chrome = plain ? 0 : 2 * (CSS_TEXT.zonePad + CSS_TEXT.zoneBorder);
  const headed = !!(sec.title || sec.subtitle);
  const h = gridH + chrome + (headed ? CSS_TEXT.gap : 0);
  return { px: plain ? h : Math.max(h, CSS_TEXT.zoneMinH), exact: !headed };
}

// ── RAIL TITLE WIDTH ──────────────────────────────────────────────────────
// The px a thin rail's title can wrap in, inside a cell `cellPx` wide. A hue
// rail pads 4px instead of 16. An INDENTED rail (`.rail.indented`) moves its
// frame onto the title: the title loses indent × --indent-step on the left, the
// rail's right padding, and its own lateral padding and border on both sides —
// measured against the whole cell, RAILT under-predicted exactly these wraps.
function railTitleWidth(cellPx, leaf, m = CSS_TEXT) {
  const hue = RAIL_HUES.has(leaf && leaf.variant);
  const padX = hue ? m.railHuePadX : m.boxPad;
  const indent = Math.max(0, Math.floor(Number(leaf && leaf.indent) || 0));
  if (!indent) return cellPx - 2 * (m.railBorder + padX);
  return cellPx - 2 * m.railBorder - indent * m.indentStep - padX
    - 2 * (m.railIndentTitlePadX + m.railIndentTitleBorder);
}

// How a thin rail's title wraps in a cell `cellPx` wide, at the rail's own metrics.
function railTitleFit(cellPx, leaf, m = CSS_TEXT) {
  const hue = RAIL_HUES.has(leaf && leaf.variant);
  const fontPx = hue ? m.railHueTitlePx : m.railTitlePx;
  const trackEm = hue ? m.railHueTrackEm : m.railTrackEm;
  const px = railTitleWidth(cellPx, leaf, m);
  const cap = capacityFor(px, fontPx, trackEm);
  return { px, cap, fontPx, trackEm, lines: wrapLines(String(leaf && leaf.title || '').trim(), cap) };
}

// ── SECTION HEADER BUDGET ─────────────────────────────────────────────────
// `.zone-header .ztitle` is clamp(13px, 0.85vw, 14.5px) mono with 0.1em
// tracking, clamped to 2 lines; `.zsub` is 12px, clamped to 3. Both wrap inside
// the zone's inner width (the header is width:0 + min-width:100%, so it never
// widens the zone), and a line past the clamp is cut with no ellipsis visible
// in the cell — the same defect TEXT names for a box.
const zoneTitlePx = (cw, m = CSS_TEXT) =>
  Math.min(m.ztitleMaxPx, Math.max(m.ztitleMinPx, m.ztitleVw * cw / 100));

function headerBudget(sec, innerPx, cw, m = CSS_TEXT) {
  const out = { titleLines: 0, subLines: 0, findings: [] };
  const title = String(sec && sec.title || '').trim();
  const sub = String(sec && sec.subtitle || '').trim();
  const where = `in a ${Math.round(innerPx)}px zone at the ${cw}px tier`;
  if (title) {
    const px = zoneTitlePx(cw, m);
    const cap = capacityFor(innerPx, px, m.ztitleTrackEm);
    out.titleLines = wrapLines(title, cap);
    if (out.titleLines > m.ztitleLines)
      out.findings.push({ kind: 'header-title-lines', deficit: out.titleLines - m.ztitleLines, detail:
        `section title wraps to ${out.titleLines} line(s) of ${cap} char(s) at ${px}px mono + ` +
        `${m.ztitleTrackEm}em tracking and .ztitle clamps to ${m.ztitleLines} — ` +
        `${out.titleLines - m.ztitleLines} line(s) cut, ${where}. Shorten the title or widen the section.` });
  }
  if (sub) {
    const cap = capacityFor(innerPx, m.zsubPx);
    out.subLines = wrapLines(sub, cap);
    if (out.subLines > m.zsubLines)
      out.findings.push({ kind: 'header-sub-lines', deficit: out.subLines - m.zsubLines, detail:
        `section subtitle wraps to ${out.subLines} line(s) of ${cap} char(s) at ${m.zsubPx}px mono and ` +
        `.zsub clamps to ${m.zsubLines} — ${out.subLines - m.zsubLines} line(s) cut, ${where}. ` +
        `Shorten the subtitle or move it into a box's detail.` });
  }
  return out;
}

// ── THE PAGE HEIGHT, PREDICTED ────────────────────────────────────────────
// The full-page height at a container width, from the same placement model the
// geometry checks use: a leaf grid is its row tracks plus gaps (a `compact` grid
// at its own row), a zone adds its header lines, its frame and its --zone-min-h
// floor, a compound row takes its TALLEST child above the stack breakpoint and
// the SUM of its children below it, and the root-with-bands places its sections
// into rows first. Header lines are counted at NORMAL_LINE_EM, the same upper
// bound INK uses, and every value comes in as a parameter.
//
// `chromePx` is the page outside the canvas box — the canvas's top offset and
// the frame below it — which no single declaration gives. validate's VH row
// measures and prints it (`chrome NNpx`); 207 is the seed's measurement at
// 1920x1080, and a deck whose VH chrome differs passes its own value in.
const PAGE_CHROME_PX = 207;

function predictPageHeight(page, cw, m = CSS_TEXT, chromePx = PAGE_CHROME_PX) {
  const grids = discoverGrids(page);
  const gridOf = new Map(grids.map(g => [g.node, g]));
  const stacked = (hs, gap) => hs.reduce((a, b) => a + b, 0) + Math.max(0, hs.length - 1) * gap;

  function headerPx(sec, innerPx) {
    const hb = headerBudget(sec, innerPx, cw, m);
    const t = Math.min(hb.titleLines, m.ztitleLines) * zoneTitlePx(cw, m) * NORMAL_LINE_EM;
    const s = Math.min(hb.subLines, m.zsubLines) * m.zsubPx * NORMAL_LINE_EM;
    return t + s + (t && s ? m.zheaderGap : 0);
  }
  function childPx(parent, n) {
    if (!isSection(n)) return isThinRowLeaf(n) ? m.sepRowH : (parent.cellH ?? m.cellH);
    const plain = treatmentsOf(n).includes('plain');
    const header = (n.title || n.subtitle) ? headerPx(n, nestedTrackArea(parent, n, cw)) : 0;
    const h = blockPx(gridOf.get(n)) + (header ? header + m.gap : 0)
      + (plain ? 0 : 2 * (m.zonePad + m.zoneBorder));
    return plain ? h : Math.max(h, m.zoneMinH);
  }
  function blockPx(g) {
    if (!g) return 0;
    if (!g.compound) return gridHeightPx(g, cw, m) ?? 0;
    const kids = orderedChildren(g.children).map(x => x.c);
    const hs = kids.map(n => childPx(g, n));
    if (cw <= BP_STACK) return stacked(hs, m.gap);
    if (g.isRoot && g.hasBand) {
      const items = kids.map((n, i) => ({ i, w: Math.max(1, Math.min(Number(n.span) || 1, g.cols)), h: 1 }));
      const { placed, rowCount } = place(items, g.cols);
      const rows = [...Array(rowCount).keys()]
        .map(r => Math.max(0, ...placed.filter(p => p.r === r).map(p => hs[p.i])));
      return stacked(rows, m.gap);
    }
    return Math.max(0, ...hs);
  }

  const contentPx = blockPx(grids[0]);
  const canvasPad = cw <= BP_ONE ? m.canvasPadNarrow : m.canvasPad;
  return { contentPx, totalPx: contentPx + 2 * canvasPad + chromePx };
}

// The HEIGHT advisory for one page, or null when it fits the viewport.
function pageHeightAdvisory(predictedPx, vp) {
  const px = Math.round(predictedPx);
  return px > vp.h ? `predicted ${px}px > ${vp.h} (+${px - vp.h}) at ${vp.w}` : null;
}

// ── THE CHECK RUN ─────────────────────────────────────────────────────────
// Findings are collected, never printed as they are found, so the report can be
// grouped by CHECK (one line per check plus its failures) instead of interleaving
// twelve grids × five tiers of noise.
const findings = [];   // { check, sev: 'fail'|'info'|'not-asserted', where, detail }
let asserted = 0;      // how many assertions actually ran (0 => RED, see below)

const fail = (check, where, detail) => findings.push({ check, sev: 'fail', where, detail });
const info = (check, where, detail) => findings.push({ check, sev: 'info', where, detail });
// A check that COULD NOT RUN — its own severity because an advisory is something
// the gate MEASURED and chose not to fail on, and this is something it never
// measured. Measured: reported through a raw console.log instead, it recorded no
// finding, so the summary loop found no failure for the check and printed an
// affirmative [PASS] for a claim nothing had read.
const notAsserted = (check, where, detail) =>
  findings.push({ check, sev: 'not-asserted', where, detail });

function checkPage(page) {
  const pageId = page.id ?? '(no id)';
  const form = page.form ?? DEFAULT_FORM;
  const grids = discoverGrids(page);
  const trackTable = [];
  // The TEXT budget's worst finding per (box, kind) across the tier sweep, and
  // the line overflows at the presentation tier, which FAIL on a strict page.
  const textWorst = new Map();
  const textFail = new Map();
  const strict = isTextFitStrict(page);
  const FIT_KINDS = new Set(['title-lines', 'desc-lines', 'header-title-lines', 'header-sub-lines']);
  const noteText = (where, f, tierW) => {
    const key = `${where}|${f.kind}`;
    if (strict && tierW === PRESENT.w && FIT_KINDS.has(f.kind)) {
      if (!textFail.has(key)) textFail.set(key, { ...f, where: `${where} @${tierW}px` });
      return;
    }
    const prev = textWorst.get(key);
    if (!prev || f.deficit > prev.deficit) textWorst.set(key, { ...f, where });
  };
  // INK's worst overflow per box outside the fail tier, reported once.
  const inkWorst = new Map();
  // RAILT's worst finding per rail across the tier sweep — deduped like TEXT,
  // but emitted as a HARD fail: a rail row is `auto`, so an over-wrapped title
  // does not clip, it silently GROWS the row and the stack it lives in.
  const railWorst = new Map();

  // ── data-level checks (tier-independent) ────────────────────────────────

  // ORDER — a duplicate EFFECTIVE order among siblings. The engine resolves
  // `order ?? (index + 1)` with a stable tie-break, so a collision is silently
  // decided by list position: the render is correct today and can flip under an
  // unrelated edit that only moves a node in the file. Explicit collisions and
  // the mixed case (one child says `order: 2`, another sits at index 1 and
  // therefore also resolves to 2) are the same defect and are both caught here.
  for (const g of grids) {
    const seen = new Map();
    for (const x of orderedChildren(g.children)) {
      asserted++;
      const prev = seen.get(x.eff);
      if (prev !== undefined) {
        fail('ORDER', `${g.label}`,
          `siblings "${prev}" and "${x.c && x.c.id || '(no id)'}" both resolve to order ${x.eff} ` +
          `(explicit \`order\` or the 1-based list index when absent) — the engine breaks the tie by ` +
          `list position, so the intended sequence is silent and can flip under an unrelated edit. ` +
          `Give every sibling an explicit, distinct \`order\`.`);
      }
      seen.set(x.eff, x.c && x.c.id || '(no id)');
    }
  }

  // CHIP — referential integrity in both directions, plus ARITY.
  const leaves = leavesOf(page);
  const declared = (page.filters || []).map(f => f && f.key).filter(k => typeof k === 'string' && k !== RESET_CHIP);
  const members = new Map();
  const referenced = new Set();
  for (const leaf of leaves)
    for (const k of (Array.isArray(leaf.filters) ? leaf.filters : [])) {
      if (k === RESET_CHIP) continue;
      referenced.add(k);
      if (!members.has(k)) members.set(k, []);
      members.get(k).push(leaf.id ?? '(no id)');
    }
  for (const k of declared) {
    asserted++;
    const mem = members.get(k) || [];
    if (mem.length === 0) {
      fail('CHIP', `page "${pageId}" chip "${k}"`,
        `declared but NO component references it. An active chip DIMS every component it does not ` +
        `name, so this chip switches the whole deck off when clicked.`);
    } else if (mem.length === 1) {
      // ARITY. This is the half of K that closing the join could never see: the
      // join CLOSES (one chip, one member) and the chip is still broken, because
      // a chip is a RELATION — a flow, a trace, a grouping — and a relation needs
      // at least two ends. With one member, clicking it dims everything else, so
      // the deck goes dark to spotlight a single box.
      fail('CHIP', `page "${pageId}" chip "${k}"`,
        `has exactly ONE member ("${mem[0]}"). A chip expresses a RELATION between components, ` +
        `so one member is not a relation — and since an active chip dims everything it does not name, ` +
        `a one-member chip blacks out the deck to spotlight a single box. Add the other end, or drop the chip.`);
    }
  }
  for (const k of referenced) {
    asserted++;
    if (!declared.includes(k))
      fail('CHIP', `page "${pageId}" key "${k}"`,
        `referenced by component(s) [${(members.get(k) || []).join(', ')}] but NO chip declares it — ` +
        `it can never light.`);
  }

  // LIT — a filter declared on a leaf TYPE the engine cannot light. buildBox and
  // buildRail are the only builders that stamp `data-filters` on their node;
  // buildSeparator and buildSpacer emit bare structural nodes. So a `filters:`
  // on a separator passes the strict schema (COMPONENT_FIELDS allows it there),
  // counts as a CHIP member above — the join CLOSES — and the render can never
  // spotlight that end: the relation is declared in the data and silently absent
  // on screen.
  for (const leaf of leaves) {
    asserted++;
    const t = leaf.type;
    if ((t === 'separator' || t === 'spacer') &&
        Array.isArray(leaf.filters) && leaf.filters.length) {
      fail('LIT', `page "${pageId}" ${t} "${leaf.id ?? '(no id)'}"`,
        `declares filters [${leaf.filters.join(', ')}] on a \`${t}\`, a leaf type the engine ` +
        `never lights: only buildBox and buildRail emit \`data-filters\`, so this membership passes the CHIP ` +
        `join and never renders. Move the filter to a box or a rail, or drop it.`);
    }
  }

  // BAND — a declared span that EXCEEDS the columns it is placed in. The engine
  // clamps it (`min(child.span, cols)`) so it renders as a band and nothing looks
  // wrong, but the declaration is unsatisfiable as written and the author's real
  // intent is unknowable. Fail at the door rather than render a guess.
  for (const g of grids) {
    for (const s of g.slots) {
      const declaredSpan = Math.max(1, Number(s.node.span) || 1);
      asserted++;
      if (declaredSpan > g.authoredCols)
        fail('BAND', `${g.label} > ${s.node.id ?? '(no id)'}`,
          `declares span ${declaredSpan} in a ${g.authoredCols}-column grid. The engine silently clamps it to ` +
          `${g.authoredCols} (a full-width band); as written the declaration cannot be satisfied.`);
    }
    // A leaf grid whose EFFECTIVE column count is below its AUTHORED one: the
    // grow-with-content clamp removed the tracks the content could not fill, so
    // no dead track ever reaches the screen (that is why E passes) — but the
    // authored intent and the render differ, and a PARTIAL merge silently becomes
    // a full-width BAND when the clamp lands on its span. Advisory, not a
    // failure: the clamp is the documented behaviour, and the deck is correct.
    if (!g.compound && g.cols < g.authoredCols) {
      const promoted = g.slots.filter(s => {
        const sp = Math.max(1, Math.min(s.node.span || 1, g.authoredCols));
        return sp > 1 && sp >= g.cols;
      }).map(s => s.node.id ?? '(no id)');
      info('BAND', `${g.label}`,
        `authored columns:${g.authoredCols} but the content can only fill ${g.cols}, so the ` +
        `grow-with-content clamp renders ${g.cols} track(s)` +
        (promoted.length ? ` — and span ${promoted.length > 1 ? 'merges' : 'merge'} [${promoted.join(', ')}] ` +
          `therefore become FULL-WIDTH BANDS rather than partial merges` : '') + '.');
    }
  }

  // ── per-tier geometry checks ────────────────────────────────────────────
  for (const g of grids) {
    if (!g.placeable) continue;
    const row = { grid: g.label, authored: g.authoredCols, cols: g.cols, tracks: {}, kind: g.isRoot && g.compound ? 'root-with-bands' : (g.isRoot ? 'root-leaf' : 'leaf') };

    let prevTracks = null;
    for (const tier of SWEEP) {
      if (tier.w < g.minWidth) { row.tracks[tier.name] = '—'; continue; }
      const tracks = tracksFor(g.cols, tier.w);
      row.tracks[tier.name] = tracks;

      // TIER — the cascade is MONOTONE NON-DECREASING in the container width:
      // TIERS runs NARROW -> WIDE, so a grid may only ever gain tracks as the
      // container grows. A violation means the breakpoint rules disagree with each
      // other (the exact class of bug the enumerated sec-c3/c4/c5 rules had, where
      // a 6-column grid stayed uncollapsed in a window between two tiers).
      asserted++;
      if (prevTracks !== null && tracks < prevTracks)
        fail('TIER', `${g.label}`,
          `tracks SHRINK as the container GROWS: ${tracks} at ${tier.w}px vs ${prevTracks} at the narrower ` +
          `tier — the collapse cascade must be monotone non-decreasing in the container width.`);
      prevTracks = tracks;

      const items = g.slots.map(s => {
        const span = Math.max(1, Math.min(s.node.span || 1, g.cols));
        return {
          id: s.node.id ?? '(no id)',
          w: widthAtTier(span, g.cols, tracks),
          h: rowspanOf(s.node),
        };
      });
      if (!items.length) continue;

      const { placed, occ, rowCount } = place(items, tracks);
      const area = placed.reduce((n, p) => n + p.w * p.h, 0);
      const rect = tracks * rowCount;
      // The rows a rowspan cell TOUCHES are exempt from the closure, exactly as
      // the render-time L and P exempt a row a `.mrsp` cell touches: a cell-graph
      // / bar-chart row legitimately tapers (that IS the chart), and a swimlane
      // rail legitimately fills a column no single-row cell reaches.
      const exempt = new Set();
      for (const p of placed) if (p.h > 1) for (let i = 0; i < p.h; i++) exempt.add(p.r + i);
      const authoredTier = tracks === g.cols;

      // RECT — the closure identity. In the clean case it is the global form the
      // whole model rests on. Where a rowspan taper is present it is the SAME
      // identity restricted to the rows where it is a truth, which is the only
      // honest way to state it.
      asserted++;
      if (exempt.size === 0) {
        const closes = area === rect;
        if (!closes && authoredTier) {
          fail('RECT', `${g.label} @${tier.w}px`,
            `Σ(spanCols × rowspanRows) = ${area} but the rectangle is ${tracks} tracks × ${rowCount} rows = ${rect} — ` +
            `SHORT BY EXACTLY ${rect - area} cell(s), which is the hole's area. The section is not a filled rectangle.`);
        } else if (!closes) {
          info('RECT', `${g.label} @${tier.w}px`,
            `Σ area ${area} vs ${tracks}×${rowCount}=${rect} (short ${rect - area}) — a short LAST row at a ` +
            `collapsed tier is the legitimate cascade, not a hole (the render gate never asserted fill below 1200px either).`);
        }
      } else {
        // Per-row form: every NON-exempt row must be fully occupied.
        const short = [];
        for (let r = 0; r < rowCount; r++) {
          if (exempt.has(r)) continue;
          let n = 0;
          for (let c = 0; c < tracks; c++) if (occ.has(`${r},${c}`)) n++;
          if (n !== tracks) short.push(`row ${r + 1}: ${n}/${tracks} (short ${tracks - n})`);
        }
        if (short.length && authoredTier)
          fail('RECT', `${g.label} @${tier.w}px`,
            `${short.length} row(s) outside the rowspan taper do not close — ${short.join('; ')}. ` +
            `(${exempt.size} row(s) exempt: a rowspan cell touches them, so the taper IS the chart.)`);
        else if (rect > area)
          info('RECT', `${g.label} @${tier.w}px`,
            `rowspan taper — ${exempt.size}/${rowCount} row(s) exempt, ` +
            `Σ area ${area} of ${rect} (the taper's own area is ${rect - area}).`);
        else
          // A taper that CLOSES says so with the number. When every row is exempt
          // the per-row form has nothing left to assert, so the closure would
          // otherwise be reported only by the ABSENCE of a deficit — and an absence
          // is indistinguishable from a check that never ran. The identity holds
          // here in its global form even though the taper suspended the per-row
          // one, so state it: that is the difference between evidence and silence.
          info('RECT', `${g.label} @${tier.w}px`,
            `rowspan taper CLOSES — Σ(spanCols × rowspanRows) = ${area} === ` +
            `${tracks} tracks × ${rowCount} rows = ${rect}, with ${exempt.size}/${rowCount} row(s) ` +
            `exempt from the per-row form. The taper fills its rectangle exactly.`);
      }

      // HOLE — enumerate the empty cells and separate the two kinds. A TRAILING
      // hole is the tail of the last row (the cascade's legitimate short row); an
      // INTERIOR hole is a gap with content after it, which is always a defect:
      // it means a merge did not fit in the tracks left on its row and dropped
      // down, abandoning the remainder. Interior holes are asserted at EVERY
      // tier, including the collapsed ones.
      const empties = [];
      for (let r = 0; r < rowCount; r++)
        for (let c = 0; c < tracks; c++)
          if (!occ.has(`${r},${c}`)) empties.push({ r, c });
      const isTrailing = ({ r, c }) => {
        if (r !== rowCount - 1) return false;
        for (let j = c; j < tracks; j++) if (occ.has(`${r},${j}`)) return false;
        return true;
      };
      const interior = empties.filter(e => !isTrailing(e) && !exempt.has(e.r));
      asserted++;
      if (interior.length)
        fail('HOLE', `${g.label} @${tier.w}px`,
          `${interior.length} INTERIOR hole cell(s) at [${interior.map(e => `r${e.r + 1}c${e.c + 1}`).join(', ')}] ` +
          `— content follows them, so a merge did not fit in the tracks left on its row and dropped down, ` +
          `abandoning the rest. Hole area: ${interior.length} cell(s).`);

      // TEXT — the CHARACTER BUDGET. Run at EVERY tier: the authored tier gives
      // the narrowest cells and the collapsed ones the largest font, so neither
      // dominates. Findings are DEDUPED to the worst tier per (box, kind) rather
      // than emitted five times — the advisory is about the text, not the sweep.
      const gridW = g.widthAt(tier.w);
      const fontPx = titlePx(tier.w);
      for (const p of placed) {
        const slot = g.slots.find(s => (s.node.id ?? '(no id)') === p.id);
        if (!slot) continue;
        const availPx = cellTextWidth(gridW, tracks, p.w);
        const cell = `cell ${Math.round(availPx)}px = ${p.w} of ${tracks} track(s) in a ` +
          `${Math.round(gridW)}px grid, worst at the ${tier.w}px tier`;
        for (const leaf of (slot.half ? slot.pair : [slot.node])) {
          const compact = g.compactRows;
          const lt = mergeTokens(g.tok, nodeDelta(leaf, 'component'));
          const lines = { titleLines: lt.type.title.lines, descLines: lt.type.desc.lines, cellH: g.cellH };
          const budget = textBudget(leaf, { availPx, fontPx, half: !!slot.half, cell, form, compact, ...lines });
          asserted += budget.asserted;
          const where = `${g.label} > ${leaf && leaf.id != null ? leaf.id : '(no id)'}`;
          for (const f of budget.findings) noteText(where, f, tier.w);
          for (const m of budget.margins) {
            const prev = textTightest.get(m.kind);
            if (!prev || m.slack < prev.slack) textTightest.set(m.kind, { ...m, where, cw: tier.w });
          }

          // INK — the height budget. Run at every tier for the same reason TEXT
          // is, but an overflow FAILS only at the presentation tier of a strict
          // page; elsewhere the worst one per box advises.
          const ig = inkBudget(leaf, { availPx, fontPx, half: !!slot.half, compact, ...lines });
          if (!ig) continue;
          asserted++;
          const atPresent = tier.w === PRESENT.w;
          const stamp = { ...ig, where, cw: tier.w };
          if (ig.slack < inkTightest.slack) Object.assign(inkTightest, stamp);
          if (ig.slack > inkLoosest.slack) Object.assign(inkLoosest, stamp);
          const overflow = `${ig.blocks} ink block(s) need ${ig.ink.toFixed(1)}px and the slot ` +
            `is ${ig.slot.toFixed(1)}px${compact ? ' (a `compact` row)' : ''} — ` +
            `${(-ig.slack).toFixed(1)}px overflows and is clipped (a cell never grows by content), ` +
            `at the ${tier.w}px tier. Move a block into the detail, or merge the cell down a row.`;
          if (ig.slack < 0 && strict && atPresent) fail('INK', where, overflow);
          else if (ig.slack < 0) {
            const prev = inkWorst.get(where);
            if (!prev || ig.slack < prev.slack) inkWorst.set(where, { slack: ig.slack, detail: overflow });
          }
          else if (atPresent && ig.slack > inkVoidPx() && !treatmentsOf(leaf).includes('centered'))
            info('INK', where, `${ig.ink.toFixed(1)}px of ink in a ${ig.slot.toFixed(1)}px slot leaves ` +
              `${ig.slack.toFixed(1)}px undeclared below it at the ${tier.w}px tier — the cell is ` +
              `occupied, so no hole check can see it. Carry the void with \`treatment: [centered]\` ` +
              `or give the cell something to say.`);
        }
      }

      // RAILT — the rail-title ceiling. A horizontal no-rowspan rail sits in an
      // `auto` row (the thin-row rule in engine.js rowTrackList), and
      // `.rail-title` declares NO line clamp — so where a box's third title line
      // is CLIPPED (invariant C's territory), a rail's third line silently GROWS
      // its row and every stack built on the thin-row arithmetic. Run at every
      // tier like TEXT (the narrowest tier holds the fewest characters), deduped
      // to the worst tier per rail, and emitted as a HARD fail: the two-line
      // ceiling is the authored geometry of every thin-rail stack. A hue rail is
      // measured at its own tighter metrics (railHue* above).
      for (const p of placed) {
        const slot = g.slots.find(s => (s.node.id ?? '(no id)') === p.id);
        if (!slot || slot.pair) continue;
        const leaf = slot.node;
        if (!isRailLeaf(leaf) || !isThinRowLeaf(leaf)) continue;
        const track = (gridW - (tracks - 1) * CSS_TEXT.gap) / tracks;
        const fit = railTitleFit(track * p.w + (p.w - 1) * CSS_TEXT.gap, leaf);
        const { cap, lines, fontPx: titlePxRail, trackEm } = fit;
        const indentNote = leaf.indent > 0 ? ` (indent ${leaf.indent}: ${Math.round(fit.px)}px left for the title)` : '';
        asserted++;
        if (lines <= CSS_TEXT.railTitleLines) continue;
        const where = `${g.label} > ${leaf.id ?? '(no id)'}`;
        const prev = railWorst.get(where);
        if (prev && prev.lines >= lines) continue;
        railWorst.set(where, { lines, where: `${where} @${tier.w}px`, detail:
          `rail title wraps to ${lines} line(s) of ${cap} char(s)${indentNote} at ` +
          `${titlePxRail}px mono + ${trackEm}em tracking, and the rail ` +
          `ceiling is ${CSS_TEXT.railTitleLines} — .rail-title has no clamp, so the extra line ` +
          `GROWS the auto row and the stack it lives in. Shorten the title or widen the cell.` });
      }

      if (!authoredTier) continue;   // the checks below are authored-tier truths

      // TRACK — a dead track: a column no slot ever occupies. The engine's clamp
      // is what prevents this, so this check GUARDS THE CLAMP (as E does on the
      // render). If it ever fires, the clamp and the placement have diverged.
      const usedCols = new Set();
      for (const p of placed) for (let j = 0; j < p.w; j++) usedCols.add(p.c + j);
      asserted++;
      if (usedCols.size < tracks)
        fail('TRACK', `${g.label} @${tier.w}px`,
          `${tracks - usedCols.size} DEAD track(s): column(s) ` +
          `[${[...Array(tracks).keys()].filter(c => !usedCols.has(c)).map(c => c + 1).join(', ')}] ` +
          `are declared but no cell ever reaches them (a reserved empty column).`);

      // ROW — an orphan row: a lone single-track cell on its own row while a
      // sibling row holds two or more. Scoped exactly as the render-time P was:
      // grid-dense forms only, more than one track, and rows a rowspan touches
      // exempt (a tapering chart row is not an orphan).
      if (tracks > 1 && GRID_DENSE.has(form)) {
        const startsPerRow = new Map();
        for (const p of placed) {
          if (!startsPerRow.has(p.r)) startsPerRow.set(p.r, []);
          startsPerRow.get(p.r).push(p);
        }
        const grouped = [...startsPerRow.entries()].some(([r, ps]) => !exempt.has(r) && ps.length >= 2);
        for (const [r, ps] of startsPerRow) {
          if (exempt.has(r) || !grouped) continue;
          asserted++;
          if (ps.length === 1 && ps[0].w === 1)
            fail('ROW', `${g.label} @${tier.w}px`,
              `"${ps[0].id}" sits ALONE on row ${r + 1} of a ${tracks}-track grid while another row holds ` +
              `2+ cells — the group's uniformity breaks and the row reads as a dangling remainder.`);
        }
      }

      // BAND placement — a band owns its row. Structurally guaranteed by the
      // placement model, so a failure here means the model and the data disagree
      // about what a band is, which would invalidate every closure above. Only
      // reached at the authored tier (the `continue` above), which is the one tier
      // where isBandAtTier and the .msp class agree — so this asserts exactly the
      // authored `span == columns` bands it always did.
      for (const p of placed.filter(p => p.band)) {
        asserted++;
        const sharers = placed.filter(q => q !== p && q.r < p.r + p.h && q.r + q.h > p.r);
        if (sharers.length)
          fail('BAND', `${g.label} @${tier.w}px`,
            `band "${p.id}" shares row ${p.r + 1} with [${sharers.map(q => q.id).join(', ')}] — a band ` +
            `(span == columns) must occupy its own full row.`);
      }

      // LANE — swimlanes of unequal length. A row LED by a `rail` is a lane: the
      // rail is its label and the cells after it are its steps. Two lanes in one
      // grid that do not reach the same track are a ragged diagram, and unlike a
      // short last row this is not a cascade artefact — it is authored.
      const railRows = placed.filter(p => {
        const node = g.slots.find(s => (s.node.id ?? '(no id)') === p.id);
        return node && node.node.type === 'rail' && p.c === 0;
      });
      if (railRows.length >= 2) {
        const reach = railRows.map(p => {
          const inRow = placed.filter(q => q.r === p.r);
          return { id: p.id, r: p.r, end: Math.max(...inRow.map(q => q.c + q.w)) };
        });
        const widest = Math.max(...reach.map(x => x.end));
        for (const x of reach) {
          asserted++;
          if (x.end !== widest)
            fail('LANE', `${g.label} @${tier.w}px`,
              `swimlane led by rail "${x.id}" (row ${x.r + 1}) reaches track ${x.end} while another lane ` +
              `reaches ${widest} — lanes in one grid must be of equal length.`);
        }
      }
    }
    trackTable.push(row);
  }

  // FROZEN — an undeclared hole under something that CANNOT GROW.
  //
  // Above the stack breakpoint a compound grid is a flex ROW with
  // align-items:stretch, so every section in it is as tall as the TALLEST one.
  // The section stretches; the fixed rows inside it do not. For ordinary content
  // that is the doctrine's "the hole speaks" — the author can close it with a
  // row or declare it. For a `vertical` box it is neither: a rotated bar's
  // LENGTH is its assertion (its height is authored as `rowspan`), so it cannot
  // be grown to meet the row without saying something else — and nothing in the
  // data ties that rowspan to the neighbour that sets the row height. The author
  // did that arithmetic once, in their head; any later change to the neighbour
  // reopens the hole in silence. This check IS the missing tie, and it is the
  // defect the eye caught when a thin separator row grew back to --cell-h and
  // pushed its neighbour 90px past a bar that could not follow.
  //
  // WHY IT FAILS WHERE TEXT ONLY INFORMS. TEXT rests on an ASSUMED constant and
  // OVER-states demand, so it can flag text that is fine — an estimate must not
  // stop a build. This rests on no assumed constant: every input is a mirrored
  // declaration (asserted against index.html by the CSS check) or an exact
  // structural count, and the one unknowable — the zone header's height — is
  // EXCLUDED rather than estimated. So the arithmetic UNDER-states the hole and
  // the error runs the other way: a finding is a real hole of AT LEAST the size
  // reported. That is a verdict, like RECT's, not a warning.
  //
  // SCOPE, and therefore what it does not see:
  //   • GRID_DENSE forms only, exactly as ROW is: "a hole under a frozen element
  //     is a defect" is the compaction claim, and a timeline or a mind-map may
  //     leave vertical air on purpose.
  //   • only above the stack breakpoint — below it a compound is a COLUMN, so
  //     there is no shared row height and no hole to open.
  //   • only a frozen leaf inside a HEADERLESS LEAF-GRID section (the `plain`
  //     wrapper idiom that gives a rotated box its vertical axis). With a header
  //     the frozen side's own height stops being exact, and comparing two floors
  //     could invent a hole that is not there.
  //   • a frozen box sitting DIRECTLY in a compound is exempt for a different
  //     reason: the stylesheet pins it (`flex:0 0 auto; align-self:start;
  //     height:var(--cell-h)`), so its short height is DECLARED in the CSS
  //     rather than left to a neighbour's arithmetic.
  //   • a hole SMALLER than the header it declined to measure is invisible here.
  //     That is the price of never crying wolf, and it is paid on purpose.
  if (GRID_DENSE.has(form)) {
    const gridOf = new Map(grids.map(g => [g.node, g]));
    const frozenWorst = new Map();
    for (const g of grids) {
      // A `sec-c1` compound is flex-direction:column — a stack, not a row.
      if (!g.compound || g.cols <= 1) continue;
      const spanOf = n => Math.max(1, Math.min(Number(n && n.span) || 1, g.cols));
      const inRow = g.children.filter(c => spanOf(c) < g.cols);   // a band owns its row
      if (inRow.length < 2) continue;
      for (const tier of TIERS) {
        if (tier.w <= BP_STACK) continue;
        for (const sec of inRow) {
          if (!isSection(sec)) continue;
          const frozen = (sec.children || [])
            .filter(c => !isSection(c) && treatmentsOf(c).includes('vertical'));
          if (!frozen.length) continue;
          const mine = zoneHeightPx(sec, gridHeightPx(gridOf.get(sec), tier.w));
          if (!mine || !mine.exact) continue;
          let tallest = null;
          for (const sib of inRow) {
            if (sib === sec) continue;
            const h = isSection(sib)
              ? zoneHeightPx(sib, gridHeightPx(gridOf.get(sib), tier.w))
              // A leaf directly in a compound is content-sized except a box (and
              // a half-slot), which the stylesheet fixes at --cell-h. A rail, a
              // separator or a spacer has no knowable height, so it contributes
              // nothing — quieter, never louder.
              : (!sib.type || sib.type === 'box' ? { px: CSS_TEXT.cellH } : null);
            if (h && (!tallest || h.px > tallest.px))
              tallest = { px: h.px, id: sib.id ?? '(no id)' };
          }
          asserted++;
          if (!tallest || tallest.px <= mine.px) continue;
          const key = `${g.label} > ${sec.id ?? '(no id)'}`;
          const gap = tallest.px - mine.px;
          const prev = frozenWorst.get(key);
          if (prev && prev.gap >= gap) continue;
          frozenWorst.set(key, { gap, where: `${key} @${tier.w}px`,
            detail:
              `"${frozen.map(c => c.id ?? '(no id)').join(', ')}" is FROZEN at ${mine.px}px ` +
              `(a \`vertical\` box: its height is its authored rowspan, and a rotated bar cannot be ` +
              `stretched without changing what it says), but its flex row is at least ${tallest.px}px ` +
              `— sibling "${tallest.id}". align-items:stretch grows the section and NOT the fixed rows ` +
              `inside it, so at least ${gap}px of UNDECLARED hole sits under an element that cannot grow. ` +
              `The number is a floor: zone headers are excluded from the height chain, so the real hole ` +
              `is larger. Either make the neighbour's height match the bar's rowspan, or give the bar the ` +
              `rowspan the row actually needs — nothing in the data ties the two together.` });
        }
      }
    }
    for (const f of frozenWorst.values()) fail('FROZEN', f.where, f.detail);
  }

  // LANE (advisory) — parallel single-column stacks of unequal depth. Sibling
  // sections that are all `columns: 1` read as parallel lanes, so unequal depth
  // shows as a ragged bottom edge once the row stretches them. It is often
  // DELIBERATE (a tall block beside a short one is a legitimate composition), so
  // this informs and never fails.
  for (const g of grids) {
    const sibs = g.children.filter(isSection);
    if (sibs.length < 2) continue;
    const stacks = sibs.filter(s => effectiveCols(s.columns, slotsOf(s.children), (s.children || []).some(isSection)) === 1);
    if (stacks.length < 2 || stacks.length !== sibs.length) continue;
    const depths = stacks.map(s => ({ id: s.id ?? '(no id)', n: (s.children || []).length }));
    if (new Set(depths.map(d => d.n)).size > 1)
      info('LANE', `${g.label}`,
        `parallel single-column stacks of unequal depth (${depths.map(d => `${d.id}:${d.n}`).join(', ')}) — ` +
        `the row stretches the shorter one, so its bottom edge is padding rather than content. ` +
        `Deliberate in a tall-beside-short composition; a defect if they were meant to be lanes.`);
  }

  // HEADER — every section's title and subtitle against its clamp, at the
  // zone's inner width, at every tier. Compound grids included: a section's
  // header is drawn whatever its parent's layout is.
  for (const g of grids) {
    for (const c of g.children || []) {
      if (!isSection(c) || !(c.title || c.subtitle)) continue;
      const where = `${g.label} > ${c.id ?? '(no id)'} (header)`;
      for (const tier of SWEEP) {
        const hb = headerBudget(c, nestedTrackArea(g, c, tier.w), tier.w);
        asserted++;
        for (const f of hb.findings) noteText(where, f, tier.w);
      }
    }
  }

  for (const [key, f] of textWorst) if (!textFail.has(key)) info('TEXT', f.where, f.detail);
  for (const f of textFail.values())
    fail('TEXT', f.where, `${f.detail} This is the presentation tier (document.yaml \`viewport\`); ` +
      `a page that accepts it declares \`text_fit: advisory\`.`);
  for (const [where, f] of inkWorst) info('INK', where, f.detail);
  for (const f of railWorst.values()) fail('RAILT', f.where, f.detail);

  return { pageId, form, grids, trackTable, leaves: leavesOf(page).length };
}

// ── MAIN ──────────────────────────────────────────────────────────────────
function main() {
  console.log('\n══════════ STATIC LAYOUT CHECK (arithmetic, no browser) ══════════\n');
  console.log(`deck root: ${ROOT}`);

  // The census first: if the generated data does not match the authored YAML,
  // everything below still describes the YAML correctly — but the deck someone is
  // LOOKING at is a different one, and saying so first is the honest order.
  const sc = staticCensus(ROOT);
  console.log('\nCENSUS  (data/*.yaml vs data/data.generated.js)');
  if (sc.ok) {
    console.log(`    [PASS] generated data matches the authored YAML — ${sc.summary}`);
  } else {
    for (const p of sc.problems) console.log(`    [FAIL] ${p}`);
  }

  // TOKENS — the model computes with the bundle's resolved tokens, the same set
  // the engine turns into CSS properties. A bundle without them cannot be
  // judged: every number below would be a default nobody built.
  const gen = loadGenerated(ROOT);
  console.log('\nTOKENS  (window.__DOC__.tokens — what the engine and both gates compute with)');
  if (gen.ok && gen.doc.tokens) {
    applyTokens(gen.doc.tokens);
    asserted++;
    console.log(`    [PASS] row ${CSS_TEXT.cellH}px (sep ${CSS_TEXT.sepRowH}, compact ${CSS_TEXT.compactCellH}), ` +
      `title ${CSS_TEXT.titleMinPx}-${CSS_TEXT.titleMaxPx}px/${CSS_TEXT.titleLines}ln, desc ${CSS_TEXT.descPx}px/` +
      `${CSS_TEXT.descLines}ln, plane ${CSS_TEXT.planeMax}px, breakpoints ${BP_ONE}/${BP_TWO}/${BP_STACK}px, ` +
      `viewport ${PRESENT.w}x${PRESENT.h}`);
  } else {
    fail('TOKENS', 'data/data.generated.js', `${gen.problem || 'the bundle carries no `tokens`'} — the model ` +
      'falls back to DEFAULT_TOKENS, which may not be the deck that was built.');
  }

  // CSS — the breakpoints this gate computes with, against the ones index.html
  // actually declares. Every tracks-per-tier number below is derived from them, so
  // a stylesheet edit that moved a cut would otherwise leave this gate asserting a
  // cascade the browser no longer renders — green, and wrong.
  //
  // The two ways a mirror can fail to read are OPPOSITE and split accordingly. No
  // index.html at all is a data-only fixture: there is nothing to disagree with,
  // so it is NOT ASSERTED — recorded, counted, and never a pass. An index.html
  // that IS present and whose probe missed is a FAILURE: the declaration the
  // mirror needs is gone or was rewritten past the probe, so every number derived
  // from it is unverified, and "not asserted" would be exactly the silence that
  // certifies the drift. Never the reverse.
  const bp = cssBreakpoints(ROOT);
  const mirrored = [...new Set([BP_STACK, BP_TWO, BP_ONE])].sort((a, b) => b - a);
  console.log('\nCSS  (tokens.breakpoints vs the `@container stage` queries in data/breakpoints.generated.css)');
  if (bp.noFile) {
    fail('CSS', 'data/breakpoints.generated.css', `${bp.problem}, so index.html links a collapse cascade `
      + `that does not exist and the tracks-per-tier numbers below describe nothing the browser draws.`);
  } else if (!bp.ok) {
    fail('CSS', 'data/breakpoints.generated.css', `${bp.problem} — every tracks-per-tier number below `
      + `describes a cascade nothing confirmed.`);
  } else {
    asserted++;
    if (bp.widths.join('|') !== mirrored.join('|'))
      fail('CSS', 'data/breakpoints.generated.css', `container queries declare [${bp.widths.join(', ')}]px but ` +
        `tokens.breakpoints resolve to [${mirrored.join(', ')}]px — the generated stylesheet is stale; run \`npm run build\`.`);
    else console.log(`    [PASS] ${bp.widths.join(' / ')}px — the mirror matches the stylesheet`);
  }

  // The TEXT BUDGET's mirror, asserted the same way and for the same reason: the
  // budget is arithmetic over the stylesheet's own chrome, font sizes and clamps,
  // so a moved declaration turns every character number below into a measurement
  // of a deck the browser no longer draws.
  const ct = cssTextTokens(ROOT);
  console.log('\nCSS  (mirrored text metrics vs the .box / .zone / .canvas declarations in index.html)');
  if (ct.noFile) {
    notAsserted('CSS', 'index.html', `${ct.problem}, so the chrome, font sizes and clamps cannot be `
      + 'read back. The TEXT budget below uses the mirror UNVERIFIED.');
  } else if (!ct.ok) {
    fail('CSS', 'index.html', `${ct.problem} — the mirror could not be READ from a stylesheet that IS `
      + `present, so every character number in the TEXT budget below is derived from constants nothing confirmed.`);
  } else {
    asserted++;
    if (ct.drift.length)
      fail('CSS', 'index.html', `drifted — ${ct.drift.join(', ')}. The fixed chrome is computed with ` +
        `directly, and a token default is what a deck without its bundle draws, so either copy disagreeing ` +
        `with the gate describes a deck the browser does not draw.`);
    else console.log(`    [PASS] every token rule spends its var(); fixed chrome and token defaults match DEFAULT_TOKENS`);
  }

  // SPAN — the span->tracks rules, a hard check with the same two-way split: no
  // stylesheet is NOT ASSERTED, a stylesheet missing a rule FAILS.
  const spanCss = cssSpanShapes(ROOT);
  if (spanCss.noFile) {
    notAsserted('SPAN', 'index.html', 'there is no index.html under the deck root, so the span->tracks ' +
      'rules every width in this report assumes cannot be read.');
  } else {
    asserted += CSS_SPAN_SHAPES.length;
    if (spanCss.missing.length)
      fail('SPAN', 'index.html', `the stylesheet implements no [${spanCss.missing.join('; ')}]. ` +
        `Every width in this report assumes a span of M occupies M tracks; with that rule absent the ` +
        `section is auto-placed into ONE track and renders at its min-content, so the arithmetic below ` +
        `describes a geometry the browser does not draw.`);
  }
  const spanHeadline = () =>
    `all ${spanCss.present.length} rules present (band and partial span, in the leaf grid and the root band grid)`;

  const deck = loadAuthoredDeck(ROOT);
  if (!deck.manifest) {
    console.log('\n══════════════════════════════════════════════════════════════');
    for (const p of deck.problems) console.log(`    [FAIL] ${p}`);
    console.log('\nFAIL — the authored deck could not be READ, so nothing was asserted.\n');
    process.exitCode = 1;
    return;
  }

  const pages = [];
  for (const { page } of deck.pages) pages.push(checkPage(page));

  // CHIP-X — a key means one thing on every page (principle 6: the same key on
  // two pages projects one onto the other), so it carries one label everywhere.
  // Core chips hold by construction; this catches two page chips sharing a key.
  const labelsByKey = new Map();
  for (const { page } of deck.pages)
    for (const f of page.filters || []) {
      if (!f || typeof f.key !== 'string' || f.key === RESET_CHIP) continue;
      if (!labelsByKey.has(f.key)) labelsByKey.set(f.key, new Map());
      labelsByKey.get(f.key).set(f.label, [...(labelsByKey.get(f.key).get(f.label) || []), page.id ?? '(no id)']);
    }
  for (const [key, labels] of labelsByKey) {
    asserted++;
    if (labels.size > 1)
      fail('CHIP-X', `chip "${key}"`, `carries ${labels.size} labels across pages: ` +
        [...labels].map(([l, ids]) => `"${l}" on [${ids.join(', ')}]`).join(' vs ') +
        '. A key the reader meets on two pages must mean one thing: give it one label, or split it into two keys.');
  }

  // HARMONY — opt-in (`harmony: true` in document.yaml): every box and rail
  // belongs to at least one chip, so a chip-driven deck leaves nothing the
  // reader cannot spotlight. The lead band states the page and is exempt.
  const harmony = deck.manifest.harmony === true;
  if (harmony)
    for (const { page } of deck.pages)
      for (const leaf of leavesOf(page)) {
        const t = leaf.type ?? 'box';
        if ((t !== 'box' && t !== 'rail') || leaf.lead === true) continue;
        asserted++;
        if (!(Array.isArray(leaf.filters) && leaf.filters.length))
          fail('HARMONY', `page "${page.id ?? '(no id)'}" ${t} "${leaf.id ?? '(no id)'}"`,
            'belongs to no chip, and the deck declares `harmony: true`. Add it to the chip it serves, ' +
            'or, if it states the page, make it the `lead` band.');
      }
  const harmonyHeadline = () => harmony ? 'every box and rail belongs to a chip (lead bands exempt)'
    : 'off — document.yaml does not declare `harmony: true`';

  // HEIGHT — each page's predicted full height at the presentation viewport.
  // ADVISORY: a deck may mean to scroll; the finding says by how much it does.
  const heights = [];
  for (const { page } of deck.pages) {
    const { totalPx } = predictPageHeight(page, PRESENT.w);
    asserted++;
    heights.push({ id: page.id ?? '(no id)', px: Math.round(totalPx) });
    const note = pageHeightAdvisory(totalPx, PRESENT);
    if (note) info('HEIGHT', `page ${page.id ?? '(no id)'}`, note);
  }
  const heightHeadline = () => {
    const top = heights.reduce((a, b) => (b.px > a.px ? b : a), { id: '—', px: 0 });
    const over = heights.filter(x => x.px > PRESENT.h).length;
    return `predicted at ${PRESENT.w}x${PRESENT.h} — ${over} of ${heights.length} page(s) taller than the ` +
      `viewport; tallest ${top.id} ${top.px}px`;
  };

  // WORDS — every authored string against the bundle the browser loads.
  const bundleFile = path.join(ROOT, 'data', 'data.generated.js');
  if (!fs.existsSync(bundleFile)) {
    notAsserted('WORDS', 'data/data.generated.js', 'the bundle does not exist yet, so no authored string ' +
      'could be looked for in it. Run the build.');
  } else {
    const bundle = fs.readFileSync(bundleFile, 'utf8');
    for (const { page } of deck.pages) {
      const missing = [];
      for (const s of authoredStrings(page, [])) {
        asserted++;
        if (!bundle.includes(escapeForBundle(s))) missing.push(s);
      }
      if (missing.length)
        fail('WORDS', `page "${page.id ?? '(no id)'}"`,
          `${missing.length} authored string(s) are NOT in data/data.generated.js — the browser is ` +
          `rendering the OLD words while the counts still match, so CENSUS cannot see it. ` +
          `First: "${missing[0].slice(0, 60)}". Re-run the build.`);
    }
  }

  // ── report ──
  for (const p of pages) {
    console.log(`\n● page "${p.pageId}" [form:${p.form}] — ${p.grids.length} grid(s), ` +
      `${p.trackTable.length} with a track model, ${p.leaves} component(s)`);
    console.log('\n  TRACKS PER TIER — derived from the container breakpoints ' +
      `${BP_ONE} / ${BP_TWO} / ${BP_STACK}px. This is a PURE FUNCTION of ` +
      '(effective columns, container width), which is why it replaces the browser width sweep:');
    const head = `    ${'grid'.padEnd(30)} ${'auth'.padStart(4)} ${'eff'.padStart(4)}  ` +
      SWEEP.map(t => String(t.w).padStart(5)).join(' ');
    console.log(head);
    console.log(`    ${'-'.repeat(30)} ${'-'.repeat(4)} ${'-'.repeat(4)}  ${SWEEP.map(() => '-----').join(' ')}`);
    for (const r of p.trackTable)
      console.log(`    ${r.grid.slice(-30).padEnd(30)} ${String(r.authored).padStart(4)} ${String(r.cols).padStart(4)}  ` +
        SWEEP.map(t => String(r.tracks[t.name]).padStart(5)).join(' '));
  }

  const CHECKS = [
    ['RECT', 'rectangle closure — Σ(spanCols × rowspanRows) == tracks × rowCount'],
    ['HOLE', 'interior holes (a merge that did not fit and dropped down)'],
    ['TRACK', 'no dead track (a declared column the content never reaches)'],
    ['ROW', 'no orphan row (a lone cell while a sibling row is grouped)'],
    ['LANE', 'swimlanes / parallel stacks of equal length'],
    ['FROZEN', 'no undeclared hole under an element that cannot grow ' +
      '(a `vertical` box in a flex row taller than itself)'],
    ['BAND', 'band placement and declared span within the grid'],
    ['TIER', 'collapse cascade is monotone across the container tiers'],
    ['CHIP', 'filter referential integrity (both directions) + chip arity'],
    ['CHIP-X', 'a chip key carries one label on every page (core chips are inherited, page chips must agree)'],
    ['HARMONY', 'opt-in: every box and rail belongs to at least one chip, the lead band exempt', harmonyHeadline],
    ['LIT', 'no filter on a leaf type the engine cannot light (separator/spacer ' +
      'carry no data-filters, so their chip membership passes the join and never renders)'],
    ['RAILT', 'rail titles within the two-line ceiling (a thin rail row is `auto` and ' +
      '.rail-title has no clamp, so a third line grows the row instead of clipping)'],
    ['ORDER', 'no duplicate effective `order` among siblings'],
    // A third entry is an optional PASS DETAIL: what the check MEASURED when it
    // holds, so a pass reports a number instead of a bare "holds everywhere".
    ['TEXT', 'character budget: title token, kicker token, title clamp, description clamp, section ' +
      'header clamps (a line overflow FAILS at the presentation tier of a page not declared ' +
      '`text_fit: advisory`; everything else is ADVISORY — `validate` N/TXT are the render verdicts)', textHeadline],
    ['WORDS', 'every authored string is present verbatim in data/data.generated.js ' +
      '(the text-only staleness CENSUS cannot see, because node counts do not move)'],
    ['INK', 'ink height vs the MODELLED slot — overflow fails, an undeclared void advises ' +
      '(OPT-OUT: every page not declared `text_fit: advisory`, failing at the presentation tier. ' +
      'A `compact` grid is budgeted at its own row. ARITHMETIC, NOT OBSERVED: the slot is the ' +
      'one the model derives from the authored spans, so a PASS here says the text fits the cell the ' +
      'stylesheet OWES the box — not that the browser drew that cell. `validate` TXT is the verdict ' +
      'on real clamping)', inkHeadline],
    ['HEIGHT', 'page height predicted from the placement model vs the document.yaml viewport ' +
      '(ADVISORY — `validate` VH measures it)', heightHeadline],
    ['CSS', 'the mirrored breakpoints and text metrics match index.html'],
    ['SPAN', 'index.html implements the span→tracks rules every width in this report assumes ' +
      '(a missing rule FAILS: unlike a metric there is no mirrored constant to fall back to)',
      spanHeadline],
  ];
  console.log('\n  ── CHECKS ─────────────────────────────────────────────────────');
  for (const [id, name, passDetail] of CHECKS) {
    const fails = findings.filter(f => f.check === id && f.sev === 'fail');
    const quiets = findings.filter(f => f.check === id && f.sev === 'not-asserted');
    const infos = findings.filter(f => f.check === id && f.sev === 'info');
    console.log(`\n  ${id}  ${name}`);
    // A check with ANY part it could not assert never prints [PASS]: the line
    // above names what the check claims IN FULL, and half of it holding is not
    // that claim. The absence of a failure is not the presence of an assertion.
    if (!fails.length && !quiets.length)
      console.log(`    [PASS] ${passDetail ? passDetail() : 'holds everywhere it applies'}`);
    for (const f of fails) console.log(`    [FAIL] ${f.where}: ${f.detail}`);
    for (const f of quiets) console.log(`    [NOT ASSERTED] ${f.where}: ${f.detail}`);
    for (const f of infos) console.log(`    [INFO] ${f.where}: ${f.detail}`);
  }

  const failed = findings.filter(f => f.sev === 'fail').length;
  const advisories = findings.filter(f => f.sev === 'info').length;
  const unasserted = findings.filter(f => f.sev === 'not-asserted').length;
  const censusFail = sc.ok ? 0 : 1;
  console.log('\n══════════════════════════════════════════════════════════════');
  // ZERO ASSERTIONS IS RED, NEVER GREEN — the same rule validate's verdict holds.
  // `failed === 0` is a VACUOUS truth when nothing was asserted, and a guardrail
  // that measured nothing has no business printing a pass.
  if (asserted === 0) {
    console.log(`FAIL — 0 assertions ran across ${pages.length} page(s). A gate that asserted NOTHING is ` +
      `not a pass: either no page was read, or no grid carried a track model.\n`);
    process.exitCode = 1;
    return;
  }
  const adv = advisories ? ` (${advisories} advisory note(s) — [INFO], non-failing)` : '';
  // The not-asserted count sits in the HEADLINE, beside the assertion count, for
  // the same reason the assertion count is there: a reader who cites this line as
  // a verdict must see what the gate could not measure without reading upward for
  // it. A non-zero count is NOT GREEN — nothing failed, and the gate cannot say
  // the deck holds either.
  if (failed + censusFail === 0 && unasserted === 0) {
    console.log(`ALL PASS — ${asserted} assertions, 0 not-asserted, across ${pages.length} page(s) × ` +
      `${SWEEP.length} container tiers, no browser${adv}.\n`);
    process.exitCode = 0;
    return;
  }
  if (failed + censusFail === 0) {
    console.log(`NOT ASSERTED — ${asserted} assertions ran and ${unasserted} check(s) could NOT be made, ` +
      `across ${pages.length} page(s) × ${SWEEP.length} container tiers. Nothing failed, and this is not a ` +
      `pass: see the [NOT ASSERTED] lines above${adv}.\n`);
    process.exitCode = 1;
    return;
  }
  console.log(`FAIL — ${failed} failing check(s)${censusFail ? ' + a stale/divergent census' : ''}` +
    `${unasserted ? ` + ${unasserted} not-asserted` : ''} out of ${asserted} assertions. ` +
    `See the [FAIL] lines above${adv}.\n`);
  process.exitCode = 1;
}

// Run ONLY when invoked as the gate. Imported (by the agreement test in
// tools/test-guards.mjs, which asserts this placement model still matches the
// engine's) the module must expose its functions without running a gate or
// terminating the importing process.
if (process.argv[1]?.endsWith(`${path.sep}check-layout.mjs`)) main();

export { applyTokens, metricsFrom, tiersFor, widthAtTier, isBandAtTier, isBandClass, place, tracksFor,
  orderedChildren, slotsOf, effectiveCols, DEFAULT_SECTION_COLUMNS,
  planeWidth, cellTextWidth, titlePx, capacityFor, wrapLines, longestToken,
  textBudget, cssTextTokens, CSS_TEXT, MONO_ADVANCE_EM, isThinRowLeaf,
  inkBudget, slotHeightPx, railTitleWidth, railTitleFit, headerBudget, zoneTitlePx,
  predictPageHeight, pageHeightAdvisory, PAGE_CHROME_PX };
