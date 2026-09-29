// The video's timeline, derived from the deck: pages in the deck's order, what
// each sentence shows in the order the deck places it, and only the chips the
// page declares. The script says when; the deck says what and in which order.
// The plan turns it into seconds: every page is a slot of lead + speech + tail,
// laid end to end from t=0, so capture and split cut at the same boundaries.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DECK, VIDEO_DIR, argValue, fail } from './deck.mjs';

export const FRAME = { fps: 60, width: 1920, height: 1080, theme: 'light', supersample: 2 };
// A reveal is a fade only: no rise and no ring, so the frame shows the deck's
// own layout at every instant.
export const MOTION = { fade: 0.4, lead: 0.8, tail: 1.2, reveal: { duration: 0.7, anticipation: 0.3 } };
export const ALIGN_FILE = join(VIDEO_DIR, 'align.json');
// Speech rate of the estimate used while a page has no aligned audio: enough to
// check and frame the timeline, never to capture it.
const CHARS_PER_SECOND = 15;
const WORD = /[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu;

/** The letters and digits of a text, lower-cased: what a said word and a timed word share. */
export const letters = s => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

function preorder(nodes, out = []) {
  for (const n of nodes || []) {
    if (n.id) out.push(n.id);
    preorder(n.children, out);
  }
  return out;
}

// Where a cue's word first occurs in the sentence: its character index, for the
// estimate, and how many letters precede it, to find it among timed words.
function wordAnchor(say, word) {
  for (const m of say.matchAll(WORD)) {
    if (letters(m[0]) === letters(word)) return { char: m.index, offset: letters(say.slice(0, m.index)).length };
  }
  return null;
}

// A sentence's cues in the order they fire: its own show and chip at its start,
// then its word cues, which must be listed in the order their words are said.
function sentenceCues(s, at, errors) {
  const fires = [];
  if (s.show && s.show.length) fires.push({ reveal: s.show });
  if (s.chip !== undefined) fires.push({ chip: s.chip });
  let said = -1;
  for (const cue of s.cues || []) {
    const anchor = wordAnchor(s.say, cue.at);
    if (!anchor) { errors.push(`${at}: cue at "${cue.at}" is not a word of the sentence`); continue; }
    if (anchor.char < said) errors.push(`${at}: cue at "${cue.at}" is listed after a later word; list cues in the order they are said`);
    said = Math.max(said, anchor.char);
    fires.push({ word: cue.at, ...anchor, ...(cue.show ? { reveal: cue.show } : { chip: cue.chip }) });
  }
  return fires;
}

function derivePage(page, sp, errors) {
  const order = preorder(page.sections);
  const chips = (page.filters || []).map(f => f.key);
  const shown = new Set();
  const cues = [];
  let reached = -1;
  sp.sentences.forEach((s, k) => {
    const at = `${sp.page} sentence ${k + 1}`;
    for (const cue of sentenceCues(s, at, errors)) {
      for (const id of cue.reveal || []) {
        const i = order.indexOf(id);
        if (i < 0) errors.push(`${at}: shows "${id}", which is not on the page`);
        else if (i < reached) errors.push(`${at}: shows "${id}" after "${order[reached]}", out of the deck's order`);
        else reached = i;
        shown.add(id);
      }
      if (cue.chip !== undefined && cue.chip !== 'all' && !chips.includes(cue.chip)) {
        errors.push(`${at}: chip "${cue.chip}" is not a chip of the page (${chips.join(', ') || 'none'})`);
      }
      cues.push({ s: k + 1, ...cue });
    }
  });
  const base = (page.sections || []).map(n => n.id).filter(id => id && !shown.has(id));
  return { page: sp.page, audio: sp.audio, sentences: sp.sentences.map(s => s.say), base, cues };
}

/** Derives the timeline of the script's pages from the deck, or fails naming every mismatch. */
export function loadTimeline(doc, script) {
  const ids = doc.pages.map(p => p.id);
  const errors = [];
  let last = -1;
  const pages = [];
  for (const sp of script.pages) {
    const index = ids.indexOf(sp.page);
    if (index < 0) { errors.push(`${sp.page}: not a visible page of the deck (${ids.join(', ')})`); continue; }
    if (index < last) errors.push(`${sp.page}: listed after a page the deck places later; follow the deck's order`);
    last = Math.max(last, index);
    pages.push(derivePage(doc.pages[index], sp, errors));
  }
  if (errors.length) fail(`the script does not follow the deck:\n[video]   ${errors.join('\n[video]   ')}`);
  return { pages };
}

/** Returns video/align.json, or an alignment with no pages when none was made. */
export function readAlign() {
  return existsSync(ALIGN_FILE) ? JSON.parse(readFileSync(ALIGN_FILE, 'utf8')) : { pages: [] };
}

// Sentence times inside a page's audio: from align.json when it was made for
// these sentences, else an estimate by length. A stale alignment fails.
function sentenceTimes(page, align) {
  const a = align.pages.find(x => x.page === page.page);
  if (a) {
    if (a.sentences.map(s => s.text).join('\n') !== page.sentences.join('\n')) {
      fail(`${page.page}: align.json was made for other sentences; run npm run video:align --prefix ${DECK}`);
    }
    return { method: a.method, duration: a.duration, sentences: a.sentences };
  }
  let t = 0;
  const sentences = page.sentences.map(text => {
    const start = t;
    t += text.length / CHARS_PER_SECOND;
    return { text, start, end: t };
  });
  return { method: 'estimate', duration: t, sentences };
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
  return { fade: MOTION.fade, reveal: MOTION.reveal, duration: clock, pages };
}

/** `--pages id,id` as page ids in the deck's order; an id outside the timeline fails. */
export function selectedPages(timeline) {
  const all = timeline.pages.map(p => p.page);
  const asked = argValue('--pages', all.join(',')).split(',').map(s => s.trim()).filter(Boolean);
  const unknown = asked.filter(id => !all.includes(id));
  if (unknown.length) fail(`--pages: not in the script: ${unknown.join(', ')} (known: ${all.join(', ')})`);
  return all.filter(id => asked.includes(id));
}
