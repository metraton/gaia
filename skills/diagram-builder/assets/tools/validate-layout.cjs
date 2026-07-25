// ─────────────────────────────────────────────────────────────────────────
// validate-layout.cjs — the LAYOUT GUARDRAIL for a uniform-cell diagram deck.
// @version 2.0.0  (part of the diagram-builder skill; keep the engine generation
//                  in sync with engine/engine.js + engine/build-data.mjs)
//
// This is the hard gate that proves the spreadsheet-style grid still "adds up"
// after any change to data/ or the engine/CSS. It is GENERIC (domain-agnostic):
// it discovers the pages from the rendered deck and asserts each invariant
// against the REAL rendered geometry (getBoundingClientRect), not against the
// data — a CSS or data edit cannot silently break the layout without failing
// here.
//
// FLOW:  edit data/pages/*.yaml  →  npm run build  →  npm run validate  →  npm run verify
//   Each step is explicit and single-purpose. `validate` is DECOUPLED from
//   `build`: it does NOT regenerate data — it renders and asserts the EXISTING
//   data/data.generated.js. So you must `npm run build` first (build generates,
//   validate only validates). This keeps `validate` PURE-READ (no file writes to
//   the project, no child build process) — a genuinely read-only guardrail.
//
// NO FALSE GREEN. Three structural rules make a green verdict mean something:
//   • A run that measured NOTHING is RED, never green. An undeclared page `form`
//     used to match no invariant row at all, so the page reported `ALL PASS —
//     0 checks` with exit 0 (invariant A + the total===0 gate in reportVerdict).
//   • A page is joined to its manifest entry BY ID (`data-page-id`), never by
//     index. Position is not identity: a positional join reports a page under a
//     neighbour's name AND form, and silently empties the authored-span lookup.
//   • AUTHORED == RENDERED is itself asserted (invariant Z + the STATIC census
//     pre-flight). Geometry checks cannot see content that is MISSING, and a
//     `validate` decoupled from `build` cannot see that it is reading stale data.
//
// WHAT IT DOES
//   0. PRE-FLIGHT, before any browser: re-parses data/*.yaml with js-yaml and
//      censuses it against data/data.generated.js. A mismatch means the generated
//      data is STALE — it says "run build" and exits 1 (see staticCensus).
//   1. Reads the EXISTING data/data.generated.js (built by the prior `npm run
//      build` step) — validate does not regenerate it, so run build first.
//   2. Launches headless Chromium and renders EVERY page at ONE viewport width
//      (2560, the widest tier — see WIDTHS) — the 3→2→1 collapse cascade itself
//      is now proven arithmetically by tools/check-layout.mjs (TIER), not re-
//      rendered here at multiple widths.
//   3. For every page it renders 3 TIMES with a real reload (F5) and ASSERTS
//      the geometry is identical across passes (determinism).
//   4. Measures the real geometry of every leaf box, section zone, and header,
//      and ASSERTS each layout invariant.
//   5. Writes a FULL-PAGE screenshot per (page,width) to a SYSTEM TEMP DIR
//      (os.tmpdir(); override with DIAGRAM_SHOTS_DIR) — never into the project.
//   6. Prints a per-(page,width) PASS/FAIL table and exits non-zero on any fail.
//
// INVARIANTS ASSERTED (each measured from the live render):
//   A  form is declared     — the page's `form` is one of FORMS. Synthetic and
//                            fail-closed: an undeclared form matches NO row in the
//                            form-scoped table, so the render measured NOTHING and
//                            printed `ALL PASS — 0 checks`, exit 0. A is returned
//                            INSTEAD of that empty set (see runInvariants); the
//                            `total === 0` gate in reportVerdict is the second,
//                            independent layer of the same defence.
//   Z  CENSUS (authored ==  — the deck RENDERED everything the data AUTHORS:
//      rendered)             `.act` count vs `__DOC__.pages.length`, and per page
//                            sections / boxes / separators / rails / half-slots /
//                            leaf-grids vs the authored walk (which mirrors
//                            engine.js buildGrid). Every other invariant measures
//                            geometry that IS on screen, so none of them can see
//                            content that is MISSING — drop a page or a section and
//                            the remaining geometry is still perfectly valid. Sound
//                            only because the page join is by id, not by index.
//   D  DETERMINISM         — N reloads produce byte-identical geometry (cell
//                            sizes, per-grid column counts, section widths, wrap
//                            structure). Catches an F5 column/wrap flip.
//   R  scrollbar-robust    — at wide tiers, shaving a scrollbar's width off the
//                            available width does NOT change the column/wrap
//                            structure (not parked on a wrap knife-edge).
//   T  capture not truncated — the full-page viewport is grown until .canvas no
//                            longer scrolls internally, so the -full.png shows
//                            the WHOLE deck (guards the evidence on tall pages).
//   U  cells fill (equal)   — within each leaf grid the single-cell .box cells
//                            are of EQUAL width (equal fr tracks), and every SLOT
//                            is EXACTLY --cell-h tall. SLOT, not component: the two
//                            height treatments legitimately differ from it in
//                            opposite directions — `rowspan` is a MULTIPLE of the
//                            slot, `half` a FRACTION (two components share one) — so
//                            both are excluded from the component-height set and the
//                            `.half-slot` wrapper is asserted at --cell-h directly.
//                            A THIRD family joins them: the SEPARATOR ROW. A row whose
//                            only occupants are HORIZONTAL separators is --sep-row-h,
//                            not --cell-h (one pixel of ink no longer costs a 130px
//                            cell), so U asserts every leaf grid's resolved row TRACK
//                            against what its occupants entitle it to — thin exactly
//                            where the ink is thin, both directions. That track pass
//                            also finally covers `.sep`/`.rail` height, which the
//                            .box-only height set never saw.
//                            The old fixed-232 width rule is gone: cells now STRETCH
//                            to fill, so width varies by section but is equal within
//                            a grid.
//   L  cells fill width      — RETIRED, superseded by RECT/HOLE (npm run check).
//      (retired -> RECT/HOLE) "Every row spans the grid edge to edge" is the
//                            rectangle-closure identity Σ(spanCols × rowspanRows)
//                            === tracks × rowCount, proved on the DATA rather than
//                            a pixel measurement, and a gap is named by coordinate
//                            instead of inferred from a right-edge delta.
//   C  description clamp     — no .box clips its content (desc clamped to 3
//                            lines keeps every box at CELL_H; clipped == 0).
//   O  no h-overflow         — canvas horizontal overflow == 0 at the stacked
//                            tiers (min/medium/large); tolerated only at the
//                            widest side-by-side tiers.
//   F  collapse cascade 3→2→1 — RETIRED, superseded by TIER (npm run check).
//      (retired -> TIER)      The …→2→1 cascade is a container query, so the
//                            track count per tier is a pure function of (authored
//                            columns, container width) — proved arithmetically at
//                            all five widths this sweep used to render, instead of
//                            re-rendering a multiplication table in a browser.
//   S  inline fit / band spans block (ALL tiers) — INLINE sections (span:1) hug
//                            their own grid (fit-content); BAND sections (span ==
//                            columns) span the BLOCK width at EVERY tier
//                            (mutually equal, ≥ the widest inline section). A
//                            band that shrinks to its single cell on collapse
//                            FAILS — a band must span the block at every width.
//   B  centered block        — at the widest tiers leftPad ≈ rightPad.
//   H  header within section  — no section header/subtitle overflows its section.
//   E  no empty grid column   — RETIRED, superseded by TRACK (npm run check). A
//      (retired -> TRACK)     dead track is a track no slot's (column, span) ever
//                            covers, a set operation on the authored data rather
//                            than a rendered measurement — and it guards the
//                            engine's grow-with-content clamp more directly.
//   P  no orphan cell         — RETIRED, superseded by ROW (npm run check). "A
//      (retired -> ROW)       lone cell on its own row while a sibling row holds
//                            2+" is a statement about PLACEMENT, and the placement
//                            is derivable: check-layout.mjs simulates CSS sparse
//                            auto-placement and counts row starts, carrying over
//                            P's exact scope (grid-dense forms, >1 track, rowspan-
//                            touched rows exempt).
//   V  verticality signal     — at the WIDEST tier the deck must earn its canvas:
//                            the ROOT places top-level sections side by side
//                            (rootRowMax ≥ 2, not one band per row) AND some leaf
//                            grid is multi-column. Prints a soft [SIGNAL] when
//                            horizontal density is low even if the floors pass.
//                            Catches a deck that collapses to a narrow centered
//                            single column wasting the horizontal space.
//   G  no balloon / overflow — (a) a LEAF component (box/sep/rail) sitting
//                            DIRECTLY in a compound ROW must be content-sized
//                            (flex-grow 0) — never grow to an equal slice (a lone
//                            box or a divider line ballooning); a sep/rail is also
//                            checked to stay thin in absolute width. (b) a nested
//                            section stacked in a columns:1 compound must keep its
//                            CONTENT height (flex-grow 0, scrollHeight<=clientH) —
//                            never be given a divided share shorter than its
//                            content that spills onto the next section. This is
//                            the hard guard for the compound-flex exemptions
//                            (sep/rail + box exemption and the sec-c1 reset): it
//                            reads the CAUSE on the live render, so it goes red
//                            BEFORE a spill grows large enough for X to see an
//                            actual box overlap.
//   Y  band fill             — a full-width BAND must FILL its width: its content
//                            spans the band edge-to-edge so each side gap is the
//                            zone padding only (small AND equal) — not a big dead
//                            margin from centering narrower content (the old hero
//                            "extra hueco" defect, L274/R274 each side). Asserted
//                            at width ≥ 1200.
//   N  word-fit             — (flow+dashboard) every leaf cell is at least as
//                            wide as the longest indivisible TOKEN of its title,
//                            measured on the live render; below that the title
//                            wraps mid-word under .box overflow-wrap:break-word
//                            (the S4 "Orquestac/ión" defect). Independent of M:
//                            a 136px cell clears M's 120px floor yet is narrower
//                            than a 12-char monospace title, so N catches what M
//                            misses. All tiers. A `vertical`-treatment title is
//                            EXEMPT (its title runs down the block axis, so
//                            horizontal token width is not the fit constraint).
//   K  filter integrity     — RETIRED, superseded by CHIP (npm run check). A
//      (retired -> CHIP)     chip/component join is a RELATION, not geometry, so
//                            it needs no render: check-layout.mjs closes the join
//                            in both directions AND adds ARITY (a one-member chip
//                            still blacks out the deck to spotlight a single box),
//                            a half this render-based row could never see.
// ─────────────────────────────────────────────────────────────────────────
// PLAYWRIGHT IS RESOLVED LAZILY, AND ITS ABSENCE IS NOT A FAILURE.
// This used to be a top-level `require('playwright')`, which made the optional
// gate impossible to even LOAD where the browser is not installed — the process
// died with MODULE_NOT_FOUND before printing anything, and a harness that only
// wanted the pure decision logic (the invariant table, the verdict, the census)
// dragged Chromium's whole dependency in with it. Gaia is installed in places
// where no browser exists; there, `npm run validate` must SKIP and exit 0, and
// the mandatory gate is `npm run check` (tools/check-layout.mjs), which needs
// nothing but js-yaml. See the degradation block in main().
function loadChromium() {
  try { return require('playwright').chromium; }
  catch (e) { return null; }
}
const path = require('path');
const http = require('http');
const fs = require('fs');
const os = require('os');

