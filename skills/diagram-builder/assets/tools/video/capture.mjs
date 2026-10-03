// Renders the narrated video and encodes it with its narration.
//
//   npm run video:capture [-- --pages id,id] [--out name.mp4]
//
// The whole timeline is validated against the rendered deck before any frame
// is taken; only the selected pages are captured, laid end to end from t=0.
// A frame is a pure function of the state the driver sets at its instant, so
// only instants whose state differs from the previous frame's are captured,
// by several browsers at once, and every other frame holds the last capture.
// Captures are PNG files in out/video/frames/, named by the hash of the deck,
// the browser and the state they show: a run that is stopped keeps what it
// captured, and the next run captures only what is still missing.
// Frames are supersampled: laid out at FRAME width×height CSS px, so the deck's
// breakpoints do not move, rendered at FRAME.supersample device px per CSS px,
// and downscaled with lanczos, so a page scaled below 1 keeps whole 1px borders.
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import { availableParallelism } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { DECK, HERE, OUT_DIR, argValue, audioPath, fail, loadPlaywright, readScript, requireDeck } from './deck.mjs';
import { FRAME, buildPlan, loadTimeline, readAlign, selectedPages } from './timeline.mjs';
import { loadPlan, openDeck } from './browser.mjs';

const FRAMES_DIR = join(OUT_DIR, 'frames');
// Each browser renders and encodes its PNGs on its own processes; beyond about
// one browser per two cores they only contend for the same cores.
const BROWSERS = Math.max(1, Math.min(6, Math.floor(availableParallelism() / 2)));

// What a frame's pixels depend on besides its state: the deck, the driver, the
// frame settings and the browser that draws it.
function renderFingerprint(browserVersion) {
  const hash = createHash('sha256').update(JSON.stringify(FRAME)).update(browserVersion);
  const files = ['index.html', 'data/data.generated.js',
    ...readdirSync(join(DECK, 'engine')).sort().map(f => join('engine', f))];
  for (const f of files) hash.update(f).update(readFileSync(join(DECK, f)));
  return hash.update(readFileSync(join(HERE, 'driver.js'))).digest();
}

const frameFile = (fingerprint, state) =>
  join(FRAMES_DIR, createHash('sha256').update(fingerprint).update(state).digest('hex').slice(0, 32) + '.png');

// Consecutive frames with the same file become one held image.
function holds(files) {
  const runs = [];
  files.forEach((file, f) => {
    if (runs.length && runs[runs.length - 1].file === file) runs[runs.length - 1].count++;
    else runs.push({ file, first: f, count: 1 });
  });
  return runs;
}

// Takes each missing frame at the instant it first appears, sharing the queue
// between browsers. A PNG is written under a temporary name and renamed, so a
// stopped run never leaves a truncated frame that the next run would trust.
// Returns how many of the frames it took were already on disk.
async function captureMissing(playwright, plan, missing) {
  let next = 0, recaptured = 0;
  const worker = async () => {
    const { browser, page } = await openDeck(playwright);
    await loadPlan(page, plan);
    const cdp = await page.context().newCDPSession(page);
    while (next < missing.length) {
      const { file, first } = missing[next++];
      if (existsSync(file)) recaptured++;
      await page.evaluate(t => window.__seek(t), first / FRAME.fps);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
      const partial = `${file}.${process.pid}.partial`;
      writeFileSync(partial, Buffer.from(data, 'base64'));
      renameSync(partial, file);
    }
    await browser.close();
  };
  await Promise.all(Array.from({ length: Math.min(BROWSERS, missing.length) }, worker));
  return recaptured;
}

// The held images as an ffconcat list: each lasts its frames, and the last is
// listed twice because the demuxer gives the final entry no duration. Every
// image declares the frame rate: without it the demuxer times images in the
// image reader's default 1/25 s, and neighbouring one-frame images collapse.
function concatList(runs) {
  const lines = ['ffconcat version 1.0'];
  const image = file => [`file '${file}'`, `option framerate ${FRAME.fps}`];
  for (const r of runs) lines.push(...image(r.file), `duration ${(r.count / FRAME.fps).toFixed(6)}`);
  lines.push(...image(runs[runs.length - 1].file));
  return lines.join('\n') + '\n';
}

