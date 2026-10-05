// Renders the narrated video and encodes it with its narration.
//
//   npm run video:capture [-- --pages id,id] [--quality 480p|preview|default|1440p|2160p]
//                            [--seconds n] [--out name.mp4]
//
// Only the selected pages are validated against the rendered deck, before any
// frame is taken, and captured, laid end to end from t=0; --seconds keeps only
// the first n seconds, to time a quality on a sample.
// A frame is a pure function of the state the driver sets at its instant, so
// only instants whose state differs from the previous frame's are captured,
// by several browsers at once, and every other frame holds the last capture.
// Captures are PNG files in out/video/frames/, named by the hash of the deck,
// the browser, the quality and the state they show: a run that is stopped keeps
// what it captured, and the next run captures only what is still missing.
// Frames are supersampled: laid out at FRAME width×height CSS px, so the deck's
// breakpoints do not move, rendered at the quality's supersample device px per
// CSS px, and scaled to its size with lanczos, so a page scaled below 1 keeps
// whole 1px borders.
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import { availableParallelism } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { DECK, HERE, OUT_DIR, argValue, audioPath, fail, loadPlaywright, readScript, requireDeck } from './deck.mjs';
import { FRAME, LOUDNESS, buildPlan, loadTimeline, readAlign, selectedPages, selectedQuality } from './timeline.mjs';
import { loadPlan, openDeck } from './browser.mjs';

const FRAMES_DIR = join(OUT_DIR, 'frames');
// Each browser renders and encodes its PNGs on its own processes; beyond about
// one browser per two cores they only contend for the same cores.
const BROWSERS = Math.max(1, Math.min(6, Math.floor(availableParallelism() / 2)));
const QUALITY = selectedQuality();
const DEVICE = { width: FRAME.width * QUALITY.supersample, height: FRAME.height * QUALITY.supersample };