const ROOT = path.join(__dirname, '..');
const OUT = process.env.DIAGRAM_SHOTS_DIR || path.join(os.tmpdir(), 'diagram-deck-layout');
// --cell-h is the FIXED row height (must match the design token in index.html).
// --cell-w is no longer a track width in the fill model (cells stretch to equal
// fr widths) — kept only as a documented reference for the readability step-down.
const CELL_W = 232, CELL_H = 130;
// --sep-row-h — the ONE row height that is not CELL_H (must match the design
// token in index.html). A row whose only occupants are HORIZONTAL separators is
// reduced to it: a 1px rule no longer costs a 130px cell. This is the third
// height family invariant U recognises (see the U/height row below).
const SEP_ROW_H = 40;
// ── ONE WIDTH, THE WIDEST. ────────────────────────────────────────────────
// This was a FIVE-width sweep (600/900/1200/1920/2560) whose job was to prove the
// …→2→1 collapse cascade while that cascade was being BUILT. It is stable now, and
// — more to the point — the cascade is a CONTAINER QUERY: the cuts at 640/1000/1440
// depend on nothing but the stage container's width, so the track count is a PURE
// FUNCTION of (authored columns, container width). Arithmetic proves it exactly, at
// all five of those widths, in tools/check-layout.mjs (`npm run check`, the
// MANDATORY gate). Re-measuring a multiplication table in a browser five times over
// is not evidence, it is ceremony.
// What is LEFT here is what only pixels can answer, and the widest tier is where it
// is answerable: the side-by-side regime (>1440) is the only one that exercises
// centering (B), band fill (Y), span-weighted compound widths (Q), the compound
// flex row (G) and scrollbar robustness (R) at all — at the stacked tiers those
// checks are either inapplicable or vacuous.
const WIDTHS = { ultra: 2560 };
const WIDE_TIERS = new Set(['huge', 'ultra']);   // >1440: side-by-side + centered; h-overflow tolerated
// Reloads per (page,width) for the determinism check. Lowered 5 -> 3: three
// independent renders still catch a nondeterministic wrap/column flip (the failure
// mode is a coin flip, not a rare tail), and the run is now one width instead of
// five, so the evidence-per-second is far better spent here.
const PASSES = 3;
const CENTER_TOL = 10;   // px tolerance for leftPad ≈ rightPad
const FIT_TOL = 48;      // px a zone may exceed its grid (padding+border+gap)
const SB_GUARD = 17;     // widest classic vertical scrollbar to be robust against
const MAX_FULL_H = 12000; // hard cap on the grown full-page viewport height (px)
const FULL_MARGIN = 160;  // px slack below the last content row in the full-page capture
const CELLW_TOL = 2;      // px spread allowed among a grid's equal fr cells (U)
const FILL_TOL = 6;       // px a row's right/left edge may miss the grid edge (L)
const MIN_LEGIBLE = 120;  // px — the readable floor for a leaf cell (M). Kept in
                          // sync with --cell-min-w in index.html. Below this a
                          // short title can only show ~1 char per line.
const LEGIBLE_TOL = 6;    // px sub-pixel slack under MIN_LEGIBLE before M fires
const SPAN_TOL_PCT = 15;  // % a compound section child's rendered width may
                          // deviate from its AUTHORED-span share (Q). Absorbs the
                          // min-content floor (~3% on the reference 2:1 split); a
                          // regression to equal shares is 25%+ off and so fails.
const WORDFIT_TOL = 1.5;  // px sub-pixel slack between the canvas-measured token
                          // width and the cell's available width (N). Small: a
                          // token that needs >~1.5px more than its cell WILL wrap
                          // mid-word under .box overflow-wrap:break-word.

// The equality used by the geometry checks (deep-equal via JSON).
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ─────────────────────────────────────────────────────────────────────────
// FORM-SCOPED INVARIANTS (flat lookup, not a case tree).
//
// A PAGE declares its FORM (page YAML `form:`; default `dashboard`). Each
// invariant is a declarative row that names WHICH FORMS it applies to, its
// CLASS (integrity vs design), its SEVERITY (`dura` = a failure, `consejo` = an
// advisory that never fails the build), the TIERS it runs at (`when`), an
// optional RETIREMENT clause (`superseded` → the id that replaced it; a retired
// row is skipped, never evaluated), and a pure `check(m, ctx) → {ok, detail}`.
// The main loop just filters this table by (form, tier, not-retired) and runs
// each check — there is NO branching tree of `if (tier===…)/(w>=…)` anymore.
//
//   INTEGRITY (D R T C O F S B H) — the layout "adds up": determinism, capture,
//     no clipping/overflow, the collapse cascade, band/inline fit, centering,
//     headers contained. TRUE FOR EVERY FORM, always `dura`.
//   DESIGN (E P U L Y + M + N) — how the deck reads: no dead track, no orphan
//     cell, equal/uniform cells, filled bands, the readable-cell floor (M), and
//     the word-fit floor (N — a title token never wider than its cell). SCOPED to
//     the forms where the concern is real.
//   V (verticality) — now a `consejo` (was `dura`): a long single row is a
//     LEGITIMATE shape for a `timeline`, so V never applies to it and, where it
//     does apply (the grid-dense forms), it only ADVISES, never fails.
// ─────────────────────────────────────────────────────────────────────────
const FORMS = ['dashboard', 'timeline', 'flow', 'comparison', 'mindmap', 'planner'];
const DEFAULT_FORM = 'dashboard';
const ALL_FORMS = new Set(FORMS);
// GRIDDED — every form whose cells sit in a real grid of rows AND columns (so a
// dead track, a lopsided row, or an illegibly narrow cell is a defect). Excludes
// `timeline`, whose content is legitimately ONE long row.
const GRIDDED = new Set(['dashboard', 'comparison', 'flow', 'mindmap', 'planner']);
// GRID_DENSE — the forms that should EARN a wide canvas by composing sections
// side by side and grouping cells (so an orphan cell or a collapse-to-one-column
// stack is worth flagging). A `timeline`/`flow`/`mindmap` may legitimately be
// sparse or linear, so P/V do not judge them.
const GRID_DENSE = new Set(['dashboard', 'comparison', 'planner']);
// WORDFIT — the narrative forms whose cells carry a real, human-language TITLE
// that must not fracture mid-word. Scoped to flow + dashboard (the forms in this
// deck); a token wider than its cell breaks under .box overflow-wrap:break-word,
// a defect the M floor can miss (a 136px cell clears M's 120px floor yet is still
// narrower than a 12-char monospace title). See invariant N.
const WORDFIT = new Set(['dashboard', 'flow']);

