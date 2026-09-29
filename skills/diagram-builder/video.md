# The video of a deck

A deck can become a narrated video, as an optional last step once the person has
closed the deck. The video is made from a deck and nothing else. There is no
video without a deck. Every step first checks that the deck is there (its
`index.html`, its engine and its built data) and that its engine exposes the
`?video` hook. If either is missing, the step stops with an error that names
what is missing.

## What the video takes from the deck

- **Pages, in the deck's order.** The video runs through the pages the script
  names, in the order `document.yaml` gives them. A script that lists pages in
  another order is refused, and so is a page the deck does not render.
- **Sections, in the deck's order.** Each sentence may *show* sections or boxes
  of its page by their id. They appear in the order the deck places them: a
  section that comes later in the YAML is never shown before an earlier one,
  and a section is never shown after something it contains. A top-level
  section that no sentence shows is on screen from the start.
- **Chips.** A sentence may light one of the page's own chips, including the
  core chips it inherits, or `all` to clear it. When the chip lights, at least
  one of its members must already be on screen.
- **The look.** The frame is the rendered deck, with its chip bar and its
  content, scaled to fit. No layout, colour or text is changed for the video.

The script only says *when*. The deck says *what* appears and *in which order*.
`npm run video:check` loads the real deck under `?video` and confirms that
every cue can be rendered. It captures no frames.

## The script

The deck's `video/script.json` holds, for each page, what is said and what each
sentence shows:

```json
{ "pages": [ { "page": "p10-flow-phases", "audio": "audio/p10-flow-phases.wav",
  "sentences": [ { "say": "A story that moves is told in phases.", "show": ["p10-lead"] },
                 { "say": "The chip traces the way the story runs.", "chip": "path" } ] } ] }
```

- `say` is only the words. A voice, a speed or any markup belongs to the voice
  step, never to the script, so any field other than `say`, `show` and `chip`
  is refused by name.
- `audio` declares where that page's narration will be. The path is relative
  to `video/` and must stay inside it. Audio is never read from the deck's root.

`npm run video:script` exports the script per page as plain text, one sentence
per line, to `out/video/script/NN-<page>.txt`. That text is what any voice
reads, and it carries no marks from any provider. The voice step writes each
page's audio to the path the script declares.

## The voice

`npm run video:voice --prefix <deck> -- --provider kokoro|manual` voices every
page of the script. Every provider keeps the same contract:

- **In:** the page's exported text, `out/video/script/NN-<page>.txt`. The step
  refuses to run when that text is missing or no longer matches the script, and
  asks for `video:script` first.
- **Out:** the page's audio at the path the script declares and, when the
  provider can time words, `<audio>.words.json` beside it: a list of
  `{ "word", "start", "end" }` in seconds inside that audio.

Two providers ship:

- **`kokoro`** (the default) runs the deck's own `tools/video/kokoro_say.py`
  with the interpreter of a venv the person created, on a model they
  downloaded, and installs nothing. The venv defaults to
  `~/.local/share/gaia-tts/kokoro/.venv` (`--kokoro-venv` points elsewhere) and
  the model to `~/.local/share/gaia-tts/kokoro/model` (`--kokoro-model`). It
  voices each page with `am_michael`, an American male voice (`--voice` picks
  another), and writes the words file too. When the install is absent, or Kokoro fails on a page,
  the step says so on one line and continues as `manual`. That is not an
  error: the video never depends on a voice being installed.
- **`manual`** is always there. It prints, per page, the text to voice and the
  path to leave its audio at, or that the audio is already there. Any voice
  can be used this way: record it, generate it in a web tool, or run a local
  model by hand, then leave the WAV at the declared path.

`video:align` prefers the words file. Each sentence then runs from its first
word to its last (`method=words`). It uses the file only when it is at least as
new as the audio and its words spell the page's sentences; audio left later by
hand was not timed by it. Otherwise the page falls back to silencedetect,
which matches sentence ends to pauses in the audio (`method=silencedetect`),
or, when the pauses do not fit, to an estimate by length (`method=chars`).
Kokoro in Spanish, Qwen3-TTS and most manual audio have no word timings and
take that fallback without error.

### Which local voice to use

