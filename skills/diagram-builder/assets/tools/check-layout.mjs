// ─────────────────────────────────────────────────────────────────────────
// check-layout.mjs — the MANDATORY, PROGRAMMATIC layout gate. No browser.
// @version 1.0.0  (part of the diagram-builder skill; keep the placement model in
//                  sync with engine/engine.js buildGrid + the container-query
//                  tiers in index.html)
//
// Run: npm run check   (or: node tools/check-layout.mjs [deckRoot])
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
//   npm run check     MANDATORY. Static, arithmetic, js-yaml only (already a build
//                     dependency). Proves the layout CLOSES and the data is sound.
//   npm run validate  OPTIONAL REINFORCEMENT. Renders one width in Chromium and
//                     asserts only what genuinely needs PIXELS (legibility, word
//                     fit, text truncation, flex wrap points, real geometry).
//                     Skips cleanly and exits 0 where no browser exists.
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
//   BAND   band placement: a band owns its whole row, and a declared span never
//          exceeds the columns it is placed in.
//   TIER   the derived tracks-per-tier table, and the monotonicity of the cascade
//          (tracks never grow as the container narrows). Supersedes F.
//   CHIP   filter referential integrity in BOTH directions, plus ARITY: a chip
//          with a single member does not express a relation, and since an active
//          chip dims everything it does not name, a one-member chip switches the
//          deck off. Supersedes K, which only closed the join.
//   ORDER  a duplicate effective `order` among siblings — today resolved silently
//          by the index tie-break, so the author's intended sequence is a
//          coin flip that can change under an unrelated edit.
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
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import censusLib from './static-census.cjs';

const { loadAuthoredDeck, staticCensus, cssBreakpoints,
  DEFAULT_ROOT, DEFAULT_FORM, GRID_DENSE, BREAKPOINTS } = censusLib;

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
const { stack: BP_STACK, two: BP_TWO, one: BP_ONE } = BREAKPOINTS;

// The five widths the retired browser sweep used, kept EXACTLY so this gate
// covers the same span it replaced: the 1-track endpoint, the 2-track
// intermediate, the first fully-authored tier, and the two side-by-side tiers.
const TIERS = [
  { name: 'min', w: 600 }, { name: 'medium', w: 900 }, { name: 'large', w: 1200 },
  { name: 'huge', w: 1920 }, { name: 'ultra', w: 2560 },
];

