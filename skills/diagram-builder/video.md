# Diagram Builder — the video

A closed deck can become a narrated video: the deck's own pages, revealed in
the order a voice explains them. The video is made from the deck and nothing
else; the script only says when something appears, and the deck says what
appears and where. You agree the script with the person first, then the tools
voice it, time it and record it.

## The questions that write the script

Ask them before writing a sentence; each answer decides something the script
takes for granted.

1. Who watches, and what do they already know? The voice starts from their
   reality, so it has to know where that is.
2. Where will they watch: in a meeting, on their own, as a clip in a document?
   That sets the length and whether each page must stand alone.
3. What should they understand or do when it ends? That is the closing
   sentence, and every page leads to it.
4. In which language? Chatterbox speaks English; Kokoro has a few voices in
   other languages, with weaker timing.
5. How long, in total and per page? At 140-160 words a minute, a one-minute
   page holds about 150 words.
6. Which details behind the clicks should the voice tell? A video cannot open a
   detail, so what only the detail says is said aloud or lost.
7. Whose voice? Chatterbox copies the voice of a short reference clip the person
   provides; Kokoro speaks one of its own stock voices.

## Writing the script

The page already shows its titles; the voice adds what the page cannot show.

- **The voice explains and never reads the titles.** A title read aloud is
  heard twice and learned once; the time is better spent on why the box is
  there.
- **Start from the viewer's reality**: what they do today, what they see, the
  problem they already have. A sentence that starts from the system asks them
  to care before they know why.
- **Use connectors** (so, because, then, which means). Without them a page
  becomes a list of facts the viewer has to join alone.
- **Explain each term in the same sentence that uses it.** A term explained
  later is not heard in between.
- **Say what the page cannot say**: the detail behind a click, the reason, the
  consequence. That is the voice's whole value over the page.
- **Chips are numbered moments.** Light a chip when the voice reaches that
  moment, so the viewer's eye goes where the voice is.
- **End each page with a sentence that hands off to the next**, and open the
  next page by picking it up. The joins are reviewed together, in a table of
  last sentence against first sentence, because that is where a story breaks.
- **Avoid "not X, it is Y", triplets by reflex, and seamless, powerful,
  crucial.** Each one sounds like a pitch and spends attention on a form
  instead of a fact.

## Pace

- One second of image before the first word, so the viewer sees the page before
  hearing about it. Each page's slot opens with `MOTION.lead` (0.8 s,
  `assets/tools/video/timeline.mjs::MOTION`); the rest comes from silence at the
  start of the audio.
- A visual appears with the phrase that names it, never before: a box shown
  early is read instead of heard.
- Pauses: 0.3 s after a comma or inside a list, 0.7 s at the end of a sentence,
  1.6 s between moments, 2.0 s before the close. A sentence's `pause` in the
  script is voiced as that much real silence after it.
- 140-160 words per minute, pauses counted. Speech alone runs near 180-200,
  which is normal; the pauses are what bring it down.

## The voices

Both are local, need no key and no account, and are offered with their
trade-off. The script is the same for both; only the voice step changes.

| | Kokoro | Chatterbox |
|---|--------|------------|
| Use it for | the draft, to judge rhythm and reveals | the final video |
| Quality | `--quality 480p` | `--quality 1440p` (2K) |
| Cost on this CPU | about one minute per page | a three-minute page takes 40-60 minutes (about 50 s per sentence, measured on deck gaia-architecture-overview v8) |
| Delivery | fixed stock voices, steady | natural, cloned from a reference clip |
| Timing it leaves | word timings in English (`<audio>.words.json`) | exact sentence spans (`<audio>.sentences.json`) |

Chatterbox keeps every voiced sentence in a cache
(`~/.local/share/gaia-tts/chatterbox/cache`), so changing one sentence revoices
that sentence only, and a stopped run resumes where it stopped. When a provider
is missing or fails, the step says so on one line and continues as `manual`,
which reports where each page's audio goes; that is never an error.

## The script file

`video/script.json` holds, per page, what is said and what each sentence shows.
Its keys are closed by `assets/tools/video/deck.mjs::readScript`; any other key
is refused by name.

| Level | Key | What it does |
|-------|-----|--------------|
| top | `pages` | The pages, in the deck's order; a page the deck does not render is refused. |
| top | `placeholder` | The text a prompt box shows until its `ask` fires; required when any `ask` exists. |
| page | `page` | The page id. |
| page | `audio` | Where the page's narration goes, relative to `video/` and inside it. |
| page | `duration` | Seconds of a silent page instead of `audio`. |
| page | `sentences` | What is said, in order. |
| sentence | `say` | The words only; a voice, a speed or markup belongs to the voice step. |
| sentence | `seconds` | A fixed length for the sentence, instead of audio timing. |
| sentence | `pause` | Seconds of silence after the sentence (≥ 0), voiced as real silence. |
| sentence | `show` | Box or section ids revealed when the sentence starts. |
| sentence | `chip` | A chip of the page, or `all`, lit when the sentence starts. |
| sentence | `type` | A box id whose title is typed one word per 0.25 s. |
| sentence | `ask` | A box id whose title shows the placeholder, with its description hidden, until it fires. |
| sentence | `cues` | Word cues: each has `at` (a word of `say`) and exactly one of `show`, `chip`, `type`, `ask`. |

