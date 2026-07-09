#!/usr/bin/env node
/**
 * screenshot.cjs -- REFERENCE IMPLEMENTATION of the visual-verify technique.
 * It captures a URL/file across several viewport widths (and, optionally,
 * color schemes) using a Chromium that is ALREADY present on disk, launched
 * via an explicit executablePath. See ../SKILL.md for the disposition this
 * embodies -- the script is a support, not a spec; adapt it to the environment.
 *
 * Two principles this code encodes:
 *   1. Find the browser where it lives. The Playwright browser cache location
 *      varies by OS and by the PLAYWRIGHT_BROWSERS_PATH override -- do not
 *      assume one path. The chromium build is resolved DYNAMICALLY (highest
 *      revision present); a hardcoded revision is a time-bomb. If nothing is
 *      present, obtaining a browser is a legitimate deliberate step (see the
 *      error branch) -- the friction to avoid is a tool silently fetching a
 *      MISMATCHED revision while a usable browser already sits in the cache.
 *   2. The OUTPUT LOCATION is the caller's decision, not the tool's. out-dir
 *      is an argument on purpose: send captures where their context wants them
 *      (a brief's evidence dir inside Gaia; a temp dir for generic use; or a
 *      location the user named). The script does not choose for you.
 *
 * Usage:
 *   node screenshot.cjs <url-or-file-path> <out-dir> [widths] [colorSchemes]
 *     widths        default 1440,900,700,500,380 (desktop -> narrow mobile)
 *     colorSchemes  optional, e.g. light,dark -- captures each via
 *                   prefers-color-scheme. Apps with a bespoke theme toggle
 *                   (localStorage/class) may need a project-specific step.
 *
 * Examples:
 *   node screenshot.cjs file:///abs/path/index.html /abs/out/dir
 *   node screenshot.cjs file:///abs/path/index.html /abs/out/dir 1440,768,375
 *   node screenshot.cjs http://localhost:3000 /abs/out/dir 1440,390 light,dark
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

// Conventional global npm module roots, checked without shelling out --
// covers the common Linux/macOS locations plus a user-level prefix.
const GLOBAL_NPM_ROOTS = [
  process.env.npm_config_prefix ? path.join(process.env.npm_config_prefix, 'lib', 'node_modules') : null,
  '/usr/local/lib/node_modules',
  '/usr/lib/node_modules',
  path.join(os.homedir(), '.npm-global', 'lib', 'node_modules'),
].filter(Boolean);

// Find a usable playwright/playwright-core module already present on disk.
// Priority: project-local dependency (respects the project's pinned
// version) -> an already-fetched npx cache -> a conventional global root.
function findPlaywrightModule() {
  const candidates = [];

  for (const name of ['playwright', 'playwright-core']) {
    try {
      candidates.push(require.resolve(name, { paths: [process.cwd()] }));
    } catch (_) {
      // not a project dependency -- keep looking
    }
  }

  const npxRoot = path.join(os.homedir(), '.npm', '_npx');
  if (fs.existsSync(npxRoot)) {
    for (const hash of fs.readdirSync(npxRoot)) {
      for (const name of ['playwright', 'playwright-core']) {
        const p = path.join(npxRoot, hash, 'node_modules', name, 'index.js');
        if (fs.existsSync(p)) candidates.push(p);
      }
    }
  }

  for (const root of GLOBAL_NPM_ROOTS) {
    for (const name of ['playwright', 'playwright-core']) {
      const p = path.join(root, name, 'index.js');
      if (fs.existsSync(p)) candidates.push(p);
    }
  }

  return candidates[0] || null;
}

// Conventional Playwright browser-cache roots, in priority order. The
// location is not one fixed path: the PLAYWRIGHT_BROWSERS_PATH override wins
// when set, then the per-OS default. Do not assume a single directory.
function browserCacheRoots() {
  const roots = [];
  const override = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (override && override !== '0') roots.push(override);
  roots.push(path.join(os.homedir(), '.cache', 'ms-playwright')); // Linux / WSL
  roots.push(path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright')); // macOS
  roots.push(path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright')); // Windows
  return roots;
}

// Find a Chromium binary already on disk, picking the highest chromium-*
// revision present. Resolving dynamically is the point -- the cache holds
// whatever was last fetched, which need not match any installed package's
// pinned revision. Returns null when no browser is present anywhere.
function findCachedChromium() {
  for (const cacheRoot of browserCacheRoots()) {
    if (!fs.existsSync(cacheRoot)) continue;

    const revisions = fs
      .readdirSync(cacheRoot)
      .filter((d) => /^chromium-\d+$/.test(d))
      .map((d) => ({ dir: d, rev: parseInt(d.split('-')[1], 10) }))
      .sort((a, b) => b.rev - a.rev);

    for (const { dir } of revisions) {
      for (const layout of ['chrome-linux64', 'chrome-linux', 'chrome-mac', 'chrome-win']) {
        const exe = path.join(
          cacheRoot,
          dir,
          layout,
          layout.startsWith('chrome-win') ? 'chrome.exe' : 'chrome'
        );
        if (fs.existsSync(exe)) return exe;
      }
    }
  }
  return null;
}

async function main() {
  const [, , target, outDir, widthsArg, schemesArg] = process.argv;
  if (!target || !outDir) {
    console.error(
      'Usage: node screenshot.cjs <url-or-file-path> <out-dir> [widths] [colorSchemes]'
    );
    process.exit(1);
  }

  const url =
    target.startsWith('file://') || /^https?:\/\//.test(target)
      ? target
      : `file://${path.resolve(target)}`;

  const widths = (widthsArg || '1440,900,700,500,380')
    .split(',')
    .map((w) => parseInt(w.trim(), 10));

  // No color-scheme arg -> one capture pass with the page's own default.
  const schemes = schemesArg
    ? schemesArg.split(',').map((s) => s.trim()).filter(Boolean)
    : [null];

  const pwPath = findPlaywrightModule();
  if (!pwPath) {
    console.error(
      'No playwright/playwright-core module found (project deps, npx cache, or a conventional ' +
        'global root). Obtaining one is a legitimate one-time step, but it mutates the ' +
        'environment (a T3 npm operation): do it deliberately with approval, not as a silent ' +
        'side effect of a verification.'
    );
    process.exit(1);
  }

  const chromeBinary = findCachedChromium();
  if (!chromeBinary) {
    console.error(
      'No Chromium found in any known Playwright browser cache. Installing one is a legitimate ' +
        'one-time step -- do it deliberately (a T3 browser fetch, with approval). What to avoid ' +
        'is a tool auto-fetching a MISMATCHED revision when a usable browser already exists; that ' +
        'was the original friction this reference script sidesteps.'
    );
    process.exit(1);
  }

  const { chromium } = require(pwPath);
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
    executablePath: chromeBinary,
    args: ['--no-sandbox'],
  });

  const written = [];
  try {
    for (const scheme of schemes) {
      for (const width of widths) {
        const opts = { viewport: { width, height: 1000 } };
        if (scheme) opts.colorScheme = scheme; // sets prefers-color-scheme
        const page = await browser.newPage(opts);
        await page.goto(url);
        await page.waitForTimeout(300);
        const name = scheme ? `${width}-${scheme}.png` : `${width}.png`;
        const file = path.join(outDir, name);
        await page.screenshot({ path: file, fullPage: true });
        written.push(file);
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }

  console.log(
    JSON.stringify(
      { playwright_module: pwPath, chromium_executable: chromeBinary, screenshots: written },
      null,
      2
    )
  );
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
