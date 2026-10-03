// Voices each page. Every provider keeps one contract: it reads the page's text
// exported by video:script and leaves the audio at the path the script declares,
// plus, when it can time words, the page's words file beside it (wordsPath).
//
//   npm run video:voice [-- --provider kokoro|manual] [--voice <id>[,<id>...]] [--speed <x>]
//                          [--kokoro-venv <dir>] [--kokoro-model <dir>]
//
// kokoro runs this folder's kokoro_say.py with the interpreter of a venv the
// person created, on a model they downloaded; it installs nothing. When either
// is absent or Kokoro fails, the step continues as manual, which only reports
// where each page's audio goes and whether it is already there; neither case is
// an error.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { DECK, HERE, argValue, audioPath, fail, readScript, requireDeck, scriptTextPath, wordsPath } from './deck.mjs';
import { loadTimeline } from './timeline.mjs';

const PROVIDERS = ['kokoro', 'manual'];
const KOKORO_HOME = join(homedir(), '.local', 'share', 'gaia-tts', 'kokoro');
const KOKORO_SAY = join(HERE, 'kokoro_say.py');
const KOKORO_VOICE = 'am_michael';
const KOKORO_SPEED = '1.0';

function manual(jobs) {
  for (const j of jobs) {
    if (existsSync(j.audio)) console.log(`[video] ${j.page}: audio found at ${j.audio}`);
    else console.log(`[video] ${j.page}: no audio yet; voice ${j.text} and leave the audio at ${j.audio}`);
  }
}

/**
 * Voices every page with Kokoro; returns why it could not, or null when every page was voiced.
 * `voice` is one voice or a comma-separated blend, each name a .pt under the model's voices/.
 */
function kokoro(jobs, venv, model, voice, speed) {
  const python = join(venv, 'bin', 'python');
  const voices = voice.split(',').map(v => join(model, 'voices', `${v.trim()}.pt`));
  const missing = [python, join(model, 'config.json'), ...voices].filter(f => !existsSync(f));
  if (missing.length) return `Kokoro is not installed (missing ${missing.join(', ')})`;
  for (const j of jobs) {
    const args = [KOKORO_SAY, '--model-dir', model, '--text-file', j.text, '--voice', voice, '--speed', speed,
      '--out', j.audio, '--words', j.words];
    const r = spawnSync(python, args, { encoding: 'utf8' });
    if (r.status !== 0) {
      const last = (r.stderr || r.error?.message || `exit ${r.status}`).trim().split('\n').pop();
      return `Kokoro failed on ${j.page}: ${last}`;
    }
    console.log(`[video] ${j.page}: kokoro ${voice} at ${speed} -> ${j.audio}, word timings -> ${j.words}`);
  }
  return null;
}

const provider = argValue('--provider', 'kokoro');
if (!PROVIDERS.includes(provider)) fail(`--provider: "${provider}" is not a voice provider (${PROVIDERS.join(', ')})`);
const speed = argValue('--speed', KOKORO_SPEED);
if (!(Number(speed) > 0)) fail(`--speed: "${speed}" is not a positive number`);
const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
const jobs = timeline.pages.map((p, i) => ({
  page: p.page, text: scriptTextPath(p, i), audio: audioPath(p), words: wordsPath(p), said: p.sentences.join('\n') + '\n',
}));
const stale = jobs.filter(j => !existsSync(j.text) || readFileSync(j.text, 'utf8') !== j.said).map(j => j.text);
if (stale.length) fail(`the exported text is missing or older than the script: ${stale.join(', ')}; run npm run video:script --prefix ${DECK}`);

const failed = provider === 'kokoro'
  ? kokoro(jobs, argValue('--kokoro-venv', join(KOKORO_HOME, '.venv')), argValue('--kokoro-model', join(KOKORO_HOME, 'model')),
    argValue('--voice', KOKORO_VOICE), speed)
  : null;
if (failed) console.log(`[video] ${failed}; continuing with the manual provider`);
if (provider === 'manual' || failed) manual(jobs);
