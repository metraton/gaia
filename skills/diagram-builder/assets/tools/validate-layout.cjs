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
// WHAT IT DOES
//   1. Reads the EXISTING data/data.generated.js (built by the prior `npm run
//      build` step) — validate does not regenerate it, so run build first.
//   2. Launches headless Chromium and renders EVERY page at FIVE viewport
//      widths spanning the 3→2→1 collapse cascade and the side-by-side regime.
//   3. For every (page,width) it renders MULTIPLE TIMES with a real reload (F5)
//      and ASSERTS the geometry is identical across passes (determinism).
//   4. Measures the real geometry of every leaf box, section zone, and header,
//      and ASSERTS each layout invariant.
//   5. Writes a FULL-PAGE screenshot per (page,width) to a SYSTEM TEMP DIR
//      (os.tmpdir(); override with DIAGRAM_SHOTS_DIR) — never into the project.
//   6. Prints a per-(page,width) PASS/FAIL table and exits non-zero on any fail.
//
// INVARIANTS ASSERTED (each measured from the live render):
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
//                            are of EQUAL width (equal fr tracks) and every .box
//                            is EXACTLY --cell-h tall (uniform height). The old
//                            fixed-232 width rule is gone: cells now STRETCH to
//                            fill, so width varies by section but is equal within
//                            a grid.
//   L  cells fill width      — at width ≥ 1200 every leaf-grid row spans the grid
//                            edge-to-edge: no gap on the right (no hueco a la
//                            derecha) and none on the left. A section is a FILLED
//                            rectangle. Not asserted at min/medium where a short
//                            last row is the legitimate cascade, NOR on a row a
//                            row-span (.mrsp) cell touches (FASE 2a) — a
//                            cell-graph/bar-chart row legitimately tapers, and a
//                            swimlane rail/sep legitimately fills a column no
//                            single-row cell reaches.
//   C  description clamp     — no .box clips its content (desc clamped to 3
//                            lines keeps every box at CELL_H; clipped == 0).
//   O  no h-overflow         — canvas horizontal overflow == 0 at the stacked
//                            tiers (min/medium/large); tolerated only at the
//                            widest side-by-side tiers.
//   F  collapse cascade 3→2→1 — at the MINIMUM width every leaf grid renders
//                            EXACTLY 1 track and the whole page is one vertical
//                            stack (maxRowCount==1 — the endpoint). At MEDIUM the
//                            INTERMEDIATE 2-col "two-table" step (a ≥2-col grid
//                            renders exactly 2 tracks; a 1-col grid stays 1).
//   S  inline fit / band spans block (ALL tiers) — INLINE sections (span:1) hug
//                            their own grid (fit-content); BAND sections (span ==
//                            columns) span the BLOCK width at EVERY tier
//                            (mutually equal, ≥ the widest inline section). A
//                            band that shrinks to its single cell on collapse
//                            FAILS — a band must span the block at every width.
//   B  centered block        — at the widest tiers leftPad ≈ rightPad.
//   H  header within section  — no section header/subtitle overflows its section.
//   E  no empty grid column   — every leaf grid fills EVERY track it declares; a
//                            section authoring more columns than its content can
//                            fill (a reserved dead track, a "columna vacia") FAILS.
//                            Guards the engine's grow-with-content column clamp.
//                            (all tiers)
//   P  no orphan cell         — within a leaf grid, a lone single-cell box on its
//                            own row while a sibling row holds 2+ cells breaks the
//                            group's uniformity and FAILS (the GASTO FIJO /
//                            FALABELLA dangling-alone defect). Asserted only where
//                            the authored columns fully show (width > 1000); at the
//                            collapsed tiers a short last row is the cascade, not
//                            an orphan. A row a row-span (.mrsp) cell touches is
//                            also exempted (FASE 2a) — same reasoning as L above.
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
// ─────────────────────────────────────────────────────────────────────────
const { chromium } = require('playwright');
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
// Five tiers spanning the 3→2→1 cascade and the side-by-side regime:
//   min=600    — the 3→2→1 ENDPOINT: every leaf grid is 1 column, whole page a
//                single vertical stack (narrow chrome shrinks so 1 cell fits).
//   medium=900 — the INTERMEDIATE "two-table" step: leaf grids at 2 columns,
//                sections stacked, bands spanning the block.
//   large=1200 — sections STILL stacked (< 1440), leaf grids at full 3 columns.
//   huge=1920  — sections side by side (> 1440), bands full-width, centered.
//   ultra=2560 — same regime at high resolution.
const WIDTHS = { min: 600, medium: 900, large: 1200, huge: 1920, ultra: 2560 };
const WIDE_TIERS = new Set(['huge', 'ultra']);   // >1440: side-by-side + centered; h-overflow tolerated
const PASSES = 5;        // reloads per (page,width) for the determinism check
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
//   DESIGN (E P U L Y + M) — how the deck reads: no dead track, no orphan cell,
//     equal/uniform cells, filled bands, and the new readable-cell floor. SCOPED
//     to the forms where the concern is real.
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

