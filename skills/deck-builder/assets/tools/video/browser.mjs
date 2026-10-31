// Opens the deck under ?video in Chromium with driver.js injected, for the
// scripts that need the rendered deck: check, contact and capture.
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { DECK, HERE, fail } from './deck.mjs';
import { FRAME } from './timeline.mjs';

// Playwright pins one Chromium build; when that build is not in the cache, the
// newest chromium-<n> already there is used rather than downloading another.
async function launch(chromium) {
  try {
    return await chromium.launch();
  } catch {
    const cache = join(homedir(), '.cache', 'ms-playwright');
    const builds = existsSync(cache) ? readdirSync(cache).filter(d => /^chromium-\d+$/.test(d)) : [];
    builds.sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]));
    for (const b of builds) {
      const exe = join(cache, b, 'chrome-linux64', 'chrome');
      if (existsSync(exe)) return chromium.launch({ executablePath: exe });
    }
    fail(`no Chromium for Playwright; install it with: npm exec --prefix ${HERE} -- playwright install chromium`);
  }
}

/** Returns { browser, page } with the deck loaded under ?video, or fails when the hook does not run. */
export async function openDeck(playwright, scale = FRAME.supersample) {
  const browser = await launch(playwright.chromium);
  const context = await browser.newContext({
    viewport: { width: FRAME.width, height: FRAME.height }, deviceScaleFactor: scale, colorScheme: FRAME.theme,
  });
  await context.addInitScript(theme => {
    localStorage.setItem('theme', theme);
    localStorage.setItem('help-seen', '1');
  }, FRAME.theme);
  const page = await context.newPage();
  await page.goto(pathToFileURL(join(DECK, 'index.html')).href + '?video');
  if (!(await page.evaluate(() => Boolean(window.__deck)))) {
    await browser.close();
    fail(`the deck at ${DECK} exposed no window.__deck under ?video; its ?video hook does not run`);
  }
  await page.addScriptTag({ path: join(HERE, 'driver.js') });
  return { browser, page };
}

/** Loads the plan into the live deck and returns every cue the rendered deck cannot honour. */
export const loadPlan = (page, plan) => page.evaluate(p => window.__videoLoad(p), plan);
