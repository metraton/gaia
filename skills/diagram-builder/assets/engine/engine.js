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
// measurement pass. The layout is a UNIFORM-CELL spreadsheet grid: every leaf
// cell is a fixed --cell-w × --cell-h; a section is an integer number of those
// cells wide. Responsive behaviour is pure CSS (stage container queries in
// index.html): a leaf grid's column count cascades 3→2→1 as width tightens
// (2-column "two-table" is the intermediate step; 1 column is the endpoint,
// where the whole page is a single vertical stack). A `span` merges cells
// (Excel-style); `span == columns` is a full-width band that takes its own row.
// Cells never resize — only the track count changes — so nothing scrolls
// sideways at the stacked tiers. The engine tags each grid `sec-c{N}` (authored
// column count) and `sec-compound` (holds nested sections) so the CSS can step
// each grid by its real width need; it emits `--cols` + `--span` and never a
// literal grid-column (the container queries own the collapse).
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

  // ── variant → CSS class maps (mirror the classes already in index.html) ──
  const COMPONENT_VARIANT = {
    normal: '', crit: 'crit', warn: 'warn', ok: 'ok',
    strong: 'strong', ext: 'ext', store: 'store'
  };
  // Section variants: normal (dashed zone), danger/safe (colored zone),
  // envelope (borderless dashed container that groups nested sections), plain
  // (a bare, border-free structural wrapper — used to stack sub-sections in one
  // parent column with no extra frame).
  const ZONE_VARIANT = {
    normal: '', danger: 'danger', safe: 'safe', envelope: 'envelope', plain: 'plain'
  };

  // Default column count for a section's grid when it omits `columns`.
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
  // is the presentation eyebrow line that renders the component's `status`.)
  function buildBox(comp, detailRegistry) {
    const box = el('div', componentClasses(comp), { 'data-k': comp.id });
    if (comp.status) { const k = el('div', 'k'); k.textContent = comp.status; box.appendChild(k); }
    const t = el('div', 't'); t.textContent = comp.title || ''; box.appendChild(t);
    const rawDesc = comp.description;
    const lines = Array.isArray(rawDesc) ? rawDesc : (rawDesc != null ? [rawDesc] : []);
    // Description lines live in ONE `.desc` container so CSS can clamp the whole
    // description to a fixed number of visual lines (see .box .desc line-clamp),
    // keeping every box at the same fixed --cell-h regardless of line count. The
    // full text is always available in the click-through detail panel.
    if (lines.length) {
      const descBox = el('div', 'desc');
      for (const line of lines) { const m = el('div', 'm'); m.textContent = line; descBox.appendChild(m); }
      box.appendChild(descBox);
    }

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

  // Build a `separator` component (a leaf, `type: separator`): a minimal divider
  // LINE — NOT a box (no border/padding). Props: `orientation`
  // (horizontal|vertical, default horizontal), `style` (solid|dotted, default
  // solid), optional `text`. A horizontal separator is a thin full-width rule
  // across its grid cell(s); with `text` the label renders centered/inline on
  // the line (muted, small). A vertical separator is a thin vertical rule. Span
  // is honored by the caller (buildGrid) exactly like any component. Not
  // clickable — no detail registry entry.
  function buildSeparator(sep) {
    const orient = sep.orientation === 'vertical' ? 'v' : 'h';
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
  // status/description/detail). `orientation: vertical` renders the title
  // rotated (vertical text) for swimlane labeling; default horizontal is a slim
  // title-only box. Span is honored by the caller. Not clickable.
  function buildRail(rail) {
    const orient = rail.orientation === 'vertical' ? 'v' : 'h';
    const node = el('div', `rail rail-${orient}`);
    const t = el('div', 'rail-title'); t.textContent = rail.title || ''; node.appendChild(t);
    return node;
  }

  function sectionHeader(sec) {
    const h = el('div', 'zone-header');
    const t = el('div', 'ztitle'); t.textContent = sec.title || sec.label || ''; h.appendChild(t);
    const sub = sec.subtitle || sec.sublabel;
    if (sub) { const s = el('div', 'zsub'); s.textContent = sub; h.appendChild(s); }
    return h;
  }

  // Build the .sec-grid for a set of children with N columns. `columns` is the
  // authored track count; the CSS container queries in index.html do the only
  // responsive stepping (3→2→1 as width tightens). A child that is a SECTION
  // (has `children`) recurses through buildSection; a leaf goes to buildBox. A
  // child may declare `span` to occupy M columns; span is clamped to the
  // section's own columns (a band = span == columns). The grid-column value is
  // applied by CSS (from `.msp`) so the container queries can cap it per tier.
  function buildGrid(children, columns, reg) {
    const cols = Math.max(1, Number.isInteger(columns) && columns > 0 ? columns : DEFAULT_SECTION_COLUMNS);
    // `sec-c{N}` (authored column count) lets CSS step each grid's own collapse
    // threshold by how many tracks it actually has to fit, instead of one
    // blanket breakpoint for every grid. `sec-compound` marks a grid that holds
    // at least one NESTED section (a child with its own `children`) rather than
    // only leaf components — a compound grid's cell has to fit a WHOLE nested
    // grid, not just one box, so it is a flex-wrap row of sections while a leaf
    // grid is fixed --cell-w tracks. This is structural (derived from the data),
    // not hardcoded to any id, so it stays true if the content changes.
    const isCompound = (children || []).some(c => Array.isArray(c.children));
    const classes = ['sec-grid', `sec-c${cols}`];
    if (cols <= 1) classes.push('sec-c1');
    if (isCompound) classes.push('sec-compound');
    const grid = el('div', classes.join(' '));
    grid.style.setProperty('--cols', String(cols));
    for (const child of orderedChildren(children)) {
      // A child WITH `children` is a section (recurse). A leaf dispatches on its
      // `type`: separator | rail | box (default when `type` is absent/"box", so
      // existing components render unchanged).
      const node = Array.isArray(child.children) ? buildSection(child, reg)
        : child.type === 'separator' ? buildSeparator(child)
        : child.type === 'rail' ? buildRail(child)
        : buildBox(child, reg);
      const span = Math.max(1, Math.min(child.span || 1, cols));
      if (span > 1) { node.style.setProperty('--span', String(span)); node.classList.add('msp'); }
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
    const vclass = ZONE_VARIANT[sec.variant] ?? '';
    const zone = el('section', ['zone', vclass].filter(Boolean).join(' '), { 'data-zone': sec.id });
    // Titleless container: draw no header when the section declares no
    // title/subtitle/label — so a pure structural wrapper (e.g. a `plain`
    // stack) shows only its children's frames, with no empty header line.
    if (sec.title || sec.subtitle || sec.label) zone.appendChild(sectionHeader(sec));
    zone.appendChild(buildGrid(sec.children, sec.columns, reg));
    return zone;
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

    // stage + canvas. The page/root IS a section: page.columns = root columns,
    // page.sections = root children. The root grid lives inside a
    // width:max-content plane so the authored columns stay authoritative — the
    // plane centers (margin-inline:auto) when narrower than the canvas and the
    // overflow:auto canvas scrolls horizontally when wider. No JS measurement.
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
        if (captured && e && e.pointerId != null) { try { canvas.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ } }
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
