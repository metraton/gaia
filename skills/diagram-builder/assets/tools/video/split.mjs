// Cuts a captured video into one clip per page, at the page boundaries of the
// same plan the capture rendered from.
//
//   npm run video:split [-- --pages id,id] [--in name.mp4]
//
// Pass the same --pages the capture used, so every boundary is the same. Each
// clip is re-encoded, not stream-copied: a stream copy can only cut on a
// keyframe, and x264's default spacing would land cuts seconds off the boundary.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { OUT_DIR, argValue, fail, readScript, requireDeck } from './deck.mjs';
import { FRAME, buildPlan, loadTimeline, readAlign, selectedPages } from './timeline.mjs';

const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
const plan = buildPlan(timeline, readAlign(), selectedPages(timeline));
const unvoiced = plan.pages.filter(p => p.method === 'estimate').map(p => p.page);
if (unvoiced.length) fail(`split needs the aligned narration of ${unvoiced.join(', ')}; its boundaries would not match the capture`);
const input = resolve(OUT_DIR, argValue('--in', 'deck.mp4'));
if (!existsSync(input)) fail(`no captured video at ${input}; run npm run video:capture first`);
const outdir = join(OUT_DIR, 'pages');

mkdirSync(outdir, { recursive: true });
plan.pages.forEach((p, i) => {
  const clip = join(outdir, `${String(i + 1).padStart(2, '0')}-${p.page}.mp4`);
  const args = ['-hide_banner', '-nostats', '-loglevel', 'error', '-y', '-i', input,
    '-ss', p.start.toFixed(3), '-to', p.end.toFixed(3),
    '-c:v', 'libx264', '-preset', 'slow', '-tune', 'animation', '-crf', '10', '-pix_fmt', 'yuv420p', '-r', String(FRAME.fps),
    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', clip];
  const r = spawnSync('ffmpeg', args, { stdio: 'inherit' });
  if (r.status !== 0) fail(`ffmpeg failed on ${p.page} (exit ${r.status})`);
  console.log(`[video] ${clip}: ${p.start.toFixed(3)}-${p.end.toFixed(3)} s`);
});