Both run on this machine with no key and no account. The script is the same
for both; only the voice step changes.

- **Kokoro-82M is the default.** Use it for English. It runs on CPU, a little
  faster than real time for a page, gives per-word timings in English, and
  every page uses the same fixed voice. Its Spanish voices exist
  (`ef_dora`, `em_alex`), but its own model card calls non-English support
  thin, and in Spanish it gives no word timings. It has fixed voices, with no
  cloning and no control of tone.
- **Qwen3-TTS is the alternative.** Use it for a video in Spanish, for a voice
  cloned from a 3-second sample, or for a tone set by an instruction ("calm",
  "excited"). It gives no word timings, so `video:align` uses silencedetect.
  Its examples all run on a CUDA GPU, and its speed on a CPU is not
  documented. Its output is sampled, so the delivery may change between runs:
  voice the whole video in one sitting. It has no adapter here: it is used
  through `manual`.

**Kokoro, once.** Apache-2.0, about 330 MB of weights. The person runs these
three steps, with their consent; the skill never runs them.

1. `python3 -m venv ~/.local/share/gaia-tts/kokoro/.venv`
2. `~/.local/share/gaia-tts/kokoro/.venv/bin/pip install "kokoro>=0.9.4" soundfile`
   (`uv pip install --torch-backend cpu` installs the CPU-only torch and skips CUDA).
3. `hf download hexgrad/Kokoro-82M config.json kokoro-v1_0.pth voices/am_michael.pt --local-dir ~/.local/share/gaia-tts/kokoro/model`

Then `npm run video:voice --prefix <deck>` uses it. The deck's
`tools/video/kokoro_say.py` takes `--text-file`, `--voice`, `--out`, `--words`
and `--model-dir`, reads the model offline, and writes a 24 kHz mono WAV and
the word list from Kokoro's token timestamps, one segment per line of text. The first letter of the
voice sets the language: `a` American English, `b` British, `e` Spanish.

**Qwen3-TTS, once.** Apache-2.0, 2.5 GB (0.6B) to 4.5 GB (1.7B) downloaded on
first use.

1. A fresh Python 3.12 venv, for example `~/.local/share/gaia-tts/qwen3/.venv`.
2. `<venv>/bin/pip install -U qwen-tts`
3. In Python, `Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", device_map="cuda:0")`
   and `generate_custom_voice(text, language, speaker)` for a stock voice with
   an optional `instruct` for tone. Use the `-Base` model with a 3-second sample and its
   transcript (`ref_text`) to clone a voice. Write the result with
   `soundfile.write`. The `0.6B` models are lighter, and VoiceDesign exists
   only in 1.7B.
4. Voice each `out/video/script/NN-<page>.txt`, save it at the page's declared
   audio path, and run `npm run video:voice --prefix <deck> -- --provider manual`
   to confirm every page has its audio.

## The steps

Every step is `npm run <step> --prefix <deck>`.

1. `video:script`: export the text to be voiced.
2. `video:voice` turns each text into its page's declared audio (see above).
3. `video:align` measures where each sentence starts and ends in the audio and
   writes `video/align.json`. It needs `ffmpeg` and `ffprobe`.
4. `video:plan` prints each page's slot and the second at which every cue
   fires. It writes nothing. Until a page has audio, its timing is an estimate
   from the length of the text.
5. `video:check` validates the whole timeline against the rendered deck.
6. `video:contact -- --page <id> --at 1,3` takes stills of one page at the end
   of the chosen sentences and writes them to `out/video/contact/`. Use it to
   judge the framing without rendering the video.
7. `video:capture` renders every frame and encodes the video with its audio to
   `out/video/deck.mp4`. It refuses to run on estimated timing.
8. `video:split` cuts that video into one clip per page, under
   `out/video/pages/`.

Steps 5, 6 and 7 need a browser. Playwright is listed only in
`tools/video/package.json`, and it is loaded only from that folder: a
Playwright found anywhere else is never used. Install it only with the user's
consent. When it is missing, the three steps stop before doing anything, with
one line that gives the install command. The deck's own `package.json` keeps
zero dependencies. `out/` and `tools/video/node_modules/` stay out of git.
