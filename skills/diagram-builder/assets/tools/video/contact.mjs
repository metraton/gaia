// Stills of one page at the end of chosen sentences, to judge framing and
// reveals without rendering the video.
//
//   npm run video:contact -- --page <id> [--at 1,3]
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { OUT_DIR, argValue, fail, loadPlaywright, readScript, requireDeck } from './deck.mjs';
import { buildPlan, loadTimeline, readAlign } from './timeline.mjs';
import { loadPlan, openDeck } from './browser.mjs';

const doc = requireDeck();
const playwright = loadPlaywright();
const timeline = loadTimeline(doc, readScript());
const id = argValue('--page', null);
const ids = timeline.pages.map(p => p.page);
if (!ids.includes(id)) fail(`--page must name a page of the script (${ids.join(', ')})`);
const plan = buildPlan(timeline, readAlign(), [id]);
const pg = plan.pages[0];
const at = argValue('--at', pg.sentences.map((_, k) => k + 1).join(',')).split(',').map(Number);
const unknown = at.filter(n => !pg.sentences[n - 1]);
if (unknown.length) fail(`--at: ${id} has sentences 1-${pg.sentences.length}, not ${unknown.join(', ')}`);

const { browser, page } = await openDeck(playwright, 1);
const errors = await loadPlan(page, plan);
if (errors.length) {
  await browser.close();
  fail(`the timeline does not fit the rendered deck:\n[video]   ${errors.join('\n[video]   ')}`);
}
const dir = join(OUT_DIR, 'contact');
mkdirSync(dir, { recursive: true });
for (const n of at) {
  const t = pg.voiceAt + pg.sentences[n - 1].end;
  await page.evaluate(x => window.__seek(x), t);
  const file = join(dir, `${id}-s${n}.png`);
  await page.screenshot({ path: file });
  console.log(`[video] ${file}: end of sentence ${n}, ${t.toFixed(2)} s`);
}
await browser.close();