// What a frame's pixels depend on besides its state: the deck, the driver, the
// frame settings, the device pixels it is taken at and the browser that draws it.
function renderFingerprint(browserVersion) {
  const hash = createHash('sha256').update(JSON.stringify({ ...FRAME, ...QUALITY, device: DEVICE })).update(browserVersion);
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
// The device scale is set again on the session that takes the screenshots: the
// context's deviceScaleFactor alone left every frame at 1920x1080, so each
// frame's size is checked against DEVICE and a frame at the wrong scale stops
// the capture.
// Returns how many of the frames it took were already on disk.
async function captureMissing(playwright, plan, missing) {
  let next = 0, recaptured = 0;
  const worker = async () => {
    const { browser, page } = await openDeck(playwright, QUALITY.supersample);
    await loadPlan(page, plan);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setDeviceMetricsOverride',
      { width: FRAME.width, height: FRAME.height, deviceScaleFactor: QUALITY.supersample, mobile: false });
    while (next < missing.length) {
      const { file, first } = missing[next++];
      if (existsSync(file)) recaptured++;
      await page.evaluate(t => window.__seek(t), first / QUALITY.fps);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
      const png = Buffer.from(data, 'base64');
      const size = { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
      if (size.width !== DEVICE.width || size.height !== DEVICE.height) {
        await browser.close();
        fail(`a frame came out ${size.width}x${size.height}, not the ${DEVICE.width}x${DEVICE.height} device px of --quality`);
      }
      const partial = `${file}.${process.pid}.partial`;
      writeFileSync(partial, png);
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
  const image = file => [`file '${file}'`, `option framerate ${QUALITY.fps}`];
  for (const r of runs) lines.push(...image(r.file), `duration ${(r.count / QUALITY.fps).toFixed(6)}`);
  lines.push(...image(runs[runs.length - 1].file));
  return lines.join('\n') + '\n';
}

// The first of loudnorm's two passes: what a page's narration measures, so the
// second pass is given the whole page's figures instead of estimating as it goes.
function measureLoudness(file) {
  const { integrated, truePeak, range } = LOUDNESS;
  const r = spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-af',
    `loudnorm=I=${integrated}:TP=${truePeak}:LRA=${range}:print_format=json`, '-f', 'null', '-'], { encoding: 'utf8' });
  const json = r.status === 0 && r.stderr.match(/\{[^{}]*"input_i"[^{}]*\}/);
  if (!json) fail(`could not measure the loudness of ${file} (ffmpeg exit ${r.status})`);
  return JSON.parse(json[0]);
}

// The second pass. linear=true applies one gain to the page; loudnorm itself
// falls back to its dynamic mode when that gain would push the true peak past
// the ceiling. It resamples to 192 kHz, hence the resample back to 48 kHz.
function normalized(m) {
  const { integrated, truePeak, range } = LOUDNESS;
  return `loudnorm=I=${integrated}:TP=${truePeak}:LRA=${range}:measured_I=${m.input_i}:measured_TP=${m.input_tp}` +
    `:measured_LRA=${m.input_lra}:measured_thresh=${m.input_thresh}:offset=${m.target_offset}:linear=true,aresample=48000`;
}

// The scale runs before fps, so each held image is scaled once, not once per
// frame it lasts. The audio is one slot per page, laid end to end with concat:
// mixing delayed loudnorm outputs with amix stopped the track after the first
// lead (measured on ffmpeg 6.1). A voiced slot is its narration after the lead,
// padded to the slot; a silent slot is silence. The track is trimmed to the
// video's exact length rather than ended with -shortest: once -frames:v stops
// the video, -shortest never fires and ffmpeg keeps padding audio forever.
function encode(plan, list, frames, out) {
  const args = ['-hide_banner', '-nostats', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list];
  const voiced = plan.pages.filter(p => p.audio !== undefined);
  voiced.forEach(p => args.push('-i', audioPath(p)));
  const video = `[0:v]scale=${QUALITY.width}:${QUALITY.height}:flags=lanczos,fps=${QUALITY.fps}[vout]`;
  const slots = plan.pages.map((p, i) => {
    const length = (p.end - p.start).toFixed(6);
    const fit = `apad=whole_dur=${length},atrim=duration=${length}[s${i}]`;
    if (p.audio === undefined) return `aevalsrc=0:s=48000:d=${length},${fit}`;
    const input = voiced.indexOf(p) + 1;
    return `[${input}:a]${normalized(measureLoudness(audioPath(p)))},adelay=${Math.round((p.voiceAt - p.start) * 1000)}:all=1,${fit}`;
  });
  const seconds = (frames / QUALITY.fps).toFixed(6);
  const track = plan.pages.map((_, i) => `[s${i}]`).join('') +
    `concat=n=${plan.pages.length}:v=0:a=1,atrim=duration=${seconds}[aout]`;
  args.push('-filter_complex', [video, ...slots, track].join(';'), '-map', '[vout]', '-map', '[aout]', '-frames:v', String(frames),
    '-c:v', 'libx264', '-preset', 'slow', '-tune', 'animation', '-crf', '10', '-pix_fmt', 'yuv420p', '-r', String(QUALITY.fps),
    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', out);
  const ffmpeg = spawn('ffmpeg', args, { stdio: ['ignore', 'inherit', 'inherit'] });
  return new Promise((ok, no) => ffmpeg.on('close', code => (code === 0 ? ok() : no(new Error(`ffmpeg exited ${code}`)))));
}

const doc = requireDeck();
const playwright = loadPlaywright();
const script = readScript();
const plan = buildPlan(loadTimeline(doc, script, selectedPages(script)), readAlign());
const unvoiced = plan.pages.filter(p => p.method === 'estimate').map(p => p.page);
if (unvoiced.length) {
  fail(`capture needs the narration of ${unvoiced.join(', ')}: voice the exported script to its declared audio, then run npm run video:align --prefix ${DECK}`);
}
const out = resolve(OUT_DIR, argValue('--out', 'deck.mp4'));
const seconds = Number(argValue('--seconds', plan.duration));
if (!(seconds > 0)) fail(`--seconds: "${argValue('--seconds')}" is not a positive number of seconds`);

const { browser, page } = await openDeck(playwright);
const errors = await loadPlan(page, plan);
if (errors.length) {
  await browser.close();
  fail(`the timeline does not fit the rendered deck:\n[video]   ${errors.join('\n[video]   ')}`);
}
const frames = Math.round(Math.min(plan.duration, seconds) * QUALITY.fps);
const fingerprint = renderFingerprint(browser.version());
const runs = holds((await page.evaluate(([n, fps]) => window.__frameStates(n, fps), [frames, QUALITY.fps]))
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
console.log(`[video] ${out}: ${frames} frames (${(frames / QUALITY.fps).toFixed(2)} s, ${QUALITY.width}x${QUALITY.height} at ${QUALITY.fps} fps) ` +
  `in ${process.uptime().toFixed(1)} s wall`);
