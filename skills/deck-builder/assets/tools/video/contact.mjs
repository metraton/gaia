// A contact sheet of one page: stills at chosen seconds, to judge framing and
// reveals without rendering the video.
//
//   npm run video:contact -- --page <id> [--at 2.5,14,31.2]
//
// Seconds count from the start of the page's slot, the same clock as its clip
// from video:split. Without --at, the stills are the ends of its sentences.
import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { OUT_DIR, argValue, fail, loadPlaywright, readScript, requireDeck } from './deck.mjs';
import { buildPlan, loadTimeline, readAlign } from './timeline.mjs';
import { loadPlan, openDeck } from './browser.mjs';

const SHEET_COLUMNS = 3;
const TILE_WIDTH = 640;

const doc = requireDeck();
const playwright = loadPlaywright();
const timeline = loadTimeline(doc, readScript());
const id = argValue('--page', null);
const ids = timeline.pages.map(p => p.page);
if (!ids.includes(id)) fail(`--page must name a page of the script (${ids.join(', ')})`);
const plan = buildPlan(timeline, readAlign(), [id]);
const pg = plan.pages[0];
const length = pg.end - pg.start;
const ends = pg.sentences.map(s => (pg.voiceAt - pg.start + s.end).toFixed(2)).join(',');
const at = argValue('--at', ends).split(',').map(Number);
const outside = at.filter(s => !(s >= 0 && s < length));
if (outside.length) fail(`--at: ${id} lasts ${length.toFixed(2)} s; seconds must be from 0 up to that, not ${outside.join(', ')}`);

const { browser, page } = await openDeck(playwright, 1);
const errors = await loadPlan(page, plan);
if (errors.length) {
  await browser.close();
  fail(`the timeline does not fit the rendered deck:\n[video]   ${errors.join('\n[video]   ')}`);
}
const dir = join(OUT_DIR, 'contact');
mkdirSync(dir, { recursive: true });
const stills = [];
for (const [k, s] of at.entries()) {
  await page.evaluate(x => window.__seek(x), pg.start + s);
  const file = join(dir, `${id}-${String(k + 1).padStart(2, '0')}.png`);
  await page.screenshot({ path: file });
  stills.push(file);
  console.log(`[video] ${file}: ${s.toFixed(2)} s into the page`);
}
await browser.close();

const sheet = join(dir, `${id}.png`);
const rows = Math.ceil(stills.length / SHEET_COLUMNS);
const tiles = `${stills.map((_, i) => `[${i}:v]`).join('')}concat=n=${stills.length}:v=1:a=0,` +
  `scale=${TILE_WIDTH}:-2,tile=${SHEET_COLUMNS}x${rows}:padding=8:margin=8:color=white`;
const r = spawnSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...stills.flatMap(f => ['-i', f]),
  '-filter_complex', tiles, '-frames:v', '1', sheet], { stdio: 'inherit' });
if (r.status !== 0) fail(`ffmpeg could not tile the contact sheet (exit ${r.status})`);
console.log(`[video] ${sheet}: ${stills.length} stills, ${SHEET_COLUMNS} per row, in order of --at, in ${process.uptime().toFixed(1)} s wall`);
