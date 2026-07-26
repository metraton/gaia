// ─────────────────────────────────────────────────────────────────────────
// engine.js — data-driven render engine for a diagram deck.
// @version 2.0.0  (part of the diagram-builder skill; keep in sync
//                  with the skill's GLOSSARY.md + reference.md)
//
// Reads window.__DOC__ (produced by build-data.mjs from the YAML manifest +
// page files) and builds the DOM. No framework, no build step beyond the
// YAML→JS transform. Plain DOM.
//
// It knows only the dialect (document / page / section / component / filter).
// Every domain-specific string lives in the data — the engine carries none.
//
// LAYOUT MODEL — ONE recursive `section` primitive.
//   • A node with a `children` key is a SECTION; without it, a COMPONENT (leaf).
//   • Every section has `columns: N` (its internal CSS-Grid column count,
//     default 2). Its children auto-flow left→right and wrap DOWN.
//   • Every child (section OR component) may declare `span: M` to occupy M of
//     the parent's columns (default 1; M == columns == a full-width band).
//     Same rule at every level.
//   • The page/root is itself a section: `page.columns` = root columns and
//     `page.sections` = the root's children.
//   • A component (leaf) dispatches on its `type`: box (default) | separator |
//     rail. Absent/"box" renders the standard box.
// There is NO envelope / subsection / wraps / layout.row and NO JS layout or
// measurement pass. The layout is a FILLED, CAPPED grid of uniform-height
// cells: a leaf grid divides its section width into `columns` EQUAL `fr` tracks
// that STRETCH to fill (equal width within a grid, fixed --cell-h height), and
// the plane fills the canvas up to a centered 1280px cap. Responsive behaviour
// is pure CSS (stage container queries in index.html): a leaf grid's column
// count cascades …→2→1 as width tightens (2-column "two-table" is the
// intermediate step; 1 column is the endpoint, where the whole page is a single
// vertical stack). A `span` merges cells (Excel-style); `span == columns` is a
// full-width band that takes its own row. Columns COLLAPSE before a cell
// degrades below its readable floor — so nothing scrolls sideways at the
// stacked tiers. The engine tags each grid `sec-c{N}` (authored
// column count) and `sec-compound` (holds nested sections) so the CSS can step
// each grid by its real width need; it emits `--cols` + `--span` and never a
// literal grid-column (the container queries own the collapse). The ONE row
// height that is not --cell-h is the SEPARATOR ROW: a row whose only occupants
// are horizontal separators is reduced to --sep-row-h, emitted as a per-tier
// `grid-auto-rows` track list (see applyRowTracks) — the separator stays a cell,
// only its row shrinks.
//
// Stable ids + order are preserved end-to-end so a future edit mode can
// overlay a localStorage {id: order} map without touching this engine or
// the YAML — see orderedChildren. NOT implemented here.
// ─────────────────────────────────────────────────────────────────────────
(function () {
  'use strict';

  const doc = window.__DOC__;
  if (!doc || !Array.isArray(doc.pages)) {
    console.error('[engine] window.__DOC__ missing or malformed; nothing to render.');
    return;
  }

  // ── the TWO AXES → CSS class maps (mirror the classes in index.html) ──
  // variant = the semantic COLOUR role (one value). treatment = STRUCTURAL
  // modifiers (a list, composable). They are separate maps because they answer
  // separate questions — see the axis note in engine/build-data.mjs, which is
  // where both enums are validated. The engine only translates; it never decides
  // what is legal.
  const COMPONENT_VARIANT = {
    neutral: '', good: 'good', warn: 'warn', bad: 'bad', accent: 'accent', muted: 'muted'
  };
  const SECTION_VARIANT = {
    neutral: '', good: 'good', bad: 'bad'
  };
  // Component treatments:
  //   centered — centre the text block
  //   half     — occupy HALF a slot; two halves stack inside one full-height cell
  //   vertical — run the text down the block axis (a rotated lane label). For a
  //              `separator`/`rail` this is what the old `orientation: vertical`
  //              spelled; folding it into `treatment` removes the parallel field.
  //   outside  — dashed frame ("outside the perimeter"). Its CSS is
  //              `border-style:dashed` and NOTHING else — no colour at all — so it
  //              is a frame treatment, not a colour role, and it composes with any
  //              variant instead of competing with one.
  const COMPONENT_TREATMENT = {
    centered: 'centered', half: 'half', vertical: 'vertical', outside: 'outside'
  };
  // Section treatments: envelope (borderless dashed container that groups nested
  // sections), plain (a bare, border-free structural wrapper — used to stack
  // sub-sections in one parent column with no extra frame).
  const SECTION_TREATMENT = {
    envelope: 'envelope', plain: 'plain'
  };

  const treatmentsOf = node => (Array.isArray(node && node.treatment) ? node.treatment : []);
  const hasTreatment = (node, v) => treatmentsOf(node).includes(v);
  // A half LEAF: only a component can occupy (and therefore divide) a slot.
  const isHalfLeaf = c => c && !Array.isArray(c.children) && hasTreatment(c, 'half');

  // Default column count for a section's grid when it omits `columns`.
  const DEFAULT_SECTION_COLUMNS = 2;

  const el = (tag, cls, attrs) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };

  // Compose a leaf's classes from BOTH axes: one colour role (+ an optional second
  // via variant_extra) and any number of structural treatments. The two never
  // collide because their value sets are disjoint and enforced at build time.
  function componentClasses(comp) {
    const parts = ['box'];
    const v = COMPONENT_VARIANT[comp.variant] ?? '';
    if (v) parts.push(v);
    for (const extra of comp.variant_extra || []) {
      const ev = COMPONENT_VARIANT[extra] ?? '';
      if (ev && !parts.includes(ev)) parts.push(ev);
    }
    for (const t of treatmentsOf(comp)) {
      const tv = COMPONENT_TREATMENT[t] ?? '';
      if (tv && !parts.includes(tv)) parts.push(tv);
    }
    return parts.join(' ');
  }

  // Order a section's children (sections OR components). Explicit `order` wins;
  // otherwise list order. Stable sort so ties keep their declared order — this
  // is exactly the hook a future edit mode overrides. DOM order here IS the
  // single-column collapse order at the phone breakpoint.
  function orderedChildren(list) {
    return [...(list || [])]
      .map((c, i) => ({ c, i }))
      .sort((a, b) => {
        const oa = a.c.order ?? (a.i + 1), ob = b.c.order ?? (b.i + 1);
        return oa === ob ? a.i - b.i : oa - ob;
      })
      .map(x => x.c);
  }

  // Build one .box for a component (a leaf — no `children`). Also fills the
  // detail registry so the panel can look it up on click by data-k. (`kicker`
  // is the small mark above the title — it names no state, it is just the mark.)
  function buildBox(comp, detailRegistry) {
    const box = el('div', componentClasses(comp), { 'data-k': comp.id });
    if (comp.kicker) { const k = el('div', 'k'); k.textContent = comp.kicker; box.appendChild(k); }
    const t = el('div', 't'); t.textContent = comp.title || ''; box.appendChild(t);
    const rawDesc = comp.description;
    const lines = Array.isArray(rawDesc) ? rawDesc : (rawDesc !== null && rawDesc !== undefined ? [rawDesc] : []);
    // Description lines live in ONE `.desc` container so CSS can clamp the whole
    // description to a fixed number of visual lines (see .box .desc line-clamp),
    // keeping every box at the same fixed --cell-h regardless of line count. The
    // full text is always available in the click-through detail panel.
    if (lines.length) {
      const descBox = el('div', 'desc');
      for (const line of lines) { const m = el('div', 'm'); m.textContent = line; descBox.appendChild(m); }
      box.appendChild(descBox);
    }

    // The attribute IS the only record of membership: setFlow re-reads it off
    // the DOM on every chip click, so no filter→nodes map is built anywhere.
    if (Array.isArray(comp.filters) && comp.filters.length) {
      box.setAttribute('data-filters', comp.filters.join(' '));
    }

    detailRegistry[comp.id] = {
      kicker: comp.kicker || '',
      title: comp.title || '',
      facts: lines.join(' · '),
      // detail falls back to joined description when absent
      body: comp.detail || lines.join('<br>'),
      note: comp.note || ''
    };
    return box;
  }

  // Build a `separator` component (a leaf, `type: separator`): a minimal divider
  // LINE — NOT a box (no border/padding). Props: `orientation`
  // (horizontal|vertical, default horizontal), `style` (solid|dotted, default
  // solid), optional `text`. A horizontal separator is a thin full-width rule
  // across its grid cell(s); with `text` the label renders centered/inline on
  // the line (muted, small). A vertical separator is a thin vertical rule. Span
  // is honored by the caller (buildGrid) exactly like any component. Not
  // clickable — no detail registry entry.
  function buildSeparator(sep) {
    // Orientation comes from the `vertical` TREATMENT (the old `orientation:`
    // field was the same switch under a second name — one axis, one spelling).
    const orient = hasTreatment(sep, 'vertical') ? 'v' : 'h';
    const style = sep.style === 'dotted' ? 'dotted' : 'solid';
    const node = el('div', `sep sep-${orient} sep-${style}`);
    if (orient === 'h' && sep.text) {
      node.classList.add('sep-labeled');
      const s = el('span', 'sep-text'); s.textContent = sep.text; node.appendChild(s);
    }
    return node;
  }

  // Build a `rail` component (a leaf, `type: rail`): a swimlane-style LABEL,
  // styled like a component/box but carrying ONLY a `title` (no
  // kicker/description/detail). `orientation: vertical` renders the title
  // rotated (vertical text) for swimlane labeling; default horizontal is a slim
  // title-only box. Span is honored by the caller. Not clickable.
  function buildRail(rail) {
    // Orientation comes from the `vertical` TREATMENT — see buildSeparator.
    const orient = hasTreatment(rail, 'vertical') ? 'v' : 'h';
    const node = el('div', `rail rail-${orient}`);
    const t = el('div', 'rail-title'); t.textContent = rail.title || ''; node.appendChild(t);
    return node;
  }

  // ── THE SEPARATOR ROW (the third row-height family) ─────────────────────
  // A `separator` is a leaf COMPONENT, so it occupies a whole cell: it drew one
  // pixel of ink and was charged the full --cell-h. The fix is NOT to stop it
  // being a cell (principle 1 — everything visible is a merged cell — stands):
  // a row whose ONLY occupants are horizontal separators gets a REDUCED TRACK
  // HEIGHT (--sep-row-h), emitted below as a `grid-auto-rows` track list.
  //
  // A VERTICAL separator is EXCLUDED on purpose. Its ink IS the row height (a
  // `.sep-v` is a line as tall as its row), so thinning its row would shorten
  // the drawing rather than fit the drawing — the opposite of the intent. Only
  // a horizontal separator draws across the row and needs none of its height.
  const isLeafOfType = (c, t) => c && !Array.isArray(c.children) && c.type === t;
  const isThinRowLeaf = c => isLeafOfType(c, 'separator') && !hasTreatment(c, 'vertical');

  // ── THE PLACEMENT MODEL ─────────────────────────────────────────────────
  // Three pieces — widthAtTier, isBandAtTier, rowOccupants — and they are the
  // ONE thing this engine shares with tools/check-layout.mjs (`place`), which
  // mirrors them because a Node gate cannot import a browser script: an ES
  // module is CORS-blocked from `file://` (origin 'null'), and the deck's
  // contract is that it opens with a double click. The mirror is not trusted, it
  // is TESTED — tools/test-guards.mjs extracts these functions from this file's
  // real source and asserts they agree with the gate's copy over a corpus of
  // grid shapes. Change the model here and that test tells you the gate drifted.

  // How many TRACKS a slot occupies at a given tier, mirroring the CSS in
  // index.html: `.msp` (span == cols) is grid-column:1/-1 at every tier, while
  // `.mspan` keeps its PROPORTION — span var(--span) at the authored tier,
  // var(--span2) = round(span/cols·2) at the 2-track tier, and 1/-1 at the
  // 1-track endpoint.
  function widthAtTier(span, cols, tracks) {
    if (tracks === cols) return span;
    if (span >= cols) return tracks;
    return Math.max(1, Math.min(tracks, Math.round(span / cols * tracks)));
  }

  // Whether a slot OWNS ITS ROW at this tier. The test is the width it resolves
  // to, never the authored span, because band-ness is TIER-RELATIVE in the CSS:
  // at the 640px endpoint `.sec-grid:not(.sec-compound) > .mspan` becomes
  // grid-column:1/-1, so a partial merge IS a band there; and a span that fills
  // both tracks of the 2-track tier already spans the whole row (the reason the
  // engine emits --span2 at all). `span >= cols` describes the .msp CLASS, which
  // is the authored tier's answer to a tier-relative question.
  function isBandAtTier(w, tracks) { return w >= tracks; }

  // CSS GRID SPARSE AUTO-PLACEMENT, SIMULATED — which row a slot lands on is a
  // pure function of the flow, so it can be derived instead of measured. Returns,
  // per row, the slot NODES that occupy it (a rowspan slot occupies every row it
  // covers, so a row a taller cell passes through is never seen as empty).
  // `grid-auto-flow` is row/SPARSE: the cursor never moves backwards, and a band
  // carries a definite full-width column position so it cannot share a row.
  function rowOccupants(items, tracks) {
    const occ = new Set();
    const rows = [];
    const key = (r, c) => r + ',' + c;
    const free = (r, c, w, h) => {
      if (c + w > tracks) return false;
      for (let i = 0; i < h; i++) for (let j = 0; j < w; j++) if (occ.has(key(r + i, c + j))) return false;
      return true;
    };
    const fill = (r, c, w, h, node) => {
      for (let i = 0; i < h; i++) {
        if (!rows[r + i]) rows[r + i] = [];
        rows[r + i].push(node);
        for (let j = 0; j < w; j++) occ.add(key(r + i, c + j));
      }
    };
    let cr = 0, cc = 0, guard;
    for (const it of items) {
      const w = Math.max(1, Math.min(it.w, tracks)), h = Math.max(1, it.h);
      if (isBandAtTier(w, tracks)) {
        let r = cc > 0 ? cr + 1 : cr; guard = 0;
        while (!free(r, 0, tracks, h) && guard++ < 10000) r++;
        fill(r, 0, tracks, h, it.node);
        cr = r; cc = tracks;                   // the row is full: the next wraps
        continue;
      }
      if (cc + w > tracks) { cr++; cc = 0; }
      guard = 0;
      while (!free(cr, cc, w, h) && guard++ < 10000) {
        cc++;
        if (cc + w > tracks) { cr++; cc = 0; }
      }
      fill(cr, cc, w, h, it.node);
      cc += w;
    }
    return rows;
  }

  // The `grid-auto-rows` TRACK LIST for one track count: one entry per row,
  // --sep-row-h where the row's only occupants are horizontal separators and
  // --cell-h everywhere else. Returns null when NO row is separator-only, so a
  // grid without one is left on the plain fixed-row default (no inline style).
  // A row with no occupant at all (an interior hole — RECT/HOLE in `npm run
  // check` owns that defect) keeps --cell-h: a hole is not a thin row.
  function rowTrackList(items, tracks) {
    const rows = rowOccupants(items, tracks);
    let thin = false;
    const out = [];
    for (let r = 0; r < rows.length; r++) {
      const occupants = rows[r] || [];
      const isThin = occupants.length > 0 && occupants.every(isThinRowLeaf);
      if (isThin) thin = true;
      out.push(isThin ? 'var(--sep-row-h)' : 'var(--cell-h)');
    }
    return thin ? out.join(' ') : null;
  }

  // Emit one track list PER COLLAPSE TIER. The placement is a function of the
  // track count (widthAtTier above), so the separator-only rows move as the grid
  // cascades …→2→1. Each tier is computed independently — a separator that SHARES
  // its row with boxes at the authored width but ends up alone at 2 tracks is
  // correctly thin only in that tier's list. Nothing is emitted for a tier with
  // no separator row, so the CSS var() falls back to --cell-h.
  function applyRowTracks(grid, slots, cols) {
    const at = (tracks) => slots.map(slot => {
      const node = slot.pair ? slot.pair[0] : slot.child;
      const span = Math.max(1, Math.min(node.span || 1, cols));
      return { node, w: widthAtTier(span, cols, tracks),
        h: Math.max(1, Math.floor(Number(node.rowspan) || 1)) };
    });
    const tiers = [['--row-tracks', cols], ['--row-tracks-2', 2], ['--row-tracks-1', 1]];
    for (const [prop, tracks] of tiers) {
      if (tracks > cols) continue;                 // no tier widens a grid
      const list = rowTrackList(at(tracks), tracks);
      if (list) grid.style.setProperty(prop, list);
    }
  }

  function sectionHeader(sec) {
    const h = el('div', 'zone-header');
    const t = el('div', 'ztitle'); t.textContent = sec.title || ''; h.appendChild(t);
    const sub = sec.subtitle;
    if (sub) { const s = el('div', 'zsub'); s.textContent = sub; h.appendChild(s); }
    return h;
  }

  // Build the .sec-grid for a set of children with N columns. `columns` is the
  // authored track count; the CSS container queries in index.html do the only
  // responsive stepping (…→2→1 as width tightens). A child that is a SECTION
  // (has `children`) recurses through buildSection; a leaf goes to buildBox. A
  // child may declare `span` to occupy M columns; span is clamped to the
  // section's own columns. span == columns is a full-width BAND (.msp,
  // grid-column:1/-1); a PARTIAL span (1 < span < columns) is .mspan and occupies
  // exactly M tracks (grid-column: span var(--span)), collapsing proportionally.
  // A child may also declare `rowspan` to occupy K rows (.mrsp, grid-row: span
  // var(--rowspan)) — a taller cell. All grid-column/grid-row values are applied
  // by CSS so the container queries can cap them per tier.
  function buildGrid(children, columns, reg) {
    let cols = Math.max(1, Number.isInteger(columns) && columns > 0 ? columns : DEFAULT_SECTION_COLUMNS);
    // `sec-c{N}` (authored column count) lets CSS step each grid's own collapse
    // threshold by how many tracks it actually has to fit, instead of one
    // blanket breakpoint for every grid. `sec-compound` marks a grid that holds
    // at least one NESTED section (a child with its own `children`) rather than
    // only leaf components — a compound grid's cell has to fit a WHOLE nested
    // grid, not just one box, so it is a flex-wrap row of sections while a leaf
    // grid is a row of equal `fr` tracks. This is structural (derived from the data),
    // not hardcoded to any id, so it stays true if the content changes.
    const kids = children || [];
    const isCompound = kids.some(c => Array.isArray(c.children));

    // ── HALF-SLOT PAIRING (the `half` treatment) ────────────────────────────
    // `half` does NOT shrink a cell — it DIVIDES a slot. Two consecutive half
    // leaves are wrapped in ONE `.half-slot`, which is what actually occupies the
    // grid cell: the slot keeps the full --cell-h and the two components split it
    // vertically. That is the whole point of the design: the rectangle stays FULL
    // (no half-empty cell, no hole), the grid's row geometry is untouched, and
    // every row/column invariant (E, L, P, M) keeps measuring one slot per cell
    // exactly as before. The only invariant that has to move is U, which now
    // asserts the height of the SLOT rather than of the component (a half
    // component is legitimately ~half of --cell-h).
    //
    // Pairing is by ADJACENCY in render order, so the author chooses the partner
    // by placement. An odd run and a span disagreement are both rejected at build
    // time (checkHalfPairing in build-data.mjs), so by the time we get here a run
    // of halves is always even and internally consistent.
    //
    // COMPUTED BEFORE the column clamp below ON PURPOSE: a pair is ONE slot, so
    // counting the two halves as two fillable cells would let an over-authored
    // `columns` reserve a dead track (invariant E). The clamp counts SLOTS.
    const ordered = orderedChildren(children);
    const slots = [];
    for (let i = 0; i < ordered.length; i++) {
      if (isHalfLeaf(ordered[i]) && isHalfLeaf(ordered[i + 1])) {
        slots.push({ pair: [ordered[i], ordered[i + 1]] });
        i++;
      } else {
        slots.push({ child: ordered[i] });
      }
    }
    // GROW-WITH-CONTENT / NO RESERVED EMPTY COLUMN. A LEAF grid renders EQUAL
    // `fr` tracks, so an authored column count LARGER than the content needs
    // would reserve empty tracks on the right (a "column vacia"). Clamp a leaf
    // grid's effective column count to what its children can actually fill: the
    // number of single-cell children (each fills one track) OR the widest band's
    // span (a band must keep its declared width), whichever is larger — never
    // above the authored count, never below 1. So a section authored columns:3
    // with only 2 single-cell children grows to just 2 tracks (no empty 3rd),
    // while a columns:2 grid whose content includes a full-width band keeps its 2
    // tracks. A COMPOUND grid is a flex-wrap row (no fixed tracks) so it never
    // reserves an empty track and is left at its authored count. Bands and the
    // sec-c{N} collapse class both derive from this clamped count, so the whole
    // grid stays self-consistent as it cascades …→2→1.
    if (!isCompound && slots.length) {
      let singleCells = 0, maxSpan = 1;
      for (const slot of slots) {
        const c = slot.pair ? slot.pair[0] : slot.child;   // a pair is ONE slot
        const s = Math.max(1, Math.min(c.span || 1, cols));
        if (s === 1) singleCells++; else if (s > maxSpan) maxSpan = s;
      }
      cols = Math.min(cols, Math.max(1, singleCells, maxSpan));
    }
    const classes = ['sec-grid', `sec-c${cols}`];
    if (cols <= 1) classes.push('sec-c1');
    if (isCompound) classes.push('sec-compound');
    const grid = el('div', classes.join(' '));
    grid.style.setProperty('--cols', String(cols));
    // THE SEPARATOR ROW. Only a LEAF grid has row tracks to size (a compound
    // grid is a flex-wrap row of sections), and the clamped `cols` above is the
    // real track count, so this runs here — after the clamp, before the children.
    if (!isCompound) applyRowTracks(grid, slots, cols);

    for (const slot of slots) {
      // A PAIR renders as a .half-slot wrapper holding the two half boxes; the
      // wrapper is the grid cell, so `span`/`rowspan` below apply to IT, and both
      // halves were validated to declare the same span.
      // A single child renders as before: a nested section, or a leaf dispatched
      // on its `type` (separator | rail | box, default box).
      const child = slot.pair ? slot.pair[0] : slot.child;
      let node;
      if (slot.pair) {
        node = el('div', 'half-slot');
        for (const halfChild of slot.pair) node.appendChild(buildBox(halfChild, reg));
      } else {
        node = Array.isArray(child.children) ? buildSection(child, reg)
          : child.type === 'separator' ? buildSeparator(child)
          : child.type === 'rail' ? buildRail(child)
          : buildBox(child, reg);
      }
      // HORIZONTAL MERGE. `span == cols` is a full-width BAND (.msp,
      // grid-column:1/-1 — unchanged: takes its own row edge-to-edge). A PARTIAL
      // span (1 < span < cols) is .mspan and occupies EXACTLY that many tracks via
      // `grid-column: span var(--span)`. --span2 is its PROPORTIONAL effective
      // span at the 2-track collapse tier (round(M/N·2), clamped [1,2]) so the
      // merge keeps its proportion as the grid cascades and only becomes a full
      // band at the 1-column endpoint (the tier CSS lives in index.html).
      const span = Math.max(1, Math.min(child.span || 1, cols));
      // SPAN-WEIGHTED COMPOUND WIDTHS. In a compound grid a nested section's
      // flex-grow IS its --span (CSS: `.sec-grid.sec-compound > .zone { flex:
      // var(--span,1) 1 0 }`), so section width FOLLOWS CONTENT WEIGHT. --span
      // is an INHERITING custom property, so it must be emitted EXPLICITLY on
      // EVERY compound child SECTION — including span:1 — otherwise an unspanned
      // section INHERITS its parent band's --span (e.g. a span:2 envelope band)
      // and renders at the parent's weight, collapsing the intended ratio (a
      // 2:1 GKE-vs-data split) back to equal 50/50 shares. A leaf child never
      // needs this (leaf spans merge grid tracks, below).
      if (isCompound && Array.isArray(child.children)) {
        node.style.setProperty('--span', String(span));
      }
      if (span > 1) {
        node.style.setProperty('--span', String(span));
        if (span >= cols) {
          node.classList.add('msp');                 // full-width band
        } else {
          node.classList.add('mspan');               // partial horizontal merge
          node.style.setProperty('--span2', String(Math.max(1, Math.min(2, Math.round(span / cols * 2)))));
        }
      }
      // VERTICAL MERGE (row-span). A child may occupy K rows (double/triple cell
      // HEIGHT) via `grid-row: span var(--rowspan)` (.mrsp) — the base for a
      // cell-graph where a cell's height encodes magnitude. Mirrors the span/.msp
      // path; column position is untouched, only the row extent grows.
      const rowspan = Math.max(1, Math.floor(Number(child.rowspan) || 1));
      if (rowspan > 1) { node.style.setProperty('--rowspan', String(rowspan)); node.classList.add('mrsp'); }
      grid.appendChild(node);
    }
    return grid;
  }

  // Build a .zone element for a section (a node WITH `children`). Draws its
  // variant frame + optional header, then its children as a .sec-grid. Recurses:
  // a child that is itself a section becomes a nested .zone holding its own
  // grid. This ONE function replaces every former per-shape builder — there is
  // no special-casing by shape anymore.
  function buildSection(sec, reg) {
    // Both axes again: the colour role tints the zone, the treatments decide
    // whether (and how) its frame is drawn at all.
    const classes = ['zone', SECTION_VARIANT[sec.variant] ?? ''];
    for (const t of treatmentsOf(sec)) classes.push(SECTION_TREATMENT[t] ?? '');
    const zone = el('section', classes.filter(Boolean).join(' '), { 'data-zone': sec.id });
    // Titleless container: draw no header when the section declares no
    // title/subtitle — so a pure structural wrapper (e.g. a `plain`
    // stack) shows only its children's frames, with no empty header line.
    if (sec.title || sec.subtitle) zone.appendChild(sectionHeader(sec));
    zone.appendChild(buildGrid(sec.children, sec.columns, reg));
    return zone;
  }

  // ── build the whole page ──
  function buildPage(page, pageIndex) {
    const detailRegistry = {};
    const filters = page.filters || [];

    // `data-page-id` is the page's STABLE IDENTITY on the render. `data-act` is a
    // POSITION, and position is not identity: any tool that joined a rendered
    // `.act` to its manifest entry by INDEX silently mismatched the moment the
    // rendered set differed from the authored set (a dropped page shifts every
    // later act, so a page gets reported under its neighbour's name AND its
    // neighbour's `form` — which then scopes the wrong invariants and reads the
    // wrong authored spans). Stamping the id lets a consumer join by identity.
    // See the id-keyed lookups in tools/validate-layout.cjs (discovery, measure).
    const act = el('section', pageIndex === 0 ? 'act active' : 'act',
      { 'data-act': String(pageIndex), 'data-page-id': String(page.id) });

    // filter chips bar. Default (no selection) MUST show everything, undimmed
    // — so the reset chip ('all') is always present and always the one
    // marked 'on' at load. A page's own data may declare it explicitly
    // (`key: all`, the documented seed pattern); when it does not, the
    // engine synthesizes one rather than falling back to marking a REAL
    // filter chip as if it were selected. That prior fallback (marking chip
    // i===0 'on' when no 'all' key existed) is what caused the
    // default-looks-filtered bug: a chip with real membership showed as
    // active while nothing was actually highlighted, and — since 'all' never
    // existed as a key — clicking any OTHER chip then had no way back to a
    // fully unfiltered view.
    const actbar = el('div', 'actbar');
    actbar.appendChild(el('span', 'spacer'));
    const chips = el('div', 'chips');
    if (!filters.some(f => f.key === 'all')) {
      const allChip = el('button', 'chip on');
      allChip.setAttribute('data-flow', 'all');
      allChip.textContent = 'Todos';
      chips.appendChild(allChip);
    }
    filters.forEach(f => {
      const chip = el('button', 'chip' + (f.key === 'all' ? ' on' : ''));
      chip.setAttribute('data-flow', f.key);
      chip.textContent = f.label;
      chips.appendChild(chip);
    });
    actbar.appendChild(chips);
    act.appendChild(actbar);

    // stage + canvas. The page/root IS a section: page.columns = root columns,
    // page.sections = root children. The root grid lives inside a .sec-plane
    // that FILLS the canvas up to a centered 1280px cap (max-width:1280px;
    // margin-inline:auto) — it fills edge-to-edge at or below the cap and
    // centers with equal side margins above it; columns collapse before a cell
    // degrades, so nothing scrolls sideways. No JS measurement.
    const stage = el('div', 'stage'); stage.setAttribute('data-stage', '');
    const canvas = el('div', 'canvas');
    const plane = el('div', 'sec-plane');
    plane.appendChild(buildGrid(page.sections, page.columns, detailRegistry));
    canvas.appendChild(plane);
    stage.appendChild(canvas);

    // shared detail/flow panel
    const panel = el('div', 'panel'); panel.setAttribute('data-panel', '');
    const phead = el('div', 'p-head');
    const pk = el('span', 'p-kicker'); pk.setAttribute('data-pkicker', ''); phead.appendChild(pk);
    const ph = el('h3'); ph.setAttribute('data-ptitle', ''); phead.appendChild(ph);
    panel.appendChild(phead);
    const psum = el('div', 'p-summary'); psum.setAttribute('data-psummary', ''); panel.appendChild(psum);
    const pf = el('div', 'p-facts'); pf.setAttribute('data-pfacts', ''); panel.appendChild(pf);
    const pn = el('p', 'p-note'); pn.setAttribute('data-pnote', ''); panel.appendChild(pn);
    stage.appendChild(panel);

    act.appendChild(stage);

    return { act, detailRegistry, filters };
  }

  // ── wiring (detail on box click, flow highlight on chip click) ──
  function wireAct(act, detailRegistry, filters) {
    const stage = act.querySelector('[data-stage]');
    const nodes = act.querySelectorAll('[data-k]');
    const chips = act.querySelectorAll('.chip');
    const panelEl = act.querySelector('[data-panel]');
    const panel = {
      kicker: act.querySelector('[data-pkicker]'),
      title: act.querySelector('[data-ptitle]'),
      summary: act.querySelector('[data-psummary]'),
      facts: act.querySelector('[data-pfacts]'),
      note: act.querySelector('[data-pnote]')
    };
    const filterByKey = {};
    for (const f of filters) filterByKey[f.key] = f;

    const closePanel = () => panelEl.classList.remove('show');
    const openPanel = () => panelEl.classList.add('show');

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && panelEl.classList.contains('show')) closePanel();
    });
    stage.addEventListener('click', e => {
      if (e.target.closest('[data-k]') || e.target.closest('[data-panel]')) return;
      closePanel();
    });

    function showDetail(n) {
      const d = detailRegistry[n.dataset.k]; if (!d) return;
      nodes.forEach(x => x.classList.remove('sel'));
      n.classList.add('sel');
      panel.kicker.textContent = d.kicker;
      panel.title.textContent = d.title;
      panel.summary.innerHTML = d.body;
      panel.facts.textContent = d.facts;
      panel.facts.classList.toggle('show', !!d.facts);
      if (d.note) { panel.note.innerHTML = d.note; panel.note.classList.add('show'); }
      else { panel.note.innerHTML = ''; panel.note.classList.remove('show'); }
      openPanel();
    }

    function showFlow(f) {
      panel.kicker.textContent = 'RELATION';
      panel.title.textContent = f.label;
      const steps = f.steps || [];
      panel.summary.innerHTML = '<ol>' + steps.map(s => '<li>' + s + '</li>').join('') + '</ol>';
      panel.facts.textContent = '';
      panel.facts.classList.remove('show');
      panel.note.innerHTML = '';
      panel.note.classList.remove('show');
      openPanel();
    }

    function clearLit() {
      act.querySelectorAll('[data-k],.zone').forEach(e => e.classList.remove('lit'));
    }

    let activeKey = 'all';
    function setFlow(key) {
      // Toggle affordance: clicking the chip that is ALREADY active resets to
      // 'all' instead of re-applying the same filter — the second way back to
      // the unfiltered view, alongside clicking the dedicated 'all' chip.
      if (key !== 'all' && key === activeKey) key = 'all';
      activeKey = key;
      chips.forEach(c => c.classList.toggle('on', c.dataset.flow === key));
      clearLit();
      if (key === 'all') { stage.classList.remove('flowing'); closePanel(); return; }
      stage.classList.add('flowing');
      // A LINEAR SCAN, on purpose: every box in the act re-reads and splits its
      // own data-filters on each click. Measured on a 1968-box deck that scan is
      // 0.6ms of an ~80ms click — the remaining ~76ms is the browser restyling
      // opacity across the deck — so a prebuilt filter→nodes index would buy
      // nothing. A box lights up because IT declares the filter; a zone lights
      // up derivatively, because one of its boxes did.
      const litZones = new Set();
      nodes.forEach(n => {
        const fs = (n.getAttribute('data-filters') || '').split(/\s+/).filter(Boolean);
        if (fs.includes(key)) {
          n.classList.add('lit');
          const z = n.closest('.zone[data-zone]');
          if (z) litZones.add(z);
        }
      });
      litZones.forEach(z => z.classList.add('lit'));
      const f = filterByKey[key];
      if (f) showFlow(f);
    }

    chips.forEach(c => c.addEventListener('click', () => setFlow(c.dataset.flow)));
    nodes.forEach(n => n.addEventListener('click', () => showDetail(n)));

    // ── grab & pan (drag-to-scroll the canvas) ──
    // .canvas is the overflow:auto scroll container (index.html). Wheel/
    // trackpad already scroll it; this adds click-and-drag panning on top,
    // scoped to THIS act's canvas only — chips (in .actbar, outside .canvas)
    // and the panel (a sibling of .canvas on .stage, not inside it) are
    // untouched. Touch already pans natively via the browser's own overflow
    // scrolling and is left alone.
    const canvas = stage.querySelector('.canvas');
    if (canvas) {
      let dragging = false, moved = false, captured = false;
      let startX = 0, startY = 0, startLeft = 0, startTop = 0;
      let suppressClick = false;
      // Configurable via --pan-drag-threshold (index.html): px of pointer travel
      // below which a pointerdown→up is a CLICK (opens the box's detail panel)
      // rather than a PAN.
      const DRAG_THRESHOLD = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pan-drag-threshold')) || 5;

      // Disambiguation: showDetail is bound directly on each box (above) and
      // fires during the click event's bubble/target phase. This listener is
      // registered on .canvas in the CAPTURE phase, so it runs first (capture
      // travels root→target, before the event reaches the box); stopping
      // propagation there prevents the box's own click handler from ever
      // firing for a drag-release, without touching the box wiring itself.
      canvas.addEventListener('click', e => {
        if (suppressClick) { suppressClick = false; e.stopPropagation(); }
      }, true);

      canvas.addEventListener('pointerdown', e => {
        if (e.button !== 0) return; // left button / touch / pen primary only
        dragging = true; moved = false;
        startX = e.clientX; startY = e.clientY;
        startLeft = canvas.scrollLeft; startTop = canvas.scrollTop;
        // NOTE: pointer capture is deliberately NOT taken here. Capturing on
        // pointerdown redirects the subsequent `click` event to the canvas,
        // which starves each box's own click handler and breaks the detail
        // panel on a plain click. Capture is taken lazily, only once real drag
        // motion is detected (below) — a pure click never captures, so it
        // reaches the box normally.
      });

      canvas.addEventListener('pointermove', e => {
        if (!dragging) return;
        const dx = e.clientX - startX, dy = e.clientY - startY;
        if (!moved && Math.hypot(dx, dy) >= DRAG_THRESHOLD) {
          moved = true;
          canvas.classList.add('dragging');
          // Now that this is a genuine drag, capture the pointer so panning
          // keeps tracking even if the cursor leaves the canvas. Safe here: a
          // drag always ends by suppressing the click, so redirecting the
          // click target to the canvas no longer starves any box handler.
          try { canvas.setPointerCapture(e.pointerId); captured = true; } catch (_) { /* ignore */ }
        }
        if (moved) {
          canvas.scrollLeft = startLeft - dx;
          canvas.scrollTop = startTop - dy;
        }
      });

      const endDrag = e => {
        if (!dragging) return;
        dragging = false;
        if (moved) suppressClick = true; // this was a pan, not a click on a box
        canvas.classList.remove('dragging');
        moved = false;
        if (captured && e && e.pointerId !== null && e.pointerId !== undefined) { try { canvas.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ } }
        captured = false;
      };
      canvas.addEventListener('pointerup', endDrag);
      canvas.addEventListener('pointercancel', endDrag);
    }
  }

  // ── mount ──
  const barTitle = document.querySelector('.bar h1');
  const barSub = document.querySelector('.bar .sub');
  const barVer = document.querySelector('.bar .ver');
  if (barTitle && doc.title) barTitle.textContent = doc.title;
  if (barSub && doc.subtitle) barSub.textContent = doc.subtitle;
  // `version` is optional (data/document.yaml) — the node stays empty (and
  // :empty-collapsed, see index.html) when it is absent, so an older seed
  // with no version degrades with no visible change.
  if (barVer && doc.version) barVer.textContent = 'v' + doc.version;
  if (document.title && doc.title) document.title = doc.title;

  const deck = document.getElementById('deck');

  // renderable = manifest pages already filtered (visible:false dropped) and
  // ordered by `order` in the build step; here we only drop unsupported layouts.
  const renderable = doc.pages.filter(p => {
    const ok = (p.layout || 'grid') === 'grid';
    if (!ok) console.warn('[engine] skipping page with unsupported layout:', p.id, p.layout);
    return ok;
  });

  const built = [];
  renderable.forEach((page, i) => {
    const { act, detailRegistry, filters } = buildPage(page, i);
    deck.appendChild(act);
    built.push({ act, detailRegistry, filters, page });
  });

  built.forEach(b => wireAct(b.act, b.detailRegistry, b.filters));

  // ── page navigator ──
  // Page names render as VISIBLE tabs in `order`; the current one is
  // highlighted, the rest dimmed but legible. Click a tab or use the arrows.
  // For 3+ pages a sliding WINDOW (max 3) centered on the selection shows the
  // current page and its neighbours, so the bar never crowds with many tabs.
  const acts = built.map(b => b.act);
  const nameOf = i => (built[i] && (built[i].page.name || built[i].page.id)) || '';
  const pagetabs = document.getElementById('pagetabs');
  const prev = document.getElementById('prev');
  const next = document.getElementById('next');
  const multi = acts.length > 1;
  const WINDOW = 3; // max tabs shown at once
  let current = 0;

  function renderTabs() {
    if (!pagetabs) return;
    pagetabs.innerHTML = '';
    // compute the sliding window [start, end) centered on current
    let start = 0, end = acts.length;
    if (acts.length > WINDOW) {
      start = Math.max(0, Math.min(current - Math.floor(WINDOW / 2), acts.length - WINDOW));
      end = start + WINDOW;
    }
    for (let i = start; i < end; i++) {
      const tab = el('button', 'pagetab' + (i === current ? ' on' : ''));
      tab.textContent = nameOf(i);
      tab.title = nameOf(i);
      tab.addEventListener('click', () => show(i));
      pagetabs.appendChild(tab);
    }
  }

  function show(idx) {
    current = Math.max(0, Math.min(acts.length - 1, idx));
    acts.forEach((a, i) => a.classList.toggle('active', i === current));
    if (document.title && doc.title) document.title = nameOf(current) + ' · ' + doc.title;
    renderTabs();

    const hasPrev = current > 0, hasNext = current < acts.length - 1;
    if (prev) { prev.disabled = !hasPrev; prev.style.display = multi ? '' : 'none';
      prev.title = hasPrev ? 'Previous: ' + nameOf(current - 1) + ' (←)' : 'Previous'; }
    if (next) { next.disabled = !hasNext; next.style.display = multi ? '' : 'none';
      next.title = hasNext ? 'Next: ' + nameOf(current + 1) + ' (→)' : 'Next'; }
  }

  if (prev) prev.addEventListener('click', () => show(current - 1));
  if (next) next.addEventListener('click', () => show(current + 1));
  document.addEventListener('keydown', e => {
    // Suppress page navigation while the help HUD is open so reading is not
    // disrupted (the modal is checked by class, order-independent of its wiring).
    if (document.querySelector('.help-modal.show')) return;
    if (e.key === 'ArrowLeft') show(current - 1);
    else if (e.key === 'ArrowRight') show(current + 1);
  });
  show(0);

  // theme toggle
  const themeToggle = document.getElementById('themeToggle');
  if (themeToggle) themeToggle.addEventListener('click', () => {
    const dark = !document.documentElement.classList.contains('dark');
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  });

  // ── help / tutorial HUD ──
  // A global, static modal (authored in index.html) that explains the diagram's
  // vocabulary. It lives OUTSIDE .deck/.stage/.canvas. Toggled by the H key or
  // the "?" button; Esc / backdrop / × close it. While it is open the ←/→ page
  // navigation is suppressed (see the arrow handler above).
  const helpBackdrop = document.querySelector('[data-help-backdrop]');
  const helpModal = document.querySelector('[data-help-modal]');
  if (helpModal && helpBackdrop) {
    const helpClose = helpModal.querySelector('[data-help-close]');
    const helpBtn = document.getElementById('helpBtn');
    const isOpen = () => helpModal.classList.contains('show');
    const openHelp = () => { helpBackdrop.classList.add('show'); helpModal.classList.add('show'); helpModal.focus(); };
    const closeHelp = () => { helpBackdrop.classList.remove('show'); helpModal.classList.remove('show'); };
    const toggleHelp = () => isOpen() ? closeHelp() : openHelp();

    if (helpBtn) helpBtn.addEventListener('click', toggleHelp);
    if (helpClose) helpClose.addEventListener('click', closeHelp);
    helpBackdrop.addEventListener('click', closeHelp);
    document.addEventListener('keydown', e => {
      const t = e.target || {};
      const tag = (t.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || t.isContentEditable) return;
      if ((e.key === 'h' || e.key === 'H') && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault(); toggleHelp();
      } else if (e.key === 'Escape' && isOpen()) {
        e.preventDefault(); closeHelp();
      }
    });

    // Auto-open once, on the first visit only (localStorage flag); thereafter
    // the HUD opens only on demand (H / "?").
    try {
      if (!localStorage.getItem('help-seen')) { openHelp(); localStorage.setItem('help-seen', '1'); }
    } catch (_) { /* localStorage blocked (e.g. file:// hardening) — skip auto-open */ }
  }
})();