const INVARIANTS = [
  // ── INTEGRITY — all forms, dura ──────────────────────────────────────────
  { id: 'D', name: `determinism (${PASSES} reloads)`, cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m, c) => ({ ok: c.deterministic,
      detail: c.deterministic ? `identical signature across ${c.PASSES} reloads`
        : `DIVERGED — ${c.uniqueSigs.length} distinct signatures:\n        ` +
          c.uniqueSigs.map((s, i) => `sig#${i + 1} (passes ${c.sigs.map((x, j) => x === s ? j + 1 : null).filter(x => x).join(',')}): ${s}`).join('\n        ') }) },
  { id: 'R', name: `scrollbar-robust (-${SB_GUARD}px)`, cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.WIDE, superseded: null,
    check: (m, c) => ({ ok: c.robustOk, detail: c.robustDetail }) },
  { id: 'T', name: 'full-page capture not truncated', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m, c) => ({ ok: c.captureOk, detail: c.captureDetail }) },
  { id: 'C', name: 'no box clipping', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => ({ ok: m.clipped === 0, detail: `clipped=${m.clipped}` }) },
  { id: 'O', name: 'h-overflow', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m, c) => c.WIDE ? ({ ok: true, detail: `overflowX=${m.overflowX} (tolerated@wide)` })
                            : ({ ok: m.overflowX === 0, detail: `overflowX=${m.overflowX}` }) },
  // F (both rows) — RETIRED into arithmetic. The …→2→1 cascade is a container
  // query, so the track count per tier is a pure function of (authored columns,
  // container width) — `tracksFor` in tools/check-layout.mjs derives it for every
  // grid at every one of the five widths this sweep used to render, and asserts the
  // cascade is monotone. Rendering five viewports to re-confirm that arithmetic is
  // the definition of a redundant check, and it was the entire reason this gate
  // needed a browser at more than one width.
  { id: 'F', name: '1-col endpoint at min', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.tier === 'min', superseded: 'TIER (npm run check)',
    check: (m) => { const bad = m.leafGrids.filter(g => g.tracks !== 1); const oneCol = bad.length === 0 && m.maxRowCount === 1;
      return { ok: oneCol, detail: bad.length ? `not-1-track: ${bad.map(g => `${g.zone}:auth${g.authored}->${g.tracks}`).join(', ')}`
        : m.maxRowCount !== 1 ? `maxRowCount=${m.maxRowCount} (expected 1 — page not a single column)`
        : `all ${m.leafGrids.length} leaf grids => 1 track; single vertical column (maxRowCount=1)` }; } },
  { id: 'F', name: '2-col intermediate at medium', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.tier === 'medium', superseded: 'TIER (npm run check)',
    check: (m) => { const bad = m.leafGrids.filter(g => g.authored >= 2 ? g.tracks !== 2 : g.tracks !== 1);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:auth${g.authored}->${g.tracks}`).join(', ')
        : `all leaf grids: >=2col=>2 tracks, 1col=>1 (${m.leafGrids.length} grids)` }; } },
  { id: 'S', name: 'inline fit / band spans block (all tiers)', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => {
      const inlineZones = m.topZones.filter(z => !z.band);
      const bandZones = m.topZones.filter(z => z.band);
      const maxInlineW = Math.max(0, ...inlineZones.map(z => z.w));
      const problems = [];
      for (const z of inlineZones) if (z.w - z.gridW > FIT_TOL) problems.push(`${z.zone}:stretched(zone${z.w}>grid${z.gridW})`);
      if (bandZones.length) {
        const bw = bandZones.map(z => z.w);
        if (Math.max(...bw) - Math.min(...bw) > FIT_TOL) problems.push(`bands-unequal(${bandZones.map(z => `${z.zone}${z.w}`).join(',')})`);
        for (const z of bandZones) if (z.w < maxInlineW - FIT_TOL) problems.push(`${z.zone}:band-shrunk-to-content(zone${z.w}<block${maxInlineW})`);
      }
      return { ok: problems.length === 0, detail: problems.length ? problems.join(', ')
        : m.topZones.map(z => `${z.zone}${z.band ? '[band]' : ''}(${z.w}/${z.gridW})`).join(' ') }; } },
  { id: 'B', name: 'centered block', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.WIDE, superseded: null,
    check: (m) => ({ ok: Math.abs(m.leftPad - m.rightPad) <= CENTER_TOL, detail: `leftPad=${m.leftPad} rightPad=${m.rightPad}` }) },
  { id: 'H', name: 'header within section', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => { const bad = m.topZones.filter(z => z.headerOverflow > 1);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(z => `${z.zone}:+${z.headerOverflow}px`).join(', ') : 'all headers contained' }; } },
  { id: 'X', name: 'no sibling-section collision', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => ({ ok: m.collisions.length === 0,
      detail: m.collisions.length ? `overlapping sections: ${m.collisions.join(', ')}` : 'no sibling sections overlap' }) },
  { id: 'G', name: 'no compound-leaf balloon / no stacked-section content overflow', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => { const bad = [...m.balloons, ...m.stackOverflow];
      return { ok: bad.length === 0, detail: bad.length ? `legibility/overflow: ${bad.join(', ')}`
        : 'compound leaves stay content-sized; stacked sections keep their content height' }; } },

  // ── DESIGN — scoped, dura (except V) ─────────────────────────────────────
  { id: 'U', name: 'cells equal width (per grid)', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => { const bad = m.leafGrids.filter(g => g.cellWSpread > CELLW_TOL);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:cells-differ(spread ${g.cellWSpread}px)`).join(', ')
        : `every leaf grid's cells are equal width (${m.leafGrids.length} grids)` }; } },
  // U (height) — asserts the SLOT height, not the component's.
  // A cell is a SLOT of CELL_H. THREE families legitimately make the height of a
  // rendered thing differ from that one number, and U recognises each EXPLICITLY —
  // with its own expected height — rather than exempting it:
  //   1. `rowspan` — the COMPONENT is a MULTIPLE of the slot. Excluded from the
  //      component-height set in measure(); the slot it covers is still CELL_H.
  //   2. `half`    — the COMPONENT is a FRACTION of the slot (two share one), so the
  //      subject moved from the component to the SLOT: every `.half-slot` (the
  //      wrapper that actually occupies the grid cell) must be exactly CELL_H and
  //      hold exactly 2 occupants. That is what proves `half` DIVIDED a slot rather
  //      than shrinking one and leaving a hole.
  //   3. THE SEPARATOR ROW — the ROW itself is thinner. A horizontal separator draws
  //      one pixel of ink and used to be charged a full CELL_H; a row whose ONLY
  //      occupants are horizontal separators is now SEP_ROW_H. The separator is still
  //      a cell (principle 1 is untouched), so this family is asserted on the TRACK:
  //      for every leaf grid, every resolved row track must equal SEP_ROW_H when that
  //      row's only occupants are horizontal separators and CELL_H otherwise. Both
  //      directions matter — a separator SHARING its row with boxes must NOT thin it
  //      (or the boxes clip), and an empty row (a hole, owned by RECT/HOLE in `npm
  //      run check`) must not be mistaken for a separator row.
  //      A VERTICAL separator is not in this family: its ink IS the row height, so its
  //      row stays CELL_H — measure() counts only `.sep:not(.sep-v)` as thin ink.
  //      The same track measurement finally covers the height of the two leaf types
  //      the `.box`-only height set never saw: a `.sep`/`.rail` that overflows the
  //      row(s) it is entitled to is reported here (the --zone-min-h-vs---cell-h
  //      contradiction was a silent 50px spill with no invariant measuring it).
  // APPLICABILITY: none — this holds for every form. Only the measured subjects grew.
  { id: 'U', name: 'uniform slot height', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => {
      const slots = m.halfSlots || [];
      const badSlots = slots.filter(s => Math.abs(s.h - CELL_H) !== 0 || s.n !== 2);
      const cellsOk = eq(m.heights, [CELL_H]);
      // family 3 — the row TRACKS of every leaf grid.
      const badTracks = [], thinRows = [], spills = [];
      for (const g of m.rowTracks || []) {
        g.tracks.forEach((h, i) => {
          const row = g.rows[i];
          const thin = row.n > 0 && row.n === row.sepH;
          const expect = thin ? SEP_ROW_H : CELL_H;
          if (h !== expect) {
            badTracks.push(`${g.zone}:row${i} track=${h}px expect ${expect}px (` +
              (thin ? 'separator-only row — the thin track was not applied'
                : `${row.n} occupant(s), ${row.sepH} separator(s) — a row that carries a box must stay ${CELL_H}px`) + ')');
          } else if (thin) thinRows.push(`${g.zone}:row${i}`);
        });
        for (const o of g.overflow) spills.push(`${g.zone}:${o.cls} overflows its row by ${o.over}px`);
      }
      const ok = cellsOk && badSlots.length === 0 && badTracks.length === 0 && spills.length === 0;
      const parts = [`cell heights=${JSON.stringify(m.heights)} expect [${CELL_H}]`];
      if (slots.length) parts.push(`${slots.length} half-slot(s) @ ${[...new Set(slots.map(s => s.h))].join('/')}px expect ${CELL_H}`);
      const nTracks = (m.rowTracks || []).reduce((n, g) => n + g.tracks.length, 0);
      parts.push(`${nTracks} row track(s) across ${(m.rowTracks || []).length} leaf grid(s): ` +
        `${thinRows.length} separator-only @ ${SEP_ROW_H}px${thinRows.length ? ` (${thinRows.join(', ')})` : ''}, ` +
        `${nTracks - thinRows.length} @ ${CELL_H}px; no .sep/.rail overflows its row`);
      if (badSlots.length) parts.push(`BAD: ${badSlots.map(s => `${s.zone}:h=${s.h}(expect ${CELL_H}),occupants=${s.n}(expect 2)`).join(', ')}`);
      if (badTracks.length) parts.push(`BAD TRACKS: ${badTracks.join(', ')}`);
      if (spills.length) parts.push(`BAD OVERFLOW: ${spills.join(', ')}`);
      return { ok, detail: parts.join(' · ') }; } },
  // L — RETIRED into arithmetic. "Every row spans the grid edge to edge" is the
  // rectangle-closure identity Σ(spanCols × rowspanRows) === tracks × rowCount,
  // which check-layout.mjs (RECT/HOLE) proves on the DATA and, unlike a pixel
  // measurement, quantifies: a gap makes the sum short by exactly its own area, and
  // the hole is named by coordinate rather than inferred from a right-edge delta.
  { id: 'L', name: 'cells fill width (no right gap)', cls: 'design', sev: 'dura', forms: GRIDDED,
    when: (c) => c.w >= 1200, superseded: 'RECT/HOLE (npm run check)',
    check: (m) => { const bad = m.leafGrids.filter(g => g.rowRightGapMax > FILL_TOL || g.leftGapMax > FILL_TOL);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:gap(right ${g.rowRightGapMax}px,left ${g.leftGapMax}px)`).join(', ')
        : `every leaf grid fills its width edge-to-edge (${m.leafGrids.length} grids)` }; } },
  // E — RETIRED into arithmetic. A dead track is a track no slot's (column, span)
  // ever covers, which is a set operation on the authored data, not a rendered
  // measurement (TRACK in check-layout.mjs). It also guards the same thing more
  // directly: the engine's grow-with-content clamp, which is arithmetic itself.
  { id: 'E', name: 'no empty grid column', cls: 'design', sev: 'dura', forms: GRIDDED,
    when: () => true, superseded: 'TRACK (npm run check)',
    check: (m) => { const bad = m.leafGrids.filter(g => g.emptyCols > 0);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:${g.tracks}tracks-${g.emptyCols}empty`).join(', ')
        : `all ${m.leafGrids.length} leaf grids fill every declared track` }; } },
  // P — RETIRED into arithmetic. "A lone cell on its own row while a sibling row
  // holds 2+" is a statement about the PLACEMENT, and the placement is derivable:
  // check-layout.mjs simulates CSS sparse auto-placement and counts the starts per
  // row (ROW), carrying over P's exact scope — grid-dense forms, >1 track, and rows
  // a rowspan touches exempt.
  { id: 'P', name: 'no orphan cell', cls: 'design', sev: 'dura', forms: GRID_DENSE,
    when: (c) => c.w > 1000, superseded: 'ROW (npm run check)',
    check: (m) => { const bad = m.leafGrids.filter(g => g.orphan);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:a lone cell sits alone while siblings are grouped (${g.tracks}-col grid)`).join(', ')
        : 'every leaf grid groups its cells uniformly (no lone cell)' }; } },
  { id: 'M', name: 'cells legible (min readable width)', cls: 'design', sev: 'dura', forms: GRIDDED,
    when: () => true, superseded: null,
    check: (m) => {
      const grids = m.leafGrids.filter(g => g.minSingleW != null);
      const bad = grids.filter(g => g.minSingleW < MIN_LEGIBLE - LEGIBLE_TOL);
      const overall = grids.length ? Math.min(...grids.map(g => g.minSingleW)) : null;
      return { ok: bad.length === 0, detail: bad.length
        ? bad.map(g => `${g.zone}:cell ${g.minSingleW}px < ${MIN_LEGIBLE}px (illegible — grid should collapse columns first)`).join(', ')
        : `all ${grids.length} leaf grids keep cells >= ${MIN_LEGIBLE}px (min observed ${overall}px)` }; } },
  // N — WORD-FIT. Complements M: M guards a fixed READABLE floor (120px); N
  // guards a CONTENT-RELATIVE floor — a leaf cell must be at least as wide as the
  // longest indivisible TOKEN of its own title, or the title wraps mid-word under
  // .box overflow-wrap:break-word. The two are independent: a 136px cell clears
  // M's 120px floor yet is narrower than a 12-char monospace title ("Orquestación"
  // ~137px), so N fails exactly where M passes (the S4 defect). Scoped to WORDFIT
  // (flow + dashboard) — the forms whose cells carry human-language titles.
  // APPLICABILITY CLAUSE (added with the `treatment` axis): a `vertical` leaf is
  // EXEMPT. N's premise is that a title flows along the INLINE (horizontal) axis, so
  // a token wider than the cell wraps mid-word. `treatment: [vertical]` rotates the
  // title onto the BLOCK axis (writing-mode:vertical-rl), where the fit constraint is
  // the cell's HEIGHT, not its width — the measured horizontal width of a rotated
  // title is meaningless and would fail every vertical label. The other new
  // treatments need no clause: `half` keeps the cell's FULL width (only the height is
  // divided), so N applies to it unchanged and still catches a fractured title;
  // `centered` only moves the text within the same width; `plain`/`envelope` are
  // section-level and carry no title token; `ext` only changes border style.
  { id: 'N', name: 'word-fit (title token fits its cell)', cls: 'design', sev: 'dura', forms: WORDFIT,
    when: () => true, superseded: null,
    check: (m) => { const applicable = (m.wordFit || []).filter(b => !b.vertical);
      const exempt = (m.wordFit || []).length - applicable.length;
      const bad = applicable.filter(b => b.wordW > b.availW + WORDFIT_TOL);
      return { ok: bad.length === 0, detail: bad.length
        ? bad.map(b => `${b.zone}>${b.k}:"${b.word}" needs ${b.wordW}px > cell ${b.availW}px (title wraps mid-word; below the longest token — M's ${MIN_LEGIBLE}px floor can pass here)`).join(', ')
        : `all ${applicable.length} leaf titles fit their cell (longest token <= cell width)${exempt ? `; ${exempt} vertical-treatment title(s) exempt (rotated onto the block axis)` : ''}` }; } },
  // K — FILTER REFERENTIAL INTEGRITY (the chip/component join).
  // A chip and the components that claim it live in TWO separate places in the YAML,
  // joined only by a string key, and nothing checked that the join closed. A typo in
  // a key was therefore SILENT in both directions: an orphan chip rendered and dimmed
  // the entire canvas because nothing matched it, and a dangling reference on a
  // component simply never lit. The strict schema (build-data.mjs) now guarantees a
  // chip's SHAPE; this asserts the RELATION, on the real render, from both sides.
  // The reset key 'all' is exempt — it is the engine's reserved "show everything"
  // chip and legitimately has no members.
  // APPLICABILITY: every form. A relation is data, not geometry: a chip that traces
  // nothing is equally broken in a timeline and in a dashboard.
  // RETIRED into arithmetic, and STRENGTHENED there. A relation is data, never
  // geometry: joining chips to members needs no render at all, and asserting it here
  // meant the check only existed where a browser did. check-layout.mjs (CHIP) closes
  // the join in both directions AND adds the half this row could never see — ARITY.
  // A chip with exactly ONE member closes the join and is still broken: a chip is a
  // relation, one member is not a relation, and because an active chip dims
  // everything it does not name, a one-member chip blacks the deck out to spotlight
  // a single box.
  { id: 'K', name: 'filter referential integrity', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: 'CHIP (npm run check)',
    check: (m) => { const f = m.filterRefs || { declared: [], referenced: [], orphanChips: [], danglingRefs: [] };
      const problems = [
        ...f.orphanChips.map(k => `chip "${k}" is declared but NO component references it (it would dim the whole canvas)`),
        ...f.danglingRefs.map(k => `component filter key "${k}" is referenced but NO chip declares it (it can never light)`)];
      return { ok: problems.length === 0, detail: problems.length ? problems.join(', ')
        : `${f.declared.length} chip(s) and ${f.referenced.length} referenced key(s) match exactly${f.declared.length ? ` (${f.declared.join(', ')})` : ''}` }; } },
  // Z — CENSUS: AUTHORED == RENDERED.
  // Every other invariant asserts something about geometry that IS on screen, so
  // none of them can see content that is MISSING: drop a page, a section or a
  // component at render time and what remains is still perfectly valid geometry —
  // the table stays green while the deck silently lost content. Z closes that by
  // counting both sides and comparing: pages (`.act` vs `__DOC__.pages`), and per
  // page the sections, boxes, separators, rails, half-slots and leaf grids the data
  // authors against the ones the render produced (the authored walk lives in
  // measure() and mirrors engine.js's buildGrid).
  // APPLICABILITY: every form, `dura`. A census is arithmetic on the data, not a
  // judgement about shape — a page that lost a section is equally broken as a
  // timeline and as a dashboard.
  // NOTE ON ORDERING: this invariant is only sound BECAUSE the act/page join is by
  // `data-page-id` and not by index. Under the old positional join a shifted page
  // was censused against ANOTHER page's authored tree, which would report
  // mismatches on the wrong page (or, worse, a coincidental match).
  { id: 'Z', name: 'census (authored == rendered)', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => { const c = m.census || { diffs: ['census not measured'], authored: {}, rendered: {} };
      return { ok: c.diffs.length === 0, detail: c.diffs.length
        ? `AUTHORED CONTENT IS MISSING FROM THE RENDER — ${c.diffs.join('; ')} ` +
          `(run \`npm run build\` if data/data.generated.js is stale; otherwise the engine dropped authored content)`
        : `pages ${c.nActs}/${c.nAuthoredPages}; page "${c.pageId}" sections=${c.rendered.zones} boxes=${c.rendered.boxes} ` +
          `seps=${c.rendered.seps} rails=${c.rendered.rails} half-slots=${c.rendered.halfSlots} leaf-grids=${c.rendered.leafGrids} — all match the authored census` }; } },
  { id: 'Y', name: 'band content fills band (no dead margin)', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.w >= 1200, superseded: null,
    check: (m) => { const FILL_MARGIN_TOL = 48, SYM_TOL = 16; const bands = m.topZones.filter(z => z.band);
      const bad = bands.filter(z => z.leftGap > FILL_MARGIN_TOL || z.rightGap > FILL_MARGIN_TOL || Math.abs(z.leftGap - z.rightGap) > SYM_TOL);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(z => `${z.zone}:not-filled(L${z.leftGap}/R${z.rightGap})`).join(', ')
        : bands.map(z => `${z.zone}(L${z.leftGap}/R${z.rightGap})`).join(' ') }; } },
  { id: 'Q', name: 'compound section widths follow authored span', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.w >= 1200, superseded: null,
    check: (m) => { const items = m.spanRatios || [];
      const bad = items.filter(it => it.errPct > SPAN_TOL_PCT);
      return { ok: bad.length === 0, detail: bad.length
        ? bad.map(it => `${it.grid}>${it.id}:span${it.span} width ${it.w}px vs expected ${it.expected}px (${it.errPct}% off, tol ${SPAN_TOL_PCT}% — span-weight not applied; a span:1 child likely inherited a parent band's --span)`).join(', ')
        : items.length ? `span-weighted compound widths proportional to authored span (${items.map(it => `${it.id}:s${it.span}@${it.w}px`).join(', ')})`
        : 'no span-weighted compound rows to check' }; } },
  { id: 'V', name: 'horizontal composition (verticality)', cls: 'design', sev: 'consejo', forms: GRID_DENSE,
    when: (c) => c.tier === 'ultra', superseded: null,
    check: (m) => { const multiCol = m.leafGrids.filter(g => g.tracks >= 2).length; const singleCol = m.leafGrids.filter(g => g.tracks === 1).length;
      const total = m.leafGrids.length || 1; const frac = multiCol / total; const ok = m.rootRowMax >= 2 && multiCol > 0;
      const why = m.rootRowMax < 2 ? `root stacks every section (rootRowMax=${m.rootRowMax}) — page.columns should be >=2 so sections sit side by side`
        : multiCol === 0 ? 'no leaf grid uses more than 1 column (nothing composed horizontally)' : '';
      const signal = ok && frac < 0.3 ? ' [SIGNAL: low horizontal density — consider condensing more sections into columns]' : '';
      return { ok, detail: ok ? `root side-by-side rows up to ${m.rootRowMax} wide; ${multiCol}/${total} leaf grids multi-column (${(frac * 100).toFixed(0)}%), ${singleCol} single-column${signal}` : why }; } },

  // ── RETIRED — kept as a record of a superseded invariant (never evaluated).
  // The fixed 232px-per-cell width rule was replaced by the fill model: cells
  // now STRETCH to equal fr widths (asserted by U + M), so a fixed pixel width
  // is no longer a truth to hold. The row demonstrates the retirement clause:
  // `superseded` points at the invariant that took over its job.
  { id: 'W', name: 'fixed 232px cell width', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: 'U', check: () => ({ ok: true, detail: '' }) },
];