A page takes exactly one timing: `audio`, `duration`, or `seconds` on every
sentence. Reveals follow the script's order; a box shown before the section
holding it is refused, and a chip lights only once one of its members is on
screen. Typing into a box before it is revealed is refused by `video:check`.

## The steps

Every step is `npm run <step> --prefix <deck>`, with its flags after `--`.
`--pages id,id` limits `video:voice`, `video:align`, `video:plan`,
`video:check`, `video:capture` and `video:split` to those pages; `video:align`
keeps the other pages' entries.

1. `video:script` exports each page's text to `out/video/script/NN-<page>.txt`,
   one sentence per line; that is what any voice reads.
2. `video:voice -- --provider kokoro|chatterbox|manual` voices every page to
   the path the script declares. Default `chatterbox`. Chatterbox takes
   `--reference <wav>` (default `~/.local/share/gaia-tts/chatterbox/reference.wav`),
   `--chatterbox-venv` and `--chatterbox-model`, and runs with exaggeration 0.5,
   cfg-weight 0.5 and seed 42
   (`assets/tools/video/voice.mjs::CHATTERBOX_SETTINGS`). Kokoro takes
   `--voice` (default `am_michael`; a comma list blends voices, and the first
   letter sets the language), `--speed` (default 1.0), `--kokoro-venv` and
   `--kokoro-model`.
3. `video:align` writes `video/align.json`: per sentence, from the word timings
   (`method=words`), the sentence spans (`method=sentences`), the pauses in
   the audio (`method=silencedetect`) or an estimate by length
   (`method=chars`). It needs `ffmpeg` and `ffprobe`.
4. `video:plan` prints each page's slot and the second every cue fires,
   `type` and `ask` cues and fixed timing included. It writes nothing.
5. `video:check` loads the deck under `?video` and confirms every cue can be
   shown. It captures no frames.
6. `video:contact -- --page <id> --at 2.5,14` tiles stills of one page at
   those seconds into `out/video/contact/<id>.png`; without `--at`, at the end
   of each sentence. It judges framing and reveals in seconds, not minutes.
7. `video:capture -- --quality <q>` records `out/video/deck.mp4` (`--out` names
   another file). `--seconds n` records only the first n seconds, to time a
   quality before the full run. It refuses estimated timing. Narration is
   brought to -16 LUFS and -1.5 dBTP per page
   (`assets/tools/video/timeline.mjs::LOUDNESS`), so pages voiced on
   different runs play at one level.
8. `video:split -- --quality <q>` cuts the video into one clip per page under
   `out/video/pages/`, numbered in the deck's order.

Qualities (`assets/tools/video/timeline.mjs::QUALITIES`): `480p` 854×480 at
30 fps, `preview` 1280×720 at 30 fps, `default` 1920×1080 at 60 fps, `1440p`
2560×1440 at 60 fps, `2160p` 3840×2160 at 60 fps. The deck is always laid out
at 1920×1080, so a quality changes the sharpness and the frame rate, never the
layout. On a six-page deck (gaia-architecture-overview v8) a full capture took
about 3 minutes at `preview`, 7 at `default`, 9 at `1440p` and 25 at `2160p`;
those runs drew every quality at 3840×2160 device pixels, and frames are now
taken at each quality's own size, so the lower qualities should be faster.

`video:capture` keeps every frame in `out/video/frames/`: a stopped run keeps
what it took, and a script or audio change reuses every frame whose picture did
not change. Because it writes that cache, running it asks for consent.

## The browser and the installs

`video:check`, `video:contact` and `video:capture` need a browser; the deck
itself never does. Playwright is listed only in `tools/video/package.json` and
loaded only from there. When it is missing, those three steps stop on one line
that gives the install command. Every install downloads and writes, so the
person runs it or signs it, never the skill on its own:

- Playwright: `npm install --prefix <deck>/tools/video`, then
  `npm exec --prefix <deck>/tools/video -- playwright install chromium`.
- Kokoro: a venv at `~/.local/share/gaia-tts/kokoro/.venv` with
  `kokoro>=0.9.4` and `soundfile`, and the model from `hexgrad/Kokoro-82M`
  (`config.json`, `kokoro-v1_0.pth`, `voices/<voice>.pt`) in
  `~/.local/share/gaia-tts/kokoro/model`.
- Chatterbox: a venv at `~/.local/share/gaia-tts/chatterbox/.venv` with
  `chatterbox-tts`, the model files `ve.safetensors`, `t3_cfg.safetensors`,
  `s3gen.safetensors`, `tokenizer.json` and `conds.pt`
  (`assets/tools/video/voice.mjs::CHATTERBOX_MODEL_FILES`) in
  `~/.local/share/gaia-tts/chatterbox/model`, and the person's reference clip
  at `~/.local/share/gaia-tts/chatterbox/reference.wav`.

`out/` and `tools/video/node_modules/` stay out of git.
</content>
</invoke>
