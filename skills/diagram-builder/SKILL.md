---
name: diagram-builder
description: Use when the user wants to build, extend or narrate a diagram deck -- an architecture map, a timeline, a process flow, a planner board, a side-by-side comparison, a mind-map or a slide-style explanation -- or wants a narrated video of a deck. Not for charts, plots or numeric visualization (route those to dataviz). Triggers -- "build a diagram", "architecture diagram", "diagram deck", "timeline diagram", "flow diagram", "planner board", "comparison diagram", "add a page/section/component to the diagram", "make a video of the deck", "arma un diagrama", "diagrama de arquitectura", "haz un timeline", "diagrama de flujo", "tablero de planificación", "haz un video del deck".
---

# Diagram Builder

Diagram Builder turns an idea, a problem or an explanation into a deck: pages
of boxes that explain it at a glance and, when asked, a narrated video of that
deck. You hold the conversation with the person; a subagent writes the YAML and
runs the checks.

```
document      the deck: title, look, core chips, pages in order
 └─ page      one claim, opened by its lead band
     └─ section     a frame that groups; sections nest freely
         └─ component   box · separator · rail · spacer
chips         questions on top; clicking one lights the components it names
```

Everything a deck is lives in YAML under `data/`: how many sections, where, in
what order, with which colour and size. Components flow left to right, then
down, and that order is the reading order. As the screen narrows the grid folds
in steps until it reads top to bottom like text, so the order is the story on
any screen. The engine draws no arrows: a relation is order plus a chip. The
seed (`assets/`) is the engine plus a tour deck, copied once per deck; no deck
needs its code touched.

## Principles

1. **General to particular.** Each phase settles what the next takes for
   granted: understanding before structure, structure before conventions,
   conventions before the shape, the shape before the build, the build before
   the voice. A skipped phase is paid in rebuild loops.
2. **Truth before drawing.** Confirm in the source how the thing really works
   before proposing its shape; a page that explains wrong is worse than none.
   Order appears only where a real order exists.
3. **Structure is the claim.** Distinct things are distinct sections, the parts
   of one thing are components inside it, what crosses them is a chip. Geometry
   serves meaning: never fold two things together to fill a rectangle; a hole
   is closed or declared.
4. **Creativity needs the whole toolbox.** Every property of every piece is a
   visual channel: width, height, order, colour, border, alignment, text.
   Knowing all of them ([toolbox.md](toolbox.md)) is what lets a page say
   something no template foresaw; knowing a few produces boxes in columns. Each
   channel used carries one claim, and the page declares it.
5. **Patterns make it learnable.** A deck agrees its conventions once (what a
   kicker carries, how chips are phrased, what each colour means) and keeps
   them on every page; a reader who learns them on page one reads the rest for
   free.
6. **The face is for a beginner, the detail for the expert.** Titles and
   subtitles in everyday words; the real name and the why behind the click.
7. **Decide cheap, fix expensive.** A row changed in a table costs minutes; a
   page changed after the build costs a loop. Every phase closes on the
   person's yes, and a requested change touches only what was asked.
8. **The checks are deterministic.** build, model, census and contrast prove
   the deck is well formed; the person's eye judges whether it says what was
   meant. No browser is opened for a diagram; only the video needs one.

## Doors

- A new idea: phase A.
- An existing deck: read its `data/` and its census, then enter at the phase
  the change touches.
- A deck fed by data: a script generates the YAML from its source on every run;
  phases A-D shape what the script emits, and generated YAML is never edited by
  hand.
- A video of a closed deck: phase F.

## Phases

Every phase runs the same way: you state what you consider and why, the person
corrects it as often as needed, and the phase closes on their yes. The first
idea that comes to mind is rarely the best: weigh the alternatives before
proposing, and name the one you set aside when the choice was close.

**A. Understand.** Ask what the reader should understand or decide afterwards,
who reads it, where it will be seen (that answer later picks the look, see
[toolbox.md](toolbox.md), Look), and in which language. Read the source.
Deliver what you understood, in a few plain sentences, with no drawing.
Approved: the understanding.

**B. Structure.** Propose how many pages and in what order, and the form of
each page (the catalogue is in [toolbox.md](toolbox.md), Forms) with why it
fits. The form is chosen per page, so one deck can mix forms. When the choice
is genuinely open, offer up to three, each with what it gives up, and recommend
one. Approved: the structure.

**C. Conventions.** Agree each page's objective in one sentence and the
sentence that hands it to the next; what kickers carry; whether chips are
questions or numbered moments; the colour legend; the face's register.
Approved: the conventions.

**D. Shape.** Per page, a table in the console: sections, their components
(kicker, title, description), the sizes that carry meaning, and, for each
component, whether it opens a detail and what that detail defines, in a few
words. You propose the chips and their members; the person confirms or changes
them. The detail text is not discussed here: it is a fact, written at build
time from the source, and the person reads it in the built deck. Approved: the
tables.

**E. Build.** Ask where the deck lives. Hand the subagent the approved tables
and conventions ([build.md](build.md)). It returns the YAML, the check results
and the census; read the census against the tables and name every difference
before the person opens the deck. A difference you name reaches the person as a
decision; one you missed reaches them as a surprise. Approved: the built deck,
once the person has opened it.

**F. Video**, offered once the deck is closed. Agree the script with the person
([video.md](video.md)). Draft with Kokoro at 480p to judge rhythm in minutes;
final with Chatterbox at 1440p (2K). Both are offered with their trade-off.

## Anti-patterns

- **Drawing to discover what to say.** The drawing then decides the story, and
  every correction to the story becomes a rebuild.
- **Assuming how the thing works.** The deck looks right and teaches something
  false, and nobody checks a page that looks right.
- **The first idea proposed without weighing others.** The person approves what
  was shown, not what was best, and never learns there was a choice.
- **A convention invented per page.** The reader relearns the deck on every
  page, so the pattern stops teaching.
- **A channel nobody declares**, a colour or a dashed frame that means nothing.
  The reader looks for the meaning anyway and finds a wrong one.
- **Changing more than was asked.** The person has to review everything again,
  and stops trusting that an approved part stays approved.
- **Animating before the script; a voice that reads the titles.** The video
  repeats the page instead of explaining it, and the pace follows the
  animation instead of the meaning.

## Where the rest lives

- [toolbox.md](toolbox.md): every piece and every property, required or
  optional, its values and what it changes on screen; the forms; the measured
  limits; where the seed shows each piece.
- [build.md](build.md): for the subagent; the build lane and the checks, and
  how the census is read against the agreed tables.
- [video.md](video.md): the questions for the script, the script itself
  (connectors, pace, pauses), the voices, the steps.
- [assets/README.md](assets/README.md): the engine and how to serve it.
</content>
</invoke>