// Evaluate the invariant table for one (form, tier) render. Returns the ordered
// list of {id, name, cls, sev, ok, detail} for the checks that APPLY — filtered
// by form membership, tier `when`, and not-retired. A flat filter+map, no tree.
//
// FAIL-CLOSED ON AN UNDECLARED FORM (invariant A — layer 1 of 2).
// The filter is a form-MEMBERSHIP match, so a `form` that is not in ALL_FORMS
// matches NOTHING — not even the `forms: ALL_FORMS` rows, since ALL_FORMS holds
// the six declared names and an invalid value is in none of them. That produced
// the worst possible outcome: `checks` came back EMPTY, `failed` stayed 0, and the
// report printed `ALL PASS — 0 checks` with exit 0. A single typo in `form:`
// therefore bought a GREEN VERDICT having measured nothing at all.
// So an undeclared form is now a SYNTHETIC HARD FAILURE that names the offending
// value and the valid set. It is returned INSTEAD of the (empty) table result, so
// the render reports exactly one check and that check is red.
// Layer 2 is the `total === 0` gate in the report below: two independent
// defences, because this one only covers the form axis while that one covers ANY
// path that ends with nothing measured.
function runInvariants(m, ctx) {
  if (!ALL_FORMS.has(ctx.form)) {
    return [{ id: 'A', name: 'page declares a valid form', cls: 'integrity', sev: 'dura', ok: false,
      detail: `undeclared page form "${ctx.form}" — NO invariant in the table applies to it, so this render measured NOTHING ` +
        `(this used to report "ALL PASS — 0 checks" with exit 0). valid forms: ${FORMS.join(', ')}` }];
  }
  return INVARIANTS
    .filter(inv => !inv.superseded && inv.forms.has(ctx.form) && inv.when(ctx))
    .map(inv => { const { ok, detail } = inv.check(m, ctx);
      return { id: inv.id, name: inv.name, cls: inv.cls, sev: inv.sev, ok, detail }; });
}

// DECOUPLED FROM BUILD: this guardrail renders and asserts the EXISTING
// data/data.generated.js and never regenerates it — run `node engine/build-data.mjs`
// (npm run build) first. Keeping the build out of here makes validate pure-read
// (T0): no child build process, no project file writes (screenshots go to a
// system temp dir). If the generated data is missing, index.html renders empty
// and the invariants fail loudly, which is the correct signal to build first.

const MIME = { '.html':'text/html', '.js':'text/javascript', '.css':'text/css',
  '.json':'application/json', '.png':'image/png', '.svg':'image/svg+xml' };

// Resolve a Chromium already on disk (any PLAYWRIGHT_BROWSERS_PATH / OS cache)
// so validation uses what is present instead of triggering a fresh download.
function resolveCachedChrome() {
  const bases = [process.env.PLAYWRIGHT_BROWSERS_PATH,
    path.join(process.env.HOME || '', '.cache', 'ms-playwright')].filter(Boolean);
  for (const base of bases) {
    if (!fs.existsSync(base)) continue;
    const builds = fs.readdirSync(base).filter(d => d.startsWith('chromium-'))
      .sort((a, b) => (parseInt(b.split('-')[1]) || 0) - (parseInt(a.split('-')[1]) || 0));
    for (const b of builds)
      for (const sub of ['chrome-linux64', 'chrome-linux', 'chrome-win', 'chrome-mac'])
        for (const bin of ['chrome', 'chrome.exe', 'Chromium.app/Contents/MacOS/Chromium']) {
          const p = path.join(base, b, sub, bin);
          if (fs.existsSync(p)) return p;
        }
  }
  return null;
}
// Returns a launched browser, or NULL when no browser can be obtained. Null is a
// legitimate outcome (see the degradation block in main), never an exception:
// this gate is the OPTIONAL reinforcement, so "there is no Chromium here" is an
// environment fact to report, not a defect in the deck.
async function launch(chromium) {
  if (!chromium) return null;
  try { return await chromium.launch({ headless: true, args: ['--no-sandbox'] }); }
  catch (e) {
    const exe = resolveCachedChrome();
    if (!exe) return null;
    console.log('[validate] default Chromium unavailable; using cached: ' + exe);
    try { return await chromium.launch({ headless: true, executablePath: exe, args: ['--no-sandbox'] }); }
    catch (e2) { return null; }
  }
}

// ─────────────────────────────────────────────────────────────────────────
// STATIC CENSUS (pre-flight): data/*.yaml  vs  data/data.generated.js
//
// The census itself now lives in tools/static-census.cjs, because it is shared
// with tools/check-layout.mjs — the STATIC gate, which must run where NO browser
// exists. It cannot be reached through THIS file: the first import here is
// `playwright`, so requiring validate-layout.cjs to get the census would drag the
// optional dependency into the mandatory gate. One module, two consumers, one
// parse path — so the two guardrails can never disagree about what the data says.
//
// What it does, unchanged: re-parse data/*.yaml with the same js-yaml the build
// uses and compare a CENSUS of it against the generated file. `validate` is
// DECOUPLED from `build` on purpose (it is pure-read), which has one sharp edge —
// it asserts the LAST BUILT data. Edit a YAML, forget `npm run build`, run
// validate, and it goes green on the OLD deck. The census closes that: a mismatch
// does not guess which side is right, it says RUN BUILD and exits non-zero.
// ─────────────────────────────────────────────────────────────────────────
const { staticCensus, nodeCensus, pageCensus } = require('./static-census.cjs');

function startServer() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p === '/') p = '/index.html';
      const fp = path.join(ROOT, p);
      if (!fp.startsWith(ROOT) || !fs.existsSync(fp) || fs.statSync(fp).isDirectory()) {
        res.statusCode = 404; res.end('not found'); return;
      }
      res.setHeader('Content-Type', MIME[path.extname(fp)] || 'application/octet-stream');
      fs.createReadStream(fp).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

