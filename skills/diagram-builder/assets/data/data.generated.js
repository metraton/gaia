// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = {
  "title": "Diagram Deck",
  "subtitle": "A portable, data-driven diagram — edit data/ and run npm run build",
  "version": "0.2.0",
  "palette": "rose-pine",
  "pages": [
    {
      "id": "p1-merged-cell",
      "layout": "grid",
      "form": "dashboard",
      "columns": 2,
      "sections": [
        {
          "id": "p1-cell",
          "title": "One slot, two axes",
          "subtitle": "the atom every merge is made of — you never resize it, you merge it",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p1-slot",
              "order": 1,
              "kicker": "SLOT",
              "title": "The slot",
              "description": [
                "130px tall, an equal share wide",
                "the unit both merges count in"
              ],
              "detail": "The base cell is a fixed <code>--cell-h</code> (130px) tall and an equal <code>fr</code> share of its grid's width. Every geometry in the deck is a whole number of these: <code>span</code> counts them sideways, <code>rowspan</code> counts them downward. There is no third way to make something bigger.",
              "variant": "neutral"
            },
            {
              "id": "p1-equal",
              "order": 2,
              "kicker": "EQUAL",
              "title": "Equal by rule",
              "description": [
                "cells in one grid share a width",
                "and never grow to fit content"
              ],
              "detail": "Width varies from section to section but is identical WITHIN a grid — the tracks are equal <code>fr</code> shares that stretch to fill. A cell never grows to accommodate its text: what does not fit moves (to the detail panel, to a merge, to a nested section), which is principle 8.",
              "variant": "neutral"
            },
            {
              "id": "p1-clamp",
              "order": 3,
              "kicker": "CLAMP",
              "title": "Text clamps",
              "description": [
                "title 2 lines, description 3",
                "the rest lives behind a click"
              ],
              "detail": "The title clamps at 2 lines and the whole description at 3, which is what keeps every box exactly one slot tall no matter how many lines the data carries. This paragraph is the <code>detail</code> field: unbounded, rendered in the bottom-centre panel, and the right home for anything longer than a gloss.",
              "variant": "neutral"
            },
            {
              "id": "p1-tracks",
              "order": 4,
              "kicker": "DIAL",
              "title": "columns: 2",
              "description": [
                "this grid declares two tracks",
                "so four cells fill two rows"
              ],
              "detail": "<code>columns: N</code> is the only thing that creates tracks in a leaf grid. Four single cells in two tracks close a 2×2 rectangle — the identity <code>Σ(spanCols × rowspanRows) == tracks × rows</code> that <code>npm run check</code> asserts on the data, with no browser.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p1-partial",
          "title": "A partial merge",
          "subtitle": "span: 2 of 3 — and the siblings that create the tracks it eats",
          "variant": "neutral",
          "order": 2,
          "span": 1,
          "columns": 3,
          "children": [
            {
              "id": "p1-t1",
              "order": 1,
              "kicker": "TRACK",
              "title": "Track 1",
              "description": [
                "one cell, one track"
              ],
              "detail": "Three single cells declare that this grid really has three tracks. Without them the engine's grow-with-content clamp would shrink the grid to what its content can fill, and the merge below would silently become a full-width band.",
              "variant": "neutral"
            },
            {
              "id": "p1-t2",
              "order": 2,
              "kicker": "TRACK",
              "title": "Track 2",
              "description": [
                "the second of three"
              ],
              "detail": "A merge consumes tracks that must ALREADY EXIST. Something has to sit beside it creating them — that is what this row is.",
              "variant": "neutral"
            },
            {
              "id": "p1-t3",
              "order": 3,
              "kicker": "TRACK",
              "title": "Track 3",
              "description": [
                "the third of three"
              ],
              "detail": "With three single cells present the grid keeps three tracks at the authored tier, which is the precondition for a partial merge to read as partial.",
              "variant": "neutral"
            },
            {
              "id": "p1-merge",
              "order": 4,
              "span": 2,
              "kicker": "MERGE",
              "title": "span: 2 of 3",
              "description": [
                "merges two of three tracks",
                "wider reach, same one row"
              ],
              "detail": "<code>1 &lt; span &lt; columns</code> is a REAL partial merge (<code>.mspan</code>, <code>grid-column: span 2</code>): it occupies exactly two of the three tracks and keeps its proportion as the grid collapses (<code>--span2</code>). <code>span == columns</code> would be something else entirely — a full-width band that takes its own row. Width is REACH: this cell claims two thirds of the row's scope, not two thirds more importance.",
              "variant": "accent"
            },
            {
              "id": "p1-close",
              "order": 5,
              "kicker": "HOLDS",
              "title": "The third track",
              "description": [
                "one track stayed open",
                "this cell closes it"
              ],
              "detail": "A merge that leaves the rest of its row empty is a hole, and a hole asserts something (principle 9). Here the remaining track is filled on purpose, so the section is a full rectangle: 3 tracks × 2 rows = 6 cells = 1+1+1+2+1.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p1-band",
          "title": "A full-width band",
          "subtitle": "span == columns: the child stops sharing and takes the whole row",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p1-band-what",
              "order": 1,
              "kicker": "BAND",
              "title": "span == columns",
              "description": [
                "this section is span 2 of the",
                "page's 2 root columns"
              ],
              "detail": "A band is not a separate primitive — it is the SAME <code>span</code> dial pushed to the parent's full column count (<code>.msp</code>, <code>grid-column: 1 / -1</code>). The section you are reading is one: <code>span: 2</code> in a <code>columns: 2</code> page.",
              "variant": "neutral"
            },
            {
              "id": "p1-band-row",
              "order": 2,
              "kicker": "OWNS",
              "title": "It owns the row",
              "description": [
                "no sibling can share it",
                "consecutive bands stack down"
              ],
              "detail": "Because a band carries a definite full-width position, the placement cursor cannot put anything beside it: it takes the first row where the whole width is free. Two bands in a row therefore stack top to bottom, which is exactly how this page's last two sections sit.",
              "variant": "neutral"
            },
            {
              "id": "p1-band-base",
              "order": 3,
              "kicker": "BASE",
              "title": "A base layer",
              "description": [
                "placed last, it reads as the",
                "floor the page rests on"
              ],
              "detail": "Position is meaning: a band placed last renders as a full-width base beneath everything above it, which is why a foundation, a substrate or a shared timeline belongs there. A band spans the block width at EVERY collapse tier — it never shrinks back to its single cell.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p1-ladder",
          "title": "Height is magnitude",
          "subtitle": "rowspan 1·2·3·4 resting on a shared base — the colour scaling in parallel, one claim, two channels",
          "variant": "neutral",
          "order": 4,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p1-gap-1",
              "type": "spacer",
              "order": 1
            },
            {
              "id": "p1-gap-2",
              "type": "spacer",
              "order": 2
            },
            {
              "id": "p1-gap-3",
              "type": "spacer",
              "order": 3
            },
            {
              "id": "p1-bar-4",
              "order": 4,
              "rowspan": 4,
              "kicker": "4 ROWS",
              "title": "rowspan: 4",
              "description": [
                "four slots — the tallest bar",
                "bad — the top of the scale"
              ],
              "detail": "The tallest bar is also what CREATES the four rows its shorter neighbours merge into: a merge consumes rows that must exist, and here the extreme of the scale is what brings them into being. It is authored FIRST because it is the only bar that reaches row 1 — the three cells before it are spacers holding that row open for it.",
              "variant": "bad"
            },
            {
              "id": "p1-gap-4",
              "type": "spacer",
              "order": 5
            },
            {
              "id": "p1-gap-5",
              "type": "spacer",
              "order": 6
            },
            {
              "id": "p1-bar-3",
              "order": 7,
              "rowspan": 3,
              "kicker": "3 ROWS",
              "title": "rowspan: 3",
              "description": [
                "three slots tall",
                "warn — the amber step"
              ],
              "detail": "Because size and colour both grow, the magnitude is legible twice: read the ladder by height and you get the same ranking you get by colour. That is principle 5 used deliberately — two channels DOUBLED on one claim to reinforce it, rather than split across two claims.",
              "variant": "warn"
            },
            {
              "id": "p1-gap-6",
              "type": "spacer",
              "order": 8
            },
            {
              "id": "p1-bar-2",
              "order": 9,
              "rowspan": 2,
              "kicker": "2 ROWS",
              "title": "rowspan: 2",
              "description": [
                "twice the slot height",
                "colour steps up with it"
              ],
              "detail": "<code>rowspan: K</code> is the vertical merge (<code>.mrsp</code>, <code>grid-row: span 2</code>): the cell becomes K slots tall, K× 130px plus the gaps between them. Its column position is untouched — the horizontal cascade never moves it sideways.",
              "variant": "good"
            },
            {
              "id": "p1-bar-1",
              "order": 10,
              "rowspan": 1,
              "kicker": "1 ROW",
              "title": "rowspan: 1",
              "description": [
                "the base slot, unmerged",
                "the zero of both scales"
              ],
              "detail": "The shortest bar is just a cell: <code>rowspan: 1</code> is the default and merges nothing. It anchors both channels at once — the smallest height AND the quietest colour role (<code>neutral</code>). Authored LAST, it lands in row 4 — the base row every taller bar also ends in, which is what makes the four heights comparable.",
              "variant": "neutral"
            }
          ]
        }
      ],
      "name": "1 · Merged cell",
      "order": 1
    },
    {
      "id": "p2-cells-or-zones",
      "layout": "grid",
      "form": "comparison",
      "columns": 4,
      "sections": [
        {
          "id": "p2-cells",
          "title": "A grid of cells",
          "subtitle": "every child is a leaf — this level is a real grid of tracks and rows",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 2,
          "children": [
            {
              "id": "p2-c-columns",
              "order": 1,
              "kicker": "COLUMNS",
              "title": "columns: 2",
              "description": [
                "declares two real tracks",
                "equal fr shares of the width"
              ],
              "detail": "In a leaf grid <code>columns: N</code> is literal: the engine emits <code>repeat(N, minmax(--cell-min-w, 1fr))</code> and the cells divide the section edge to edge. This is the only kind of level where the number you write is a track count.",
              "variant": "neutral"
            },
            {
              "id": "p2-c-span",
              "order": 2,
              "kicker": "SPAN",
              "title": "span merges",
              "description": [
                "span: M takes M of the",
                "tracks that already exist"
              ],
              "detail": "Here <code>span</code> is an Excel-style merge measured in TRACKS: <code>span: 2</code> in a 3-track grid occupies exactly two of them, and <code>span == columns</code> becomes a full-width band. The number refers to something real that the grid already drew.",
              "variant": "neutral"
            },
            {
              "id": "p2-c-rowspan",
              "order": 3,
              "kicker": "ROWSPAN",
              "title": "rowspan works",
              "description": [
                "a cell can be K rows tall",
                "because rows exist here"
              ],
              "detail": "The vertical merge only means something where there are rows to merge. A leaf grid has them — fixed <code>--cell-h</code> tracks — so <code>rowspan: K</code> makes a cell K slots tall and height becomes a channel you can encode magnitude in.",
              "variant": "neutral"
            },
            {
              "id": "p2-c-checks",
              "order": 4,
              "kicker": "CHECKS",
              "title": "The checks apply",
              "description": [
                "closure, dead track, slot",
                "height: all measured here"
              ],
              "detail": "The cell-level invariants are asserted at exactly this level: the closure identity <code>Σ(spanCols × rowspanRows) == tracks × rows</code>, the dead-track check, the orphan row, the uniform slot height. A defect on this side is caught by arithmetic.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p2-zones",
          "title": "A grid of zones",
          "subtitle": "one nested section is enough: this level became a flex row of zones",
          "variant": "neutral",
          "treatment": [
            "envelope"
          ],
          "order": 2,
          "span": 2,
          "columns": 2,
          "children": [
            {
              "id": "p2-z-changed",
              "title": "What changes",
              "variant": "neutral",
              "order": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-z-columns",
                  "order": 1,
                  "kicker": "COLUMNS",
                  "title": "columns is inert",
                  "description": [
                    "no tracks are created here",
                    "the row is flex, not a grid"
                  ],
                  "detail": "A compound level is a flex-wrap row of sections, so <code>columns</code> no longer emits tracks. It survives only as the label the CSS steps the collapse by. Writing <code>columns: 6</code> on a level whose children are sections changes nothing at all.",
                  "variant": "neutral"
                },
                {
                  "id": "p2-z-span",
                  "order": 2,
                  "kicker": "SPAN",
                  "title": "span is a weight",
                  "description": [
                    "a ratio between siblings,",
                    "not a count of tracks"
                  ],
                  "detail": "On this side <code>span</code> becomes <code>flex-grow</code>: <code>span: 2</code> beside <code>span: 1</code> means twice as wide as its sibling, whatever the parent's <code>columns</code> says. It is a proportion, and the band beneath is the legitimate use of it.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p2-z-lost",
              "title": "What is lost",
              "variant": "neutral",
              "order": 2,
              "columns": 1,
              "children": [
                {
                  "id": "p2-z-rowspan",
                  "order": 1,
                  "kicker": "ROWSPAN",
                  "title": "No rowspan",
                  "description": [
                    "there are no rows to merge",
                    "so the dial has no meaning"
                  ],
                  "detail": "A flex row has no row model, so <code>rowspan</code> on a zone has nothing to consume. The schema accepts the field and the render ignores it — which is exactly why the principle has to be known rather than discovered.",
                  "variant": "neutral"
                },
                {
                  "id": "p2-z-checks",
                  "order": 2,
                  "kicker": "CHECKS",
                  "title": "Checks step back",
                  "description": [
                    "no tracks, so no closure",
                    "and no dead-track check"
                  ],
                  "detail": "The cell invariants stop measuring a compound level: there is no rectangle to close and no track to declare dead. What is still asserted is the flex behaviour — that a zone's width follows its authored weight, that a lone leaf on the row stays content-sized, that no sibling overflows onto the next.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        },
        {
          "id": "p2-weight",
          "title": "span as weight — a 2:1 split",
          "subtitle": "the honest reason to mix: one row, two zones, one twice the other",
          "variant": "neutral",
          "order": 3,
          "span": 3,
          "columns": 3,
          "children": [
            {
              "id": "p2-w-heavy",
              "title": "Two thirds",
              "subtitle": "span: 2",
              "variant": "neutral",
              "order": 1,
              "span": 2,
              "columns": 2,
              "children": [
                {
                  "id": "p2-w-h1",
                  "order": 1,
                  "kicker": "WEIGHT",
                  "title": "span: 2 of a 2+1 row",
                  "description": [
                    "flex-grow 2 beside a 1:",
                    "two thirds of the width"
                  ],
                  "detail": "The parent's <code>columns: 3</code> creates no tracks here — the split is the RATIO of the two authored spans, 2 against 1. The guardrail asserts this on the real render: a zone's rendered width must follow its authored span within 15%, which is what catches the classic regression where both siblings inherit the parent band's span and the row silently goes 50/50.",
                  "variant": "neutral"
                },
                {
                  "id": "p2-w-h2",
                  "order": 2,
                  "kicker": "NESTED",
                  "title": "Inside, cells again",
                  "description": [
                    "this zone's own children are",
                    "leaves, so its grid is a grid"
                  ],
                  "detail": "The two levels alternate freely: a compound level holds zones, and each zone's own level is a leaf grid with real tracks again. Every dial resets to its literal meaning one level down.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p2-w-light",
              "title": "One third",
              "subtitle": "span: 1",
              "variant": "neutral",
              "order": 2,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-w-l1",
                  "order": 1,
                  "kicker": "WEIGHT",
                  "title": "span: 1 of the same row",
                  "description": [
                    "half of what its sibling gets",
                    "authored, not measured"
                  ],
                  "detail": "The weight is a claim about IMPORTANCE OR VOLUME that you author, and the layout obeys it. A zone that carries less says so by weighing less — the width is the argument, not a leftover.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        },
        {
          "id": "p2-third",
          "title": "One track of four",
          "subtitle": "span: 1 — the track the wide half left",
          "variant": "neutral",
          "order": 4,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "p2-t-band",
              "order": 1,
              "kicker": "ROOT",
              "title": "A band makes a grid",
              "description": [
                "every child of the root is a",
                "section, and it is a grid anyway"
              ],
              "detail": "The page root holds nothing but sections, so by the rule on the right its <code>columns</code> should be inert and its <code>span</code> a weight. One child changes that: a full-width band. <code>.sec-plane > .sec-grid.sec-compound:has(> .msp)</code> matches, the root becomes <code>display:grid</code> over the authored tracks, and <code>flex-grow</code> is not read here at all.",
              "variant": "neutral"
            },
            {
              "id": "p2-t-tracks",
              "order": 2,
              "kicker": "TRACKS",
              "title": "So span counts again",
              "description": [
                "3 of 4 took three tracks;",
                "this one takes the fourth"
              ],
              "detail": "In a real grid the number is literal: the section beside this one declares <code>span: 3</code> and occupies exactly three of the root's four tracks, leaving this one the fourth. Only the render can confirm it — the arithmetic closes <code>3 + 1 == 4</code> from the YAML and has never seen a pixel, while invariant Q compares the REAL width against the authored span within 15% and is the one check that catches a stylesheet missing the rule, where the wide half is auto-placed into ONE track and drawn at a quarter of what it is owed.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p2-mix",
          "title": "Mixing is legal",
          "subtitle": "phase zones divided by vertical separators — a real timeline row",
          "variant": "neutral",
          "order": 5,
          "span": 4,
          "columns": 3,
          "children": [
            {
              "id": "p2-m-phase-1",
              "title": "Phase one",
              "variant": "neutral",
              "order": 1,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-m-b1",
                  "order": 1,
                  "kicker": "ZONE",
                  "title": "A zone, not a cell",
                  "description": [
                    "each phase is a section, so",
                    "this whole row is compound"
                  ],
                  "detail": "A phase is a distinct thing with parts of its own, so it is a section — that is principle 7, structure as the assertion. Making the phases zones is what turns this row compound, and the row's dials change accordingly.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p2-m-sep-1",
              "type": "separator",
              "treatment": [
                "vertical"
              ],
              "order": 2,
              "style": "dotted"
            },
            {
              "id": "p2-m-phase-2",
              "title": "Phase two",
              "variant": "neutral",
              "order": 3,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-m-b2",
                  "order": 1,
                  "kicker": "LEAF",
                  "title": "The divider is a leaf",
                  "description": [
                    "a separator sits on the same",
                    "row, mixed in with the zones"
                  ],
                  "detail": "The vertical separators beside this zone are LEAF components sharing a row with sections — the mix the principle warns about, here on purpose. A separator is a WEAK divider: it separates within one level. If the two sides were genuinely distinct things, they would be sections, not a line.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p2-m-sep-2",
              "type": "separator",
              "treatment": [
                "vertical"
              ],
              "order": 4,
              "style": "dotted"
            },
            {
              "id": "p2-m-phase-3",
              "title": "Phase three",
              "variant": "neutral",
              "order": 5,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-m-b3",
                  "order": 1,
                  "kicker": "COST",
                  "title": "Mixed knowingly",
                  "description": [
                    "the price is the dials above",
                    "paid for a row that reads"
                  ],
                  "detail": "Mixing is not forbidden — it is a trade. Here it buys a timeline whose phases are real zones with their own contents, and the price is the one enumerated on this page: <code>columns</code> goes inert, <code>span</code> becomes a weight, <code>rowspan</code> disappears, and the cell invariants stop measuring this level. Mix when the row is worth it; know what you gave up.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        }
      ],
      "name": "2 · Cells or zones",
      "order": 2
    },
    {
      "id": "p3-sequence",
      "layout": "grid",
      "form": "flow",
      "columns": 2,
      "filters": [
        {
          "key": "packing",
          "label": "The packing order",
          "steps": [
            "Click the chip to light the three cells that carry an explicit <code>order</code>.",
            "That one number is the whole positional vocabulary — there is no row or column to set.",
            "It reads 1 → 2 → 3 here, and it is the same order the page stacks in when it collapses."
          ]
        }
      ],
      "sections": [
        {
          "id": "p3-order",
          "title": "You author a sequence",
          "subtitle": "one dial, two jobs: how children pack, and how they stack when collapsed",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p3-o-1",
              "order": 1,
              "kicker": "ORDER 1",
              "title": "No coordinate",
              "description": [
                "there is no row or column",
                "to set — only this number"
              ],
              "detail": "Nothing in the dialect names a cell position. To move something you change its <code>order</code>; the grid does the rest. That is why a change is a RECALCULATION — moving one child repacks its whole row — and never a nudge.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            },
            {
              "id": "p3-o-2",
              "order": 2,
              "kicker": "ORDER 2",
              "title": "The packing order",
              "description": [
                "children flow in order across",
                "the tracks, wrapping downward"
              ],
              "detail": "Children are sorted by <code>order</code> (falling back to list position, which is why an explicit order on every sibling is worth the keystrokes) and then flow left to right, wrapping to the next row when the tracks run out. A band interrupts the flow by claiming a whole row of its own.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            },
            {
              "id": "p3-o-3",
              "order": 3,
              "kicker": "ORDER 3",
              "title": "The stacking order",
              "description": [
                "and the same sequence is the",
                "collapse order at one column"
              ],
              "detail": "At the narrowest tier every grid drops to a single track and the whole page becomes one vertical stack — in exactly this order. So the sequence you author is also the reading order on a narrow screen: one number carries both, and they can never disagree.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            }
          ]
        },
        {
          "id": "p3-anchor",
          "title": "Filling runs forward",
          "subtitle": "nothing goes back to fill the hole a tall cell left — so sequence is design",
          "variant": "neutral",
          "order": 2,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p3-a-anchor",
              "order": 1,
              "rowspan": 2,
              "kicker": "ANCHOR",
              "title": "The tall cell",
              "description": [
                "rowspan: 2, authored FIRST",
                "so the cursor is still here"
              ],
              "detail": "A tall cell claims one track across two rows and leaves the rest of both rows open. Authoring it first is what lets the cells after it pack into that space: the placement cursor is still on this row, so it fills the tracks beside the anchor and then continues on the row below.",
              "variant": "accent"
            },
            {
              "id": "p3-a-beside-1",
              "order": 2,
              "kicker": "PACKS",
              "title": "Beside it",
              "description": [
                "authored second, so it lands",
                "in the next free track"
              ],
              "detail": "Nothing about this cell says <em>row 1, track 2</em>. It sits there because it is second in the sequence and that is where the cursor was — the position is a consequence of the order, which is the whole of principle 3.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-beside-2",
              "order": 3,
              "kicker": "PACKS",
              "title": "And beside that",
              "description": [
                "the row is now full, so the",
                "next cell wraps downward"
              ],
              "detail": "Three tracks, and the anchor took one of them across two rows. With this cell the first row closes, so the cursor wraps to the row below — where the anchor is still occupying the first track.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-under-1",
              "order": 4,
              "kicker": "FORWARD",
              "title": "Under, not back",
              "description": [
                "the anchor still holds track 1",
                "so this starts at track 2"
              ],
              "detail": "The cursor steps over the track the anchor still occupies and starts here. It moved FORWARD to do it — it never searches backwards for a gap it already passed. That asymmetry is the rule the whole principle rests on.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-under-2",
              "order": 5,
              "kicker": "FORWARD",
              "title": "Closing the block",
              "description": [
                "fifth in order, and the two",
                "rows are now a full rectangle"
              ],
              "detail": "With this cell the anchor's two rows are completely filled: 3 tracks × 2 rows = 6 cells = 2 (the anchor) + 4. Had any of these four been authored after the divider below, that space could never have been recovered.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-sep",
              "type": "separator",
              "order": 6,
              "span": 3,
              "style": "dotted",
              "text": "the cursor never moves backwards"
            },
            {
              "id": "p3-a-tail",
              "order": 7,
              "span": 3,
              "kicker": "LATE",
              "title": "Authored after the line",
              "description": [
                "a full-width row takes the",
                "next row, whatever is open"
              ],
              "detail": "This cell spans every track, so it can only start on a row where the full width is free — it lands below the divider no matter how much space is open above it. That is the practical rule: whatever belongs beside something tall must come BEFORE whatever claims a whole row. Note the divider's own row is thin (40px, not a full 130px slot): a separator is still a cell, but its footprint matches its ink.",
              "variant": "muted"
            }
          ]
        }
      ],
      "name": "3 · Sequence",
      "order": 3
    },
    {
      "id": "p4-slots",
      "layout": "grid",
      "form": "planner",
      "columns": 2,
      "sections": [
        {
          "id": "p4-fields",
          "title": "Four slots, four characters",
          "subtitle": "the engine fixes the size and the prominence — never the meaning",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p4-f-kicker",
              "order": 1,
              "kicker": "FIELD",
              "title": "kicker",
              "description": [
                "one line, uppercase",
                "the quietest mark"
              ],
              "detail": "The small uppercase mark above the title (<code>.box .k</code>, 10.5px mono, muted). Its character is fixed: one short line, the least prominent text on the card. Its meaning is open — see the row of payloads beside this one. It was renamed from <code>status</code> precisely because the old name asserted a meaning the field does not have.",
              "variant": "neutral"
            },
            {
              "id": "p4-f-title",
              "order": 2,
              "kicker": "FIELD",
              "title": "title",
              "description": [
                "two lines, bold",
                "the loudest slot"
              ],
              "detail": "The card's heading (<code>.box .t</code>, bold, clamped at 2 lines). It is the most prominent slot on a box, so whatever you put here is what the card is ABOUT. Clamping is what keeps every box exactly one slot tall no matter how long the string is.",
              "variant": "neutral"
            },
            {
              "id": "p4-f-desc",
              "order": 3,
              "kicker": "FIELD",
              "title": "description",
              "description": [
                "three lines, clamped",
                "a list of short lines"
              ],
              "detail": "A string, or a list where each item is a line. The whole block clamps at 3 VISUAL lines, so a long line costs two of them — which is why the lines on this deck are short by discipline rather than by luck. Anything that does not fit belongs in <code>detail</code>.",
              "variant": "neutral"
            },
            {
              "id": "p4-f-detail",
              "order": 4,
              "kicker": "FIELD",
              "title": "detail",
              "description": [
                "unbounded, HTML",
                "lives in the panel"
              ],
              "detail": "The only unbounded slot: it renders in the click-through panel, not on the card, so it has no clamp and accepts HTML. This paragraph is one. When a cell has more to say than three short lines, the answer is never a taller cell — it is this field.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p4-payloads",
          "title": "One kicker, four payloads",
          "subtitle": "a count, a step, a phase, a class — the field cannot tell them apart",
          "variant": "neutral",
          "order": 2,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p4-p-number",
              "order": 1,
              "kicker": "3",
              "title": "A number",
              "description": [
                "a bare count",
                "no words at all"
              ],
              "detail": "The mark holds a quantity here — a count of replicas, of owners, of open items. The engine renders the string and makes no claim about it: there is no numeric type, no sort, no scale.",
              "variant": "neutral"
            },
            {
              "id": "p4-p-step",
              "order": 2,
              "kicker": "STEP 2",
              "title": "A step",
              "description": [
                "a position in a run",
                "the same slot, again"
              ],
              "detail": "Here the same field carries a step index. On a flow page this is what makes a sequence readable at a glance, and it pairs with <code>order</code> — but that pairing is the author's convention, not something the field enforces.",
              "variant": "neutral"
            },
            {
              "id": "p4-p-phase",
              "order": 3,
              "kicker": "PHASE II",
              "title": "A phase",
              "description": [
                "a span of time",
                "still just a mark"
              ],
              "detail": "A phase label. Read together with the two cells before it, the point is that no reading of the field is privileged: a deck that means phases and a deck that means steps use the identical slot.",
              "variant": "neutral"
            },
            {
              "id": "p4-p-class",
              "order": 4,
              "kicker": "STORE",
              "title": "A class",
              "description": [
                "what kind of thing",
                "this card is"
              ],
              "detail": "A kind, not a state. This is the payload the old name <code>status</code> made hardest to reach for — and the most common one in an architecture deck, where the mark says <em>database</em>, <em>queue</em>, <em>gateway</em> far more often than it says <em>healthy</em>.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p4-less",
          "title": "Saying less, on purpose",
          "subtitle": "an empty field is an authoring choice — the card stays exactly one slot tall",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p4-l-title",
              "order": 1,
              "title": "Title only",
              "treatment": [
                "centered"
              ],
              "detail": "No kicker, no description — one line of text and nothing else. <code>treatment: [centered]</code> centres the text block (<code>text-align:center</code>, no geometry and no colour), which is what a card with a single short claim usually wants. Emptiness costs nothing: the cell is the same 130px slot as every other.",
              "variant": "neutral"
            },
            {
              "id": "p4-l-kicker",
              "order": 2,
              "kicker": "KICKER ONLY",
              "detail": "This card declares a <code>kicker</code> and NOTHING else — no title, no description. The engine renders the mark and an empty title node, so the card reads as a pure label. It is the smallest legible cell in the dialect, and a legitimate way to mark a region without claiming anything about it.",
              "variant": "muted"
            },
            {
              "id": "p4-l-half-top",
              "order": 3,
              "kicker": "TOP",
              "title": "Half a slot",
              "treatment": [
                "half"
              ],
              "detail": "<code>half</code> does not shrink a cell — it DIVIDES a slot. Two consecutive halves are wrapped in ONE <code>.half-slot</code> that occupies a single grid cell at the full 130px, so tracks, rows and closure are untouched. Halves pair by ADJACENCY and must come in twos; an odd one is a build error, because half a filled slot is a hole.",
              "variant": "neutral"
            },
            {
              "id": "p4-l-half-bottom",
              "order": 4,
              "kicker": "BOTTOM",
              "title": "The other half",
              "treatment": [
                "half"
              ],
              "detail": "The bottom half of the same slot. <code>half</code> is TITLE-ONLY by construction: a <code>description</code> on a half is a build error, since ~63px cannot hold a title plus three clamped lines without clipping. The long copy goes here, in <code>detail</code>, which is where it belonged anyway.",
              "variant": "neutral"
            },
            {
              "id": "p4-l-all",
              "order": 5,
              "kicker": "EVERYTHING",
              "title": "Every field at once",
              "description": [
                "kicker, title, three lines",
                "detail, and one note"
              ],
              "note": "⚠ <code>note</code> renders ONLY inside the panel, and nothing on the card hints that it exists — so a warning here is invisible until someone clicks.",
              "detail": "The full card, for comparison with its three neighbours: the same slot, the same 130px, carrying every content field the dialect has. The <code>note</code> is the last of them and the only one in this seed — it renders in the panel in warn colour, below the body. Judge it here: a warning nobody can see from the canvas may be the wrong shape for a warning.",
              "variant": "neutral"
            }
          ]
        }
      ],
      "name": "4 · Slots",
      "order": 4
    },
    {
      "id": "p5-channels",
      "layout": "grid",
      "form": "mindmap",
      "columns": 2,
      "sections": [
        {
          "id": "p5-core",
          "title": "Five channels",
          "subtitle": "independent by construction — changing one never changes another",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 5,
          "children": [
            {
              "id": "p5-c-position",
              "order": 1,
              "kicker": "CHANNEL",
              "title": "Position",
              "description": [
                "where it sits",
                "authored as order"
              ],
              "detail": "The strongest channel and the cheapest: a cell placed first reads as first, a band placed last reads as the floor. You do not set coordinates — you set <code>order</code> (principle 3), and position is the consequence.",
              "variant": "neutral"
            },
            {
              "id": "p5-c-size",
              "order": 2,
              "kicker": "CHANNEL",
              "title": "Size",
              "description": [
                "how much space",
                "span and rowspan"
              ],
              "detail": "Two dials, two meanings: <code>span</code> is REACH across the row and <code>rowspan</code> is MAGNITUDE down it. Size is measured in whole slots, so it is a step scale, never continuous.",
              "variant": "neutral"
            },
            {
              "id": "p5-c-colour",
              "order": 3,
              "kicker": "CHANNEL",
              "title": "Colour",
              "description": [
                "the variant role",
                "one value per cell"
              ],
              "detail": "One semantic role from a closed enum (<code>neutral</code>, <code>good</code>, <code>warn</code>, <code>bad</code>, <code>accent</code>, <code>muted</code>). It is the channel most often overloaded, because it is the one readers notice first — which is exactly why the page has to say what it means.",
              "variant": "neutral"
            },
            {
              "id": "p5-c-border",
              "order": 4,
              "kicker": "CHANNEL",
              "title": "Border",
              "description": [
                "solid or dashed",
                "no colour at all"
              ],
              "detail": "<code>treatment: [outside]</code> is <code>border-style:dashed</code> and NOTHING else — no fill, no border colour, no geometry. That is why it is a treatment rather than a variant, and why it is the cleanest proof that a channel need not be colour.",
              "variant": "neutral"
            },
            {
              "id": "p5-c-kicker",
              "order": 5,
              "kicker": "CHANNEL",
              "title": "Kicker",
              "description": [
                "one short word",
                "the quietest mark"
              ],
              "detail": "The mark above the title. It is a WORD, so it can be read exactly; it is small, so it is read last. That pairing makes it the natural partner for colour — either agreeing with it, or dividing the work with it.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p5-double",
          "title": "Double a channel",
          "subtitle": "kicker and colour carrying one claim — read either, get the same answer",
          "variant": "neutral",
          "order": 2,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p5-d-rule",
              "order": 1,
              "kicker": "RULE",
              "title": "Say it twice",
              "description": [
                "one claim, two channels",
                "nothing new is added"
              ],
              "detail": "Doubling adds no information — it adds ROBUSTNESS. A reader who skims colour and a reader who reads words arrive at the same ranking, and a projector that flattens the palette does not destroy the claim. p1's ladder doubles height and colour; this pair doubles kicker and colour, which is the same operation on different channels.",
              "variant": "neutral"
            },
            {
              "id": "p5-d-bad",
              "order": 2,
              "kicker": "AT RISK",
              "title": "One end",
              "description": [
                "the word says risk",
                "the red says it too"
              ],
              "detail": "The mark reads <em>AT RISK</em> and the role is <code>bad</code>. Two channels, one claim — and because they agree, neither is available to say anything else about this cell.",
              "variant": "bad"
            },
            {
              "id": "p5-d-good",
              "order": 3,
              "kicker": "HARDENED",
              "title": "The other end",
              "description": [
                "same pair, other value",
                "the scale reads twice"
              ],
              "detail": "The opposite end of the same two-value scale: mark <em>HARDENED</em>, role <code>good</code>. A doubled channel is only worth the cost when the scale has ends worth telling apart at a glance.",
              "variant": "good"
            },
            {
              "id": "p5-d-cost",
              "order": 4,
              "kicker": "COST",
              "title": "What it costs",
              "description": [
                "a channel is spent",
                "it cannot say more"
              ],
              "detail": "Both channels are now committed to one claim. If a second claim shows up later — a kind, a phase, an owner — it has to find an unspent channel (position, size, border) or the page has to give up the reinforcement. That trade is the whole reason to count channels at all.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p5-split",
          "title": "Separate two channels",
          "subtitle": "colour says how it is, the dashed border says where it lives",
          "variant": "neutral",
          "order": 3,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p5-s-rule",
              "order": 1,
              "kicker": "RULE",
              "title": "Say two things",
              "description": [
                "two channels, two claims",
                "one cell carries both"
              ],
              "detail": "Separating is the opposite trade: each channel keeps its own claim, so one cell can assert a state AND a location at once. It only works if the reader is told which channel says which — an undeclared split reads as noise.",
              "variant": "neutral"
            },
            {
              "id": "p5-s-outside",
              "order": 2,
              "kicker": "EDGE",
              "title": "Outside the wall",
              "description": [
                "amber: weak config",
                "dashed: not ours"
              ],
              "detail": "This cell carries <code>variant: warn</code> AND <code>treatment: [outside]</code>. The colour claims a state (weak); the dashed frame claims a location (outside the perimeter — a third-party service, an unmanaged dependency). Two channels, two claims, one 130px cell.",
              "variant": "warn",
              "treatment": [
                "outside"
              ]
            },
            {
              "id": "p5-s-inside",
              "order": 3,
              "kicker": "CORE",
              "title": "Inside the wall",
              "description": [
                "same amber, same state",
                "solid frame: ours"
              ],
              "detail": "The control case: identical colour role, no <code>outside</code>. The two cells are the same on the colour channel and differ on the border channel alone — which is the proof that the channels are independent rather than two names for one effect.",
              "variant": "warn"
            },
            {
              "id": "p5-s-cost",
              "order": 4,
              "kicker": "COST",
              "title": "What it costs",
              "description": [
                "two claims to hold",
                "declare them, or lose both"
              ],
              "detail": "A split doubles what the reader has to keep in mind, and it fails silently: a page that never says what its dashed frames mean has simply drawn two kinds of box. That is why the band below exists, and why it is text rather than a legend of swatches.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p5-legend",
          "title": "What colour means here",
          "subtitle": "a channel means nothing until the page says so — so this page says so",
          "variant": "neutral",
          "order": 4,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p5-g-good",
              "order": 1,
              "kicker": "GOOD",
              "title": "One end of the scale",
              "description": [
                "on this page only",
                "not a verdict, an end"
              ],
              "detail": "On THIS page <code>good</code> is the upper end of the example two-value scale in the DOUBLE branch, and nothing more. On another page the same role legitimately means hardened, done, or approved — the palette guarantees the ROLE is stable, never the reading.",
              "variant": "good"
            },
            {
              "id": "p5-g-bad",
              "order": 2,
              "kicker": "BAD",
              "title": "The other end",
              "description": [
                "the same scale",
                "no wider claim"
              ],
              "detail": "<code>bad</code> here is the lower end of that same scale. Note what it does NOT mean on this page: it makes no claim about the deck, the engine, or any of the cells outside that branch.",
              "variant": "bad"
            },
            {
              "id": "p5-g-warn",
              "order": 3,
              "kicker": "WARN",
              "title": "Carries two claims",
              "description": [
                "the split pair above",
                "state plus location"
              ],
              "detail": "<code>warn</code> is reserved on this page for the two cells in the SEPARATE branch, where colour is one of two channels in play. Reserving a role for one demonstration is itself a declaration — the reader can rule the rest of the canvas out.",
              "variant": "warn"
            },
            {
              "id": "p5-g-muted",
              "order": 4,
              "kicker": "MUTED",
              "title": "Commentary only",
              "description": [
                "a cost note",
                "never a risk claim"
              ],
              "detail": "<code>muted</code> marks the two <em>what it costs</em> cells: they are commentary about the operation, not participants in it. Without this line a reader could reasonably take the grey as <em>deprecated</em> or <em>inactive</em>, which is exactly the ambiguity a declaration removes.",
              "variant": "muted"
            }
          ]
        }
      ],
      "name": "5 · Channels",
      "order": 5
    },
    {
      "id": "p6-relations",
      "layout": "grid",
      "form": "flow",
      "columns": 2,
      "filters": [
        {
          "key": "packing",
          "label": "A directional flow",
          "steps": [
            "Click the chip to light the three cells of the flow, in the order they are authored.",
            "There is no arrowhead: the direction is carried by <code>order</code> and by the kickers 1 · 2 · 3.",
            "The same key is declared on page 3, where it traces the packing order — one slug, two pages."
          ]
        },
        {
          "key": "crosscut",
          "label": "A cross-cutting concept",
          "steps": [
            "Click the chip to light members in THREE different sections at once.",
            "A concept chip is not a path: nothing about it reads in an order, and it never leaves a section out.",
            "One cell belongs to this relation AND to the flow above — membership is a list, not a category."
          ]
        }
      ],
      "sections": [
        {
          "id": "p6-what",
          "title": "It lights, it does not draw",
          "subtitle": "a chip is a relation expressed as membership — the grid has no edges",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p6-w-arrow",
              "order": 1,
              "kicker": "NO EDGE",
              "title": "There are no arrows",
              "description": [
                "the grid draws cells",
                "never lines between them"
              ],
              "detail": "Nothing in the dialect can draw an edge from one cell to another — a <code>separator</code> is a divider, not a connector, and there is no coordinate space to route a line through. So a relation cannot be DRAWN, and the substitute is not a weaker arrow: it is membership.",
              "variant": "neutral"
            },
            {
              "id": "p6-w-chip",
              "order": 2,
              "kicker": "MEMBERS",
              "title": "The cell owns its tags",
              "description": [
                "a component declares keys",
                "the chip is the index"
              ],
              "detail": "A component lists the keys it belongs to in <code>filters</code>, and the engine builds the inverse index by walking the tree — so there is no central node list to maintain. Lighting a chip adds <code>.lit</code> to every member and to its enclosing section, and dims the rest of the canvas.",
              "variant": "neutral"
            },
            {
              "id": "p6-w-arity",
              "order": 3,
              "kicker": "ARITY",
              "title": "Two ends or nothing",
              "description": [
                "one member is no relation",
                "and it blacks out the page"
              ],
              "detail": "A one-member chip cannot be shown on this page, because <code>npm run check</code> FAILS it (CHIP arity): a relation needs two ends, and since an active chip dims everything it does not name, a chip with a single member switches the whole canvas off to spotlight one box. A chip with ZERO members fails the same check from the other direction. The rule is enforced, so the defect is stated here in words instead of authored.",
              "variant": "warn"
            }
          ]
        },
        {
          "id": "p6-flow",
          "title": "A flow, read in one direction",
          "subtitle": "no arrowhead exists — so order and position carry the direction",
          "variant": "neutral",
          "order": 2,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p6-f-1",
              "order": 1,
              "kicker": "1",
              "title": "Where it starts",
              "description": [
                "first in order",
                "so leftmost in the row"
              ],
              "detail": "This cell is the flow's first end. It reads as first for two independent reasons: it is authored first, and its kicker says so. Neither is an arrow, and together they are enough.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            },
            {
              "id": "p6-f-2",
              "order": 2,
              "kicker": "2",
              "title": "The middle, twice over",
              "description": [
                "second in the flow",
                "and in the concept too"
              ],
              "detail": "The only cell on this page in TWO relations: it lists both <code>packing</code> and <code>crosscut</code>. Membership is a list, so a cell can sit on a path and also belong to a theme that cuts across the page — the two chips light overlapping, not exclusive, sets.",
              "variant": "accent",
              "filters": [
                "packing",
                "crosscut"
              ]
            },
            {
              "id": "p6-f-3",
              "order": 3,
              "kicker": "3",
              "title": "Where it ends",
              "description": [
                "last in order",
                "so last in the reading"
              ],
              "detail": "The far end of the relation. When the chip lights, these three cells are the only lit ones in this section, and the eye reads them left to right because that is where <code>order</code> put them. Change the orders and the flow reverses — there is nothing else to edit.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            }
          ]
        },
        {
          "id": "p6-left",
          "title": "One section",
          "subtitle": "a member and a non-member, side by side",
          "variant": "neutral",
          "order": 3,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p6-l-in",
              "order": 1,
              "kicker": "CONCEPT",
              "title": "Tagged here",
              "description": [
                "a member of the chip",
                "in this section"
              ],
              "detail": "One end of the <code>crosscut</code> relation. A concept chip groups by theme, status or ownership — anything true of several cells at once — and it has no direction: there is no first or last member, only membership.",
              "variant": "neutral",
              "filters": [
                "crosscut"
              ]
            },
            {
              "id": "p6-l-out",
              "order": 2,
              "kicker": "DIMMED",
              "title": "Not tagged",
              "description": [
                "declares no key",
                "so it dims when lit"
              ],
              "detail": "A non-member in the same section. When the chip is active this cell dims while its neighbour lights, which is why the section itself also gains <code>.lit</code> — the zone frame tells you the relation reaches in here, and the cells tell you how far.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p6-right",
          "title": "Another section",
          "subtitle": "the same relation, across a boundary the grid drew",
          "variant": "neutral",
          "order": 4,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p6-r-in",
              "order": 1,
              "kicker": "CONCEPT",
              "title": "Tagged there too",
              "description": [
                "the other end",
                "in a different section"
              ],
              "detail": "The second end, in a section of its own. Nothing connects the two cells structurally — no span, no nesting, no line. They are related because they name the same key, which is the whole mechanism: a relation is a shared name, not a shared position.",
              "variant": "neutral",
              "filters": [
                "crosscut"
              ]
            },
            {
              "id": "p6-r-out",
              "order": 2,
              "kicker": "DIMMED",
              "title": "Also not tagged",
              "description": [
                "position never implies",
                "membership"
              ],
              "detail": "Its neighbour is a member and it is not, although they sit in the same cell grid — which is the negative case that makes the point: being beside a member is not being related. Only the declared key is.",
              "variant": "muted"
            }
          ]
        }
      ],
      "name": "6 · Relations",
      "order": 6
    },
    {
      "id": "p7-structure",
      "layout": "grid",
      "form": "comparison",
      "columns": 2,
      "sections": [
        {
          "id": "p7-folded",
          "title": "Folded into one",
          "subtitle": "a queue and a policy sharing one frame — the distinction is erased",
          "variant": "bad",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p7-fd-queue",
              "order": 1,
              "kicker": "THING A",
              "title": "A queue",
              "description": [
                "one of the two things",
                "folded in here"
              ],
              "detail": "A message queue: a runtime component with parts of its own. It is a DISTINCT thing from the policy beside it — different lifecycle, different owner, different failure mode — and nothing in this zone says so.",
              "variant": "neutral"
            },
            {
              "id": "p7-fd-policy",
              "order": 2,
              "kicker": "THING B",
              "title": "A policy",
              "description": [
                "the other thing",
                "same frame, same rank"
              ],
              "detail": "An access policy: a rule, not a running component. Sharing a frame with the queue makes the two read as siblings of one kind, which is a claim the idea never made.",
              "variant": "neutral"
            },
            {
              "id": "p7-fd-frame",
              "order": 3,
              "kicker": "THE FRAME",
              "title": "The frame asserts",
              "description": [
                "one zone says these",
                "belong to one thing"
              ],
              "detail": "A section frame is not decoration — it is a statement that everything inside it is part of ONE thing. Here that statement is false, and the reader has no way to recover the boundary the author dropped: the cells are peers, so any grouping they suggest is inference.",
              "variant": "neutral"
            },
            {
              "id": "p7-fd-green",
              "order": 4,
              "kicker": "STILL GREEN",
              "title": "And it passes",
              "description": [
                "2 tracks, 2 rows",
                "the rectangle closes"
              ],
              "detail": "This is the uncomfortable half of the principle: <code>npm run check</code> measures 2 tracks × 2 rows = 4 cells and reports a closed rectangle, and <code>npm run validate</code> finds every cell legible and uniform. Both gates are RIGHT — a fold is not a geometry defect. It is a semantic one, and no arithmetic reaches it.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p7-distinct",
          "title": "Split into two",
          "subtitle": "the same information — two things, so two sections",
          "variant": "neutral",
          "treatment": [
            "plain"
          ],
          "order": 2,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p7-d-queue",
              "title": "The queue",
              "variant": "good",
              "order": 1,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p7-q-thing",
                  "order": 1,
                  "kicker": "THING",
                  "title": "Its own zone",
                  "description": [
                    "a distinct thing gets",
                    "a frame of its own"
                  ],
                  "detail": "The queue is a section now, and the frame says the true thing: what is inside belongs to the queue. Its <code>variant: good</code> is a colour role on the SECTION — the enum there is narrower than a box's (<code>neutral</code>, <code>good</code>, <code>bad</code>) because a zone tints a whole region and only a few roles survive being read at that size.",
                  "variant": "neutral"
                },
                {
                  "id": "p7-q-part",
                  "order": 2,
                  "kicker": "PART",
                  "title": "Its parts, inside",
                  "description": [
                    "a component belongs to",
                    "the thing above it"
                  ],
                  "detail": "A part of one thing is a COMPONENT in that thing's section — never a section of its own. Promoting a part to a zone claims it is a peer of the whole, which is the same error as the fold, made in the other direction.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p7-d-policy",
              "title": "The policy",
              "variant": "good",
              "order": 2,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p7-p-thing",
                  "order": 1,
                  "kicker": "THING",
                  "title": "A second zone",
                  "description": [
                    "the second thing, named",
                    "and framed apart"
                  ],
                  "detail": "The policy gets its own frame and its own name. Nothing structural connects it to the queue, which is correct: they are related by rule, not by containment — and a relation that is not containment is a CHIP (principle 6), never a shared frame.",
                  "variant": "neutral"
                },
                {
                  "id": "p7-p-part",
                  "order": 2,
                  "kicker": "PART",
                  "title": "Parts, again",
                  "description": [
                    "same rule, other thing",
                    "parts stay components"
                  ],
                  "detail": "The same mapping applied twice is what makes the page readable: every frame is a thing, every cell inside it is a part of that thing. Once that holds everywhere, the layout can be read as the idea instead of as a picture of it.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        },
        {
          "id": "p7-judge",
          "title": "No machine can verify this",
          "subtitle": "both blocks above pass every gate — the difference is meaning, not geometry",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p7-j-blind",
              "order": 1,
              "kicker": "UNCHECKABLE",
              "title": "The gates are blind",
              "description": [
                "closure, tracks, pixels",
                "none of them see this"
              ],
              "detail": "<code>npm run check</code> asserts arithmetic (closure, dead tracks, orphan rows, chip arity) and <code>npm run validate</code> asserts pixels (legibility, word fit, real geometry). Neither has any notion of what a section MEANS, so a green run says <em>it is not broken</em> and never <em>it is right</em>.",
              "variant": "neutral"
            },
            {
              "id": "p7-j-why",
              "order": 2,
              "kicker": "THE TEST",
              "title": "Say why, element by element",
              "description": [
                "why a section, why here",
                "why this merge"
              ],
              "detail": "The substitute for a check is the question asked of every element: why is this a section rather than a cell, why does it sit here, why this column count, why this merge. An element with no answer is decoration, and decoration is what the fold on the left is made of.",
              "variant": "neutral"
            },
            {
              "id": "p7-j-line",
              "order": 3,
              "kicker": "THE LINE",
              "title": "Diagram or decoration",
              "description": [
                "structure is the only",
                "thing separating them"
              ],
              "detail": "Every other principle can be got wrong and still leave a diagram that says something. Get this one wrong and the boxes are arranged rather than asserted — which is the whole difference between a diagram and an illustration of one.",
              "variant": "warn"
            }
          ]
        }
      ],
      "name": "7 · Structure",
      "order": 7
    },
    {
      "id": "p8-does-not-fit",
      "layout": "grid",
      "form": "dashboard",
      "columns": 2,
      "sections": [
        {
          "id": "p8-never",
          "title": "The cell never grows",
          "subtitle": "the slot is a constant — content is clamped into it, never accommodated",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p8-n-fixed",
              "order": 1,
              "kicker": "FIXED",
              "title": "130px, always",
              "description": [
                "--cell-h is a constant",
                "no content changes it"
              ],
              "detail": "The slot height is a CSS variable, not a measurement of the text: every row track resolves to <code>--cell-h</code> (130px) whatever the cells carry. A longer description does not buy a taller box; it buys a hidden remainder.",
              "variant": "neutral"
            },
            {
              "id": "p8-n-clamp",
              "order": 2,
              "kicker": "CLAMP",
              "title": "Clamped, not fitted",
              "description": [
                "title 2 lines, body 3",
                "the rest is not shown"
              ],
              "detail": "The title clamps at 2 lines and the whole description at 3 VISUAL lines, so a long line costs two of them. Clamping is what keeps the grid uniform — and it is also why an overlong line does not look broken on the canvas: it looks FINISHED, one sentence short of its point.",
              "variant": "neutral"
            },
            {
              "id": "p8-n-equal",
              "order": 3,
              "kicker": "EQUAL",
              "title": "Width is the track's",
              "description": [
                "cells share one width",
                "set by the grid, not text"
              ],
              "detail": "A cell's width is an equal <code>fr</code> share of its grid, identical for every cell in that grid. Nothing a cell carries can widen it — which is what makes <code>columns</code> the real dial behind legibility: fewer tracks, wider cells.",
              "variant": "neutral"
            },
            {
              "id": "p8-n-squeeze",
              "order": 4,
              "kicker": "NEVER",
              "title": "Squeezing is not a move",
              "description": [
                "no smaller type, no",
                "shorter slot, no fifth line"
              ],
              "detail": "There is deliberately no dial for a smaller font, a taller cell, or a fourth description line. If one existed, every crowded page would reach for it and the grid's uniformity — the thing that makes the whole canvas readable at a glance — would be spent one cell at a time.",
              "variant": "bad"
            }
          ]
        },
        {
          "id": "p8-moves",
          "title": "Four places it moves to",
          "subtitle": "every move relocates the text — none of them resizes the cell",
          "variant": "neutral",
          "order": 2,
          "span": 1,
          "columns": 3,
          "children": [
            {
              "id": "p8-m-detail",
              "order": 1,
              "kicker": "MOVE 1",
              "title": "Into the detail",
              "description": [
                "the unbounded slot",
                "behind a click"
              ],
              "detail": "The first and best answer: <code>detail</code> has no clamp, accepts HTML, and renders in the bottom-centre panel. This paragraph is one. Most \"it doesn't fit\" problems are really a description carrying a paragraph that belonged here from the start.",
              "variant": "neutral"
            },
            {
              "id": "p8-m-merge",
              "order": 2,
              "span": 2,
              "kicker": "MOVE 2",
              "title": "Into a merge — this cell",
              "description": [
                "two of three tracks, so",
                "the line has room to read"
              ],
              "detail": "This cell IS the move it names: <code>span: 2</code> in a 3-track grid (<code>1 &lt; span &lt; columns</code>) reaches across two tracks and keeps that proportion as the grid collapses. Reach costs the row something — the merge consumes tracks its siblings created, and the three cells below are what close the rectangle it left open.",
              "variant": "accent"
            },
            {
              "id": "p8-m-nest",
              "order": 3,
              "kicker": "MOVE 3",
              "title": "One level down",
              "description": [
                "fewer columns,",
                "so wider cells"
              ],
              "detail": "A nested section starts its own grid, so a crowded 4-column zone becomes two zones of 2 columns and every cell doubles in width. This is the move for a whole region that reads too tight — the fix is structural, and it is the one place where principle 7 and this one pull in the same direction.",
              "variant": "neutral"
            },
            {
              "id": "p8-m-extra",
              "order": 4,
              "kicker": "MOVE 4",
              "title": "A second role",
              "description": [
                "a second claim",
                "on the same cell"
              ],
              "detail": "<code>variant_extra</code> is a LIST carrying extra colour roles from the same enum, for a thing that is a KIND and a STATE at once. This cell is <code>variant: bad</code> plus <code>variant_extra: [muted]</code>: the second role takes the fill (<code>.box.muted</code> sets background only, and it is later in the stylesheet), while the first keeps the frame and the mark. Splitting the cell in two to carry the second claim would halve both widths — that is the squeeze, wearing the costume of a structural fix.",
              "variant": "bad",
              "variant_extra": [
                "muted"
              ]
            },
            {
              "id": "p8-m-cost",
              "order": 5,
              "kicker": "COST",
              "title": "What it costs",
              "description": [
                "a click, a track,",
                "a level, a channel"
              ],
              "detail": "The detail hides the text behind a click; the merge spends tracks a sibling needed; nesting adds a level the reader must descend; the second colour role spends a channel that can then say nothing else (principle 5), and it is noise unless the page declares it. Four prices — and all four are cheaper than an unreadable cell.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p8-illegible",
          "title": "An illegible cell is a defect",
          "subtitle": "even when the arithmetic closes — so two floors are measured on the render",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p8-i-closed",
              "order": 1,
              "kicker": "GREEN",
              "title": "Closed says nothing",
              "description": [
                "arithmetic proves the",
                "rectangle, not the reading"
              ],
              "detail": "<code>npm run check</code> proves <code>Σ(spanCols × rowspanRows) == tracks × rows</code> without a browser, and a grid of eight illegible 60px cells satisfies it perfectly. Closure is a claim about the FILL, never about whether anything in it can be read.",
              "variant": "neutral"
            },
            {
              "id": "p8-i-floor",
              "order": 2,
              "kicker": "FLOOR M",
              "title": "120px, or collapse",
              "description": [
                "a fixed readable floor",
                "measured on the render"
              ],
              "detail": "Invariant M asserts that no single cell renders narrower than 120px: the grid must drop columns before a cell degrades, which is why the collapse cascade exists at all. It is a FIXED floor — it knows nothing about what the cell carries.",
              "variant": "neutral"
            },
            {
              "id": "p8-i-word",
              "order": 3,
              "kicker": "FLOOR N",
              "title": "The longest token",
              "description": [
                "a title must not break",
                "in the middle of a word"
              ],
              "detail": "Invariant N is the content-relative floor: a cell must be at least as wide as the longest indivisible token of its own title, or the title fractures mid-word under <code>overflow-wrap:break-word</code>. The two floors are independent — a 136px cell clears M and still cannot hold a 12-character monospace title.",
              "variant": "neutral"
            },
            {
              "id": "p8-i-rotated",
              "order": 4,
              "kicker": "EXEMPT",
              "title": "Rotated",
              "treatment": [
                "vertical"
              ],
              "detail": "<code>treatment: [vertical]</code> turns the title onto the block axis (<code>writing-mode:vertical-rl</code>), the same reading direction as a <code>rail</code>. It is the ONE documented exemption from the word-fit floor, and the exemption is a fact rather than a favour: a rotated title's fit constraint is the cell's HEIGHT, so measuring its horizontal width would fail every vertical label on every deck. Note the shape of the cell — a vertical leaf is title-only, because the rotated axis has no room for a clamped description.",
              "variant": "neutral"
            }
          ]
        }
      ],
      "name": "8 · Does not fit",
      "order": 8
    },
    {
      "id": "p9-the-hole",
      "layout": "grid",
      "form": "timeline",
      "columns": 2,
      "sections": [
        {
          "id": "p9-speaks",
          "title": "An empty cell asserts something",
          "subtitle": "it says nothing belongs here — and the reader cannot tell that from an oversight",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p9-s-claim",
              "order": 1,
              "kicker": "CLAIM",
              "title": "A hole is a statement",
              "description": [
                "the gap says nothing",
                "belongs in this place"
              ],
              "detail": "Every other cell in a grid carries a claim, so the empty one is read as a claim too: <em>this position is deliberately unoccupied</em>. Nothing distinguishes that from a cell the author forgot, a merge that did not fit, or a column count that was never earned — which is why a hole is treated as a defect until it is declared.",
              "variant": "neutral"
            },
            {
              "id": "p9-s-close",
              "order": 2,
              "kicker": "CLOSE IT",
              "title": "If you did not mean it",
              "description": [
                "fill the track, merge a",
                "neighbour, drop a column"
              ],
              "detail": "Three ways to close a hole, in ascending order of honesty: author the missing cell, widen a neighbour with <code>span</code> so it consumes the empty track, or lower <code>columns</code> so the track was never declared. The third is usually the right one — a hole is very often a column count the content cannot fill, and the engine's grow-with-content clamp already tries to save you from it.",
              "variant": "neutral"
            },
            {
              "id": "p9-s-declare",
              "order": 3,
              "kicker": "DECLARE IT",
              "title": "If you did",
              "description": [
                "say so — the taper of a",
                "chart is the clean case"
              ],
              "detail": "One asymmetry is legitimate and the checker knows it by name: the rows a <code>rowspan</code> cell touches are EXEMPT from the closure identity, because a ladder of bars 1·2·3·4 tapers by design — the taper IS the chart (p1). That exemption is the whole of it. Any other gap has to be closed, or explained in the text of the cells around it, because the geometry cannot carry the explanation.",
              "variant": "warn"
            }
          ]
        },
        {
          "id": "p9-lanes",
          "title": "A shared row needs equal lanes",
          "subtitle": "two rail-led swimlanes of three steps each — then the row both of them meet in",
          "variant": "neutral",
          "order": 2,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p9-l-rail-build",
              "type": "rail",
              "order": 1,
              "title": "Build"
            },
            {
              "id": "p9-l-commit",
              "order": 2,
              "kicker": "STEP 1",
              "title": "Commit",
              "description": [
                "the lane's first step"
              ],
              "detail": "A horizontal rail is a slim title-only banner (<code>.rail-h</code>) that labels the cells to its right. It is a structural leaf, not a card: no kicker, no description, no click — its whole payload is the lane's name.",
              "variant": "neutral"
            },
            {
              "id": "p9-l-package",
              "order": 3,
              "kicker": "STEP 2",
              "title": "Package",
              "description": [
                "the second step"
              ],
              "detail": "The rail costs one track of the row, so a 4-column lane carries three steps. That is the trade a swimlane makes: the label is a cell like any other, and it is charged like one.",
              "variant": "neutral"
            },
            {
              "id": "p9-l-publish",
              "order": 4,
              "kicker": "STEP 3",
              "title": "Publish",
              "description": [
                "the third — the lane ends"
              ],
              "detail": "With this cell the lane reaches track 4, which is where the row closes. The LANE check records that reach and compares it against the other rail's row.",
              "variant": "neutral"
            },
            {
              "id": "p9-l-rail-ship",
              "type": "rail",
              "order": 5,
              "title": "Ship"
            },
            {
              "id": "p9-l-stage",
              "order": 6,
              "kicker": "STEP 1",
              "title": "Stage",
              "description": [
                "the second lane starts"
              ],
              "detail": "The second rail wraps to a row of its own because the row above is full, and it lands at track 1 — which is what makes this row a lane rather than a continuation of the one above it.",
              "variant": "neutral"
            },
            {
              "id": "p9-l-verify",
              "order": 7,
              "kicker": "STEP 2",
              "title": "Verify",
              "description": [
                "step 2 of the same lane"
              ],
              "detail": "Two lanes in one grid must be of EQUAL length: <code>npm run check</code> fails a grid where one rail-led row reaches track 4 and another stops at 3, and it fails HARD, because unlike a short last row that is a collapse artefact, a ragged lane is authored.",
              "variant": "neutral"
            },
            {
              "id": "p9-l-release",
              "order": 8,
              "kicker": "STEP 3",
              "title": "Release",
              "description": [
                "and the lanes now match"
              ],
              "detail": "Both lanes are three steps long, so the two rows are directly comparable: step 2 of Build sits above step 2 of Ship. That vertical alignment is a claim — and it is only true because the lengths agree.",
              "variant": "neutral"
            },
            {
              "id": "p9-l-handoff",
              "order": 9,
              "span": 4,
              "kicker": "HANDOFF",
              "title": "The row both lanes meet in",
              "description": [
                "it means one thing only",
                "because the lanes match"
              ],
              "detail": "The foot of a real timeline: a full-width row (<code>span == columns</code>) that both lanes hand off to. Its meaning depends entirely on the equality above it — if Build were four steps and Ship two, this row would sit under two different points in time and assert a synchronisation that never happens. That is the second half of the principle: a shared row is a claim about SIMULTANEITY, and unequal lanes make it a lie.",
              "variant": "accent"
            }
          ]
        },
        {
          "id": "p9-vlane",
          "title": "A lane labelled down the rows",
          "subtitle": "a vertical rail with rowspan: 2 — and the four cells that create the rows it spans",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p9-v-rail",
              "type": "rail",
              "treatment": [
                "vertical"
              ],
              "rowspan": 2,
              "order": 1,
              "title": "Runtime"
            },
            {
              "id": "p9-v-r1a",
              "order": 2,
              "kicker": "ROW 1",
              "title": "What it labels",
              "description": [
                "the cells beside it are",
                "one lane, two rows deep"
              ],
              "detail": "<code>treatment: [vertical]</code> rotates the rail's title onto the block axis (<code>writing-mode:vertical-rl</code>) and <code>align-self:stretch</code> makes it fill the rows it spans — so one label serves a block of cells instead of a single row. This is the rail's best use: the same swimlane idea turned ninety degrees.",
              "variant": "neutral"
            },
            {
              "id": "p9-v-r1b",
              "order": 3,
              "kicker": "ROW 1",
              "title": "The first row",
              "description": [
                "two tracks wide, beside",
                "the label's one"
              ],
              "detail": "The rail takes track 1 in both rows, so each row of the lane is two cells wide. Nothing about these cells is different from an ordinary box — the lane is drawn entirely by the label's height.",
              "variant": "neutral"
            },
            {
              "id": "p9-v-r2a",
              "order": 4,
              "kicker": "ROW 2",
              "title": "The second row",
              "description": [
                "the same label still",
                "reaches down to here"
              ],
              "detail": "<code>rowspan: 2</code> on the rail is the vertical merge (<code>grid-row: span 2</code>) applied to a structural leaf. The rows a rowspan touches are exempt from the closure identity — but this band closes anyway, which is the honest way to author it: the exemption is there for a chart that tapers, not as a licence for a gap.",
              "variant": "neutral"
            },
            {
              "id": "p9-v-r2b",
              "order": 5,
              "kicker": "HOLDS",
              "title": "What holds it up",
              "description": [
                "these four cells create",
                "the rows the rail spans"
              ],
              "detail": "A merge consumes rows that must already exist (principle 1), so a rail spanning two rows needs two rows' worth of siblings beside it. Author the rail alone and there is nothing to span: the grid has one row, the rowspan is silently satisfied by it, and the label reads as a cell rather than a lane.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p9-spacer",
          "title": "The hole you meant, spelled out",
          "subtitle": "type: spacer — a cell that occupies its track and draws nothing, so the rectangle closes without inventing content",
          "variant": "neutral",
          "order": 4,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p9-sp-gap-1",
              "type": "spacer",
              "order": 1
            },
            {
              "id": "p9-sp-gap-2",
              "type": "spacer",
              "order": 2
            },
            {
              "id": "p9-sp-holds",
              "order": 3,
              "rowspan": 2,
              "kicker": "WHAT IT HOLDS",
              "title": "Two rows, one cell",
              "description": [
                "the two empty cells above",
                "are the tool, not a hole"
              ],
              "detail": "This cell is two rows tall, and the two cells above its neighbours are <code>type: spacer</code>. That is the whole demonstration: without them the two boxes to the left would start in row 1 and this band would taper upward from a ragged floor; with them, every cell in the band ends in row 2. A spacer buys ALIGNMENT, and alignment is what makes neighbouring cells comparable.",
              "variant": "accent"
            },
            {
              "id": "p9-sp-is",
              "order": 4,
              "kicker": "WHAT IT IS",
              "title": "A declared hole",
              "description": [
                "it occupies its cell and",
                "draws nothing at all"
              ],
              "detail": "A spacer is a leaf like a box, a rail or a separator: it takes a whole cell, it honours <code>span</code> and <code>rowspan</code>, and it renders no frame, no text and no click. The closure identity counts it, the census counts it, and the guardrail can therefore tell a hole you MEANT from one you forgot — which is the entire difference this principle is about.",
              "variant": "neutral"
            },
            {
              "id": "p9-sp-is-not",
              "order": 5,
              "kicker": "WHAT IT IS NOT",
              "title": "Not an empty card",
              "description": [
                "no title, no colour, no",
                "filter — rejected by name"
              ],
              "detail": "A spacer carries <em>only</em> <code>id</code>, <code>type</code>, <code>order</code>, <code>span</code> and <code>rowspan</code>. Every other field is refused with a message naming it, because each one presupposes ink that is never drawn: a title would make it an empty card, a <code>variant</code> would colour a frame that does not exist, and a <code>filters</code> key would enrol an invisible cell as a member of a relation it can never light.",
              "variant": "warn"
            }
          ]
        }
      ],
      "name": "9 · The hole",
      "order": 9
    },
    {
      "id": "overview",
      "layout": "grid",
      "columns": 2,
      "filters": [
        {
          "key": "all",
          "label": "All"
        },
        {
          "key": "flow",
          "label": "Example flow",
          "steps": [
            "Chips are flows: click one to spotlight every component that declares it and dim the rest.",
            "A component joins a flow by listing the filter key in its own <code>filters</code>.",
            "Here the flow traces Why this page → Item 3 → Item 7."
          ]
        }
      ],
      "sections": [
        {
          "id": "section-a",
          "title": "Compositions and edge cases",
          "subtitle": "what the principle pages do not need — and the engine must keep supporting",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "item-1",
              "order": 1,
              "kicker": "REMIT",
              "title": "Why this page",
              "description": [
                "p1–p9 carry one idea each",
                "the compositions live here"
              ],
              "detail": "Each principle page states ONE idea in its cleanest form. The cases that are compositions of several tools, or that sit on the edge of what the model allows, would muddy those pages — but they still have to be rendered, and a regression in them still has to be caught. That is this page's whole job: it is the deck's edge-case fixture, and the five cells beside this one are its inventory.",
              "variant": "accent",
              "filters": [
                "flow"
              ]
            },
            {
              "id": "item-2",
              "order": 2,
              "kicker": "CASE 1",
              "title": "Reserved chip",
              "description": [
                "a filter with zero members",
                "the reset, not an orphan"
              ],
              "detail": "A chip that no component references is an orphan and a hard CHIP failure — except <code>all</code>, the reserved reset key, which declares no members BY DESIGN. This page carries the only instance of it, so the exemption is exercised rather than merely written down.",
              "variant": "neutral"
            },
            {
              "id": "case-two-treatments",
              "order": 3,
              "kicker": "CASE 2",
              "title": "Two treatments",
              "description": [
                "one leaf composing two",
                "structural modifiers at once"
              ],
              "detail": "Section J's cells carry <code>[centered, outside]</code> and <code>[half, centered]</code> — a colour role AND two structural treatments on the SAME leaf. Composition is the point of making <code>treatment</code> a list, and this is the only page that composes two of them, so it is the only place the composition can regress and be seen.",
              "variant": "neutral"
            },
            {
              "id": "case-lone-box",
              "order": 4,
              "kicker": "CASE 3",
              "title": "A lone box",
              "description": [
                "a leaf sibling of sections",
                "inside a row of zones"
              ],
              "detail": "Section I's <code>Card</code> is a plain box standing among nested sections in a compound row. It is one of the only two live subjects invariant G has: G fails the moment that box balloons to an equal flex slice instead of staying card-sized. Retire this page and G asserts nothing.",
              "variant": "neutral"
            },
            {
              "id": "case-vertical-in-row",
              "order": 5,
              "kicker": "CASE 4",
              "title": "Vertical in a row",
              "description": [
                "a rail and a separator",
                "directly among zones"
              ],
              "detail": "Section F puts a <code>vertical</code> rail and a <code>vertical</code> separator DIRECTLY into a compound row beside two sub-sections. They must stay content-sized (<code>flex: 0 0 auto</code>) instead of taking an equal share — the second subject of invariant G, and the geometry invariant X reads for sibling collision.",
              "variant": "neutral"
            },
            {
              "id": "case-cascade",
              "order": 6,
              "kicker": "CASE 5",
              "title": "Six columns",
              "description": [
                "the collapse cascade at any N",
                "not an enumerated per-N rule"
              ],
              "detail": "Section H is a six-column band. The cascade used to be enumerated per column count, which left a 6+-column grid uncollapsed between ~640 and 1000px where it could overflow. The general …→2→1 rule is asserted here and nowhere else, because no principle page needs more than four columns.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "section-b",
          "title": "Section B",
          "subtitle": "a section can nest other sections — a grid of grids",
          "variant": "neutral",
          "treatment": [
            "envelope"
          ],
          "order": 2,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "group-1",
              "title": "Group 1",
              "variant": "good",
              "columns": 1,
              "children": [
                {
                  "id": "item-3",
                  "kicker": "INTERNAL",
                  "title": "Item 3",
                  "description": [
                    "one level of nesting deep"
                  ],
                  "detail": "A nested section is drawn as its own framed zone inside the parent. Its <code>variant</code> (here <code>good</code>) tints the whole group.",
                  "variant": "good",
                  "filters": [
                    "flow"
                  ]
                }
              ]
            },
            {
              "id": "group-2",
              "variant": "neutral",
              "treatment": [
                "plain"
              ],
              "columns": 1,
              "children": [
                {
                  "id": "item-4",
                  "kicker": "INTERNAL",
                  "title": "Item 4",
                  "description": [
                    "another nested group"
                  ],
                  "detail": "Sections nest as deep as the idea needs — a recursive grid of grids down to the boxes at the leaves.",
                  "variant": "neutral"
                },
                {
                  "id": "item-5",
                  "kicker": "INTERNAL",
                  "title": "Item 5",
                  "description": [
                    "stacks below Item 4 (columns: 1)"
                  ],
                  "detail": "This group is <code>columns: 1</code>, so its two boxes stack. The <code>muted</code> variant gives a box its own secondary fill.",
                  "variant": "muted"
                }
              ]
            }
          ]
        },
        {
          "id": "section-c",
          "title": "Section C",
          "subtitle": "span == columns makes this a full-width band on its own row",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "sep-c",
              "type": "separator",
              "order": 1,
              "span": 3,
              "style": "dotted",
              "text": "A labeled separator"
            },
            {
              "id": "rail-c",
              "type": "rail",
              "order": 2,
              "title": "Rail"
            },
            {
              "id": "item-6",
              "order": 3,
              "kicker": "UNCHANGED",
              "title": "Item 6",
              "description": [
                "the rail labels this row"
              ],
              "detail": "A rail is a swimlane-style label banner; a separator is a thin divider line. Both are structural leaf types, not data-carrying boxes."
            },
            {
              "id": "item-7",
              "order": 4,
              "kicker": "NEW",
              "title": "Item 7",
              "description": [
                "the last step in the example flow"
              ],
              "detail": "Click the <b>Example flow</b> chip above to trace Item 1 → Item 3 → Item 7 end to end.",
              "variant": "accent",
              "filters": [
                "flow"
              ]
            }
          ]
        },
        {
          "id": "section-d",
          "title": "Section D",
          "subtitle": "a mini bar chart — rowspan 1, 2, 3: a cell's HEIGHT encodes its magnitude",
          "variant": "neutral",
          "order": 4,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "bar-1",
              "order": 1,
              "rowspan": 1,
              "title": "1",
              "description": [
                "rowspan: 1",
                "height = 1 cell"
              ],
              "variant": "neutral"
            },
            {
              "id": "bar-2",
              "order": 2,
              "rowspan": 2,
              "title": "2",
              "description": [
                "rowspan: 2",
                "height = 2 cells"
              ],
              "variant": "good"
            },
            {
              "id": "bar-3",
              "order": 3,
              "rowspan": 3,
              "title": "3",
              "description": [
                "rowspan: 3",
                "height = 3 cells"
              ],
              "variant": "accent"
            }
          ]
        },
        {
          "id": "section-e",
          "title": "Section E",
          "subtitle": "a partial merge — Item C spans 2 of 4 real tracks, earned by the six single cells around it",
          "variant": "neutral",
          "order": 5,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "item-a",
              "order": 1,
              "span": 1,
              "kicker": "UNCHANGED",
              "title": "Item A",
              "description": [
                "one cell of four"
              ]
            },
            {
              "id": "item-b",
              "order": 2,
              "span": 1,
              "kicker": "UNCHANGED",
              "title": "Item B",
              "description": [
                "one cell of four"
              ]
            },
            {
              "id": "item-c",
              "order": 3,
              "span": 2,
              "kicker": "NEW",
              "title": "Item C — span 2",
              "description": [
                "occupies exactly 2 of the 4 tracks"
              ],
              "detail": "A partial merge (1 &lt; span &lt; columns) occupies exactly that many tracks and keeps its proportion as the grid collapses; only a span == columns child becomes a full-width band. It is partial only while the grid has more tracks than the span — which is why the row below it is authored.",
              "variant": "accent"
            },
            {
              "id": "item-d",
              "order": 4,
              "span": 1,
              "kicker": "TRACK 1",
              "title": "Item D",
              "description": [
                "the second row proves",
                "the grid has four tracks"
              ],
              "detail": "The grow-with-content clamp counts SINGLE cells: four of them are the minimum that lets a columns:4 grid keep four tracks. Without this row the clamp would fold the grid to 2 tracks and Item C's <code>span: 2</code> would silently become a full-width band."
            },
            {
              "id": "item-e",
              "order": 5,
              "span": 1,
              "kicker": "TRACK 2",
              "title": "Item E",
              "description": [
                "a single cell — the unit",
                "the clamp counts"
              ]
            },
            {
              "id": "item-f",
              "order": 6,
              "span": 1,
              "kicker": "TRACK 3",
              "title": "Item F",
              "description": [
                "one cell of four again"
              ]
            },
            {
              "id": "item-g",
              "order": 7,
              "span": 1,
              "kicker": "TRACK 4",
              "title": "Item G",
              "description": [
                "and the rectangle closes",
                "6×1 + 1×2 = 4 × 2"
              ]
            }
          ]
        },
        {
          "id": "section-f",
          "title": "Tall block",
          "subtitle": "a compound row — rail + vertical separator dividing two sub-sections",
          "variant": "neutral",
          "order": 6,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "rail-f",
              "type": "rail",
              "treatment": [
                "vertical"
              ],
              "order": 1,
              "title": "Lane"
            },
            {
              "id": "grp-l",
              "title": "Left group",
              "variant": "neutral",
              "order": 2,
              "columns": 1,
              "children": [
                {
                  "id": "l-1",
                  "kicker": "STEP",
                  "title": "Step 1",
                  "description": [
                    "tall sub-section"
                  ]
                },
                {
                  "id": "l-2",
                  "kicker": "STEP",
                  "title": "Step 2",
                  "description": [
                    "four stacked cells"
                  ]
                },
                {
                  "id": "l-3",
                  "kicker": "STEP",
                  "title": "Step 3",
                  "description": [
                    "makes this block tall"
                  ]
                },
                {
                  "id": "l-4",
                  "kicker": "STEP",
                  "title": "Step 4",
                  "description": [
                    "the taller sibling"
                  ]
                }
              ]
            },
            {
              "id": "sep-f",
              "type": "separator",
              "treatment": [
                "vertical"
              ],
              "order": 3
            },
            {
              "id": "grp-r",
              "title": "Right group",
              "variant": "neutral",
              "order": 4,
              "columns": 1,
              "children": [
                {
                  "id": "r-1",
                  "kicker": "NOTE",
                  "title": "Note A",
                  "description": [
                    "a shorter sub-section"
                  ]
                },
                {
                  "id": "r-2",
                  "kicker": "NOTE",
                  "title": "Note B",
                  "description": [
                    "beside the separator"
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "section-g",
          "title": "Short stack",
          "subtitle": "columns:1 stack — shorter, so the row stretches it",
          "variant": "neutral",
          "treatment": [
            "envelope"
          ],
          "order": 7,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "gg-1",
              "title": "Group A",
              "variant": "neutral",
              "columns": 1,
              "children": [
                {
                  "id": "gg-1-box-1",
                  "kicker": "INTERNAL",
                  "title": "One item",
                  "description": [
                    "a stacked sub-section"
                  ]
                },
                {
                  "id": "gg-1-box-2",
                  "kicker": "INTERNAL",
                  "title": "Two item",
                  "description": [
                    "with a second cell"
                  ]
                },
                {
                  "id": "gg-1-box-3",
                  "kicker": "INTERNAL",
                  "title": "Three item",
                  "description": [
                    "a third stacked cell — makes Group A the content-heavy",
                    "sub-section, so if the columns:1 reset regressed it would be the",
                    "one starved by a divided height and overflow onto Group B"
                  ]
                }
              ]
            },
            {
              "id": "gg-2",
              "title": "Group B",
              "variant": "neutral",
              "columns": 1,
              "children": [
                {
                  "id": "gg-2-box",
                  "kicker": "INTERNAL",
                  "title": "Another item",
                  "description": [
                    "stretched taller than its content"
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "section-h",
          "title": "Section H",
          "subtitle": "a six-column band — the collapse cascade now generalises to any N (6 → 2 → 1)",
          "variant": "neutral",
          "order": 8,
          "span": 2,
          "columns": 6,
          "children": [
            {
              "id": "h-1",
              "kicker": "STEP",
              "title": "One",
              "description": [
                "cell 1 of 6"
              ]
            },
            {
              "id": "h-2",
              "kicker": "STEP",
              "title": "Two",
              "description": [
                "cell 2 of 6"
              ]
            },
            {
              "id": "h-3",
              "kicker": "STEP",
              "title": "Three",
              "description": [
                "cell 3 of 6"
              ]
            },
            {
              "id": "h-4",
              "kicker": "STEP",
              "title": "Four",
              "description": [
                "cell 4 of 6"
              ]
            },
            {
              "id": "h-5",
              "kicker": "STEP",
              "title": "Five",
              "description": [
                "cell 5 of 6"
              ]
            },
            {
              "id": "h-6",
              "kicker": "STEP",
              "title": "Six",
              "description": [
                "cell 6 of 6"
              ]
            }
          ]
        },
        {
          "id": "section-i",
          "title": "Section I",
          "subtitle": "a mixed compound — Heavy (span:2) is wider than Light (span:1); the lone Card box stays card-sized",
          "variant": "neutral",
          "order": 9,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "heavy",
              "title": "Heavy",
              "subtitle": "span:2 — grows twice as wide",
              "variant": "neutral",
              "order": 1,
              "span": 2,
              "columns": 2,
              "children": [
                {
                  "id": "heavy-1",
                  "kicker": "NEW",
                  "title": "Alpha",
                  "description": [
                    "content-heavy section"
                  ]
                },
                {
                  "id": "heavy-2",
                  "kicker": "NEW",
                  "title": "Beta",
                  "description": [
                    "four boxes in two columns"
                  ]
                },
                {
                  "id": "heavy-3",
                  "kicker": "NEW",
                  "title": "Gamma",
                  "description": [
                    "so it earns the width"
                  ]
                },
                {
                  "id": "heavy-4",
                  "kicker": "NEW",
                  "title": "Delta",
                  "description": [
                    "span:2 → flex-grow 2"
                  ]
                }
              ]
            },
            {
              "id": "card",
              "type": "box",
              "order": 2,
              "kicker": "NOTE",
              "title": "Card",
              "description": [
                "a lone box beside sections",
                "sizes to content, never balloons"
              ],
              "detail": "This box is a LEAF sibling of the two sections in a compound grid. Because ANY section child makes the grid compound, a naive rule would give this box an equal flex slice and balloon it. Instead it sizes to its content and keeps the uniform cell height — invariant G fails if it ever grows."
            },
            {
              "id": "light",
              "title": "Light",
              "subtitle": "span:1 — grows half as wide",
              "variant": "neutral",
              "order": 3,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "light-1",
                  "kicker": "UNCHANGED",
                  "title": "Solo",
                  "description": [
                    "a lighter section"
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "section-j",
          "title": "Section J",
          "subtitle": "the treatment axis — a vertical label, two half-slot pairs, composed treatments",
          "variant": "neutral",
          "treatment": [
            "envelope"
          ],
          "order": 10,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "j-lane",
              "order": 1,
              "kicker": "LANE",
              "title": "Lane",
              "treatment": [
                "vertical"
              ],
              "detail": "A <code>vertical</code> treatment rotates the text onto the block axis, the same reading direction as a <code>rail</code>. Because the title no longer flows horizontally, the word-fit invariant (N) does not apply to it — its applicability clause exempts vertical leaves."
            },
            {
              "id": "j-h1",
              "order": 2,
              "kicker": "TOP",
              "title": "Half A",
              "treatment": [
                "half"
              ],
              "detail": "Two <code>half</code> components share ONE grid slot: this is the top half. The slot keeps the full 130px cell height, so the grid's rows, tracks and fill are unchanged — a half pair reads as one full cell from the outside."
            },
            {
              "id": "j-h2",
              "order": 3,
              "kicker": "BOTTOM",
              "title": "Half B",
              "treatment": [
                "half"
              ],
              "detail": "The bottom half of the same slot. Invariant U now asserts the height of the SLOT rather than of the component, which is what lets a half legitimately be a fraction of the cell without leaving a hole."
            },
            {
              "id": "j-center",
              "order": 4,
              "kicker": "NEW",
              "title": "Centered",
              "description": [
                "colour and structure compose"
              ],
              "variant": "good",
              "treatment": [
                "centered",
                "outside"
              ],
              "detail": "This cell carries a colour role (<code>good</code>) AND two structural treatments (<code>centered</code>, <code>outside</code>) at once — the composition a single closed <code>variant</code> enum made impossible, and the reason <code>centered</code> once had to be smuggled in through <code>variant_extra</code>."
            },
            {
              "id": "j-h3",
              "order": 5,
              "kicker": "TOP",
              "title": "Half C",
              "variant": "muted",
              "treatment": [
                "half",
                "centered"
              ],
              "detail": "A second half pair, this one composing a colour role (<code>muted</code> — a fill, so it stays a variant) with TWO treatments."
            },
            {
              "id": "j-h4",
              "order": 6,
              "kicker": "BOTTOM",
              "title": "Half D",
              "variant": "muted",
              "treatment": [
                "half",
                "centered"
              ],
              "detail": "The bottom half of the second pair. Both members of a pair must declare the same <code>span</code>, since they share one slot."
            }
          ]
        }
      ],
      "name": "Compositions & edge cases",
      "order": 10
    }
  ]
};
if (typeof document !== 'undefined' && document.documentElement)
  document.documentElement.setAttribute('data-palette', window.__DOC__.palette || 'neutral');
