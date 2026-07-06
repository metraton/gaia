// ─────────────────────────────────────────────────────────────────────────
// engine.js — data-driven render engine for a diagram deck.
// @version 1.0.0  (vendored by the diagram-builder Gaia skill; keep in sync
//                  with skills/diagram-builder/GLOSSARY.md + reference.md)
//
// Reads window.__DOC__ (produced by build-data.mjs from the YAML manifest +
// page files) and builds the DOM. No framework, no build step beyond the
// YAML→JS transform. Plain DOM.
//
// It knows only the dialect (document / page / section / subsection /
// component / filter) — every domain-specific string lives in the data.
//
// Layout: only `grid` is implemented (the SVG-absolute engine is retired).
// A page with any other layout is skipped with a console warning, so the
// deck degrades instead of throwing.
//
// Stable ids + order are preserved end-to-end so a future edit mode can
// overlay a localStorage {id: order} map without touching this engine or
// the YAML — see the dialect reference. NOT implemented here.
// ─────────────────────────────────────────────────────────────────────────
(function () {
  'use strict';

  const doc = window.__DOC__;
  if (!doc || !Array.isArray(doc.pages)) {
    console.error('[engine] window.__DOC__ missing or malformed; nothing to render.');
    return;
  }

  // ── variant → CSS class maps (mirror the classes already in index.html) ──
  const COMPONENT_VARIANT = {
    normal: '', crit: 'crit', warn: 'warn', ok: 'ok',
    strong: 'strong', ext: 'ext', store: 'store'
  };
  const ZONE_VARIANT = {
    normal: '', danger: 'danger', safe: 'safe', envelope: 'envelope'
  };

  // Default column count for a section's direct-component grid: pack components
  // two-per-row by default (when a section has >=2 components), so a lone
  // component stays 1 col but a pair sits side by side. Parametrizable; a
  // section may override with its own `columns`.
  const DEFAULT_SECTION_COLUMNS = 2;

  const el = (tag, cls, attrs) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };

  function componentClasses(comp) {
    const parts = ['box'];
    const v = COMPONENT_VARIANT[comp.variant] ?? '';
    if (v) parts.push(v);
    for (const extra of comp.variant_extra || []) {
      const ev = COMPONENT_VARIANT[extra] ?? '';
      if (ev && !parts.includes(ev)) parts.push(ev);
    }
    return parts.join(' ');
  }

  function orderedComponents(list) {
    // Explicit `order` wins; otherwise list order. Stable sort so ties keep
    // their declared order — this is exactly the hook a future edit mode
    // overrides.
    return [...(list || [])]
      .map((c, i) => ({ c, i }))
      .sort((a, b) => {
        const oa = a.c.order ?? (a.i + 1), ob = b.c.order ?? (b.i + 1);
        return oa === ob ? a.i - b.i : oa - ob;
      })
      .map(x => x.c);
  }

  // Build one .box for a component. Also fills the detail registry so the
  // panel can look it up on click by data-k. (`kicker` is the presentation
  // eyebrow line that renders the component's `status`.)
  function buildBox(comp, detailRegistry) {
    const box = el('div', componentClasses(comp), { 'data-k': comp.id });
    if (comp.status) { const k = el('div', 'k'); k.textContent = comp.status; box.appendChild(k); }
    const t = el('div', 't'); t.textContent = comp.title || ''; box.appendChild(t);
    const desc = comp.description;
    const lines = Array.isArray(desc) ? desc : (desc != null ? [desc] : []);
    for (const line of lines) { const m = el('div', 'm'); m.textContent = line; box.appendChild(m); }

    // filter membership → data attribute for the inverted index
    if (Array.isArray(comp.filters) && comp.filters.length) {
      box.setAttribute('data-filters', comp.filters.join(' '));
    }

    detailRegistry[comp.id] = {
      kicker: comp.status || '',
      title: comp.title || '',
      facts: lines.join(' · '),
      // detail falls back to joined description when absent
      body: comp.detail || lines.join('<br>'),
      note: comp.note || ''
    };
    return box;
  }

  // Build the .zone-grid for a set of components with N columns.
  // A component may declare `span` to occupy N interior columns (horizontal
  // span). This is native CSS `grid-column: span N` — the .zone-grid is already
  // a CSS grid, and inline-axis span does not touch the row-track sizing that
  // the mosaic avoids grid for, so it is safe here (leaf boxes, single-pass).
  // Span is clamped to the effective column count; span 1 is the default and
  // sets nothing (back-compat: existing pages render unchanged).
  function buildGrid(components, columns, detailRegistry) {
    const ordered = orderedComponents(components);
    // effective columns: never reserve empty tracks (see the dialect reference)
    const cols = Math.max(1, Math.min(columns || 1, ordered.length || 1));
    const grid = el('div', 'zone-grid');
    grid.style.setProperty('--cols', String(cols));
    for (const comp of ordered) {
      const node = buildBox(comp, detailRegistry);
      const span = Math.max(1, Math.min(comp.span || 1, cols));
      if (span > 1) node.style.gridColumn = 'span ' + span;
      grid.appendChild(node);
    }
    return grid;
  }

  function zoneHeader(sec) {
    const h = el('div', 'zone-header');
    const t = el('div', 'ztitle'); t.textContent = sec.title || sec.label || ''; h.appendChild(t);
    const sub = sec.subtitle || sec.sublabel;
    if (sub) { const s = el('div', 'zsub'); s.textContent = sub; h.appendChild(s); }
    return h;
  }

  // Effective content columns a section renders at its widest — how many
  // component columns wide it actually is. Drives the content-width sizing cap
  // (a narrow section should not stretch to fill an over-wide mosaic column).
  function contentCols(sec) {
    if (Array.isArray(sec.subsections) && sec.subsections.length) {
      let m = 1;
      for (const sub of sec.subsections) {
        const n = (sub.components || []).length;
        m = Math.max(m, Math.min(sub.columns ?? 2, n || 1));
      }
      return m;
    }
    const n = Array.isArray(sec.components) ? sec.components.length : 0;
    return Math.max(1, Math.min(sec.columns ?? DEFAULT_SECTION_COLUMNS, n || 1));
  }

  // ── intrinsic width (ANALYTIC, deterministic) ────────────────────────────
  // The width a cell WANTS so its declared columns each render at --box-min.
  // Computed from the data model, not measured from the DOM: measuring the
  // min-content of a `minmax(box-min,1fr)` grid through the envelope's flex
  // chain is unreliable (it collapses), so we derive the number directly from
  // the same token math the CSS uses. Read the spacing tokens once.
  function tokens() {
    const r = getComputedStyle(document.documentElement);
    const num = (v, d) => { const n = parseFloat(r.getPropertyValue(v)); return Number.isFinite(n) ? n : d; };
    return { boxMin: num('--box-min', 280), s2: num('--s-2', 8), s3: num('--s-3', 16) };
  }
  // Width of a non-envelope zone = effective columns × box-min + inner gaps +
  // zone padding (both sides). Matches the .zone-grid + .zone padding in CSS.
  function zoneContentWidth(sec, t) {
    const cols = contentCols(sec);
    return cols * t.boxMin + (cols - 1) * t.s2 + 2 * t.s3;
  }
  // Width of a top-level cell. Envelopes recurse over their wrapped children by
  // shape: banded = widest row (sum of the row's child widths + gaps); columns
  // = sum of each column's widest child; flat = main + side-stack (max of rest).
  // Plus the envelope's own zone padding.
  function intrinsicWidth(sec, byId, t) {
    if (sec.variant === 'envelope' && Array.isArray(sec.wraps)) {
      const wraps = sec.wraps;
      const banded = Number.isInteger(sec.columns) && sec.columns > 0;
      const columnsMode = wraps.length > 0 && Array.isArray(wraps[0]);
      let inner = 0;
      if (banded) {
        const rows = new Map();
        for (const id of wraps) {
          const c = byId[id]; if (!c) continue;
          const r = (c.layout || {}).row ?? 1;
          if (!rows.has(r)) rows.set(r, []);
          rows.get(r).push(c);
        }
        for (const arr of rows.values()) {
          let w = 0; arr.forEach((c, i) => { w += zoneContentWidth(c, t) + (i ? t.s2 : 0); });
          inner = Math.max(inner, w);
        }
      } else if (columnsMode) {
        let n = 0;
        for (const colIds of wraps) {
          let cw = 0;
          for (const id of colIds) { const c = byId[id]; if (c) cw = Math.max(cw, zoneContentWidth(c, t)); }
          if (cw) { inner += cw + (n ? t.s2 : 0); n++; }
        }
      } else {
        const main = byId[wraps[0]] ? zoneContentWidth(byId[wraps[0]], t) : 0;
        let side = 0;
        for (let i = 1; i < wraps.length; i++) { const c = byId[wraps[i]]; if (c) side = Math.max(side, zoneContentWidth(c, t)); }
        inner = main + (side ? t.s2 + side : 0);
      }
      return inner + 2 * t.s3;
    }
    return zoneContentWidth(sec, t);
  }

  // Build a .zone element for a section (non-envelope). If it has subsections,
  // each becomes its own header+grid; otherwise its direct components render as
  // a grid whose column count defaults to DEFAULT_SECTION_COLUMNS (min 2 cols
  // when there are >=2 components; a lone component stays 1 col). `columns`
  // overrides the default.
  function buildZone(sec, detailRegistry) {
    const vclass = ZONE_VARIANT[sec.variant] ?? '';
    const hasSubs = Array.isArray(sec.subsections) && sec.subsections.length;
    const dcCount = Array.isArray(sec.components) ? sec.components.length : 0;
    // direct-component grid column count: min(default|override, component count)
    const dcCols = hasSubs ? 0 : Math.max(1, Math.min(sec.columns ?? DEFAULT_SECTION_COLUMNS, dcCount || 1));
    // `single` (forces 1 col, exempt from responsive stepping) applies only
    // when the zone truly renders a single column.
    const single = !hasSubs && dcCols <= 1;
    const zone = el('section',
      ['zone', vclass, single ? 'single' : ''].filter(Boolean).join(' '),
      { 'data-zone': sec.id });
    // Titleless container: draw no header at all when the section declares no
    // title/subtitle/label — so a merged wrapper shows only its sub-section
    // headers, with no empty/redundant header line.
    if (sec.title || sec.subtitle || sec.label) zone.appendChild(zoneHeader(sec));

    if (hasSubs) {
      // A single subsection is just the zone's implicit grid container — the
      // zone header already labels it, so no sub-header is drawn. With 2+
      // subsections each gets its own labeled sub-header.
      const multi = sec.subsections.length > 1;
      for (const sub of sec.subsections) {
        if (multi && (sub.label || sub.sublabel)) {
          const subHead = el('div', 'zone-header sub-header');
          const st = el('div', 'ztitle'); st.textContent = sub.label || ''; subHead.appendChild(st);
          if (sub.sublabel) { const ss = el('div', 'zsub'); ss.textContent = sub.sublabel; subHead.appendChild(ss); }
          zone.appendChild(subHead);
        }
        zone.appendChild(buildGrid(sub.components, sub.columns ?? 2, detailRegistry));
      }
    } else {
      zone.appendChild(buildGrid(sec.components, dcCols, detailRegistry));
    }
    return zone;
  }

  // Build an envelope zone that wraps other sections named in `wraps`.
  // Two shapes of `wraps` are supported:
  //   (a) flat list of ids  → LEGACY: first = wide main column (env-main), rest
  //       stacked in one narrow lateral column (env-side-group).
  //   (b) list of lists     → COLUMNS grid: each inner list is one envelope
  //       column that stacks its sub-zones vertically. N sibling columns that
  //       collapse col1→col2→col3 to a single column when narrow.
  function buildEnvelope(sec, sectionsById, detailRegistry, consumed) {
    const t = tokens();
    const zone = el('section', 'zone envelope env', { 'data-zone': sec.id });
    zone.appendChild(zoneHeader(sec));
    const body = el('div', 'env-body');
    const env = sec.wraps || [];
    const columnsMode = env.length > 0 && Array.isArray(env[0]);

    // BANDED mode (envelope declares `columns: N`): the wrapped sections are
    // laid out as a mosaic INSIDE the envelope — grouped into rows by
    // `layout.row`, each spanning `layout.span` of the envelope's N columns
    // (same vocabulary as the top-level mosaic). A full-width band is span=N;
    // a 2-col row is two span-1 cells. Rendered as measured flex-rows (NOT
    // CSS-grid auto-rows): a flex row's cross-size is a post-layout measurement
    // of its children's final height, so it is immune to the row-track
    // under-measure that made the top-level mosaic avoid CSS grid.
    if (Number.isInteger(sec.columns) && sec.columns > 0) {
      const columns = sec.columns;
      body.classList.add('env-rows');
      body.style.setProperty('--env-cols', String(columns));
      const children = env.map(id => sectionsById[id]).filter(Boolean);
      // order by section.order (explicit wins, else list order) — same stable
      // rule as elsewhere — then bucket by layout.row.
      const ordered = children
        .map((s, i) => ({ s, i }))
        .sort((a, b) => {
          const oa = a.s.order ?? (a.i + 1), ob = b.s.order ?? (b.i + 1);
          return oa === ob ? a.i - b.i : oa - ob;
        })
        .map(x => x.s);
      const rowsByRow = new Map();
      for (const child of ordered) {
        consumed.add(child.id);
        const r = (child.layout || {}).row ?? 1;
        if (!rowsByRow.has(r)) rowsByRow.set(r, []);
        rowsByRow.get(r).push(child);
      }
      const sortedRows = [...rowsByRow.keys()].sort((a, b) => a - b);
      for (const r of sortedRows) {
        const rowEl = el('div', 'env-row');
        for (const child of rowsByRow.get(r)) {
          const span = Math.max(1, Math.min((child.layout || {}).span || 1, columns));
          const cell = buildZone(child, detailRegistry);
          cell.classList.add('env-cell');
          cell.style.setProperty('--span', String(span));
          // flex-basis = the child's own content width; flex-grow = span, so a
          // band splits the leftover by span while each cell keeps room for its
          // real columns (deterministic — no reliance on grid min-content
          // propagating through the flex chain).
          cell.style.flexBasis = zoneContentWidth(child, t) + 'px';
          rowEl.appendChild(cell);
        }
        body.appendChild(rowEl);
      }
      zone.appendChild(body);
      return zone;
    }

    if (columnsMode) {
      body.classList.add('env-grid');
      env.forEach(colIds => {
        const col = el('div', 'env-col');
        let colW = 0;
        (colIds || []).forEach(cid => {
          const child = sectionsById[cid];
          if (!child) { console.warn('[engine] envelope column references unknown section:', cid); return; }
          consumed.add(cid);
          colW = Math.max(colW, zoneContentWidth(child, t));
          col.appendChild(buildZone(child, detailRegistry));
        });
        if (col.children.length) { col.style.flexBasis = colW + 'px'; body.appendChild(col); }
      });
      zone.appendChild(body);
      return zone;
    }

    // legacy flat mode: main + one stacked side group
    const sideGroup = el('div', 'env-side-group');
    let sideW = 0;
    env.forEach((childId, idx) => {
      const child = sectionsById[childId];
      if (!child) { console.warn('[engine] envelope references unknown section:', childId); return; }
      consumed.add(childId);
      const childZone = buildZone(child, detailRegistry);
      if (idx === 0) { childZone.classList.add('env-main'); childZone.style.flexBasis = zoneContentWidth(child, t) + 'px'; body.appendChild(childZone); }
      else { sideW = Math.max(sideW, zoneContentWidth(child, t)); sideGroup.appendChild(childZone); }
    });
    if (sideGroup.children.length) { sideGroup.style.flexBasis = sideW + 'px'; body.appendChild(sideGroup); }
    zone.appendChild(body);
    return zone;
  }

  // ── MOSAIC MASONRY LAYOUT (intrinsic-width) ──────────────────────────────
  // Position the top-level mosaic cells (.mos-cell inside .mos-plane). Each
  // cell is sized to its INTRINSIC content width (its declared columns ×
  // box-min + gaps) — NOT to a fraction of the page — so a section always holds
  // the columns its author declared. Cells flow left→right within their row by
  // actual width; the column index still drives the vertical masonry, so a
  // short cell in row 1 gets a row-2 cell packed directly beneath it. When the
  // widest row exceeds the canvas the plane grows and the canvas scrolls
  // horizontally (see index.html); the plane is margin-inline:auto so a diagram
  // narrower than the canvas is centered. Column count is AUTHORITATIVE: it is
  // never reduced by viewport width here — only the coarse, whole-diagram
  // tablet/phone breakpoints (mos-stacked / mos-narrow / mos-mono) do that.
  //
  // Why measure, not CSS-grid: a cell can be an envelope whose nested inline-
  // size container query changes its height once it lands at its real width; a
  // measured pass reads the FINAL laid-out height and cannot under-size it.
  function layoutMosaic(canvas) {
    if (!canvas || !canvas.classList.contains('mosaic')) return;
    const plane = canvas.querySelector('.mos-plane');
    if (!plane) return;
    const cells = [...plane.children].filter(n => n.classList.contains('mos-cell'));
    if (!cells.length) return;
    // Inactive acts are display:none → zero width; nothing to measure yet.
    if (canvas.clientWidth === 0) return;

    const columns = parseInt(canvas.dataset.mosCols, 10) || 1;
    const gap = parseFloat(getComputedStyle(plane).getPropertyValue('--mos-gap')) || 24;

    // Responsive breakpoints are read from CSS custom properties (configurable,
    // single source of truth) and measured from the STAGE's OWN width (it is the
    // responsive container), so a narrow split-screen pane collapses even when
    // the window is wide.
    const stage = canvas.closest('[data-stage]');
    const stageW = stage ? stage.clientWidth : canvas.clientWidth;
    const rootCS = getComputedStyle(document.documentElement);
    const bpTablet = parseFloat(rootCS.getPropertyValue('--bp-tablet')) || 768;
    const bpPhone = parseFloat(rootCS.getPropertyValue('--bp-phone')) || 480;

    // Coarse, whole-diagram responsive collapse (NOT the old per-zone collapse):
    // phone → every grid to 1 column; tablet band → grids capped at 2 columns;
    // both stack the top-level mosaic into a single column. Desktop (> tablet)
    // honors the author's declared columns with no width-driven reduction.
    canvas.classList.toggle('mos-mono', stageW <= bpPhone);
    canvas.classList.toggle('mos-narrow', stageW > bpPhone && stageW <= bpTablet);
    const stacked = stageW <= bpTablet;
    canvas.classList.toggle('mos-stacked', stacked);

    if (stacked) {
      for (const c of cells) { c.style.position = ''; c.style.left = ''; c.style.top = ''; c.style.width = ''; }
      plane.style.height = ''; plane.style.width = '';
      return;
    }

    // Each cell's INTRINSIC width is the analytic value computed at build time
    // (data-mos-w) — declared columns × box-min + gaps + padding — not a DOM
    // measurement (min-content collapses through the envelope's flex chain).
    const intrinsic = cells.map(c => parseFloat(c.dataset.mosW) || 0);
    const colOf = i => parseInt(cells[i].dataset.mosCol, 10) || 0;
    const spanOf = i => parseInt(cells[i].dataset.mosSpan, 10) || 1;

    // COLUMN-CONSISTENT widths: a column's width is the max intrinsic of every
    // cell that occupies it, ACROSS ROWS — so a wide cell in row 2 (e.g. a
    // 2-column cell) reserves that width in its column for row 1 too, and the
    // neighbouring cell (the envelope) never gets overlapped. Single-span
    // cells set their column's floor first; multi-span cells then top up any
    // deficit across the columns they cover.
    const colW = new Array(columns).fill(0);
    for (let i = 0; i < cells.length; i++)
      if (spanOf(i) === 1) colW[colOf(i)] = Math.max(colW[colOf(i)], intrinsic[i]);
    for (let i = 0; i < cells.length; i++) {
      const span = spanOf(i); if (span <= 1) continue;
      const col = colOf(i);
      let have = (span - 1) * gap;
      for (let k = col; k < col + span; k++) have += colW[k];
      if (intrinsic[i] > have) {
        const add = (intrinsic[i] - have) / span;
        for (let k = col; k < col + span; k++) colW[k] += add;
      }
    }
    const colX = new Array(columns).fill(0);
    for (let c = 1; c < columns; c++) colX[c] = colX[c - 1] + colW[c - 1] + gap;

    // Place: left = the cell's column offset; width = its intrinsic (a cell
    // narrower than its reserved column span is left-aligned, leaving slack —
    // e.g. a lone-component section in a column widened by its row-mate). Pack
    // columns upward by column index so a row-2 cell lands under its row-1
    // neighbour.
    const colBottom = new Array(columns).fill(0);
    let maxBottom = 0;
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      const col = colOf(i), span = spanOf(i);
      let reserved = (span - 1) * gap;
      for (let k = col; k < col + span; k++) reserved += colW[k];
      const w = Math.min(intrinsic[i], reserved);
      let y = 0;
      for (let k = col; k < col + span; k++) y = Math.max(y, colBottom[k] || 0);
      c.style.position = 'absolute';
      c.style.left = colX[col] + 'px';
      c.style.top = y + 'px';
      c.style.width = w + 'px';
      const bottom = y + c.offsetHeight;
      for (let k = col; k < col + span; k++) colBottom[k] = bottom + gap;
      if (bottom > maxBottom) maxBottom = bottom;
    }
    plane.style.width = (colX[columns - 1] + colW[columns - 1]) + 'px';
    plane.style.height = maxBottom + 'px';
  }

  // ── build the whole page ──
  function buildPage(page, pageIndex) {
    const detailRegistry = {};
    const filters = page.filters || [];

    const act = el('section', pageIndex === 0 ? 'act active' : 'act', { 'data-act': String(pageIndex) });

    // filter chips bar
    const actbar = el('div', 'actbar');
    actbar.appendChild(el('span', 'spacer'));
    const chips = el('div', 'chips');
    filters.forEach((f, i) => {
      const chip = el('button', 'chip' + (f.key === 'all' || (i === 0 && !filters.some(x => x.key === 'all')) ? ' on' : ''));
      chip.setAttribute('data-flow', f.key);
      chip.textContent = f.label;
      chips.appendChild(chip);
    });
    actbar.appendChild(chips);
    act.appendChild(actbar);

    // stage + canvas
    const stage = el('div', 'stage'); stage.setAttribute('data-stage', '');
    const canvas = el('div', 'canvas');

    const sections = page.sections || [];
    const sectionsById = {};
    for (const s of sections) sectionsById[s.id] = s;

    // Sections consumed as children of an envelope are not top-level cells.
    // Precomputed from the data (wraps, flat or list-of-lists) BEFORE the
    // build loop, so a section always knows whether it is a cell regardless of
    // the order envelopes happen to be visited in.
    const consumed = new Set();
    for (const sec of sections) {
      if (sec.variant === 'envelope' && Array.isArray(sec.wraps)) {
        for (const item of sec.wraps) {
          if (Array.isArray(item)) item.forEach(id => consumed.add(id));
          else consumed.add(item);
        }
      }
    }

    // Top-level cells are the non-consumed sections, ordered by `section.order`
    // (explicit wins, else list order — same stable rule as orderedComponents).
    // This DOM order IS the single-column collapse order: when the mosaic
    // narrows to one column, the cells stack top-to-bottom in this sequence.
    const cells = [...sections]
      .map((s, i) => ({ s, i }))
      .filter(x => !consumed.has(x.s.id))
      .sort((a, b) => {
        const oa = a.s.order ?? (a.i + 1), ob = b.s.order ?? (b.i + 1);
        return oa === ob ? a.i - b.i : oa - ob;
      })
      .map(x => x.s);

    const buildCell = sec => (sec.variant === 'envelope' && Array.isArray(sec.wraps))
      ? buildEnvelope(sec, sectionsById, detailRegistry, consumed)
      : buildZone(sec, detailRegistry);

    // MOSAIC MODE (page.columns ≥ 1): the canvas is a `columns`-wide grid;
    // each cell declares `layout: {row, span}` — the row it belongs to and how
    // many columns it stretches across (Excel-style). Span is clamped to the
    // column count so a cell never overflows the grid. Horizontal placement is
    // fixed by the data; VERTICAL placement is MASONRY: each column packs upward
    // independently (see layoutMosaic below), so a short cell no longer leaves a
    // tall gap under it and the cell beneath falls up to the first free slot in
    // its column(s). When the stage is narrow, the mosaic collapses to a single
    // stacked column in DOM (= order) order — the recursive collapse principle
    // one level up from the intra-zone grids.
    const columns = page.columns;
    const useMosaic = Number.isInteger(columns) && columns > 0;

    if (useMosaic) {
      canvas.classList.add('mosaic');
      canvas.style.setProperty('--mos-cols', String(columns));
      canvas.dataset.mosCols = String(columns);
      // Cells are laid out by the JS masonry pass `layoutMosaic`, NOT by CSS
      // Grid and NOT by `grid-template-rows:masonry` — for the same reason the
      // previous flex-row builder existed. A mosaic cell can be an envelope
      // whose OWN inline-size container query flips its body side-by-side→
      // stacked once it lands in a narrow column, growing its rendered height
      // well past what a grid track's pre-query sizing pass measured. CSS
      // Grid's `auto` track-sizing does not re-measure after such a nested
      // container query resolves, so the track is left short and the taller
      // cell visually overflows into the next row. `grid-template-rows:masonry`
      // is a grid feature and inherits the same pre-sizing flaw, so it is out
      // too. layoutMosaic instead reads each cell's FINAL rendered
      // `offsetHeight` AFTER its width (hence all its container queries) is
      // applied — a plain post-layout measurement that always matches reality —
      // and uses it to pack the columns.
      //
      // Bucket cells by declared `row`, then emit them into a positioning
      // `.mos-plane` in (row asc, order within row) order — the same reading
      // order the flex rows used, so the single-column collapse order is
      // unchanged. `cells` arrives pre-sorted by `order`, so each row bucket
      // preserves order within itself. A cell's STARTING COLUMN is the running
      // sum of the spans of the cells before it in its row (data-mos-col); its
      // width is its own span (data-mos-span). Repositioning a cell stays a
      // one-field change to `row`/`span`/`order` — no markup surgery.
      const rowsByRow = new Map();
      for (const sec of cells) {
        const row = (sec.layout || {}).row ?? 1;
        if (!rowsByRow.has(row)) rowsByRow.set(row, []);
        rowsByRow.get(row).push(sec);
      }
      const sortedRows = [...rowsByRow.keys()].sort((a, b) => a - b);
      const plane = el('div', 'mos-plane');
      const tk = tokens();
      for (const row of sortedRows) {
        let col = 0;
        for (const sec of rowsByRow.get(row)) {
          const m = sec.layout || {};
          // Top-level span DEFAULTS to the section's own effective column count
          // (min(columns ?? 2, items)); an explicit layout.span overrides. This
          // keeps a cell's mosaic width consistent with the columns it must
          // render — the author sets one number (`columns`) and cannot silently
          // contradict its own width.
          const derived = m.span ?? contentCols(sec);
          const span = Math.max(1, Math.min(derived, columns));
          // Defensive: keep a cell inside the grid if a row's spans over-sum.
          if (col + span > columns) col = Math.max(0, columns - span);
          const node = buildCell(sec);
          node.classList.add('mos-cell');
          node.style.setProperty('--mos-span', String(span));
          node.dataset.mosRow = String(row);
          node.dataset.mosCol = String(col);
          node.dataset.mosSpan = String(span);
          // Analytic intrinsic width (declared columns × box-min + gaps + pad),
          // stashed for layoutMosaic. No content-cap: a section's width IS its
          // column width — it never stretches to fill and never collapses below
          // its columns; the diagram grows / scrolls instead.
          node.dataset.mosW = String(intrinsicWidth(sec, sectionsById, tk));
          plane.appendChild(node);
          col += span;
        }
      }
      canvas.appendChild(plane);
    } else {
      // LEGACY positional mode (position: left|right|center). Kept so a
      // page without `columns` renders exactly as before.
      const posWrap = {};
      for (const sec of cells) {
        const node = buildCell(sec);
        const pos = sec.position;
        if (pos === 'left' || pos === 'right') {
          if (!posWrap[pos]) {
            posWrap[pos] = el('div', 'pos-' + pos);
            canvas.appendChild(posWrap[pos]);
          }
          posWrap[pos].appendChild(node);
        } else {
          node.classList.add('pos-center');
          canvas.appendChild(node);
        }
      }
    }

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
      panel.kicker.textContent = 'FLOW';
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

    function setFlow(key) {
      chips.forEach(c => c.classList.toggle('on', c.dataset.flow === key));
      clearLit();
      if (key === 'all') { stage.classList.remove('flowing'); closePanel(); return; }
      stage.classList.add('flowing');
      // inverted index: a component/zone lights up because IT declares the filter
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
  }

  // ── mount ──
  const bar = document.querySelector('.bar');
  const barTitle = document.querySelector('.bar h1');
  const barSub = document.querySelector('.bar .sub');
  if (barTitle && doc.title) barTitle.textContent = doc.title;
  if (barSub && doc.subtitle) barSub.textContent = doc.subtitle;
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

  // ── mosaic relayout wiring ──
  // Masonry positions cells from live geometry, so re-run it whenever the
  // visible mosaic's size can change: on page switch (a hidden act had zero
  // width and could not be measured), on stage resize, and once fonts settle
  // (a late font swap changes cell heights). Coalesced to one rAF per frame.
  function relayoutMosaics() {
    document.querySelectorAll('.act.active .canvas.mosaic').forEach(layoutMosaic);
  }
  let relayoutRaf = 0;
  function scheduleRelayout() {
    if (relayoutRaf) cancelAnimationFrame(relayoutRaf);
    relayoutRaf = requestAnimationFrame(() => { relayoutRaf = 0; relayoutMosaics(); });
  }
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(scheduleRelayout);
    document.querySelectorAll('.stage').forEach(s => ro.observe(s));
  }
  window.addEventListener('resize', scheduleRelayout);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(relayoutMosaics);

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

    // The newly active act's mosaic could not be measured while hidden — lay it
    // out now that it has width.
    relayoutMosaics();
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
  // vocabulary. It lives OUTSIDE .deck/.stage/.canvas, so the mosaic masonry
  // pass (layoutMosaic) and its ResizeObserver never see it. Toggled by the H
  // key or the "?" button; Esc / backdrop / × close it. While it is open the
  // ←/→ page navigation is suppressed (see the arrow handler above).
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
