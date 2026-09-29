# From the data to the look

The person fills in the data: the story, the boxes, what goes together and in
what order. The look follows from one more answer, and it is not a colour or a
size. It is where the deck will be seen.

## One question, one line

Ask: **"Where will people see this?"** The answer picks a look, and the look is
one line at the top of the document:

```yaml
look: projector
```

That line decides the colours and the text sizes of every page. Nothing else
changes. No box, no section, no page carries a colour or a size of its own, so
moving the same deck from a meeting room to a written report is a one-line
change, and the diff shows exactly that one line.

## The three looks

| The person says | Look | What they get |
|-----------------|------|---------------|
| "It goes on a projector", "a big room", "people at the back" | `projector` | High contrast and larger text, with more room per box so the larger text still fits. |
| "It is a document", "they will read it on their own", "keep it sober" | `report` | The quiet house colours and compact text, for reading up close. |
| "It is our deck", "it has to look like us", "a client presentation" | `brand` | A coloured palette with the house sizes; the brand's own colours go on top of it. |

Why these three and not others. Each one names a situation the person already
knows before they have drawn anything, so they can answer without knowing the
engine. The three differ where legibility actually changes: distance (the room
needs contrast and size), time (a reader up close can take denser text), and
identity (a presented deck carries someone's colours). A look named after a
colour or a mood ("dark", "elegant") would ask the person a design question
they came here not to answer.

If the person names two situations, ask which one happens first, or build the
deck twice: the pages do not change, only the line.

## What every look keeps

- **Readable text.** Every look keeps the minimum text sizes, and the layout
  check refuses a look that squeezes a box below the narrowest readable width.
- **Contrast.** Every look's colours pass the contrast check, in both the
  light and the dark theme, at the bar its colours promise; `projector` is the
  one held to the full standard for every pair, because a room is the harshest
  place to read.
- **Meaning.** A risk stays a risk and a secure part stays secure in every
  look. The look changes how the deck is drawn, never what it says.

What no check can see is how the text lands on the real screen, at the real
distance. After the build, open the deck with the chosen look and read it as
the audience will. That judgement is the person's eye.

## When the look is not enough

- **The brand has its own colours.** Keep `look: brand` and give the brand's
  colours as overrides; they are measured for contrast like every other colour.
  The fields are in [build.md](build.md), "Palette overrides".
- **One number really must change.** A size can still be written in the
  document's `tokens:`; it applies on top of the look, within the same minimums.
  Reach for it only when the look is right and one value is not: a deck tuned
  number by number stops being one line away from another look.
- **No look at all.** Without `look`, the document's `palette` picks the colours
  alone and the sizes stay the house defaults. A document carries either a look
  or a palette, never both.
