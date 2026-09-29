// Writes video/align.json: the start and end of every sentence, in seconds
// inside its page's audio. Cues name sentences, never seconds, so this file is
// the only timing source and can be replaced without touching the script.
//
//   npm run video:align
//
// Per page, the character estimate spreads the sentences over the speech span
// in proportion to their length; ffmpeg silencedetect proposes pauses, and the
// sentence boundaries are matched to them monotonically, preferring long
// pauses near the estimate. The match is kept only when every matched pause
// lies within TOLERANCE_S of its estimate; otherwise the page keeps the estimate.
import { spawnSync } from 'node:child_process';
import { existsSync, writeFileSync } from 'node:fs';
import { audioPath, fail, readScript, requireDeck } from './deck.mjs';
import { ALIGN_FILE, loadTimeline } from './timeline.mjs';

const SILENCE_FILTER = 'silencedetect=noise=-35dB:d=0.15';
const TOLERANCE_S = 2.0;
const PAUSE_WEIGHT = 1.5;
const EDGE_S = 0.01;

function run(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: 'utf8' });
  if (r.status !== 0) fail(`${cmd} ${args.join(' ')} failed: ${(r.stderr || r.error?.message || '').trim()}`);
  return r;
}

function durationOf(wav) {
  const r = run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', wav]);
  return Number(r.stdout.trim());
}

function silencesOf(wav, duration) {
  const r = run('ffmpeg', ['-hide_banner', '-nostats', '-i', wav, '-af', SILENCE_FILTER, '-f', 'null', '-']);
  const gaps = [];
  let start = null;
  for (const line of r.stderr.split('\n')) {
    const s = line.match(/silence_start: (-?[\d.]+)/);
    if (s) start = Math.max(0, Number(s[1]));
    const e = line.match(/silence_end: ([\d.]+)/);
    if (e && start !== null) { gaps.push({ start, end: Number(e[1]) }); start = null; }
  }
  if (start !== null) gaps.push({ start, end: duration });
  return gaps;
}

// Boundary k (between sentence k and k+1) is expected where the characters
// spoken so far reach their share of the speech span.
function charBoundaries(sentences, speechStart, speechEnd) {
  const total = sentences.reduce((n, s) => n + s.length, 0);
  const out = [];
  let spoken = 0;
  for (let k = 0; k < sentences.length - 1; k++) {
    spoken += sentences[k].length;
    out.push(speechStart + (spoken / total) * (speechEnd - speechStart));
  }
  return out;
}

// Monotonic assignment of boundaries to distinct pauses minimising
// |pause middle − estimate| − PAUSE_WEIGHT × pause length.
function matchPauses(expected, gaps) {
  const n = expected.length, m = gaps.length;
  if (n === 0) return [];
  if (m < n) return null;
  const cost = (k, j) => Math.abs((gaps[j].start + gaps[j].end) / 2 - expected[k]) - PAUSE_WEIGHT * (gaps[j].end - gaps[j].start);
  const best = Array.from({ length: n }, () => new Array(m).fill(Infinity));
  const from = Array.from({ length: n }, () => new Array(m).fill(-1));
  for (let j = 0; j < m; j++) best[0][j] = cost(0, j);
  for (let k = 1; k < n; k++) {
    let runMin = Infinity, runArg = -1;
    for (let j = 0; j < m; j++) {
      if (j > 0 && best[k - 1][j - 1] < runMin) { runMin = best[k - 1][j - 1]; runArg = j - 1; }
      if (runArg >= 0) { best[k][j] = runMin + cost(k, j); from[k][j] = runArg; }
    }
  }
  let j = best[n - 1].indexOf(Math.min(...best[n - 1]));
  const picked = new Array(n);
  for (let k = n - 1; k >= 0; k--) { picked[k] = j; j = from[k][j]; }
  return picked.map(i => gaps[i]);
}

function alignPage(page, wav) {
  const { sentences } = page;
  const duration = durationOf(wav);
  const gaps = silencesOf(wav, duration);
  const lead = gaps.find(g => g.start <= EDGE_S);
  const trail = gaps.find(g => g.end >= duration - EDGE_S && g !== lead);
  const speechStart = lead ? lead.end : 0;
  const speechEnd = trail ? trail.start : duration;
  const interior = gaps.filter(g => g !== lead && g !== trail);
  const expected = charBoundaries(sentences, speechStart, speechEnd);
  const matched = matchPauses(expected, interior);
  const deviations = matched ? matched.map((g, k) => Math.abs((g.start + g.end) / 2 - expected[k])) : [];
  const accepted = matched !== null && deviations.every(d => d <= TOLERANCE_S);
  const starts = [speechStart, ...(accepted ? matched.map(g => g.end) : expected)];
  const ends = [...(accepted ? matched.map(g => g.start) : expected), speechEnd];
  const round = x => Math.round(x * 1000) / 1000;
  return {
    page: page.page,
    audio: page.audio,
    duration: round(duration),
    method: accepted ? 'silencedetect' : 'chars',
    speech: [round(speechStart), round(speechEnd)],
    pauses: interior.length,
    sentences: sentences.map((text, k) => ({ text, start: round(starts[k]), end: round(ends[k]) })),
  };
}

const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
const wavs = timeline.pages.map(p => [p, audioPath(p)]);
const missing = wavs.filter(([, wav]) => !existsSync(wav)).map(([p, wav]) => `${p.page} (${wav})`);
if (missing.length) fail(`no narration audio for ${missing.join(', ')}: voice the exported script (npm run video:script) to those paths`);
const pages = wavs.map(([p, wav]) => alignPage(p, wav));
writeFileSync(ALIGN_FILE, JSON.stringify({ pages }, null, 2) + '\n');
for (const p of pages) {
  console.log(`[video] ${p.page}: method=${p.method} duration=${p.duration}s speech=${p.speech.join('-')}s ` +
    `sentences=${p.sentences.length} pauses=${p.pauses}`);
}
