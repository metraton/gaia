---
name: visual-verify
description: Use when about to declare done on any output with a visual surface -- an HTML page, a rendered UI, a diagram, a slide, a screenshot-able view -- to confirm by looking at the rendered result instead of asserting it. Also use when asked to screenshot or visually check a page or a local file.
metadata:
  user-invocable: true
  type: technique
---

# Visual Verify

Visual verification is the discipline of confirming a visual output by
rendering it and looking at the pixels, not by asserting the markup "should"
look right. When work produces something a person would see -- a page, a UI, a
diagram, a slide -- the honest check is a screenshot the agent then reads with
its own image-reading tool. An agent that ships visual output without looking
at it has not verified; it has hoped.

This skill teaches the disposition and hands you a reference implementation
(`scripts/screenshot.cjs`) as support, not as a spec. It is generic -- any HTML
or URL, any project, inside Gaia or out. For the response contract and the
`verification` block this feeds, see `agent-protocol`.

## Core principle

Three judgments shape a good visual check. Reason through each; do not follow a
fixed recipe.

- **Look, and look where it breaks.** Markup that parses is not layout that
  works. Text clips, boxes collide, a column that is fine at 1440px overflows
  at 380px, a palette that reads in light mode fails in dark. So render across
  a spread of widths (desktop down to narrow mobile) AND across the themes the
  output supports (light/dark) -- one viewport in one theme is not a
  verification of a responsive, themed surface. Reading the images is the
  verification; the script exiting 0 is not.
- **Find the browser where it lives; obtain it only if it is truly absent.**
  Playwright caches its browsers in a location that varies by OS and by the
  `PLAYWRIGHT_BROWSERS_PATH` override -- do not assume one fixed path. Locate
  the browser dynamically (resolve the highest `chromium-*` build present) and
  launch it via an explicit `executablePath`, so you use what is already on
  disk. If nothing is present anywhere, obtaining a browser is a legitimate,
  deliberate one-time step -- a real install you run with approval. The
  friction to avoid is not installing per se; it is letting a tool silently
  fetch a *mismatched* revision when a perfectly usable browser already sits in
  the cache.
- **Put the captures where their context wants them.** The output location is a
  decision, not a fixed folder. Read the context and choose:
  - **Backing a brief (inside Gaia):** write them where that brief's evidence
    lives, so the capture becomes part of the audit trail.
  - **Generic / outside a brief:** a temporary directory -- it keeps the
    workspace clean and is not a meaningful mutation -- or the location the
    user or context names.
  The reference script takes the out-dir as an argument precisely so this
  decision stays with you.

## Process

1. **Have the output at a URL a browser can open** -- a local file as
   `file:///absolute/path`, or a running dev server as its `http://localhost:PORT`.
2. **Decide the output location** by the criterion above (brief-evidence vs
   temp vs given), then **locate the browser** where it lives.
3. **Capture across the widths and themes that matter** for this surface. The
   reference script does this:

   ```
   node <skill-dir>/scripts/screenshot.cjs <url-or-file> <out-dir> [widths] [colorSchemes]
   ```

   Defaults to `1440,900,700,500,380`; pass `light,dark` as the fourth argument
   to capture both color schemes. An app with a bespoke theme toggle
   (localStorage/class rather than `prefers-color-scheme`) may need a
   project-specific step -- adapt the reference, do not assume it fits.
4. **Read every screenshot** with the Read tool and actually look: overflow,
   clipping, collisions, contrast in each theme, and whether the change you made
   is visible and correct. Skipping this and trusting the exit code is the
   failure mode the whole skill exists to prevent.
5. **Report what you observed.** In the `verification` block use
   `method: "self-review"` (visual inspection is a genuine self-review), list
   the widths/themes checked in `checks`, and put the PNG paths and concrete
   observations in `details`. Saw a defect -> `result` is not `pass`; loop and
   fix.

## Why the reference script is shaped this way

The explicit-`executablePath` + dynamic-revision approach exists to use the
browser already present instead of triggering a redundant fetch. Two
alternatives are **rejected by evidence** -- know why before reaching for them:

- **The one-line `playwright screenshot` CLI.** It targets the revision the
  installed package pins, not the one on disk; when they differ it silently
  triggers a browser download and stalls the check on an approval prompt. The
  reference script sidesteps this by launching what is cached.
- **`chrome-devtools-mcp`.** It does not support WSL and requires a live Chrome
  instance, so it is not a portable verification path here.

## Anti-patterns

- **Declaring done without looking.** Emitting `verification.result: "pass"` on
  visual work because the script ran is the exact hollow pass this skill
  prevents. The pass is the image you read, not the exit code.
- **Assuming one browser path or one revision.** A hardcoded cache path or
  `chromium-1223` in a launch line is a time-bomb; the location varies by
  environment and the cache is repopulated over time. Resolve both dynamically.
- **Fetching a browser reflexively when one already exists.** The reflex to run
  a fetch on any hiccup converts a free check into an approval wall. Use what is
  on disk; obtain deliberately only when nothing is present.
- **Checking one width in one theme.** Layout and contrast fail at the edges --
  narrow viewports and the non-default theme -- not at the comfortable default.