// engine.js: `const DEFAULT_SECTION_COLUMNS = 2`. It cannot be imported from
// there (see THE PLACEMENT MODEL below), so it is mirrored and pinned by the
// agreement test in tools/test-guards.mjs. GRID_DENSE and DEFAULT_FORM are NOT
// mirrored — both gates take them from static-census.cjs.
const DEFAULT_SECTION_COLUMNS = 2;
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
// and still checked for sibling-level defects (ORDER, LANE).
function discoverGrids(page) {
  const grids = [];
  const walk = (node, label, isRoot) => {
    const children = isRoot ? (node.sections || []) : (node.children || []);
    const slots = slotsOf(children);
    const compound = children.some(isSection);
    const authored = node.columns;
    const cols = effectiveCols(authored, slots, compound);
    const hasBand = slots.some(s => isBandClass(Math.max(1, Math.min(s.node.span || 1, cols)), cols));
    grids.push({
      label, isRoot, compound, children, slots,
      authoredCols: Math.max(1, Number.isInteger(authored) && authored > 0 ? authored : DEFAULT_SECTION_COLUMNS),
      cols, hasBand,
      // Placeable = it is laid out as a CSS grid with tracks.
      placeable: !compound || (isRoot && hasBand),
      // The root-with-bands grid only exists above the stack breakpoint.
      minWidth: (isRoot && compound) ? BP_STACK + 1 : 0,
    });
    for (const c of children) if (isSection(c)) walk(c, `${label} > ${c.id ?? '(no id)'}`, false);
  };
  walk(page, page.id != null ? `${page.id}:root` : 'root', true);
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

// ── THE CHECK RUN ─────────────────────────────────────────────────────────
// Findings are collected, never printed as they are found, so the report can be
// grouped by CHECK (one line per check plus its failures) instead of interleaving
// twelve grids × five tiers of noise.
const findings = [];   // { check, sev: 'fail'|'info', where, detail }
let asserted = 0;      // how many assertions actually ran (0 => RED, see below)

const fail = (check, where, detail) => findings.push({ check, sev: 'fail', where, detail });
const info = (check, where, detail) => findings.push({ check, sev: 'info', where, detail });

function checkPage(page) {
  const pageId = page.id ?? '(no id)';
  const form = page.form ?? DEFAULT_FORM;
  const grids = discoverGrids(page);
  const trackTable = [];

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
    for (const tier of TIERS) {
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

  // CSS — the breakpoints this gate computes with, against the ones index.html
  // actually declares. Every tracks-per-tier number below is derived from them, so
  // a stylesheet edit that moved a cut would otherwise leave this gate asserting a
  // cascade the browser no longer renders — green, and wrong. A deck with no
  // index.html (a data-only fixture) cannot be checked and says so rather than
  // claiming a pass it did not earn.
  const bp = cssBreakpoints(ROOT);
  const mirrored = [...new Set(Object.values(BREAKPOINTS))].sort((a, b) => b - a);
  console.log('\nCSS  (mirrored breakpoints vs the `@container stage` queries in index.html)');
  if (!bp.ok) {
    console.log(`    [INFO] not asserted — ${bp.problem}. The cascade below uses the mirror: ${mirrored.join(' / ')}px.`);
  } else {
    asserted++;
    if (bp.widths.join('|') !== mirrored.join('|'))
      fail('CSS', 'index.html', `container queries declare [${bp.widths.join(', ')}]px but the gate ` +
        `computes with [${mirrored.join(', ')}]px — the stylesheet moved and static-census.cjs BREAKPOINTS did not, ` +
        `so every tracks-per-tier number below describes a cascade the browser does not render.`);
    else console.log(`    [PASS] ${bp.widths.join(' / ')}px — the mirror matches the stylesheet`);
  }

  const deck = loadAuthoredDeck(ROOT);
  if (!deck.manifest) {
    console.log('\n══════════════════════════════════════════════════════════════');
    for (const p of deck.problems) console.log(`    [FAIL] ${p}`);
    console.log('\nFAIL — the authored deck could not be READ, so nothing was asserted.\n');
    process.exit(1);
  }

  const pages = [];
  for (const { page } of deck.pages) pages.push(checkPage(page));

  // ── report ──
  for (const p of pages) {
    console.log(`\n● page "${p.pageId}" [form:${p.form}] — ${p.grids.length} grid(s), ` +
      `${p.trackTable.length} with a track model, ${p.leaves} component(s)`);
    console.log('\n  TRACKS PER TIER — derived from the container breakpoints ' +
      `${BP_ONE} / ${BP_TWO} / ${BP_STACK}px. This is a PURE FUNCTION of ` +
      '(effective columns, container width), which is why it replaces the browser width sweep:');
    const head = `    ${'grid'.padEnd(30)} ${'auth'.padStart(4)} ${'eff'.padStart(4)}  ` +
      TIERS.map(t => String(t.w).padStart(5)).join(' ');
    console.log(head);
    console.log(`    ${'-'.repeat(30)} ${'-'.repeat(4)} ${'-'.repeat(4)}  ${TIERS.map(() => '-----').join(' ')}`);
    for (const r of p.trackTable)
      console.log(`    ${r.grid.slice(-30).padEnd(30)} ${String(r.authored).padStart(4)} ${String(r.cols).padStart(4)}  ` +
        TIERS.map(t => String(r.tracks[t.name]).padStart(5)).join(' '));
  }

  const CHECKS = [
    ['RECT', 'rectangle closure — Σ(spanCols × rowspanRows) == tracks × rowCount'],
    ['HOLE', 'interior holes (a merge that did not fit and dropped down)'],
    ['TRACK', 'no dead track (a declared column the content never reaches)'],
    ['ROW', 'no orphan row (a lone cell while a sibling row is grouped)'],
    ['LANE', 'swimlanes / parallel stacks of equal length'],
    ['BAND', 'band placement and declared span within the grid'],
    ['TIER', 'collapse cascade is monotone across the container tiers'],
    ['CHIP', 'filter referential integrity (both directions) + chip arity'],
    ['ORDER', 'no duplicate effective `order` among siblings'],
    ['CSS', 'the mirrored breakpoints match the container queries in index.html'],
  ];
  console.log('\n  ── CHECKS ─────────────────────────────────────────────────────');
  for (const [id, name] of CHECKS) {
    const fails = findings.filter(f => f.check === id && f.sev === 'fail');
    const infos = findings.filter(f => f.check === id && f.sev === 'info');
    console.log(`\n  ${id}  ${name}`);
    if (!fails.length) console.log(`    [PASS] holds everywhere it applies`);
    for (const f of fails) console.log(`    [FAIL] ${f.where}: ${f.detail}`);
    for (const f of infos) console.log(`    [INFO] ${f.where}: ${f.detail}`);
  }

  const failed = findings.filter(f => f.sev === 'fail').length;
  const advisories = findings.filter(f => f.sev === 'info').length;
  const censusFail = sc.ok ? 0 : 1;
  console.log('\n══════════════════════════════════════════════════════════════');
  // ZERO ASSERTIONS IS RED, NEVER GREEN — the same rule validate's verdict holds.
  // `failed === 0` is a VACUOUS truth when nothing was asserted, and a guardrail
  // that measured nothing has no business printing a pass.
  if (asserted === 0) {
    console.log(`FAIL — 0 assertions ran across ${pages.length} page(s). A gate that asserted NOTHING is ` +
      `not a pass: either no page was read, or no grid carried a track model.\n`);
    process.exit(1);
  }
  const adv = advisories ? ` (${advisories} advisory note(s) — [INFO], non-failing)` : '';
  if (failed + censusFail === 0) {
    console.log(`ALL PASS — ${asserted} assertions across ${pages.length} page(s) × ${TIERS.length} container tiers, ` +
      `no browser${adv}.\n`);
    process.exit(0);
  }
  console.log(`FAIL — ${failed} failing check(s)${censusFail ? ' + a stale/divergent census' : ''} ` +
    `out of ${asserted} assertions. See the [FAIL] lines above${adv}.\n`);
  process.exit(1);
}

// Run ONLY when invoked as the gate. Imported (by the agreement test in
// tools/test-guards.mjs, which asserts this placement model still matches the
// engine's) the module must expose its functions without running a gate or
// calling process.exit.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();

export { widthAtTier, isBandAtTier, isBandClass, place, tracksFor,
  orderedChildren, slotsOf, effectiveCols, DEFAULT_SECTION_COLUMNS };