// Collect the raw geometry we assert on, measured from the live render.
function measure() {
  const act = document.querySelector('.act.active');
  const canvas = act.querySelector('.canvas');
  const cl = canvas.getBoundingClientRect().left;
  const cw = canvas.clientWidth;

  const allBoxes = [...act.querySelectorAll('.box')];
  const boxes = allBoxes.map(b => {
    const r = b.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
      band: b.classList.contains('msp'),      // full-width band (span == columns)
      hspan: b.classList.contains('mspan'),   // partial horizontal merge
      rowspan: b.classList.contains('mrsp'),  // vertical merge (a taller cell)
      half: b.classList.contains('half'),     // half-height: shares a slot
      clipped: b.scrollHeight > b.clientHeight + 1 };
  });
  // single-COLUMN cells: neither a band nor a partial horizontal span. A row-span
  // cell is still one column wide, so it belongs to this equal-width set.
  const single = boxes.filter(b => !b.band && !b.hspan);
  const singleWidths = [...new Set(single.map(b => b.w))].sort((a,b)=>a-b);
  // HEIGHT set EXCLUDES row-span (.mrsp) AND half (.half) cells — the two merge
  // axes of the height dimension, in opposite directions:
  //   • a row-span cell is legitimately a MULTIPLE of the slot height;
  //   • a half cell is legitimately a FRACTION of it (two share one slot).
  // In both cases the component's own height is not the invariant; the SLOT's is.
  // So U asserts CELL_H over this set AND, separately, over every `.half-slot`
  // (collected below) — which is the wrapper that actually occupies the grid cell.
  const heights = [...new Set(boxes.filter(b => !b.rowspan && !b.half).map(b => b.h))].sort((a,b)=>a-b);
  const clipped = boxes.filter(b => b.clipped).length;

  // HALF SLOTS — the wrapper a `half` PAIR renders into. It is the real grid cell,
  // so it must be exactly one CELL_H tall: that is what proves `half` DIVIDED a
  // slot rather than shrinking one (which would leave a hole and break the filled
  // rectangle). Also records how many components share it, so a slot that somehow
  // ended up with one occupant is visible in the report.
  const halfSlots = [...act.querySelectorAll('.half-slot')].map(s => {
    const r = s.getBoundingClientRect();
    const zoneEl = s.closest('.zone[data-zone]');
    return { zone: zoneEl ? zoneEl.getAttribute('data-zone') : '(root)',
      h: Math.round(r.height), n: s.querySelectorAll(':scope > .box').length };
  });

  // ROW TRACKS — the SEPARATOR ROW, U's third height family, plus the height of
  // the two leaf types the height set never covered (`.sep`, `.rail`).
  //
  // WHY THE TRACK AND NOT THE COMPONENT: a horizontal separator's own height is 0
  // (or ~17px labeled), so measuring the ELEMENT says nothing about the space it
  // costs — the cost is the ROW. So read the grid's resolved `grid-template-rows`
  // (the used size of every track, implicit ones included) and assert each track
  // against what its occupants entitle it to: SEP_ROW_H for a row whose only
  // occupants are horizontal separators, CELL_H otherwise. That is what proves
  // the thin row is thin EXACTLY where the ink is thin — a separator sharing its
  // row with boxes must NOT thin it, and a hole must not be mistaken for one.
  //
  // Occupancy is derived from each child's own start row (the band containing its
  // top edge — an item is either stretched to the band start or centred inside it)
  // extended by its declared `--rowspan`, NOT from raw overlap: an item that
  // OVERFLOWS its row would otherwise be read as legitimately occupying the next
  // one, which is precisely the defect below.
  //
  // OVERFLOW (`.sep` / `.rail`): the height set is built from `.box` only, so the
  // --zone-min-h (180px) floor on `.sep-v`/`.rail-v` overflowed a CELL_H (130px)
  // row by 50px with NOTHING measuring it. Its extent is now compared against the
  // extent of the rows it is entitled to.
  const rowTracks = [...act.querySelectorAll('.sec-grid:not(.sec-compound)')].map(grid => {
    const cs = getComputedStyle(grid);
    const gap = parseFloat(cs.rowGap) || 0;
    const tracks = (cs.gridTemplateRows || '').split(/\s+/)
      .filter(v => v && v !== 'none').map(v => Math.round(parseFloat(v)))
      .filter(v => Number.isFinite(v));
    const gTop = grid.getBoundingClientRect().top;
    const bands = []; let y = 0;
    for (const t of tracks) { bands.push([y, y + t]); y += t + gap; }
    const rows = bands.map(() => ({ n: 0, sepH: 0 }));
    const overflow = [];
    for (const child of grid.children) {
      const r = child.getBoundingClientRect();
      const top = r.top - gTop, bot = r.bottom - gTop;
      // start row: the band CONTAINING the top edge; fall back to the nearest
      // band start when the child overflows above its own row (a centred item
      // taller than its track sticks out both ways).
      let start = bands.findIndex(([a, b]) => top >= a - 0.5 && top <= b + 0.5);
      if (start < 0) {
        let best = 0, bd = Infinity;
        bands.forEach(([a], i) => { const d = Math.abs(top - a); if (d < bd) { bd = d; best = i; } });
        start = best;
      }
      const rowspan = Math.max(1, Math.floor(Number(child.style.getPropertyValue('--rowspan')) || 1));
      const end = Math.min(bands.length - 1, start + rowspan - 1);
      const isSepH = child.classList.contains('sep') && !child.classList.contains('sep-v');
      for (let i = start; i <= end; i++) { rows[i].n++; if (isSepH) rows[i].sepH++; }
      if (child.classList.contains('sep') || child.classList.contains('rail')) {
        const over = Math.round(Math.max(0, bot - bands[end][1]) + Math.max(0, bands[start][0] - top));
        if (over > 1) overflow.push({ cls: child.className, over });
      }
    }
    const zoneEl = grid.closest('.zone[data-zone]');
    return { zone: zoneEl ? zoneEl.getAttribute('data-zone') : '(root)', tracks, rows, overflow };
  });

  // FILTER REFERENTIAL INTEGRITY (invariant K). The chips and the components that
  // claim them are TWO separate places in the YAML, joined only by a string key —
  // so a typo used to be invisible: the chip rendered, matched nothing, and
  // dimmed the whole canvas with no error. Read both sides off the real render:
  // the declared keys from the chip bar, the referenced keys from every
  // component's data-filters. The reset chip 'all' is EXEMPT — it is the engine's
  // reserved "show everything" key (synthesized when a page does not declare it),
  // so it legitimately has no members.
  const filterRefs = (() => {
    const RESET_KEY = 'all';
    const declared = [...act.querySelectorAll('.actbar .chip')]
      .map(c => c.getAttribute('data-flow'))
      .filter(k => k && k !== RESET_KEY);
    const referenced = new Set();
    act.querySelectorAll('[data-filters]').forEach(n => {
      (n.getAttribute('data-filters') || '').split(/\s+/).filter(Boolean).forEach(k => referenced.add(k));
    });
    const declaredSet = new Set(declared);
    return { declared, referenced: [...referenced],
      orphanChips: declared.filter(k => !referenced.has(k)),
      danglingRefs: [...referenced].filter(k => !declaredSet.has(k)) };
  })();

  // WORD-FIT (invariant N): for every leaf box, measure the RENDERED width of the
  // longest indivisible token in its TITLE and the title's available content
  // width, on the live render. `.box` sets overflow-wrap:break-word, so a token
  // WIDER than its cell wraps MID-WORD (the S4 "Orquestac/ión" defect). This
  // returns the raw px per box; the Node-side N check applies WORDFIT_TOL and
  // decides pass/fail (measure() must stay a pure browser-side geometry read).
  // A 2D canvas measures the token at the title's own computed font, so it does
  // not mutate layout. `.t` carries no padding (box padding is on .box), so its
  // clientWidth IS the space the title has. Row-span/bands included — every leaf
  // title must fit whatever cell it lands in.
  const wordFit = (() => {
    const ctx = document.createElement('canvas').getContext('2d');
    const out = [];
    for (const b of allBoxes) {
      const t = b.querySelector('.t');
      if (!t) continue;
      const txt = (t.textContent || '').trim();
      if (!txt) continue;
      const cs = getComputedStyle(t);
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const ls = parseFloat(cs.letterSpacing) || 0;   // may be negative (tightening)
      const availW = t.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
      let word = '', wordW = 0;
      for (const w of txt.split(/\s+/)) {
        if (!w) continue;
        const ww = ctx.measureText(w).width + ls * Math.max(0, w.length - 1);
        if (ww > wordW) { wordW = ww; word = w; }
      }
      const zoneEl = b.closest('.zone[data-zone]');
      out.push({ zone: zoneEl ? zoneEl.getAttribute('data-zone') : '(root)',
        k: b.getAttribute('data-k') || '?', word,
        // A `vertical` treatment rotates the title onto the BLOCK axis, so the
        // horizontal token width is no longer the fit constraint. Flagged here and
        // filtered by N's applicability clause (the clause belongs in the invariant
        // table, not hidden in this measurement).
        vertical: b.classList.contains('vertical'),
        wordW: Math.round(wordW), availW: Math.round(availW) });
    }
    return out;
  })();

  // rows: max boxes sharing a rounded top => visible column count
  const rows = {};
  allBoxes.forEach(b => { const t = Math.round(b.getBoundingClientRect().top/4)*4; rows[t]=(rows[t]||0)+1; });
  const maxRowCount = Math.max(...Object.values(rows), 0);

  const overflowX = Math.max(0, canvas.scrollWidth - canvas.clientWidth);

  // content bounding box → centering
  let maxRight = 0, minLeft = 1e9;
  canvas.querySelectorAll('.zone, .box').forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.right - cl > maxRight) maxRight = r.right - cl;
    if (r.left - cl < minLeft) minLeft = r.left - cl;
  });
  if (minLeft === 1e9) minLeft = 0;
  const leftPad = Math.round(minLeft), rightPad = Math.max(0, Math.round(cw - maxRight));

  // top-level sections: direct .zone children of the root grid.
  const rootGrid = act.querySelector('.sec-plane > .sec-grid');
  const topZones = [...rootGrid.children].filter(z => z.classList.contains('zone')).map(z => {
    const zr = z.getBoundingClientRect();
    const g = z.querySelector(':scope > .sec-grid');
    const gr = g ? g.getBoundingClientRect() : zr;
    const hdr = z.querySelector(':scope > .zone-header');
    let headerOverflow = 0;
    if (hdr) {
      const hr = hdr.getBoundingClientRect();
      headerOverflow = Math.max(0, Math.round(hr.right - zr.right), Math.round((zr.left) - hr.left));
      if (hdr.scrollWidth > hdr.clientWidth + 1) headerOverflow = Math.max(headerOverflow, hdr.scrollWidth - hdr.clientWidth);
    }
    // Content bounds: leftmost/rightmost leaf (box/rail) inside this zone, so the
    // Y invariant can check the content is CENTERED within the zone (equal
    // left/right margin) rather than hugging one edge with a gap on the other.
    let cl = Infinity, cr = -Infinity;
    z.querySelectorAll('.box, .rail').forEach(e => {
      const er = e.getBoundingClientRect();
      if (er.width < 1) return;
      if (er.left < cl) cl = er.left;
      if (er.right > cr) cr = er.right;
    });
    const hasContent = cl !== Infinity;
    return { zone: z.getAttribute('data-zone') || '?',
      w: Math.round(zr.width), gridW: Math.round(gr.width), headerOverflow,
      band: z.classList.contains('msp'),
      leftGap: hasContent ? Math.round(cl - zr.left) : 0,
      rightGap: hasContent ? Math.round(zr.right - cr) : 0 };
  });

  // leaf grids with their authored column count (sec-cN), rendered track count,
  // the composition data for E/P, and the FILL metrics for U/L (the fill model:
  // cells STRETCH to equal fr widths that span the grid edge-to-edge).
  const leafGrids = [...act.querySelectorAll('.sec-grid:not(.sec-compound)')].map(g => {
    const m = g.className.match(/sec-c(\d+)/);
    const authored = m ? Number(m[1]) : 1;
    const tracks = getComputedStyle(g).gridTemplateColumns.split(' ').filter(Boolean).length;
    const z = g.closest('.zone[data-zone]');
    const gr = g.getBoundingClientRect();
    // Direct child GRID CELLS only (a leaf grid may hold ANY leaf component
    // type — box, rail, or separator — not just `box`; a lateral rail/sep with
    // row-span must be counted here too, or it looks like a dead column/left
    // gap to the E/P/L checks below even though it visually fills the cell).
    // Cells come in four widths: a single cell (1 track), a partial span
    // (.mspan, --span tracks), a full band (.msp, every track), and a row-span
    // (.mrsp, 1 track but K rows tall). E now counts REAL multi-track coverage
    // from geometry (no short-circuit on span), and every cell is credited to
    // each row it crosses so a tall cell no longer fakes a left gap on its
    // lower rows.
    const colGap = parseFloat(getComputedStyle(g).columnGap) || 0;
    const rowGap = parseFloat(getComputedStyle(g).rowGap) || 0;
    const cellH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--cell-h')) || 130;
    const rowPitch = cellH + rowGap;   // top-to-top distance between grid rows
    // `.half-slot` counts as a grid cell here — it IS the cell a `half` pair
    // occupies (the two boxes inside it are NOT direct children of the grid). Omit
    // it and the slot looks like a dead track to E and a left/right gap to L, even
    // though it visually fills its cell completely.
    const kidBoxes = [...g.children].filter(e =>
      e.classList.contains('box') || e.classList.contains('rail')
      || e.classList.contains('sep') || e.classList.contains('half-slot'));
    const rowsByTop = {};    // rounded-top -> [rects] — every cell OCCUPYING that row
    const cellRows = {};     // rounded-top -> count of non-band cells occupying it
    // Rows touched by AT LEAST ONE row-span (.mrsp) cell — whether the cell
    // starts or merely continues through that row. A row-span cell tapers a
    // grid's row occupancy BY DESIGN (a cell-graph bar chart legitimately has
    // shorter rows below a taller neighbor; a swimlane rail/sep legitimately
    // fills a column no single-row cell reaches) — that is not the same defect
    // P/L exist to catch (an accidental stranded cell / dead edge after a
    // purely horizontal collapse). So these rows are EXEMPTED from the P
    // (orphan) and L (edge-fill) comparisons below, mirroring how U already
    // excludes row-span cells from the uniform-cell-height check.
    const rowspanRows = new Set();
    const singleWs = [];     // 1-column cell widths (should be equal — equal fr tracks)
    let maxCellRight = -Infinity;
    for (const b of kidBoxes) {
      const r = b.getBoundingClientRect();
      const isBand = b.classList.contains('msp');       // full-width band
      const isHspan = b.classList.contains('mspan');    // partial horizontal merge
      const isRowspan = b.classList.contains('mrsp');   // vertical merge (K rows)
      const rowspanK = isRowspan ? (parseInt(getComputedStyle(b).getPropertyValue('--rowspan')) || 1) : 1;
      // A VERTICAL separator (.sep-v) is BY DESIGN a thin divider LINE
      // (width:0, centered in its track — see index.html), not a filled cell
      // like a box/rail (which stretch to fill their track). So it legitimately
      // does not share the box-equal width the U check expects; exclude it from
      // that comparison the same way a row-span cell is excluded from the U
      // height comparison. It still counts for maxCellRight/rowsByTop/cellRows
      // below — it still OCCUPIES its column/row, just narrowly.
      const isThinSep = b.classList.contains('sep-v');
      if (!isBand && !isHspan && !isThinSep) singleWs.push(r.width);   // 1-column cell (incl. row-span)
      if (r.right > maxCellRight) maxCellRight = r.right;
      // ROW-SPAN ATTRIBUTION (the L fix): credit a cell to EVERY row it crosses,
      // not just the row of its top. A tall cell in column 1 previously looked
      // like a left gap on its lower rows (a false gap) because it was attributed
      // only to its top row; here it is added to all K rows it spans.
      for (let i = 0; i < rowspanK; i++) {
        const top = Math.round((r.top + i * rowPitch) / 4) * 4;
        (rowsByTop[top] = rowsByTop[top] || []).push(r);
        if (!isBand) cellRows[top] = (cellRows[top] || 0) + 1;
        if (isRowspan) rowspanRows.add(top);
      }
    }
    // EMPTY COLUMN (the E fix): count REAL multi-track coverage instead of
    // short-circuiting on the mere presence of a span. A reserved dead track
    // exists only when the widest content still falls ~one track short of the
    // grid's right edge — i.e. NOTHING (single cell, partial span, or band) ever
    // reaches it. A partial span that fills the remaining tracks leaves no gap.
    const trackW = singleWs.length ? Math.min(...singleWs) : (gr.width - (tracks - 1) * colGap) / tracks;
    const rightDead = maxCellRight === -Infinity ? 0 : Math.max(0, gr.right - maxCellRight);
    const emptyCols = rightDead > trackW * 0.5 ? Math.round(rightDead / (trackW + colGap)) : 0;
    // ORPHAN: a lone cell on a row while a SIBLING row holds 2+, counting every
    // non-band cell (single, partial span, or a row-span credited to each row it
    // crosses). Band (.msp) rows are excluded — a full-width band alone on its row
    // is not an orphan. Rows a row-span cell touches are ALSO excluded (see
    // rowspanRows above) — a cell-graph's tapered bottom row (e.g. rowspan 1,2,3,4
    // side by side) legitimately ends with fewer occupants and is not an orphan.
    // Only meaningful at >=2 tracks (a 1-col stack never orphans).
    const rowCounts = Object.keys(cellRows)
      .filter(top => !rowspanRows.has(Number(top)))
      .map(top => cellRows[top]);
    const orphan = tracks >= 2 && rowCounts.length > 0 &&
      Math.min(...rowCounts) === 1 && Math.max(...rowCounts) >= 2;
    // FILL metrics (the "filled rectangle" guarantee, measured from geometry):
    //   cellWSpread    — max-min width of 1-column cells; 0 => all equal width.
    //   rowRightGapMax — max over rows of (grid right - row's rightmost cell);
    //                    a full/band/filled-span row => ~0, a partial last row => ~one cell.
    //   leftGapMax     — max over rows of (row's leftmost cell - grid left);
    //                    ~0 when the row starts flush at the grid's left edge
    //                    (a row-span cell keeps its lower rows flush — the L fix).
    let rowRightGapMax = 0, leftGapMax = 0;
    for (const top in rowsByTop) {
      if (rowspanRows.has(Number(top))) continue; // see rowspanRows above — a
        // row a vertical merge touches has a legitimate partial profile, not a
        // fill defect (e.g. a bar-chart's short bottom row, or a swimlane rail's
        // column no single-row cell reaches).
      const rs = rowsByTop[top];
      rowRightGapMax = Math.max(rowRightGapMax, Math.round(gr.right - Math.max(...rs.map(r => r.right))));
      leftGapMax = Math.max(leftGapMax, Math.round(Math.min(...rs.map(r => r.left)) - gr.left));
    }
    const cellWSpread = singleWs.length ? Math.round(Math.max(...singleWs) - Math.min(...singleWs)) : 0;
    // MINIMO LEGIBLE (the M metric): the NARROWEST 1-column cell in this grid.
    // A grid whose cells fall below the readable floor (MIN_LEGIBLE) has squeezed
    // its text to ~1 char per line instead of collapsing columns — the RowSpan
    // 2A/2B defect. Uses the same singleWs set as the equal-width check (excludes
    // bands, partial spans, and thin vertical separators; includes row-span cells,
    // which are one column wide). null when the grid has no 1-column cell.
    const minSingleW = singleWs.length ? Math.round(Math.min(...singleWs)) : null;
    return { zone: z ? z.getAttribute('data-zone') : '(root)', authored, tracks,
      emptyCols, orphan, nSingleRows: rowCounts.length,
      cellWSpread, rowRightGapMax, leftGapMax, minSingleW };
  });

  // ROOT canvas horizontality: how the TOP-LEVEL sections distribute across the
  // root grid's visual rows. rootRowMax > 1 means some sections sit SIDE BY SIDE
  // (the deck uses the horizontal canvas); rootRowMax == 1 means every section is
  // a full-width row (a purely vertical, narrow-centered stack).
  let rootRowMax = 0;
  {
    const rootGridEl = act.querySelector('.sec-plane > .sec-grid');
    if (rootGridEl) {
      const tops = {};
      [...rootGridEl.children].forEach(k => {
        if (!k.classList.contains('zone')) return;
        const t = Math.round(k.getBoundingClientRect().top / 4) * 4;
        tops[t] = (tops[t] || 0) + 1;
      });
      rootRowMax = Math.max(0, ...Object.values(tops));
    }
  }

  // WRAP STRUCTURE of every compound grid (incl. the root): how its DIRECT
  // children distribute across visual rows. `[2|1|1]` means row1 holds 2
  // sections, row2 holds 1, row3 holds 1.
  const wrap = [...act.querySelectorAll('.sec-grid.sec-compound')].map(g => {
    const z = g.closest('.zone[data-zone]');
    const id = z ? z.getAttribute('data-zone') : '(root)';
    const tops = {};
    [...g.children].forEach(k => { const t = Math.round(k.getBoundingClientRect().top/4)*4; tops[t]=(tops[t]||0)+1; });
    const rowCounts = Object.keys(tops).map(Number).sort((a,b)=>a-b).map(t => tops[t]);
    return `${id}:[${rowCounts.join('|')}]`;
  }).sort();

  // COLLISION (ratchet): sibling SECTIONS that overlap. The fill/flex model can,
  // at some tier, stretch a column-direction stack taller than its content and
  // make one section overflow onto its next sibling — a TEXT COLLISION every
  // other geometry check misses, because each BOX is individually intact and a
  // header sits within its OWN zone (H compares a header to its own section, not
  // to a sibling). Assert that no two SIBLING .zone elements (direct children of
  // the same grid) overlap. This is the invariant the columns:1 compound stack
  // collision earned.
  const OVERLAP_TOL = 4; // px of intersection tolerated (borders/rounding)
  const collisions = [];
  act.querySelectorAll('.sec-grid').forEach(g => {
    const sibs = [...g.children].filter(z => z.classList.contains('zone'))
      .map(z => ({ id: z.getAttribute('data-zone') || '?', r: z.getBoundingClientRect() }));
    for (let i = 0; i < sibs.length; i++)
      for (let j = i + 1; j < sibs.length; j++) {
        const a = sibs[i].r, b = sibs[j].r;
        const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ox > OVERLAP_TOL && oy > OVERLAP_TOL)
          collisions.push(`${sibs[i].id}×${sibs[j].id}(${Math.round(ox)}×${Math.round(oy)}px)`);
      }
  });

  // BALLOON / CONTENT-OVERFLOW guard (ratchet, invariant G). The X collision
  // check above only turns red when two sibling section BOXES actually overlap —
  // and benign, short seed content never overlaps, so a regression of the
  // compound-flex exemptions stays green there. This measures the CAUSE instead
  // of waiting for a visible overlap, on the live computed render:
  //   (1) BALLOON — a LEAF component (.box / .sep / .rail) sitting DIRECTLY in a
  //       ROW-direction compound grid must be content-sized (flex-grow 0). If it
  //       has grown (flex-grow > 0) it takes an equal flex slice — a separator
  //       line or a lone box balloons to a full share. This is exactly the
  //       regression of the sep/rail/box exemption (fix-1 / bug #2). A separator
  //       is additionally checked to stay thin in absolute width — a divider line
  //       is never legitimately wide — as the observable EFFECT of the balloon.
  //   (2) STACK OVERFLOW — a nested section (.zone) stacked in a columns:1
  //       (column-direction) compound must keep its CONTENT height (flex-grow 0).
  //       If it grows, a stretched parent divides its height between the stacked
  //       sections and a content-heavy one is given a box SHORTER than its
  //       content, which then spills onto the next section. Caught here as either
  //       flex-grow > 0 OR the section's own content overflowing its box
  //       (scrollHeight > clientHeight). This is the regression of the sec-c1
  //       reset (fix-2). Measured per element, so it goes red BEFORE the spill is
  //       large enough to make two boxes overlap (which is all X can see).
  const SEP_MAX_W = 96;   // px — a divider line / thin rail is never this wide as a flex item
  const balloons = [];
  const stackOverflow = [];
  act.querySelectorAll('.sec-grid.sec-compound').forEach(g => {
    const column = getComputedStyle(g).flexDirection.startsWith('column');
    const gid = (g.closest('.zone[data-zone]') && g.closest('.zone[data-zone]').getAttribute('data-zone')) || '(root)';
    for (const kid of g.children) {
      const grow = parseFloat(getComputedStyle(kid).flexGrow) || 0;
      const label = kid.getAttribute('data-zone') || kid.getAttribute('data-k')
        || (kid.classList.contains('sep') ? 'sep' : kid.classList.contains('rail') ? 'rail'
          : kid.classList.contains('half-slot') ? 'half-slot' : kid.classList.contains('box') ? 'box' : '?');
      // A `.half-slot` is a LEAF occupant too: it stands in for the single box it
      // divides, so it must stay content-sized in a compound row exactly like one.
      const isLeaf = kid.classList.contains('box') || kid.classList.contains('sep')
        || kid.classList.contains('rail') || kid.classList.contains('half-slot');
      const isZone = kid.classList.contains('zone');
      if (!column && isLeaf) {
        if (grow > 0) balloons.push(`${gid}>${label}:flex-grow=${grow} (leaf grew to an equal slice)`);
        if ((kid.classList.contains('sep') || kid.classList.contains('rail'))
            && Math.round(kid.getBoundingClientRect().width) > SEP_MAX_W)
          balloons.push(`${gid}>${label}:width=${Math.round(kid.getBoundingClientRect().width)}px > ${SEP_MAX_W}px (divider/rail ballooned)`);
      }
      if (column && isZone) {
        if (grow > 0) stackOverflow.push(`${gid}>${label}:flex-grow=${grow} (stacked section given a divided share)`);
        if (kid.scrollHeight > kid.clientHeight + 2)
          stackOverflow.push(`${gid}>${label}:content-overflow(scrollH ${kid.scrollHeight} > clientH ${kid.clientHeight})`);
      }
    }
  });

  // SPAN-WEIGHTED COMPOUND WIDTH (ratchet, invariant Q). In a compound grid a
  // nested section's width FOLLOWS its AUTHORED span via flex-grow=--span (CSS:
  // `.sec-grid.sec-compound > .zone { flex: var(--span,1) 1 0 }`). Because --span
  // is an INHERITING custom property, an unspanned child that fails to carry its
  // OWN --span inherits its parent band's --span and the intended ratio collapses
  // to equal shares (bug #3: gcpenv's 2:1 GKE-vs-data split rendering 50/50). We
  // compare each SIDE-BY-SIDE section child's rendered width against its AUTHORED
  // span read from window.__DOC__ — NOT the computed --span, because the bug
  // corrupts the rendered --span itself, so width∝rendered-span would stay green
  // while broken (both children read --span:2 and both are equal width). Scoped to
  // ROW-direction grids (side by side, not the stacked sec-c1 tier) and non-band
  // (.msp) children, on a visual row where >=2 such children sit with DIFFERING
  // authored spans (equal spans => equal widths, nothing weighted to assert).
  // RESOLVED BY ID, NOT BY POSITION. This used to be
  // `pages[[...document.querySelectorAll('.act')].indexOf(act)]` — a positional
  // join with the same defect as discovery's: when the rendered set differs from
  // the authored set, `docPage` is ANOTHER page, so `authoredSpan` fills with
  // FOREIGN ids, none of the ids in THIS page's grids match, `spanRatios` comes
  // back empty and invariant Q passes vacuously ("no span-weighted compound rows
  // to check") while the real ratios go unasserted. The engine stamps
  // `data-page-id`; join on it.
  const activePageId = act.getAttribute('data-page-id');
  const allDocPages = (window.__DOC__ && window.__DOC__.pages) || [];
  const docPage = allDocPages.find(p => p && String(p.id) === String(activePageId)) || null;
  const authoredSpan = {};
  (function walk(nodes) { (nodes || []).forEach(n => {
    if (n.id != null) authoredSpan[n.id] = Math.max(1, Number(n.span) || 1);
    if (Array.isArray(n.children)) walk(n.children); }); })(docPage ? docPage.sections : []);
  const spanRatios = [];
  act.querySelectorAll('.sec-grid.sec-compound').forEach(g => {
    if (getComputedStyle(g).flexDirection.startsWith('column')) return; // stacked tier: not span-weighted
    const gid = (g.closest('.zone[data-zone]') && g.closest('.zone[data-zone]').getAttribute('data-zone')) || '(root)';
    const kids = [...g.children].filter(k => k.classList.contains('zone') && !k.classList.contains('msp'))
      .map(k => { const r = k.getBoundingClientRect(); const id = k.getAttribute('data-zone') || '?';
        return { id, w: r.width, top: Math.round(r.top / 4) * 4, span: authoredSpan[id] || 1 }; });
    const byRow = {};
    kids.forEach(z => { (byRow[z.top] = byRow[z.top] || []).push(z); });
    Object.values(byRow).forEach(row => {
      if (row.length < 2) return;
      const spans = row.map(z => z.span);
      if (Math.max(...spans) === Math.min(...spans)) return; // equal authored spans: no weighting to assert
      const totalSpan = spans.reduce((a, b) => a + b, 0);
      const totalW = row.reduce((a, z) => a + z.w, 0);
      row.forEach(z => { const expected = totalW * z.span / totalSpan;
        const errPct = expected > 0 ? Math.abs(z.w - expected) / expected * 100 : 0;
        spanRatios.push({ grid: gid, id: z.id, span: z.span, w: Math.round(z.w),
          expected: Math.round(expected), errPct: +errPct.toFixed(1) }); });
    });
  });

  // ── CENSUS: AUTHORED == RENDERED (invariant Z) ────────────────────────────
  // Every other invariant measures the geometry of what IS on screen. NONE of them
  // notices something that is NOT on screen: a page, a section, or a component that
  // was authored and then silently vanished at render time leaves the remaining
  // geometry perfectly valid, so the whole table stays green while the deck is
  // missing content. (`nBoxes`, `canvasScrollHeight`, `canvasClientWidth` and
  // `nSingleRows` were already measured here and no invariant read any of them —
  // this is what they are for.)
  //
  // So COUNT BOTH SIDES and compare. The authored walk below mirrors engine.js's
  // buildGrid EXACTLY — that mirroring is the point: any divergence between the two
  // implementations shows up as a census mismatch rather than as silence.
  //   • a node with a `children` array is a SECTION      -> one `.zone[data-zone]`
  //   • a leaf's `type` dispatches: separator -> `.sep`, rail -> `.rail`,
  //     anything else -> `.box`
  //   • two CONSECUTIVE `half` leaves are ONE `.half-slot` holding two `.box`es
  //   • a grid with no section child is a LEAF grid      -> one `.sec-grid:not(.sec-compound)`
  //     (counted for the page root too — the root grid is a leaf grid when the
  //     page's own children are all components)
  const census = (() => {
    const a = { zones: 0, boxes: 0, seps: 0, rails: 0, halfSlots: 0, leafGrids: 0 };
    const isSectionNode = n => !!(n && Array.isArray(n.children));
    const orderKids = list => [...(list || [])].map((c, i) => ({ c, i }))
      .sort((x, y) => { const ox = x.c.order ?? (x.i + 1), oy = y.c.order ?? (y.i + 1);
        return ox === oy ? x.i - y.i : ox - oy; }).map(x => x.c);
    const isHalfLeaf = c => c && !isSectionNode(c) &&
      Array.isArray(c.treatment) && c.treatment.includes('half');
    // Walk ONE grid's children: count its own shape, then recurse into sections.
    const walkGrid = (kids) => {
      const list = kids || [];
      if (!list.some(isSectionNode)) a.leafGrids++;      // engine: !isCompound
      const ordered = orderKids(list);
      for (let i = 0; i < ordered.length; i++) {
        const c = ordered[i];
        if (isHalfLeaf(c) && isHalfLeaf(ordered[i + 1])) {   // engine: adjacency pairing
          a.halfSlots++; a.boxes += 2; i++; continue;
        }
        if (isSectionNode(c)) { a.zones++; walkGrid(c.children); continue; }
        if (c && c.type === 'separator') a.seps++;
        else if (c && c.type === 'rail') a.rails++;
        else a.boxes++;
      }
    };
    if (docPage) walkGrid(docPage.sections);
    const r = {
      zones: act.querySelectorAll('.zone[data-zone]').length,
      boxes: allBoxes.length,
      seps: act.querySelectorAll('.sep').length,
      rails: act.querySelectorAll('.rail').length,
      halfSlots: halfSlots.length,
      leafGrids: leafGrids.length,
    };
    const diffs = [];
    if (!docPage) diffs.push(`page "${activePageId}" resolves to NO window.__DOC__ entry (cannot census)`);
    else for (const k of Object.keys(a)) if (a[k] !== r[k]) diffs.push(`${k}: authored ${a[k]} != rendered ${r[k]}`);
    // DECK-LEVEL page census: an authored page that renders no `.act` is invisible
    // to every per-page check, because a page that is not there is never visited.
    const nActs = document.querySelectorAll('.act').length;
    const nAuthoredPages = allDocPages.length;
    if (nActs !== nAuthoredPages) diffs.push(`pages: authored ${nAuthoredPages} != rendered ${nActs} .act element(s)`);
    return { authored: a, rendered: r, diffs, nActs, nAuthoredPages, pageId: activePageId };
  })();

  return { singleWidths, heights, clipped, maxRowCount, overflowX,
    leftPad, rightPad, topZones, leafGrids, wrap, rootRowMax, collisions, balloons, stackOverflow, spanRatios, wordFit,
    halfSlots, rowTracks, filterRefs, census, nBoxes: boxes.length,
    canvasScrollHeight: canvas.scrollHeight, canvasClientWidth: cw };
}

