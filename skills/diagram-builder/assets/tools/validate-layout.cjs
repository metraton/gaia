// ─────────────────────────────────────────────────────────────────────────
// validate-layout.cjs — the LAYOUT GUARDRAIL for a uniform-cell diagram deck.
//
// This is the hard gate that proves the spreadsheet-style grid still "adds up"
// after any change to data/ or the engine/CSS. It is GENERIC (domain-agnostic):
// it discovers the pages from the rendered deck and asserts each invariant
// against the REAL rendered geometry (getBoundingClientRect), not against the
// data — a CSS or data edit cannot silently break the layout without failing
// here.
//
// FLOW:  edit data/pages/*.yaml  →  npm run build  →  npm run validate
//   (this script re-runs the build itself, so `npm run validate` alone is
//    enough; the explicit build step is for the edit→preview loop.)
//
// WHAT IT DOES
//   1. Rebuilds data/data.generated.js from the YAML (validates what the deck
//      actually renders, not stale generated data).
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
//   U  uniform leaf cells   — every single-cell (non-span) .box is EXACTLY
//                            CELL_W × CELL_H; no inflation.
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
// ─────────────────────────────────────────────────────────────────────────
const { chromium } = require('playwright');
const path = require('path');
const http = require('http');
const fs = require('fs');
const os = require('os');

const ROOT = path.join(__dirname, '..');
const OUT = process.env.DIAGRAM_SHOTS_DIR || path.join(os.tmpdir(), 'diagram-deck-layout');
// MUST match the --cell-w / --cell-h design tokens in index.html.
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

