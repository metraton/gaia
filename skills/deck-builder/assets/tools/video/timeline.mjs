// The video's timeline, derived from the deck: pages in the deck's order, what
// each sentence shows, and only the chips the page declares. The script says
// when and in which order; the deck says what, and a box never shows before the
// section that holds it.
// The plan turns it into seconds: every page is a slot of lead + speech + tail,
// laid end to end from t=0, so capture and split cut at the same boundaries.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DECK, VIDEO_DIR, argValue, fail } from './deck.mjs';

export const FRAME = { fps: 60, width: 1920, height: 1080, theme: 'light', supersample: 2 };
// What `--quality` renders. The deck is always laid out at FRAME width×height
// CSS px, so no quality moves the layout: one sets only the device px per CSS px
// the frame is drawn at, the size it is scaled to, and the frame rate.
export const QUALITIES = {
  '480p': { fps: 30, supersample: 1, width: 854, height: 480 },
  preview: { fps: 30, supersample: 1, width: 1280, height: 720 },
  default: { fps: FRAME.fps, supersample: FRAME.supersample, width: FRAME.width, height: FRAME.height },
  '1440p': { fps: 60, supersample: 2, width: 2560, height: 1440 },
  '2160p': { fps: 60, supersample: 2, width: 3840, height: 2160 },
};
// Integrated loudness and true-peak ceiling every page's narration is brought
// to when it is muxed, so pages voiced on different runs play at one level.
export const LOUDNESS = { integrated: -16, truePeak: -1.5, range: 11 };
// A reveal is a fade only: no rise and no ring, so the frame shows the deck's
// own layout at every instant.
export const MOTION = {
  fade: 0.4, lead: 0.8, tail: 1.2, reveal: { duration: 0.7, anticipation: 0.3 }, type: { interval: 0.25 },
};
export const ALIGN_FILE = join(VIDEO_DIR, 'align.json');
// Speech rate of the estimate used while a page has no aligned audio: enough to
// check and frame the timeline, never to capture it.
const CHARS_PER_SECOND = 15;
const WORD = /[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu;

/** The letters and digits of a text, lower-cased: what a said word and a timed word share. */
export const letters = s => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

// Every id of the page, each with the nearest id that holds it.
function parents(nodes, parentOf = new Map(), parent = null) {
  for (const n of nodes || []) {
    if (n.id) parentOf.set(n.id, parent);
    parents(n.children, parentOf, n.id ?? parent);
  }
  return parentOf;
}

// Where a cue's word first occurs in the sentence: its character index, for the
// estimate, and how many letters precede it, to find it among timed words.
function wordAnchor(say, word) {
  for (const m of say.matchAll(WORD)) {
    if (letters(m[0]) === letters(word)) return { char: m.index, offset: letters(say.slice(0, m.index)).length };
  }
  return null;
}

function cueAction(c) {
  if (c.show) return { reveal: c.show };
  if (c.chip !== undefined) return { chip: c.chip };
  return c.type !== undefined ? { type: c.type } : { ask: c.ask };
}

// A sentence's cues in the order they fire: its own show, chip and typing at its
// start, then its word cues, which must be listed in the order their words are said.
function sentenceCues(s, at, errors) {
  const fires = [];
  if (s.show && s.show.length) fires.push({ reveal: s.show });
  if (s.chip !== undefined) fires.push({ chip: s.chip });
  if (s.type !== undefined) fires.push({ type: s.type });
  if (s.ask !== undefined) fires.push({ ask: s.ask });
  let said = -1;
  for (const cue of s.cues || []) {
    const anchor = wordAnchor(s.say, cue.at);
    if (!anchor) { errors.push(`${at}: cue at "${cue.at}" is not a word of the sentence`); continue; }
    if (anchor.char < said) errors.push(`${at}: cue at "${cue.at}" is listed after a later word; list cues in the order they are said`);
    said = Math.max(said, anchor.char);
    fires.push({ word: cue.at, ...anchor, ...cueAction(cue) });
  }
  return fires;
}

// A page's cues fire in the order they are listed, so the position of a box's
// first reveal in that list stands for its time when no audio is aligned yet.
function derivePage(page, sp, errors) {
  const parentOf = parents(page.sections);
  const chips = (page.filters || []).map(f => f.key);
  const shown = new Set();
  const cues = [];
  const firstShown = new Map();
  sp.sentences.forEach((s, k) => {
    const at = `${sp.page} sentence ${k + 1}`;
    for (const cue of sentenceCues(s, at, errors)) {
      for (const id of cue.reveal || []) {
        if (!parentOf.has(id)) errors.push(`${at}: shows "${id}", which is not on the page`);
        else if (!firstShown.has(id)) firstShown.set(id, { position: cues.length, at });
        shown.add(id);
      }
      if (cue.chip !== undefined && cue.chip !== 'all' && !chips.includes(cue.chip)) {
        errors.push(`${at}: chip "${cue.chip}" is not a chip of the page (${chips.join(', ') || 'none'})`);
      }
      if (cue.type !== undefined && !parentOf.has(cue.type)) errors.push(`${at}: types into "${cue.type}", which is not on the page`);
      if (cue.ask !== undefined && !parentOf.has(cue.ask)) errors.push(`${at}: asks in "${cue.ask}", which is not on the page`);
      cues.push({ s: k + 1, ...cue });
    }
  });
  for (const [id, own] of firstShown) {
    for (let a = parentOf.get(id); a; a = parentOf.get(a)) {
      const section = firstShown.get(a);
      if (section && section.position > own.position) {
        errors.push(`${own.at}: shows "${id}" before its section "${a}", which ${section.at.replace(`${sp.page} `, '')} shows`);
      }
    }
  }
  const base = (page.sections || []).map(n => n.id).filter(id => id && !shown.has(id));
  return { page: sp.page, audio: sp.audio, duration: sp.duration, sentences: sp.sentences.map(s => s.say),
    timing: sp.sentences.map(s => ({ seconds: s.seconds, pause: s.pause ?? 0 })), base, cues };
}

/**
 * Derives the timeline of the script's pages named in `pageIds` from the deck, or
 * fails naming every mismatch; a page left out is neither checked nor derived.
 */
export function loadTimeline(doc, script, pageIds = script.pages.map(p => p.page)) {
  const ids = doc.pages.map(p => p.id);
  const errors = [];
  let last = -1;
  const pages = [];
  for (const sp of script.pages.filter(p => pageIds.includes(p.page))) {
    const index = ids.indexOf(sp.page);
    if (index < 0) { errors.push(`${sp.page}: not a visible page of the deck (${ids.join(', ')})`); continue; }
    if (index < last) errors.push(`${sp.page}: listed after a page the deck places later; follow the deck's order`);
    last = Math.max(last, index);
    pages.push(derivePage(doc.pages[index], sp, errors));
  }
  if (errors.length) fail(`the script does not follow the deck:\n[video]   ${errors.join('\n[video]   ')}`);
  return { pages, placeholder: script.placeholder };
}

/** The pages that declare narration audio; a silent page has a fixed duration instead. */
export const voicedPages = timeline => timeline.pages.filter(p => p.audio !== undefined);

/** Returns video/align.json, or an alignment with no pages when none was made. */
export function readAlign() {
  return existsSync(ALIGN_FILE) ? JSON.parse(readFileSync(ALIGN_FILE, 'utf8')) : { pages: [] };
}

// Sentence times inside a page's audio: from align.json when it was made for
// these sentences, else an estimate by length. A stale alignment fails. A silent
// page takes each sentence's own seconds and pause, or spreads the estimate over
// its declared duration; capture accepts both.
function sentenceTimes(page, align) {
  const a = align.pages.find(x => x.page === page.page);
  if (a) {
    if (a.sentences.map(s => s.text).join('\n') !== page.sentences.join('\n')) {
      fail(`${page.page}: align.json was made for other sentences; run npm run video:align --prefix ${DECK}`);
    }
    return { method: a.method, duration: a.duration, sentences: a.sentences };
  }
  let t = 0;
  if (page.timing.every(x => x.seconds !== undefined)) {
    const sentences = page.sentences.map((text, k) => {
      const start = t, end = t + page.timing[k].seconds;
      t = end + page.timing[k].pause;
      return { text, start, end };
    });
    return { method: 'fixed', duration: t, sentences };
  }
  const sentences = page.sentences.map(text => {
    const start = t;
    t += text.length / CHARS_PER_SECOND;
    return { text, start, end: t };
  });
  if (page.duration === undefined) return { method: 'estimate', duration: t, sentences };
  const k = page.duration / t;
  return { method: 'fixed', duration: page.duration, sentences: sentences.map(s => ({ ...s, start: s.start * k, end: s.end * k })) };
}

// The start of the timed word that holds the letter at `offset` of its sentence;
// punctuation carries no letters and is skipped.
function wordStart(words, offset) {
  let spelled = 0, start = words[0].start;
  for (const w of words) {
    const n = letters(w.word).length;
    if (!n) continue;
    if (spelled > offset) break;
    start = w.start;
    spelled += n;
  }
  return start;
}

// Seconds of a cue inside its page's audio. A word cue takes its word's time
// when align kept the page's word timings; without them it is placed by its
// share of the sentence's characters and marked estimated, so the chips of one
// sentence still fire apart and in the order they are said.
function cueTime(c, sentence, say) {
  if (c.word === undefined) return { t: sentence.start };
  if (sentence.words) return { t: wordStart(sentence.words, c.offset) };
  return { t: sentence.start + (c.char / say.length) * (sentence.end - sentence.start), estimated: true };
}

/** Lays the chosen pages end to end and resolves every cue to seconds. */
export function buildPlan(timeline, align, pageIds = timeline.pages.map(p => p.page)) {
  let clock = 0;
  const pages = timeline.pages.filter(p => pageIds.includes(p.page)).map(p => {
    const times = sentenceTimes(p, align);
    const start = clock;
    const voiceAt = start + MOTION.lead;
    const end = voiceAt + times.duration + MOTION.tail;
    clock = end;
    const cues = p.cues.map(({ char, offset, ...c }) => {
      const at = cueTime({ ...c, char, offset }, times.sentences[c.s - 1], p.sentences[c.s - 1]);
      return { ...c, t: voiceAt + at.t, ...(at.estimated ? { estimated: true } : {}) };
    });
    const estimated = cues.filter(c => c.estimated).length;
    if (estimated) {
      console.log(`[video] ${p.page}: warning: ${estimated} word cue(s) with no word timings (timing ${times.method}); ` +
        `placed by their share of the sentence's characters`);
    }
    return { page: p.page, audio: p.audio, method: times.method, start, voiceAt, end, base: p.base, cues,
      sentences: times.sentences };
  });
  return { fade: MOTION.fade, reveal: MOTION.reveal, type: MOTION.type, prompt: { placeholder: timeline.placeholder },
    duration: clock, pages };
}

/** `--quality <name>` as one of QUALITIES, `default` when absent; an unknown name fails. */
export function selectedQuality() {
  const name = argValue('--quality', 'default');
  if (!QUALITIES[name]) fail(`--quality: "${name}" is not a quality (${Object.keys(QUALITIES).join(', ')})`);
  return QUALITIES[name];
}

/**
 * `--pages id,id` as page ids in the order of `source.pages` (the script or a
 * timeline), every page when the flag is absent; an id outside it fails.
 */
export function selectedPages(source) {
  const all = source.pages.map(p => p.page);
  const asked = argValue('--pages', all.join(',')).split(',').map(s => s.trim()).filter(Boolean);
  const unknown = asked.filter(id => !all.includes(id));
  if (unknown.length) fail(`--pages: not in the script: ${unknown.join(', ')} (known: ${all.join(', ')})`);
  return all.filter(id => asked.includes(id));
}