// The geometry SIGNATURE that must be identical across reloads.
function signature(m) {
  return JSON.stringify({
    singleWidths: m.singleWidths,
    heights: m.heights,
    maxRowCount: m.maxRowCount,
    leafGrids: m.leafGrids.map(g => `${g.zone}:${g.authored}/${g.tracks}`).sort(),
    topZones: m.topZones.map(z => `${z.zone}:${z.w}`).sort(),
    wrap: m.wrap,
  });
}

// The COLUMN/WRAP structure only (no absolute widths): what must stay put when
// the available width is perturbed by a scrollbar's worth of pixels.
function wrapSig(m) {
  return JSON.stringify({
    maxRowCount: m.maxRowCount,
    leafGrids: m.leafGrids.map(g => `${g.zone}:${g.authored}/${g.tracks}`).sort(),
    wrap: m.wrap,
  });
}

// Load index.html, dismiss the help HUD, wait for fonts + layout to settle,
// and select the requested page tab.
async function settle(page, tabIndex) {
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.evaluate(() => {
    const m = document.querySelector('.help-modal.show');
    if (m) { const bd = document.querySelector('[data-help-backdrop]'); if (bd) bd.classList.remove('show'); m.classList.remove('show'); }
    try { localStorage.setItem('help-seen', '1'); } catch (e) {}
  });
  // Navigate via the ArrowRight key (same mechanism as tools/verify.mjs)
  // instead of clicking a .pagetab button: the tab bar renders only a
  // sliding WINDOW of 3 tabs centered on the current page (engine.js), so a
  // deck with >3 pages has no rendered tab for every index — clicking
  // document.querySelectorAll('.pagetab')[tabIndex] would silently no-op past
  // the window and leave the render on whatever page it already was (a false
  // pass measuring the wrong page). A fresh load/reload always starts at page
  // 0, so tabIndex ArrowRight presses reliably lands on the requested page
  // regardless of how many tabs are actually rendered.
  for (let i = 0; i < tabIndex; i++) await page.keyboard.press('ArrowRight');
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.waitForTimeout(150);
  await page.evaluate(() => new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res))));
}

