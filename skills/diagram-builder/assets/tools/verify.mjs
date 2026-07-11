// Headless render QA for a diagram deck — GENERIC (domain-agnostic).
// Renders the real index.html, walks every page, and asserts the layout
// invariants the engine guarantees for ANY diagram:
//   - each page renders (a canvas holding the root .sec-plane .sec-grid);
//   - the root grid has >= 1 child (something rendered);
//   - no two direct children of the root section grid overlap.
// It screenshots every page across a spread of widths and both themes, so the
// PNGs can be read by eye (this is the engine's verify-UI capability). There are
// NO diagram-specific zone-name assertions here — a fresh deck has its own ids.
//
// Screenshots are written to a SYSTEM TEMP DIR, never into the project — nobody
// reuses verification shots, so the scaffolded repo stays clean (no images
// folder). Override the location with DIAGRAM_SHOTS_DIR.
//
// Run: npm run verify   (or: node tools/verify.mjs)
import { chromium } from 'playwright';
import { mkdirSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const FILE = pathToFileURL(join(ROOT, 'index.html')).href;
const OUT = process.env.DIAGRAM_SHOTS_DIR || join(tmpdir(), 'diagram-deck-screenshots');
mkdirSync(OUT, { recursive: true });

// Resolve a Chromium already on disk (any PLAYWRIGHT_BROWSERS_PATH / OS cache),
// so verification uses what is present instead of triggering a fresh download.
function resolveCachedChrome() {
  const bases = [
    process.env.PLAYWRIGHT_BROWSERS_PATH,
    join(process.env.HOME || '', '.cache', 'ms-playwright')
  ].filter(Boolean);
  for (const base of bases) {
    if (!existsSync(base)) continue;
    const builds = readdirSync(base).filter(d => d.startsWith('chromium-'))
      .sort((a, b) => (parseInt(b.split('-')[1]) || 0) - (parseInt(a.split('-')[1]) || 0));
    for (const b of builds)
      for (const sub of ['chrome-linux64', 'chrome-linux', 'chrome-win', 'chrome-mac'])
        for (const bin of ['chrome', 'chrome.exe', 'Chromium.app/Contents/MacOS/Chromium']) {
          const p = join(base, b, sub, bin);
          if (existsSync(p)) return p;
        }
  }
  return null;
}
async function launch() {
  try { return await chromium.launch({ headless: true }); }
  catch (e) {
    const exe = resolveCachedChrome();
    if (!exe) throw e;
    console.log('[verify] default Chromium unavailable; using cached: ' + exe);
    return await chromium.launch({ headless: true, executablePath: exe });
  }
}

// overlap area guard between two rects (a few px of touching is tolerated)
function overlaps(a, b) {
  if (!a || !b) return false;
  const ox = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const oy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return ox > 2 && oy > 2;
}

// Measure the active page: does it render, how many top-level cells/zones, and
// any collisions among the ROOT section grid's direct children (the top-level
// cells — sections or leaf components that sit directly on the page root).
function auditActive() {
  const act = document.querySelector('.act.active');
  if (!act) return { rendered: false };
  const canvas = act.querySelector('.canvas');
  const rootGrid = act.querySelector('.canvas .sec-plane > .sec-grid');
  const cells = rootGrid ? [...rootGrid.children] : [];
  const zones = [...act.querySelectorAll('.canvas [data-zone]')];
  const rect = n => { const r = n.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  return {
    rendered: !!(canvas && rootGrid),
    cellCount: cells.length,
    zoneCount: zones.length,
    cellRects: cells.map(rect),
    zoneRects: zones.map(rect)
  };
}

const browser = await launch();
const problems = [];

for (const theme of ['light', 'dark']) {
  for (const { w, h, assert } of [
    { w: 1920, h: 1080, assert: true },
    { w: 1440, h: 900, assert: true },
    { w: 600, h: 900, assert: false } // narrow: screenshot only (coarse stack)
  ]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    const page = await ctx.newPage();
    await page.addInitScript(t => localStorage.setItem('theme', t), theme);
    // Pre-seed 'help-seen' so the first-visit help HUD never auto-opens.
    // Left unset, engine.js opens it on load and its keydown handler ignores
    // ArrowLeft/Right while it's open — every page-walk press below would be
    // silently swallowed, leaving every "page{i}" screenshot showing page 0.
    await page.addInitScript(() => localStorage.setItem('help-seen', '1'));
    await page.goto(FILE);
    await page.waitForTimeout(300);

    // walk every page tab
    const pageCount = await page.evaluate(() => document.querySelectorAll('.act').length);
    for (let i = 0; i < Math.max(1, pageCount); i++) {
      if (i > 0) { await page.keyboard.press('ArrowRight'); await page.waitForTimeout(350); }
      const a = await page.evaluate(auditActive);
      const label = `${theme} ${w}x${h} page${i}`;
      await page.screenshot({ path: join(OUT, `page${i}-${theme}-${w}x${h}.png`) });

      if (!assert) continue;
      if (!a.rendered) { problems.push(`[${label}] no root .sec-grid rendered`); continue; }
      if ((a.cellCount || 0) < 1)
        problems.push(`[${label}] nothing rendered (root grid has 0 children)`);
      // collision check: only the ROOT grid's direct children (top-level cells).
      // NESTED zones legitimately sit inside their parent zone, so a
      // containment "overlap" there is correct — never flag it.
      const rects = a.cellRects;
      for (let x = 0; x < rects.length; x++)
        for (let y = x + 1; y < rects.length; y++)
          if (overlaps(rects[x], rects[y]))
            problems.push(`[${label}] top-level cell collision: #${x} x #${y}`);
      console.log(`[${label}] rendered cells=${a.cellCount} zones=${a.zoneCount}`);
    }
    await ctx.close();
  }
}

await browser.close();
if (problems.length) { console.log('\nFAIL:\n' + problems.join('\n')); process.exit(1); }
else console.log(`\nPASS — every page renders with no top-level cell collisions across widths + themes. Screenshots in ${OUT}`);
