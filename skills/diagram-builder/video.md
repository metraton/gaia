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

## The steps

Every step is `npm run <step> --prefix <deck>`.

1. `video:script`: export the text to be voiced.
2. The voice step turns each text into its page's declared audio.
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