require('child_process').execSync('node engine/build-data.mjs', { cwd: ROOT, stdio: 'inherit' });

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
      span: b.classList.contains('msp'),
      clipped: b.scrollHeight > b.clientHeight + 1 };
  });
  const single = boxes.filter(b => !b.span);
  const singleWidths = [...new Set(single.map(b => b.w))].sort((a,b)=>a-b);
  const heights = [...new Set(boxes.map(b => b.h))].sort((a,b)=>a-b);
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
    return { zone: z.getAttribute('data-zone') || '?',
      w: Math.round(zr.width), gridW: Math.round(gr.width), headerOverflow,
      band: z.classList.contains('msp') };
  });

  // leaf grids with their authored column count (sec-cN) and rendered track count
  const leafGrids = [...act.querySelectorAll('.sec-grid:not(.sec-compound)')].map(g => {
    const m = g.className.match(/sec-c(\d)/);
    const authored = m ? Number(m[1]) : 1;
    const tracks = getComputedStyle(g).gridTemplateColumns.split(' ').filter(Boolean).length;
    const z = g.closest('.zone[data-zone]');
    return { zone: z ? z.getAttribute('data-zone') : '(root)', authored, tracks };
  });

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

  return { singleWidths, heights, clipped, maxRowCount, overflowX,
    leftPad, rightPad, topZones, leafGrids, wrap, nBoxes: boxes.length,
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
  await page.evaluate((idx) => { const t = document.querySelectorAll('.pagetab'); if (t[idx]) t[idx].click(); }, tabIndex);
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
  const discovery = await (async () => {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1000 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    const pages = await page.evaluate(() => {
      const acts = [...document.querySelectorAll('.act')];
      const tabs = [...document.querySelectorAll('.pagetab')];
      return acts.map((a, i) => ({ name: (tabs[i] && tabs[i].textContent.trim()) || `page${i}`, tabIndex: i }));
    });
    await ctx.close();
    return pages.length ? pages : [{ name: 'page0', tabIndex: 0 }];
  })();

  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const results = [];
  let failed = 0;

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

      // ── invariants (asserted on the first pass geometry) ──
      const m = m0;
      const checks = [];
      const add = (id, name, ok, detail) => { checks.push({ id, name, ok, detail }); if (!ok) failed++; };

      add('D', `determinism (${PASSES} reloads)`, deterministic,
        deterministic ? `identical signature across ${PASSES} reloads`
                      : `DIVERGED — ${uniqueSigs.length} distinct signatures:\n        ` +
                        uniqueSigs.map((s, i) => `sig#${i+1} (passes ${sigs.map((x,j)=>x===s?j+1:null).filter(x=>x).join(',')}): ${s}`).join('\n        '));
      if (WIDE_TIERS.has(tier)) add('R', `scrollbar-robust (-${SB_GUARD}px)`, robustOk, robustDetail);
      add('T', 'full-page capture not truncated', captureOk, captureDetail);
      add('U', 'uniform cell width',  eq(m.singleWidths, [CELL_W]), `singleWidths=${JSON.stringify(m.singleWidths)} expect [${CELL_W}]`);
      add('U', 'uniform cell height', eq(m.heights, [CELL_H]),      `heights=${JSON.stringify(m.heights)} expect [${CELL_H}]`);
      add('C', 'no box clipping', m.clipped === 0, `clipped=${m.clipped}`);
      if (WIDE_TIERS.has(tier)) add('O', 'h-overflow (tolerated@wide)', true, `overflowX=${m.overflowX}`);
      else add('O', 'no h-overflow', m.overflowX === 0, `overflowX=${m.overflowX}`);
      // F — 1-COLUMN ENDPOINT at minimum.
      if (tier === 'min') {
        const bad = m.leafGrids.filter(g => g.tracks !== 1);
        const oneCol = bad.length === 0 && m.maxRowCount === 1;
        add('F', '1-col endpoint at min', oneCol,
          bad.length ? `not-1-track: ${bad.map(g => `${g.zone}:auth${g.authored}->${g.tracks}`).join(', ')}`
                     : m.maxRowCount !== 1 ? `maxRowCount=${m.maxRowCount} (expected 1 — page not a single column)`
                     : `all ${m.leafGrids.length} leaf grids => 1 track; single vertical column (maxRowCount=1)`);
      }
      // F — INTERMEDIATE 2-col step at medium.
      if (tier === 'medium') {
        const bad = m.leafGrids.filter(g => g.authored >= 2 ? g.tracks !== 2 : g.tracks !== 1);
        add('F', '2-col intermediate at medium', bad.length === 0,
          bad.length ? bad.map(g => `${g.zone}:auth${g.authored}->${g.tracks}`).join(', ')
                     : `all leaf grids: >=2col=>2 tracks, 1col=>1 (${m.leafGrids.length} grids)`);
      }
      // S — inline sections fit their content; band sections span the BLOCK.
      {
        const inlineZones = m.topZones.filter(z => !z.band);
        const bandZones = m.topZones.filter(z => z.band);
        const maxInlineW = Math.max(0, ...inlineZones.map(z => z.w));
        const problems = [];
        for (const z of inlineZones) {
          if (z.w - z.gridW > FIT_TOL) problems.push(`${z.zone}:stretched(zone${z.w}>grid${z.gridW})`);
        }
        if (bandZones.length) {
          const bw = bandZones.map(z => z.w);
          if (Math.max(...bw) - Math.min(...bw) > FIT_TOL) {
            problems.push(`bands-unequal(${bandZones.map(z => `${z.zone}${z.w}`).join(',')})`);
          }
          for (const z of bandZones) {
            if (z.w < maxInlineW - FIT_TOL)
              problems.push(`${z.zone}:band-shrunk-to-content(zone${z.w}<block${maxInlineW})`);
          }
        }
        add('S', 'inline fit / band spans block (all tiers)', problems.length === 0,
          problems.length ? problems.join(', ')
                          : m.topZones.map(z => `${z.zone}${z.band?'[band]':''}(${z.w}/${z.gridW})`).join(' '));
      }
      // B — centered block at wide tiers.
      if (WIDE_TIERS.has(tier)) add('B', 'centered block', Math.abs(m.leftPad - m.rightPad) <= CENTER_TOL,
        `leftPad=${m.leftPad} rightPad=${m.rightPad}`);
      // H — header inside section.
      {
        const bad = m.topZones.filter(z => z.headerOverflow > 1);
        add('H', 'header within section', bad.length === 0,
          bad.length ? bad.map(z => `${z.zone}:+${z.headerOverflow}px`).join(', ') : 'all headers contained');
      }

      results.push({ page: pg.name, tier, width: w, maxRowCount: m.maxRowCount,
        wrap: m.wrap.join(' '), checks });
      await ctx.close();
    }
  }
  await browser.close();
  srv.close();

  // ── report ──
  console.log('\n══════════ LAYOUT VALIDATION ══════════\n');
  for (const r of results) {
    console.log(`● ${r.page} @ ${r.width}px (${r.tier})  cols-on-screen=${r.maxRowCount}  wrap=${r.wrap}`);
    for (const c of r.checks) console.log(`    [${c.ok ? 'PASS' : 'FAIL'}] ${c.id} ${c.name}: ${c.detail}`);
    console.log('');
  }
  const total = results.reduce((n, r) => n + r.checks.length, 0);
  console.log('═══════════════════════════════════════');
  console.log(`Screenshots: ${OUT}`);
  if (failed === 0) { console.log(`ALL PASS — ${total} checks across ${results.length} (page,width) renders, ${PASSES} reloads each.\n`); process.exit(0); }
  else { console.log(`FAIL — ${failed}/${total} checks failed. See [FAIL] lines above.\n`); process.exit(1); }
})();