async function main() {
  // ── PRE-FLIGHT: the STATIC census (no browser needed). ──
  // Runs FIRST so a run against stale generated data fails immediately, instead of
  // spending five widths × five reloads certifying a deck that no longer matches
  // the YAML on disk. Pure-read; writes nothing.
  console.log('\n══════════ STATIC CENSUS (data/*.yaml vs data.generated.js) ══════════\n');
  const sc = staticCensus();
  if (!sc.ok) {
    for (const p of sc.problems) console.log(`    [FAIL] ${p}`);
    console.log('\nSTALE OR DIVERGENT DATA — `validate` asserts data/data.generated.js, which no longer');
    console.log('matches data/*.yaml. Any verdict here would describe the OLD deck, not your edit.');
    console.log('Run `npm run build`, then re-run `npm run validate`.\n');
    process.exit(1);
  }
  console.log(`    [PASS] generated data matches the authored YAML — ${sc.summary}\n`);

  // ── DEGRADATION: NO BROWSER IS A SKIP, NOT A FAILURE. ──
  // This gate is the OPTIONAL REINFORCEMENT. The mandatory one is `npm run check`
  // (tools/check-layout.mjs): static, arithmetic, js-yaml only — and it has already
  // run by the time anyone gets here, because it is the gate. So an environment with
  // no Chromium is not an unverified deck; it is a deck verified by everything that
  // does not need pixels. Exiting non-zero here would make a correct deck fail for a
  // reason that has nothing to do with the deck, and would make `validate`
  // unrunnable in exactly the installs where Gaia most often lives.
  // The census above STILL ran and still fails hard — a skip never skips that.
  const chromium = loadChromium();
  if (!chromium) {
    console.log('══════════ SKIPPED (no browser) ══════════\n');
    console.log('Playwright is not installed here, so the RENDER invariants were not asserted.');
    console.log('This is not a failure: `npm run validate` is the OPTIONAL reinforcement gate.');
    console.log('The MANDATORY gate is `npm run check` (tools/check-layout.mjs) — static,');
    console.log('arithmetic, no browser — which proves the layout closes, plus the STATIC CENSUS');
    console.log('above, which ran and passed.\n');
    console.log('Skipped (render-only, needs pixels): legibility floor (M), word fit (N), text');
    console.log('clamping (C), flex wrap points (D/R), rendered span proportions (Q), compound');
    console.log('balloon/overflow (G), sibling collision (X), centering (B), band fill (Y),');
    console.log('authored==rendered census (Z), equal cell width / slot height (U).\n');
    console.log('To enable it: `npm install` in the deck (playwright is a devDependency),');
    console.log('then `npx playwright install chromium`.\n');
    process.exit(0);
  }

  fs.mkdirSync(OUT, { recursive: true });
  const srv = await startServer();
  const PORT = srv.address().port;
  const BASE = `http://127.0.0.1:${PORT}/index.html`;
  const browser = await launch(chromium);
  if (!browser) {
    srv.close();
    console.log('══════════ SKIPPED (browser could not launch) ══════════\n');
    console.log('The `playwright` module resolved but no Chromium could be launched (none');
    console.log('downloaded, or the sandbox refuses it). Same verdict as an absent browser:');
    console.log('the RENDER invariants were not asserted, the MANDATORY static gate');
    console.log('(`npm run check`) and the census above are what stand. Exiting 0.\n');
    console.log('To enable it: `npx playwright install chromium`.\n');
    process.exit(0);
  }

  // Discover the deck's pages from the rendered DOM (no hardcoded page names).
  // Name comes from window.__DOC__.pages (the full manifest), NOT from the
  // rendered .pagetab buttons — the tab bar only ever shows a sliding WINDOW
  // of 3 tabs (engine.js), so past 3 pages tabs[i] for the later pages is
  // undefined and would silently fall back to a fake "pageN" label.
  //
  // JOINED BY ID, NEVER BY INDEX (the root cause fix behind invariant Z).
  // This used to read `docPages[i]` for `acts[i]` — a POSITIONAL join. Position is
  // not identity: if the rendered set ever differs from the authored set (a page
  // dropped at render time, a page reordered), every later act is reported under
  // ANOTHER page's name and — far worse — another page's `form`, which then scopes
  // the WRONG invariant set. The same positional bug in measure() filled
  // `authoredSpan` from the wrong page's sections, which silently emptied it and
  // made invariant Q vacuous (no ids matched => no spanRatios => nothing asserted).
  // The engine now stamps `data-page-id` on every `.act`, so the join is by
  // identity; an act whose id resolves to NO manifest page is a hard failure
  // rather than a silent fallback to `pageN` + the default form.
  const discovery = await (async () => {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1000 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    const pages = await page.evaluate((defForm) => {
      const acts = [...document.querySelectorAll('.act')];
      const docPages = (window.__DOC__ && window.__DOC__.pages) || [];
      const byId = new Map(docPages.filter(p => p && p.id != null).map(p => [String(p.id), p]));
      // FORM comes from the page manifest entry resolved BY ID. It scopes which
      // invariants apply (see INVARIANTS). Default: dashboard.
      return acts.map((a, i) => {
        const pageId = a.getAttribute('data-page-id');
        const dp = pageId != null ? byId.get(pageId) : null;
        return { pageId, resolved: !!dp,
          name: (dp && (dp.name || dp.id)) || pageId || `page${i}`,
          form: (dp && dp.form) || defForm,
          tabIndex: i };
      });
    }, DEFAULT_FORM);
    await ctx.close();
    // An act that cannot be joined to a manifest page means the render and the
    // data have diverged — the exact condition the positional join used to hide.
    // Fail loudly instead of validating a page under a foreign identity.
    const unresolved = pages.filter(p => !p.resolved);
    if (unresolved.length) {
      console.error('\n══════════ IDENTITY FAILURE ══════════\n');
      console.error(`${unresolved.length} rendered .act element(s) carry a data-page-id that matches NO ` +
        `window.__DOC__.pages entry: ${unresolved.map(p => JSON.stringify(p.pageId)).join(', ')}.`);
      console.error('The render and the data have diverged; validating by position would report each page ' +
        'under a foreign name and form. Re-run `npm run build`.\n');
      await browser.close(); srv.close();
      process.exit(1);
    }
    // A deck that rendered NO page cannot be validated. The old fallback invented a
    // synthetic `page0` here, which then crashed in measure() (no `.act.active`) or,
    // worse, produced an empty check set read as a pass. Nothing rendered is red.
    if (!pages.length) {
      console.error('\n══════════ NOTHING RENDERED ══════════\n');
      console.error('No .act element rendered: index.html produced an empty deck. There is no geometry to ' +
        'assert, so this is a FAILURE, not a pass. Check data/data.generated.js exists and run `npm run build`.\n');
      await browser.close(); srv.close();
      process.exit(1);
    }
    return pages;
  })();

  const results = [];
  let failed = 0;     // dura checks that failed (fails the build)
  let advisories = 0; // consejo checks that flagged (never fails the build)

  for (const pg of discovery) {
    for (const [tier, w] of Object.entries(WIDTHS)) {
      const ctx = await browser.newContext({ viewport: { width: w, height: 1000 }, deviceScaleFactor: 1 });
      const page = await ctx.newPage();

      // ── DETERMINISM: render PASSES times with a real reload (F5). ──
      const sigs = [];
      let m0 = null;
      for (let p = 0; p < PASSES; p++) {
        if (p === 0) await page.goto(BASE, { waitUntil: 'networkidle' });
        else await page.reload({ waitUntil: 'networkidle' });
        await settle(page, pg.tabIndex);
        const m = await page.evaluate(measure);
        if (p === 0) m0 = m;
        sigs.push(signature(m));
      }
      const uniqueSigs = [...new Set(sigs)];
      const deterministic = uniqueSigs.length === 1;

      // ── FULL-PAGE screenshot: grow the viewport until .canvas no longer
      // scrolls internally, then capture the whole deck. ──
      const measureFull = () => {
        const c = document.querySelector('.act.active .canvas');
        const cr = c.getBoundingClientRect();
        return { canvasTop: Math.max(0, Math.ceil(cr.top)),
                 scrollH: c.scrollHeight, clientH: c.clientHeight,
                 frameBottom: Math.max(0, Math.ceil(window.innerHeight - cr.bottom)) };
      };
      let ff = await page.evaluate(measureFull);
      let capH = 1000;
      for (let iter = 0; iter < 5; iter++) {
        capH = Math.min(MAX_FULL_H, ff.canvasTop + ff.scrollH + ff.frameBottom + FULL_MARGIN);
        await page.setViewportSize({ width: w, height: capH });
        await page.reload({ waitUntil: 'networkidle' });
        await settle(page, pg.tabIndex);
        ff = await page.evaluate(measureFull);
        if (ff.scrollH <= ff.clientH + 1) break;
      }
      // ONE CAPTURE, and it is the FULL-PAGE one. There used to be a second,
      // viewport-sized screenshot per (page,width) — at five widths that was ten
      // images per page, nine of which nobody ever opened: the full-page capture at
      // the widest tier strictly contains what the others showed. Invariant T
      // asserts THIS capture is not truncated, so the single image is the evidence.
      await page.screenshot({ path: path.join(OUT, `${pg.name}-${w}-full.png`), fullPage: true });
      const captureOk = ff.scrollH <= ff.clientH + 1;
      const captureDetail = captureOk
        ? `canvas fully expanded (scrollH=${ff.scrollH} ≤ clientH=${ff.clientH}); full-page viewport=${capH}px`
        : `TRUNCATION: canvas still scrolls at capture (scrollH=${ff.scrollH} > clientH=${ff.clientH}, viewport=${capH}px${capH >= MAX_FULL_H ? `, hit cap ${MAX_FULL_H}` : ''})`;
      // ── R: scrollbar-robustness (wide tiers only). ──
      let robustDetail = 'n/a (only asserted at wide tiers)';
      let robustOk = true;
      if (WIDE_TIERS.has(tier)) {
        await page.setViewportSize({ width: w - SB_GUARD, height: 1000 });
        await page.reload({ waitUntil: 'networkidle' });
        await settle(page, pg.tabIndex);
        const mNarrow = await page.evaluate(measure);
        robustOk = wrapSig(mNarrow) === wrapSig(m0);
        robustDetail = robustOk
          ? `wrap/columns unchanged under -${SB_GUARD}px`
          : `FLIPPED under -${SB_GUARD}px:\n        @${w}px : ${wrapSig(m0)}\n        @${w-SB_GUARD}px: ${wrapSig(mNarrow)}`;
      }

      // ── invariants (flat form-scoped lookup, asserted on first-pass geometry) ──
      // ctx carries the per-render facts a check may need beyond the geometry m:
      // the page's FORM (scopes applicability), the tier, and the run-computed
      // determinism / robustness / capture results. runInvariants filters the
      // INVARIANTS table by (form, tier, not-retired) and evaluates each check —
      // no per-tier branching tree here anymore.
      const m = m0;
      const invCtx = { form: pg.form, tier, w, WIDE: WIDE_TIERS.has(tier), PASSES,
        deterministic, uniqueSigs, sigs, robustOk, robustDetail, captureOk, captureDetail };
      const checks = runInvariants(m, invCtx);
      for (const c of checks) {
        if (c.ok) continue;
        if (c.sev === 'dura') failed++; else advisories++;
      }

      results.push({ page: pg.name, form: pg.form, tier, width: w,
        maxRowCount: m.maxRowCount, wrap: m.wrap.join(' '), checks });
      await ctx.close();
    }
  }
  await browser.close();
  srv.close();

  // ── report ──
  console.log('\n══════════ LAYOUT VALIDATION ══════════\n');
  // Retirement clause: any invariant that has been superseded is listed once
  // (never evaluated). This is the audit trail of what the guardrail USED to
  // hold and what replaced it.
  const retired = INVARIANTS.filter(inv => inv.superseded);
  if (retired.length) {
    console.log('Retired invariants (superseded, not evaluated):');
    for (const inv of retired) console.log(`    [RETIRED] ${inv.id} ${inv.name} → superseded by ${inv.superseded}`);
    console.log('');
  }
  // A marker per severity: a dura miss is a FAIL (fails the build); a consejo
  // miss is ADVICE (flagged, never fails).
  const marker = c => c.ok ? 'PASS' : (c.sev === 'consejo' ? 'ADVICE' : 'FAIL');
  for (const r of results) {
    console.log(`● ${r.page} [form:${r.form}] @ ${r.width}px (${r.tier})  cols-on-screen=${r.maxRowCount}  wrap=${r.wrap}`);
    for (const c of r.checks) console.log(`    [${marker(c)}] ${c.id} ${c.name}: ${c.detail}`);
    console.log('');
  }
  console.log('═══════════════════════════════════════');
  console.log(`Screenshots: ${OUT}`);
  const v = reportVerdict(results, failed, advisories);
  console.log(v.line + '\n');
  process.exit(v.code);
}

