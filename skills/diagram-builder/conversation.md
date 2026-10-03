# The conversation

For you, the orchestrator, facing a person with an idea. You leave this moment
with the idea closed: a sketch the person said yes to, and the values each piece
carries. No YAML exists yet.

## What you send, in this order

1. **The story in one sentence.** What the diagram will say, in the person's
   words. If you cannot write it, you do not understand the idea yet: ask one
   question, with the answer you would assume.
2. **The sketch**, in a fenced block, at most 80 columns wide. Every mark in it
   is one piece of the deck, and every piece is one mark. Draw it; never
   describe it in prose.
3. **The reading rules**, the first time you show a sketch in the conversation,
   and never again.
4. **The neighbour piece, only where the choice was close.** One sentence: what
   you chose, what the other piece was, why this one. Where the choice was
   obvious, say nothing.
5. **The question**: "Yes, like this, or change something?"

The person answers in their own words: "yes", "like this", or "change X"
("put approve before tests", "the scan is red"). They never need a field name,
because you never showed one.

## The reading rules

Send these once, as written, under the first sketch:

```
+-- name ---+   a frame is a group: the boxes inside are parts of one thing
[ text ]        a box: one part
[ ...across ]   a box as wide as the page: a row that heads or spans it all
+ - name - -+   a dashed frame: outside the scope, drawn without colour
(1)             a box marked (1) lights up when you click question (1)
```

- Read left to right, then down. That order is the order of the story.
- Width is reach and height is size: a box drawn twice as wide covers twice as
  much.
- Colour is said in words under the sketch, one line per colour that means
  something. A colour the sketch does not name is plain.

Only these marks appear in a sketch. When a page needs one of the rarer pieces,
write its plain word where it sits: `line` for a divider inside a frame, `hole`
for a gap left on purpose, `label` for the name at the start of a lane.

## From the sketch to the pieces

This is for you; the person never sees it. It is how the sketch stays 1:1 with
the deck, and how [review.md](review.md) checks the result against it.

| Mark | Piece |
|------|-------|
| a frame with its name on the top edge | a section; a frame inside a frame is a nested section |
| `[ text ]` | a box |
| `[ ... ]` from edge to edge of the page | a band (the first one is the lead band, the page's claim) |
| a dashed frame | the `outside` treatment on what it encloses |
| `question (n): ...` above the sketch | a chip, labelled with that question |
| `(n)` inside a box | that box is a member of chip `n` |
| a colour line under the sketch | the variant of the boxes it names, and the legend that declares it |

A frame's position against its neighbours is its order and its width.
Frames side by side share a row; a frame below another starts a new one.

## One worked example

The person says: "I want a diagram of how a change gets to production. We
write it, CI checks it, someone approves and it ships. Security is checked in
several places. The old manual deploy script still exists, but nobody should
use it."

You answer:

> A change reaches production in three phases, write, check and ship, security
> is checked in three of their steps, and the old script sits outside the path.

```
question (1): Where is security checked?

[ A change reaches production in three phases                            ]

+-- write -------------+ +-- check -------------+ +-- ship --------------+
| [ branch       (1) ] | | [ tests            ] | | [ approve          ] |
| [ review           ] | | [ scan         (1) ] | | [ deploy       (1) ] |
+----------------------+ +----------------------+ +----------------------+

+ - not in the path - - - - - - - - - - - - - - - - - - - - - - - - - - -+
: [ old manual deploy script                                           ] :
+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -+
```

> Colour: the scan is amber, because it is the step that can stop a release.
> Everything else is plain.
>
> (The reading rules go here, the first time.)
>
> Security could also be a row across the whole page, under the phases. I made
> it a question instead, because it has no content of its own: it only points
> at three steps that already exist.
>
> Yes, like this, or change something?

The person answers: "Like this. But the old script is gone, we deleted it."

You recalculate, never nudge: "Then the dashed frame goes and nothing else
moves: the three phases keep their widths and their order." Redraw only what
changed when the change moves something; here, one sentence is enough. The idea
is closed. Ask where the deck lives before anything is saved.

## The handoff

The subagent gets the agreed sketch and the values each piece carries, in the
person's words. It translates them into fields with [build.md](build.md); you
never do.

```
Agreed sketch: (paste it as the person approved it)

Values:
- claim: "A change reaches production in three phases"
- write: branch (secrets are scanned on push), review (a second person
  reads every change)
- check: tests (unit and integration), scan (dependencies and image)
- ship: approve (one named person), deploy (rolling, no downtime)
- colour: scan in amber, meaning "can stop a release"; say it on the page
- question (1): "Where is security checked?" lights branch, scan, deploy
- the deck lives in: <the path the person gave>
```

The subagent returns the YAML, the check results and the census. Reading them
against this sketch is [review.md](review.md).
