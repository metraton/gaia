// Voices each page. Every provider keeps one contract: it reads the page's text
// exported by video:script and leaves the audio at the path the script declares,
// plus, beside it, the page's timings: word timings (wordsPath) when it can time
// words, otherwise its exact sentence spans (sentencesPath).
//
//   npm run video:voice [-- --provider chatterbox|kokoro|manual] [--pages id,id]
//                          [--reference <wav>] [--chatterbox-venv <dir>] [--chatterbox-model <dir>]
//                          [--voice <id>[,<id>...]] [--speed <x>] [--kokoro-venv <dir>] [--kokoro-model <dir>]
//
// A sentence's `pause` in the script is voiced as that many seconds of silence
// after it, so the alignment of a paced page stays sentence-accurate.
// chatterbox (the default) clones the voice of the reference clip and keeps
// every voiced sentence in its cache, so a stopped run resumes where it stopped;
// kokoro speaks one of its own voices. Each runs this folder's <provider>_say.py
// with the interpreter of a venv the person created, on a model they
// downloaded; it installs nothing. When either is absent or the provider fails,
// the step continues as manual, which only reports where each page's audio goes
// and whether it is already there; neither case is an error.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { DECK, HERE, argValue, audioPath, fail, readScript, requireDeck, scriptTextPath, sentencesPath, wordsPath } from './deck.mjs';
import { loadTimeline, selectedPages, voicedPages } from './timeline.mjs';

const PROVIDERS = ['chatterbox', 'kokoro', 'manual'];
const TTS_HOME = join(homedir(), '.local', 'share', 'gaia-tts');
const CHATTERBOX_HOME = join(TTS_HOME, 'chatterbox');
const CHATTERBOX_SAY = join(HERE, 'chatterbox_say.py');
const CHATTERBOX_MODEL_FILES = ['ve.safetensors', 't3_cfg.safetensors', 's3gen.safetensors', 'tokenizer.json', 'conds.pt'];
const CHATTERBOX_SETTINGS = ['--exaggeration', '0.5', '--cfg-weight', '0.5', '--seed', '42'];
const KOKORO_HOME = join(TTS_HOME, 'kokoro');
const KOKORO_SAY = join(HERE, 'kokoro_say.py');
const KOKORO_VOICE = 'am_michael';
const KOKORO_SPEED = '1.0';

function manual(jobs) {
  for (const j of jobs) {
    if (existsSync(j.audio)) console.log(`[video] ${j.page}: audio found at ${j.audio}`);
    else console.log(`[video] ${j.page}: no audio yet; voice ${j.text} and leave the audio at ${j.audio}`);
  }
}

/** Runs one say script per page; returns why a page could not be voiced, or null when every page was. */
function runPages(provider, python, jobs, argsOf, done) {
  for (const j of jobs) {
    const r = spawnSync(python, argsOf(j), { encoding: 'utf8', stdio: ['ignore', 'inherit', 'pipe'] });
    if (r.status !== 0) {
      const last = (r.stderr || r.error?.message || `exit ${r.status}`).trim().split('\n').pop();
      return `${provider} failed on ${j.page}: ${last}`;
    }
    console.log(`[video] ${j.page}: ${done(j)}`);
  }
  return null;
}

/** Voices every page with Chatterbox, cloning `reference`; returns why it could not, or null. */
function chatterbox(jobs, venv, model, reference) {
  const python = join(venv, 'bin', 'python');
  const missing = [python, reference, ...CHATTERBOX_MODEL_FILES.map(f => join(model, f))].filter(f => !existsSync(f));
  if (missing.length) return `Chatterbox is not installed (missing ${missing.join(', ')})`;
  return runPages('Chatterbox', python, jobs, j => [CHATTERBOX_SAY, '--model-dir', model, '--reference', reference,
    '--cache-dir', join(CHATTERBOX_HOME, 'cache'), ...CHATTERBOX_SETTINGS, '--text-file', j.text, '--out', j.audio,
    '--sentences', j.sentences, ...(j.gaps ? ['--gaps', j.gaps] : [])],
  j => `chatterbox -> ${j.audio}, sentence spans -> ${j.sentences}`);
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
  return runPages('Kokoro', python, jobs, j => [KOKORO_SAY, '--model-dir', model, '--text-file', j.text, '--voice', voice,
    '--speed', speed, '--out', j.audio, '--words', j.words, ...(j.gaps ? ['--gaps', j.gaps] : [])],
  j => `kokoro ${voice} at ${speed} -> ${j.audio}, word timings -> ${j.words}`);
}

const provider = argValue('--provider', 'chatterbox');
if (!PROVIDERS.includes(provider)) fail(`--provider: "${provider}" is not a voice provider (${PROVIDERS.join(', ')})`);
const speed = argValue('--speed', KOKORO_SPEED);
if (!(Number(speed) > 0)) fail(`--speed: "${speed}" is not a positive number`);
const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
const chosen = selectedPages(timeline);
const jobs = voicedPages(timeline).map((p, i) => ({
  page: p.page, text: scriptTextPath(p, i), audio: audioPath(p), words: wordsPath(p), sentences: sentencesPath(p),
  said: p.sentences.join('\n') + '\n', gaps: p.timing.some(x => x.pause) ? p.timing.map(x => x.pause).join(',') : '',
})).filter(j => chosen.includes(j.page));
const stale = jobs.filter(j => !existsSync(j.text) || readFileSync(j.text, 'utf8') !== j.said).map(j => j.text);
if (stale.length) fail(`the exported text is missing or older than the script: ${stale.join(', ')}; run npm run video:script --prefix ${DECK}`);

const failed = provider === 'chatterbox'
  ? chatterbox(jobs, argValue('--chatterbox-venv', join(CHATTERBOX_HOME, '.venv')),
    argValue('--chatterbox-model', join(CHATTERBOX_HOME, 'model')), argValue('--reference', join(CHATTERBOX_HOME, 'reference.wav')))
  : provider === 'kokoro'
    ? kokoro(jobs, argValue('--kokoro-venv', join(KOKORO_HOME, '.venv')), argValue('--kokoro-model', join(KOKORO_HOME, 'model')),
      argValue('--voice', KOKORO_VOICE), speed)
    : null;
if (failed) console.log(`[video] ${failed}; continuing with the manual provider`);
if (provider === 'manual' || failed) manual(jobs);
