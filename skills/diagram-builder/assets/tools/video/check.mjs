// Validates the video's timeline against the rendered deck without taking a
// frame: every page, section and chip the script names must be on the page,
// and every box revealed no earlier than the section that holds it. With
// --pages only those pages are checked; without it, every page is.
//
//   npm run video:check [-- --pages id,id]
import { loadPlaywright, readScript, requireDeck, fail } from './deck.mjs';
import { buildPlan, loadTimeline, readAlign, selectedPages } from './timeline.mjs';
import { loadPlan, openDeck } from './browser.mjs';

const doc = requireDeck();
const playwright = loadPlaywright();
const script = readScript();
const plan = buildPlan(loadTimeline(doc, script, selectedPages(script)), readAlign());
const { browser, page } = await openDeck(playwright, 1);
const errors = await loadPlan(page, plan);
await browser.close();
if (errors.length) fail(`the timeline does not fit the rendered deck:\n[video]   ${errors.join('\n[video]   ')}`);
for (const p of plan.pages) {
  console.log(`[video] ${p.page}: ${p.cues.length} cues, ${p.base.length} shown from the start, timing ${p.method}`);
}
console.log(`[video] timeline valid against the rendered deck: ${plan.pages.length} pages, ${plan.duration.toFixed(1)} s`);