const INVARIANTS = [
  // ── INTEGRITY — all forms, dura ──────────────────────────────────────────
  { id: 'D', name: 'determinism (5 reloads)', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
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
  { id: 'F', name: '1-col endpoint at min', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.tier === 'min', superseded: null,
    check: (m) => { const bad = m.leafGrids.filter(g => g.tracks !== 1); const oneCol = bad.length === 0 && m.maxRowCount === 1;
      return { ok: oneCol, detail: bad.length ? `not-1-track: ${bad.map(g => `${g.zone}:auth${g.authored}->${g.tracks}`).join(', ')}`
        : m.maxRowCount !== 1 ? `maxRowCount=${m.maxRowCount} (expected 1 — page not a single column)`
        : `all ${m.leafGrids.length} leaf grids => 1 track; single vertical column (maxRowCount=1)` }; } },
  { id: 'F', name: '2-col intermediate at medium', cls: 'integrity', sev: 'dura', forms: ALL_FORMS,
    when: (c) => c.tier === 'medium', superseded: null,
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
  { id: 'U', name: 'uniform cell height', cls: 'design', sev: 'dura', forms: ALL_FORMS,
    when: () => true, superseded: null,
    check: (m) => ({ ok: eq(m.heights, [CELL_H]), detail: `heights=${JSON.stringify(m.heights)} expect [${CELL_H}]` }) },
  { id: 'L', name: 'cells fill width (no right gap)', cls: 'design', sev: 'dura', forms: GRIDDED,
    when: (c) => c.w >= 1200, superseded: null,
    check: (m) => { const bad = m.leafGrids.filter(g => g.rowRightGapMax > FILL_TOL || g.leftGapMax > FILL_TOL);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:gap(right ${g.rowRightGapMax}px,left ${g.leftGapMax}px)`).join(', ')
        : `every leaf grid fills its width edge-to-edge (${m.leafGrids.length} grids)` }; } },
  { id: 'E', name: 'no empty grid column', cls: 'design', sev: 'dura', forms: GRIDDED,
    when: () => true, superseded: null,
    check: (m) => { const bad = m.leafGrids.filter(g => g.emptyCols > 0);
      return { ok: bad.length === 0, detail: bad.length ? bad.map(g => `${g.zone}:${g.tracks}tracks-${g.emptyCols}empty`).join(', ')
        : `all ${m.leafGrids.length} leaf grids fill every declared track` }; } },
  { id: 'P', name: 'no orphan cell', cls: 'design', sev: 'dura', forms: GRID_DENSE,
    when: (c) => c.w > 1000, superseded: null,
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
function runInvariants(m, ctx) {
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
async function launch() {
  try { return await chromium.launch({ headless: true, args: ['--no-sandbox'] }); }
  catch (e) {
    const exe = resolveCachedChrome();
    if (!exe) throw e;
    console.log('[validate] default Chromium unavailable; using cached: ' + exe);
    return await chromium.launch({ headless: true, executablePath: exe, args: ['--no-sandbox'] });
  }
}

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
      clipped: b.scrollHeight > b.clientHeight + 1 };
  });
  // single-COLUMN cells: neither a band nor a partial horizontal span. A row-span
  // cell is still one column wide, so it belongs to this equal-width set.
  const single = boxes.filter(b => !b.band && !b.hspan);
  const singleWidths = [...new Set(single.map(b => b.w))].sort((a,b)=>a-b);
  // HEIGHT set EXCLUDES row-span (.mrsp) cells: a vertical merge legitimately has
  // a MULTIPLE height, so it must not break the uniform-cell-height (U) invariant.
  const heights = [...new Set(boxes.filter(b => !b.rowspan).map(b => b.h))].sort((a,b)=>a-b);
  const clipped = boxes.filter(b => b.clipped).length;

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
    const kidBoxes = [...g.children].filter(e =>
      e.classList.contains('box') || e.classList.contains('rail') || e.classList.contains('sep'));
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
        || (kid.classList.contains('sep') ? 'sep' : kid.classList.contains('rail') ? 'rail' : kid.classList.contains('box') ? 'box' : '?');
      const isLeaf = kid.classList.contains('box') || kid.classList.contains('sep') || kid.classList.contains('rail');
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
  const activeIdx = [...document.querySelectorAll('.act')].indexOf(act);
  const docPage = (window.__DOC__ && window.__DOC__.pages && window.__DOC__.pages[activeIdx]) || null;
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

  return { singleWidths, heights, clipped, maxRowCount, overflowX,
    leftPad, rightPad, topZones, leafGrids, wrap, rootRowMax, collisions, balloons, stackOverflow, spanRatios, nBoxes: boxes.length,
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

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const srv = await startServer();
  const PORT = srv.address().port;
  const BASE = `http://127.0.0.1:${PORT}/index.html`;
  const browser = await launch();

  // Discover the deck's pages from the rendered DOM (no hardcoded page names).
  // Name comes from window.__DOC__.pages (the full manifest), NOT from the
  // rendered .pagetab buttons — the tab bar only ever shows a sliding WINDOW
  // of 3 tabs (engine.js), so past 3 pages tabs[i] for the later pages is
  // undefined and would silently fall back to a fake "pageN" label.
  const discovery = await (async () => {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1000 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    const pages = await page.evaluate((defForm) => {
      const acts = [...document.querySelectorAll('.act')];
      const docPages = (window.__DOC__ && window.__DOC__.pages) || [];
      // FORM comes from the page manifest (window.__DOC__.pages[i].form). It
      // scopes which invariants apply (see INVARIANTS). Default: dashboard.
      return acts.map((a, i) => ({
        name: (docPages[i] && (docPages[i].name || docPages[i].id)) || `page${i}`,
        form: (docPages[i] && docPages[i].form) || defForm,
        tabIndex: i }));
    }, DEFAULT_FORM);
    await ctx.close();
    return pages.length ? pages : [{ name: 'page0', form: DEFAULT_FORM, tabIndex: 0 }];
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
      await page.screenshot({ path: path.join(OUT, `${pg.name}-${w}-full.png`), fullPage: true });
      const captureOk = ff.scrollH <= ff.clientH + 1;
      const captureDetail = captureOk
        ? `canvas fully expanded (scrollH=${ff.scrollH} ≤ clientH=${ff.clientH}); full-page viewport=${capH}px`
        : `TRUNCATION: canvas still scrolls at capture (scrollH=${ff.scrollH} > clientH=${ff.clientH}, viewport=${capH}px${capH >= MAX_FULL_H ? `, hit cap ${MAX_FULL_H}` : ''})`;
      await page.setViewportSize({ width: w, height: 1000 });
      await page.reload({ waitUntil: 'networkidle' });
      await settle(page, pg.tabIndex);
      await page.screenshot({ path: path.join(OUT, `${pg.name}-${w}.png`) });

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
  const total = results.reduce((n, r) => n + r.checks.length, 0);
  console.log('═══════════════════════════════════════');
  console.log(`Screenshots: ${OUT}`);
  const adv = advisories ? ` (${advisories} consejo advisories — see [ADVICE] lines, non-failing)` : '';
  if (failed === 0) { console.log(`ALL PASS — ${total} checks across ${results.length} (page,width) renders, ${PASSES} reloads each${adv}.\n`); process.exit(0); }
  else { console.log(`FAIL — ${failed}/${total} dura checks failed. See [FAIL] lines above${adv}.\n`); process.exit(1); }
})();
