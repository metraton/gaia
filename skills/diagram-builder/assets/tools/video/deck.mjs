// The deck the video is made from, its script, and the one place Playwright is
// loaded from. Every pipeline script starts with requireDeck(): there is no
// video without a deck whose engine exposes the ?video hook.
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

export const HERE = dirname(fileURLToPath(import.meta.url));
export const DECK = resolve(HERE, '..', '..');
export const VIDEO_DIR = join(DECK, 'video');
export const OUT_DIR = join(DECK, 'out', 'video');

const DECK_FILES = ['index.html', 'engine/engine.js', 'data/data.generated.js'];
const HOOK = /window\.__deck\s*=/;
const SCRIPT_FILE = join(VIDEO_DIR, 'script.json');
const PLAYWRIGHT = join(HERE, 'node_modules', 'playwright');

// The script holds what is said and what it shows, nothing else: a voice, a
// speed or any markup belongs to the voice step, so an unknown key is refused.
const SCRIPT_KEYS = new Set(['pages']);
const PAGE_KEYS = new Set(['page', 'audio', 'sentences']);
const SENTENCE_KEYS = new Set(['say', 'show', 'chip', 'cues']);
const CUE_KEYS = new Set(['at', 'show', 'chip']);

/** Prints one `[video]` message and ends the process with exit 1. */
export function fail(message) {
  console.error(`[video] ${message}`);
  process.exit(1);
}

/** Returns the deck's built document, or fails when there is no deck or no ?video hook. */
export function requireDeck() {
  const missing = DECK_FILES.filter(f => !existsSync(join(DECK, f)));
  if (missing.length) fail(`no deck at ${DECK}: the video is made from a deck, and ${missing.join(', ')} is missing`);
  if (!HOOK.test(readFileSync(join(DECK, 'engine', 'engine.js'), 'utf8'))) {
    fail(`the deck at ${DECK} has no ?video hook: engine/engine.js never sets window.__deck, so nothing can drive its pages`);
  }
  const sandbox = { window: {} };
  vm.runInNewContext(readFileSync(join(DECK, 'data', 'data.generated.js'), 'utf8'), sandbox);
  if (!sandbox.window.__DOC__) fail(`data/data.generated.js holds no deck; run npm run build --prefix ${DECK}`);
  return sandbox.window.__DOC__;
}

/**
 * Returns Playwright from this folder's node_modules, or fails on one line naming
 * the install. It is loaded by path, never by name: a bare import walks up the
 * directory tree and can load a Playwright this deck never installed.
 */
export function loadPlaywright() {
  if (!existsSync(join(PLAYWRIGHT, 'package.json'))) {
    fail(`Playwright is not installed for the video; install it with: npm install --prefix ${HERE}`);
  }
  return createRequire(import.meta.url)(PLAYWRIGHT);
}

/** Returns video/script.json after refusing every field that is not what is said or shown. */
export function readScript() {
  if (!existsSync(SCRIPT_FILE)) fail(`no video script at ${SCRIPT_FILE}`);
  let script;
  try { script = JSON.parse(readFileSync(SCRIPT_FILE, 'utf8')); } catch (e) { fail(`${SCRIPT_FILE} is not JSON: ${e.message}`); }
  const errors = [];
  const refuseUnknown = (obj, keys, where) => {
    for (const k of Object.keys(obj)) {
      if (!keys.has(k)) errors.push(`${where}: "${k}" is not a script field (only ${[...keys].join(', ')})`);
    }
  };
  refuseUnknown(script, SCRIPT_KEYS, 'script');
  if (!Array.isArray(script.pages) || !script.pages.length) errors.push('script: "pages" must list at least one page');
  for (const [i, p] of (script.pages || []).entries()) {
    const where = `page ${p.page ?? i + 1}`;
    refuseUnknown(p, PAGE_KEYS, where);
    if (typeof p.page !== 'string') errors.push(`${where}: "page" must name a page id of the deck`);
    if (typeof p.audio !== 'string') errors.push(`${where}: "audio" must declare the narration file, relative to ${VIDEO_DIR}`);
    if (!Array.isArray(p.sentences) || !p.sentences.length) errors.push(`${where}: "sentences" must list what is said`);
    for (const [k, s] of (p.sentences || []).entries()) {
      const at = `${where} sentence ${k + 1}`;
      refuseUnknown(s, SENTENCE_KEYS, at);
      if (typeof s.say !== 'string' || !s.say.trim()) errors.push(`${at}: "say" must hold the words said`);
      if (s.show !== undefined && !(Array.isArray(s.show) && s.show.every(id => typeof id === 'string'))) {
        errors.push(`${at}: "show" must list ids of the page`);
      }
      if (s.chip !== undefined && typeof s.chip !== 'string') errors.push(`${at}: "chip" must be one chip key`);
      if (s.cues !== undefined && !(Array.isArray(s.cues) && s.cues.length)) {
        errors.push(`${at}: "cues" must list word cues ({ "at": <word>, "show" or "chip" })`);
      }
      for (const [c, cue] of (Array.isArray(s.cues) ? s.cues : []).entries()) {
        const where = `${at} cue ${c + 1}`;
        refuseUnknown(cue, CUE_KEYS, where);
        if (typeof cue.at !== 'string' || !cue.at.trim()) errors.push(`${where}: "at" must name a word of the sentence`);
        if ((cue.show === undefined) === (cue.chip === undefined)) errors.push(`${where}: give exactly one of "show" or "chip"`);
        if (cue.show !== undefined && !(Array.isArray(cue.show) && cue.show.length && cue.show.every(id => typeof id === 'string'))) {
          errors.push(`${where}: "show" must list ids of the page`);
        }
        if (cue.chip !== undefined && typeof cue.chip !== 'string') errors.push(`${where}: "chip" must be one chip key`);
      }
    }
  }
  if (errors.length) fail(errors.join('\n[video] '));
  return script;
}

/** Resolves a page's declared audio inside video/, refusing a path that leaves it. */
export function audioPath(page) {
  const file = resolve(VIDEO_DIR, page.audio);
  const rel = relative(VIDEO_DIR, file);
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) {
    fail(`${page.page}: audio "${page.audio}" is not inside ${VIDEO_DIR}; declare it there`);
  }
  return file;
}

/** Returns the exported text of the page at `index` of the timeline: out/video/script/NN-<page>.txt. */
export function scriptTextPath(page, index) {
  return join(OUT_DIR, 'script', `${String(index + 1).padStart(2, '0')}-${page.page}.txt`);
}

/**
 * Returns where a voice provider leaves the page's per-word timings, beside its
 * audio: `<audio without extension>.words.json`, a list of {word, start, end} in
 * seconds inside that audio.
 */
export function wordsPath(page) {
  return audioPath(page).replace(/\.[^./]+$/, '') + '.words.json';
}

/** Returns the value after `flag` on the command line, or `fallback`. */
export function argValue(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
