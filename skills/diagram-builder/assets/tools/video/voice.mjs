// Voices each page. Every provider keeps one contract: it reads the page's text
// exported by video:script and leaves the audio at the path the script declares,
// plus, when it can time words, the page's words file beside it (wordsPath).
//
//   npm run video:voice [-- --provider kokoro|manual] [--voice <id>] [--kokoro-dir <dir>]
//
// kokoro runs an existing local install and installs nothing. When that install
// is absent or fails, the step continues as manual, which only reports where each
// page's audio goes and whether it is already there; neither case is an error.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { DECK, argValue, audioPath, fail, readScript, requireDeck, scriptTextPath, wordsPath } from './deck.mjs';
import { loadTimeline } from './timeline.mjs';

const PROVIDERS = ['kokoro', 'manual'];
const KOKORO_DIR = join(homedir(), '.local', 'share', 'gaia-tts', 'kokoro');
const KOKORO_VOICE = 'am_michael';

function manual(jobs) {
  for (const j of jobs) {
    if (existsSync(j.audio)) console.log(`[video] ${j.page}: audio found at ${j.audio}`);
    else console.log(`[video] ${j.page}: no audio yet; voice ${j.text} and leave the audio at ${j.audio}`);
  }
}

/** Voices every page with Kokoro; returns why it could not, or null when every page was voiced. */
function kokoro(jobs, dir, voice) {
  const python = join(dir, '.venv', 'bin', 'python');
  const say = join(dir, 'kokoro_say.py');
  const missing = [python, say].filter(f => !existsSync(f));
  if (missing.length) return `Kokoro is not installed at ${dir} (missing ${missing.join(', ')})`;
  for (const j of jobs) {
    const args = [say, '--text-file', j.text, '--voice', voice, '--out', j.audio, '--words', j.words];
    const r = spawnSync(python, args, { encoding: 'utf8' });
    if (r.status !== 0) {
      const last = (r.stderr || r.error?.message || `exit ${r.status}`).trim().split('\n').pop();
      return `Kokoro failed on ${j.page}: ${last}`;
    }
    console.log(`[video] ${j.page}: kokoro ${voice} -> ${j.audio}, word timings -> ${j.words}`);
  }
  return null;
}

const provider = argValue('--provider', 'kokoro');
if (!PROVIDERS.includes(provider)) fail(`--provider: "${provider}" is not a voice provider (${PROVIDERS.join(', ')})`);
const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
const jobs = timeline.pages.map((p, i) => ({
  page: p.page, text: scriptTextPath(p, i), audio: audioPath(p), words: wordsPath(p), said: p.sentences.join('\n') + '\n',
}));
const stale = jobs.filter(j => !existsSync(j.text) || readFileSync(j.text, 'utf8') !== j.said).map(j => j.text);
if (stale.length) fail(`the exported text is missing or older than the script: ${stale.join(', ')}; run npm run video:script --prefix ${DECK}`);

const failed = provider === 'kokoro' ? kokoro(jobs, argValue('--kokoro-dir', KOKORO_DIR), argValue('--voice', KOKORO_VOICE)) : null;
if (failed) console.log(`[video] ${failed}; continuing with the manual provider`);
if (provider === 'manual' || failed) manual(jobs);
