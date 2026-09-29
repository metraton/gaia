// Frame-exact renderer for the narrated video, injected by browser.mjs into
// index.html?video. Every visual property it sets is a pure function of the
// time passed to window.__seek(t), and CSS transitions are switched off, so a
// screenshot after __seek depends on t alone and never on the wall clock.
//
// Reveals use `filter: opacity()` and `translate`, not `opacity`/`transform`,
// so the deck's own `.stage.flowing` dimming (an `opacity` rule) keeps working
// on revealed boxes.
(function () {
  'use strict';

  const deck = window.__deck;
  if (!deck) throw new Error('driver.js: window.__deck is missing; load index.html?video');

  // The frame shows the page's chip bar and its content on the day background;
  // all other deck chrome is hidden. The "all" reset chip is hidden too: at rest
  // it is the only highlighted pill, so it would read as a lit chip.
  // VIDEO_DIM is the opacity of a lit chip's non-members: lighter than the
  // interactive deck's, so the page stays readable while the members stand out.
  const VIDEO_DIM = 0.6;
  const css = document.createElement('style');
  css.textContent =
    '*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important}' +
    '.panel,.bar,.help-modal,[data-help-backdrop],.actbar .bar-spacer,.actbar .chip[data-flow="all"]{display:none!important}' +
    'html,body,.deck,.act,.stage,.canvas,.actbar{background:var(--bg)!important;border:0!important;box-shadow:none!important}' +
    '.canvas{inset:0!important;padding:0!important;overflow:hidden!important;scrollbar-width:none!important;cursor:default!important}' +
    '.actbar{position:absolute!important;left:0;right:0;z-index:1;padding:0!important;justify-content:center!important}' +
    '.actbar .chips{justify-content:center}' +
    `.stage.flowing .box:not(.lit),.stage.flowing .rail:not(.lit){opacity:${VIDEO_DIM}!important}` +
    '.stage.flowing .box:not([data-filters]),.stage.flowing .canvas .zone-header{opacity:1!important}' +
    '::-webkit-scrollbar{display:none!important}';
  document.head.appendChild(css);

  // Fraction of the frame kept free on each side, and the gap between the chip
  // bar and the content, in frame pixels.
  const FRAME_MARGIN = 0.06;
  const BAR_GAP = 28;

  const ID_SELECTOR = id => `[data-k="${id}"],[data-zone="${id}"],[data-cid="${id}"]`;
  const ID_ATTRS = '[data-k],[data-zone],[data-cid]';
  const idOf = n => n.dataset.k || n.dataset.zone || n.dataset.cid;
  const clamp01 = x => (x < 0 ? 0 : x > 1 ? 1 : x);
  const easeOutCubic = x => 1 - Math.pow(1 - x, 3);

  let video = null;
  let shownIndex = -1;
  let appliedChip = 'all';

  // Stacks the chip bar above the content plane, scales the plane so both fit
  // inside FRAME_MARGIN, and centres the stack. The scale is a transform over
  // the deck's own layout, not a relayout, so the deck's breakpoints hold.
  function frame(act) {
    const plane = act.querySelector('.sec-plane');
    const bar = act.querySelector('.actbar');
    plane.style.cssText = '';
    const w = plane.offsetWidth, h = plane.offsetHeight, barH = bar.offsetHeight;
    const W = window.innerWidth, H = window.innerHeight;
    const byWidth = (W * (1 - 2 * FRAME_MARGIN)) / w;
    const byHeight = (H * (1 - 2 * FRAME_MARGIN) - barH - BAR_GAP) / h;
    const scale = Math.min(byWidth, byHeight);
    const top = (H - (barH + BAR_GAP + h * scale)) / 2;
    const x = (W - w * scale) / 2, y = top + barH + BAR_GAP;
    bar.style.top = `${top}px`;
    plane.style.cssText = `position:absolute;left:0;top:0;margin:0;max-width:none;width:${w}px;` +
      `transform-origin:0 0;transform:translate(${x}px,${y}px) scale(${scale})`;
  }

  // plan: { fade, reveal:{duration,rise,anticipation},
  //         pages:[{ page, start, voiceAt, end, base:[id], cues:[{ t, reveal?:[id], chip? }] }] }
  // A reveal or a lit chip fires reveal.anticipation seconds before its anchor,
  // never before the page's fade-in ends; a clear (chip "all") fires on it.
  // Returns the problems found against the rendered deck; empty means it fits.
  function load(plan) {
    const errors = [];
    const pages = plan.pages.map(p => {
      const index = deck.acts.findIndex(a => a.dataset.pageId === p.page);
      if (index < 0) { errors.push(`${p.page}: no rendered page with this id`); return null; }
      const act = deck.acts[index];
      deck.show(index);
      frame(act);
      const filters = [...act.querySelectorAll('.chip[data-flow]')].map(c => c.dataset.flow);
      const early = t => Math.max(p.start + plan.fade, t - (plan.reveal.anticipation || 0));
      const at = new Map();
      const reveals = [];
      const chips = [];
      const find = (id, where) => {
        const n = act.querySelector(ID_SELECTOR(id));
        if (!n) errors.push(`${p.page}: ${where} names "${id}", which is not on the rendered page`);
        return n;
      };
      for (const cue of p.cues) {
        const fire = early(cue.t);
        for (const id of cue.reveal || []) {
          const n = find(id, 'reveal');
          if (!n) continue;
          reveals.push({ el: n, t: fire, rise: plan.reveal.rise });
          at.set(n, Math.min(at.has(n) ? at.get(n) : Infinity, fire));
        }
        if (cue.chip !== undefined) {
          if (cue.chip !== 'all' && !filters.includes(cue.chip)) errors.push(`${p.page}: chip "${cue.chip}" is not rendered on the page`);
          chips.push({ anchor: cue.t, t: cue.chip === 'all' ? cue.t : fire, key: cue.chip });
        }
      }
      for (const id of p.base) find(id, 'base');

      // An element is on screen once it and every cued ancestor are revealed.
      const visibleAt = n => {
        let t = -Infinity;
        for (let a = n; a && act.contains(a); a = a.parentElement && a.parentElement.closest(ID_ATTRS)) {
          if (at.has(a)) t = Math.max(t, at.get(a));
        }
        return t;
      };
      const secs = x => (x - p.voiceAt).toFixed(2);
      for (const c of chips) {
        if (c.key === 'all') continue;
        const members = [...act.querySelectorAll('[data-filters]')]
          .filter(m => m.getAttribute('data-filters').split(/\s+/).includes(c.key));
        const first = Math.min(...members.map(visibleAt));
        if (!(first <= c.t)) errors.push(`${p.page}: chip "${c.key}" fires at ${secs(c.t)} s but no member is revealed before ${secs(first)} s`);
      }
      const roots = [...act.querySelectorAll(ID_ATTRS)].filter(n => !n.parentElement.closest(ID_ATTRS));
      for (const r of roots) {
        if (!p.base.includes(idOf(r)) && !at.has(r)) errors.push(`${p.page}: top-level "${idOf(r)}" is neither shown from the start nor revealed`);
      }
      for (const [n, t] of at) {
        for (let a = n.parentElement.closest(ID_ATTRS); a && act.contains(a); a = a.parentElement.closest(ID_ATTRS)) {
          if (at.has(a) && at.get(a) > t) errors.push(`${p.page}: "${idOf(n)}" is revealed before its section "${idOf(a)}"`);
        }
      }
      chips.sort((a, b) => a.anchor - b.anchor);
      return { ...p, index, act, reveals, chips };
    }).filter(Boolean);
    video = { ...plan, pages };
    shownIndex = -1;
    return errors;
  }

  function seek(t) {
    const pages = video.pages;
    const pg = pages.find(p => t < p.end) || pages[pages.length - 1];
    if (pg.index !== shownIndex) {
      if (shownIndex >= 0 && appliedChip !== 'all') deck.setFlow(shownIndex, 'all');
      deck.show(pg.index);
      shownIndex = pg.index;
      appliedChip = 'all';
    }
    pg.act.style.opacity = String(clamp01((t - pg.start) / video.fade) * clamp01((pg.end - t) / video.fade));

    for (const r of pg.reveals) {
      const e = easeOutCubic(clamp01((t - r.t) / video.reveal.duration));
      r.el.style.filter = e >= 1 ? '' : `opacity(${e})`;
      r.el.style.translate = e >= 1 ? '' : `0 ${(1 - e) * r.rise}px`;
    }

    let key = 'all';
    for (const c of pg.chips) if (c.t <= t) key = c.key;
    if (key !== appliedChip) {
      deck.setFlow(pg.index, key);
      deck.closePanel(pg.index);
      appliedChip = key;
    }
  }

  window.__videoLoad = load;
  window.__seek = seek;
})();
