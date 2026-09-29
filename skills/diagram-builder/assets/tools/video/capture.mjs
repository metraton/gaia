// Renders the narrated video frame by frame and encodes it with its narration.
//
//   npm run video:capture [-- --pages id,id] [--out name.mp4]
//
// The whole timeline is validated against the rendered deck before any frame
// is taken; only the selected pages are captured, laid end to end from t=0.
// Frames are PNG screenshots piped straight into ffmpeg, so none touch disk.
// They are supersampled: laid out at FRAME width×height CSS px, so the deck's
// breakpoints do not move, rendered at FRAME.supersample device px per CSS px,
// and downscaled with lanczos, so a page scaled below 1 keeps whole 1px borders.
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { DECK, OUT_DIR, argValue, audioPath, fail, loadPlaywright, readScript, requireDeck } from './deck.mjs';
import { FRAME, buildPlan, loadTimeline, readAlign, selectedPages } from './timeline.mjs';
import { loadPlan, openDeck } from './browser.mjs';

function encoder(plan, out) {
  const args = ['-hide_banner', '-nostats', '-y', '-f', 'image2pipe', '-framerate', String(FRAME.fps), '-i', '-'];
  plan.pages.forEach(p => args.push('-i', audioPath(p)));
  const video = `[0:v]scale=${FRAME.width}:${FRAME.height}:flags=lanczos[vout]`;
  const delayed = plan.pages.map((p, i) => `[${i + 1}:a]adelay=${Math.round(p.voiceAt * 1000)}:all=1[a${i}]`);
  const mix = plan.pages.length === 1
    ? '[a0]apad[aout]'
    : plan.pages.map((_, i) => `[a${i}]`).join('') + `amix=inputs=${plan.pages.length}:normalize=0,apad[aout]`;
  args.push('-filter_complex', [video, ...delayed, mix].join(';'), '-map', '[vout]', '-map', '[aout]',
    '-c:v', 'libx264', '-preset', 'slow', '-tune', 'animation', '-crf', '10', '-pix_fmt', 'yuv420p', '-r', String(FRAME.fps),
    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', out);
  return spawn('ffmpeg', args, { stdio: ['pipe', 'inherit', 'inherit'] });
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
mkdirSync(dirname(out), { recursive: true });
const ffmpeg = encoder(plan, out);
const done = new Promise((ok, no) => ffmpeg.on('close', code => (code === 0 ? ok() : no(new Error(`ffmpeg exited ${code}`)))));

const frames = Math.round(plan.duration * FRAME.fps);
const started = Date.now();
for (let f = 0; f < frames; f++) {
  await page.evaluate(t => window.__seek(t), f / FRAME.fps);
  const png = await page.screenshot({ type: 'png' });
  if (!ffmpeg.stdin.write(png)) await new Promise(r => ffmpeg.stdin.once('drain', r));
}
ffmpeg.stdin.end();
await done.catch(e => fail(e.message));
await browser.close();
const wall = (Date.now() - started) / 1000;
console.log(`[video] ${out}: ${frames} frames (${plan.duration.toFixed(2)} s) in ${wall.toFixed(1)} s, ${(1000 * wall / frames).toFixed(1)} ms/frame`);