// The scale runs before fps, so each held image is downscaled once, not once
// per frame it lasts. The audio is padded to the video's exact length rather
// than endlessly with -shortest: once -frames:v stops the video, -shortest
// never fires and ffmpeg keeps padding audio forever (measured on ffmpeg 6.1).
function encode(plan, list, frames, out) {
  const args = ['-hide_banner', '-nostats', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list];
  plan.pages.forEach(p => args.push('-i', audioPath(p)));
  const video = `[0:v]scale=${FRAME.width}:${FRAME.height}:flags=lanczos,fps=${FRAME.fps}[vout]`;
  const delayed = plan.pages.map((p, i) => `[${i + 1}:a]adelay=${Math.round(p.voiceAt * 1000)}:all=1[a${i}]`);
  const pad = `apad=whole_dur=${(frames / FRAME.fps).toFixed(6)}[aout]`;
  const mix = plan.pages.length === 1
    ? `[a0]${pad}`
    : plan.pages.map((_, i) => `[a${i}]`).join('') + `amix=inputs=${plan.pages.length}:normalize=0,${pad}`;
  args.push('-filter_complex', [video, ...delayed, mix].join(';'), '-map', '[vout]', '-map', '[aout]', '-frames:v', String(frames),
    '-c:v', 'libx264', '-preset', 'slow', '-tune', 'animation', '-crf', '10', '-pix_fmt', 'yuv420p', '-r', String(FRAME.fps),
    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', out);
  const ffmpeg = spawn('ffmpeg', args, { stdio: ['ignore', 'inherit', 'inherit'] });
  return new Promise((ok, no) => ffmpeg.on('close', code => (code === 0 ? ok() : no(new Error(`ffmpeg exited ${code}`)))));
}

const doc = requireDeck();
const playwright = loadPlaywright();
const timeline = loadTimeline(doc, readScript());
const align = readAlign();
const plan = buildPlan(timeline, align, selectedPages(timeline));
const unvoiced = plan.pages.filter(p => p.method === 'estimate').map(p => p.page);
if (unvoiced.length) {
  fail(`capture needs the narration of ${unvoiced.join(', ')}: voice the exported script to its declared audio, then run npm run video:align --prefix ${DECK}`);
}
const out = resolve(OUT_DIR, argValue('--out', 'deck.mp4'));

const { browser, page } = await openDeck(playwright);
const errors = await loadPlan(page, buildPlan(timeline, align));
if (errors.length) {
  await browser.close();
  fail(`the timeline does not fit the rendered deck:\n[video]   ${errors.join('\n[video]   ')}`);
}
await loadPlan(page, plan);
const frames = Math.round(plan.duration * FRAME.fps);
const fingerprint = renderFingerprint(browser.version());
const runs = holds((await page.evaluate(([n, fps]) => window.__frameStates(n, fps), [frames, FRAME.fps]))
  .map(state => frameFile(fingerprint, state)));
await browser.close();

mkdirSync(FRAMES_DIR, { recursive: true });
const distinct = [...new Map(runs.map(r => [r.file, r])).values()];
const cached = new Set(distinct.filter(r => existsSync(r.file)).map(r => r.file));
const missing = distinct.filter(r => !cached.has(r.file));
console.log(`[video] ${frames} frames, ${runs.length} held images of ${distinct.length} distinct frames: ` +
  `${cached.size} in the cache, ${missing.length} to capture with ${Math.min(BROWSERS, missing.length)} browsers`);
let t0 = process.uptime();
const recaptured = await captureMissing(playwright, plan, missing);
console.log(`[video] captured ${missing.length} frames in ${(process.uptime() - t0).toFixed(1)} s; ` +
  `recaptured ${recaptured} of the ${cached.size} already in the cache`);

mkdirSync(dirname(out), { recursive: true });
const list = join(FRAMES_DIR, 'last-capture.ffconcat');
writeFileSync(list, concatList(runs));
const partial = out.replace(/\.mp4$/, '') + '.partial.mp4';
t0 = process.uptime();
await encode(plan, list, frames, partial).catch(e => fail(e.message));
renameSync(partial, out);
console.log(`[video] encoded in ${(process.uptime() - t0).toFixed(1)} s`);
console.log(`[video] ${out}: ${frames} frames (${plan.duration.toFixed(2)} s) in ${process.uptime().toFixed(1)} s wall`);