// The final verdict, as a PURE function of the run's tallies, so the decision can
// be exercised without a browser (see the harness note at the bottom of this file).
//
// ZERO CHECKS IS RED, NEVER GREEN (layer 2 of 2 — layer 1 is the undeclared-form
// guard in runInvariants). `failed === 0` is a VACUOUS truth when nothing was
// measured: no check ran, so no check could fail. The old report read that as
// success and printed `ALL PASS — 0 checks` with exit 0 — a guardrail declaring
// green having asserted nothing at all. The total===0 branch is evaluated BEFORE
// the ALL PASS branch can be reached, so an empty measurement is a failure by
// construction whatever caused it (an undeclared `form`, a deck that rendered no
// page, a discovery that produced no act).
function reportVerdict(results, failed, advisories) {
  const total = results.reduce((n, r) => n + r.checks.length, 0);
  const adv = advisories ? ` (${advisories} consejo advisories — see [ADVICE] lines, non-failing)` : '';
  if (total === 0) {
    return { code: 1, total, line:
      `FAIL — 0 checks ran across ${results.length} (page,width) render(s). ` +
      `A guardrail that measured NOTHING is not a pass: an empty check set means the ` +
      `invariant table matched no row (check each page's \`form\`) or no page rendered at all.` };
  }
  if (failed === 0) {
    return { code: 0, total, line:
      `ALL PASS — ${total} checks across ${results.length} (page,width) renders, ${PASSES} reloads each${adv}.` };
  }
  return { code: 1, total, line: `FAIL — ${failed}/${total} dura checks failed. See [FAIL] lines above${adv}.` };
}

// ENTRY POINT / TESTABILITY.
// The guardrail runs only when this file IS the entry point (`npm run validate` →
// `node tools/validate-layout.cjs`), so `require()`-ing it never launches Chromium.
// That is what lets a harness exercise the pure decision logic — the invariant
// table, the undeclared-form guard, the verdict, the static census — in an
// environment with no browser at all.
module.exports = { INVARIANTS, FORMS, ALL_FORMS, DEFAULT_FORM, GRIDDED, GRID_DENSE,
  WORDFIT, runInvariants, reportVerdict, staticCensus, nodeCensus, pageCensus };

if (require.main === module) main();
