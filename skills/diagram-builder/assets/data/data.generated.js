// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = {
  "title": "Diagram Deck",
  "subtitle": "A portable, data-driven diagram — edit data/ and run npm run build",
  "version": "0.2.0",
  "palette": "rose-pine",
  "pages": [
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
            "Here the flow traces Item 1 → Item 3 → Item 7."
          ]
        }
      ],
      "sections": [
        {
          "id": "section-a",
          "title": "Section A",
          "subtitle": "an inline section (span 1) — sits side by side with Section B",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "item-1",
              "order": 1,
              "status": "NEW",
              "title": "Item 1",
              "description": [
                "a leaf component (a box)",
                "click it for its full detail"
              ],
              "detail": "The atom of the diagram is a box: a status badge, a title, and a short description. The full text always lives in this click-through panel, so the box itself stays a fixed height. Every field is documented in the diagram-builder dialect reference (GLOSSARY.md and reference.md).",
              "variant": "accent",
              "filters": [
                "flow"
              ]
            },
            {
              "id": "item-2",
              "order": 2,
              "status": "ENTRY",
              "title": "Item 2",
              "description": [
                "cells in a row are equal width",
                "and fill the section edge to edge"
              ],
              "detail": "Every leaf cell stretches to an equal share of its section's width, so a row of cells always spans the section with no gap on the right.",
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
                  "status": "INTERNAL",
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
              "title": "Group 2",
              "variant": "neutral",
              "treatment": [
                "plain"
              ],
              "columns": 1,
              "children": [
                {
                  "id": "item-4",
                  "status": "INTERNAL",
                  "title": "Item 4",
                  "description": [
                    "another nested group"
                  ],
                  "detail": "Sections nest as deep as the idea needs — a recursive grid of grids down to the boxes at the leaves.",
                  "variant": "neutral"
                },
                {
                  "id": "item-5",
                  "status": "INTERNAL",
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
              "status": "UNCHANGED",
              "title": "Item 6",
              "description": [
                "the rail labels this row"
              ],
              "detail": "A rail is a swimlane-style label banner; a separator is a thin divider line. Both are structural leaf types, not data-carrying boxes."
            },
            {
              "id": "item-7",
              "order": 4,
              "status": "NEW",
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
          "subtitle": "a partial merge — Item C spans 2 of 4 columns, not the whole row",
          "variant": "neutral",
          "order": 5,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "item-a",
              "order": 1,
              "span": 1,
              "status": "UNCHANGED",
              "title": "Item A",
              "description": [
                "one cell of four"
              ]
            },
            {
              "id": "item-b",
              "order": 2,
              "span": 1,
              "status": "UNCHANGED",
              "title": "Item B",
              "description": [
                "one cell of four"
              ]
            },
            {
              "id": "item-c",
              "order": 3,
              "span": 2,
              "status": "NEW",
              "title": "Item C — span 2",
              "description": [
                "occupies exactly 2 of the 4 tracks"
              ],
              "detail": "A partial merge (1 &lt; span &lt; columns) occupies exactly that many tracks and keeps its proportion as the grid collapses; only a span == columns child becomes a full-width band.",
              "variant": "accent"
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
                  "status": "STEP",
                  "title": "Step 1",
                  "description": [
                    "tall sub-section"
                  ]
                },
                {
                  "id": "l-2",
                  "status": "STEP",
                  "title": "Step 2",
                  "description": [
                    "four stacked cells"
                  ]
                },
                {
                  "id": "l-3",
                  "status": "STEP",
                  "title": "Step 3",
                  "description": [
                    "makes this block tall"
                  ]
                },
                {
                  "id": "l-4",
                  "status": "STEP",
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
                  "status": "NOTE",
                  "title": "Note A",
                  "description": [
                    "a shorter sub-section"
                  ]
                },
                {
                  "id": "r-2",
                  "status": "NOTE",
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
                  "status": "INTERNAL",
                  "title": "One item",
                  "description": [
                    "a stacked sub-section"
                  ]
                },
                {
                  "id": "gg-1-box-2",
                  "status": "INTERNAL",
                  "title": "Two item",
                  "description": [
                    "with a second cell"
                  ]
                },
                {
                  "id": "gg-1-box-3",
                  "status": "INTERNAL",
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
                  "status": "INTERNAL",
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
              "status": "STEP",
              "title": "One",
              "description": [
                "cell 1 of 6"
              ]
            },
            {
              "id": "h-2",
              "status": "STEP",
              "title": "Two",
              "description": [
                "cell 2 of 6"
              ]
            },
            {
              "id": "h-3",
              "status": "STEP",
              "title": "Three",
              "description": [
                "cell 3 of 6"
              ]
            },
            {
              "id": "h-4",
              "status": "STEP",
              "title": "Four",
              "description": [
                "cell 4 of 6"
              ]
            },
            {
              "id": "h-5",
              "status": "STEP",
              "title": "Five",
              "description": [
                "cell 5 of 6"
              ]
            },
            {
              "id": "h-6",
              "status": "STEP",
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
                  "status": "NEW",
                  "title": "Alpha",
                  "description": [
                    "content-heavy section"
                  ]
                },
                {
                  "id": "heavy-2",
                  "status": "NEW",
                  "title": "Beta",
                  "description": [
                    "four boxes in two columns"
                  ]
                },
                {
                  "id": "heavy-3",
                  "status": "NEW",
                  "title": "Gamma",
                  "description": [
                    "so it earns the width"
                  ]
                },
                {
                  "id": "heavy-4",
                  "status": "NEW",
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
              "status": "NOTE",
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
                  "status": "UNCHANGED",
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
              "status": "LANE",
              "title": "Lane",
              "treatment": [
                "vertical"
              ],
              "detail": "A <code>vertical</code> treatment rotates the text onto the block axis, the same reading direction as a <code>rail</code>. Because the title no longer flows horizontally, the word-fit invariant (N) does not apply to it — its applicability clause exempts vertical leaves."
            },
            {
              "id": "j-h1",
              "order": 2,
              "status": "TOP",
              "title": "Half A",
              "treatment": [
                "half"
              ],
              "detail": "Two <code>half</code> components share ONE grid slot: this is the top half. The slot keeps the full 130px cell height, so the grid's rows, tracks and fill are unchanged — a half pair reads as one full cell from the outside."
            },
            {
              "id": "j-h2",
              "order": 3,
              "status": "BOTTOM",
              "title": "Half B",
              "treatment": [
                "half"
              ],
              "detail": "The bottom half of the same slot. Invariant U now asserts the height of the SLOT rather than of the component, which is what lets a half legitimately be a fraction of the cell without leaving a hole."
            },
            {
              "id": "j-center",
              "order": 4,
              "status": "NEW",
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
              "status": "TOP",
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
              "status": "BOTTOM",
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
      "name": "Overview",
      "order": 1
    },
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
              "status": "SLOT",
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
              "status": "EQUAL",
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
              "status": "CLAMP",
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
              "status": "DIAL",
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
              "status": "TRACK",
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
              "status": "TRACK",
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
              "status": "TRACK",
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
              "status": "MERGE",
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
              "status": "HOLDS",
              "title": "The third track",
              "description": [
                "the merge left one track open",
                "this cell is what closes it"
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
              "status": "BAND",
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
              "status": "OWNS",
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
              "status": "BASE",
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
          "subtitle": "rowspan 1·2·3·4 with the colour scaling in parallel — one claim, two channels",
          "variant": "neutral",
          "order": 4,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p1-bar-1",
              "order": 1,
              "rowspan": 1,
              "status": "1 ROW",
              "title": "rowspan: 1",
              "description": [
                "the base slot, unmerged",
                "the zero of both scales"
              ],
              "detail": "The shortest bar is just a cell: <code>rowspan: 1</code> is the default and merges nothing. It anchors both channels at once — the smallest height AND the quietest colour role (<code>neutral</code>).",
              "variant": "neutral"
            },
            {
              "id": "p1-bar-2",
              "order": 2,
              "rowspan": 2,
              "status": "2 ROWS",
              "title": "rowspan: 2",
              "description": [
                "twice the slot height",
                "colour steps up with it"
              ],
              "detail": "<code>rowspan: K</code> is the vertical merge (<code>.mrsp</code>, <code>grid-row: span 2</code>): the cell becomes K slots tall, K× 130px plus the gaps between them. Its column position is untouched — the horizontal cascade never moves it sideways.",
              "variant": "good"
            },
            {
              "id": "p1-bar-3",
              "order": 3,
              "rowspan": 3,
              "status": "3 ROWS",
              "title": "rowspan: 3",
              "description": [
                "three slots tall",
                "warn — the amber step"
              ],
              "detail": "Because size and colour both grow, the magnitude is legible twice: read the ladder by height and you get the same ranking you get by colour. That is principle 5 used deliberately — two channels DOUBLED on one claim to reinforce it, rather than split across two claims.",
              "variant": "warn"
            },
            {
              "id": "p1-bar-4",
              "order": 4,
              "rowspan": 4,
              "status": "4 ROWS",
              "title": "rowspan: 4",
              "description": [
                "four slots — the tallest bar",
                "bad — the top of the scale"
              ],
              "detail": "The tallest bar is also what CREATES the four rows its shorter neighbours merge into: a merge consumes rows that must exist, and here the extreme of the scale is what brings them into being. The rows a <code>rowspan</code> cell touches are exempt from the rectangle-closure check and from the orphan-row check — a tapered ladder is the chart, not a hole.",
              "variant": "bad"
            }
          ]
        }
      ],
      "name": "1 · Merged cell",
      "order": 2
    },
    {
      "id": "p2-cells-or-zones",
      "layout": "grid",
      "form": "comparison",
      "columns": 2,
      "sections": [
        {
          "id": "p2-cells",
          "title": "A grid of cells",
          "subtitle": "every child is a leaf — this level is a real grid of tracks and rows",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p2-c-columns",
              "order": 1,
              "status": "COLUMNS",
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
              "status": "SPAN",
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
              "status": "ROWSPAN",
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
              "status": "CHECKS",
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
          "span": 1,
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
                  "status": "COLUMNS",
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
                  "status": "SPAN",
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
                  "status": "ROWSPAN",
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
                  "status": "CHECKS",
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
          "span": 2,
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
                  "status": "WEIGHT",
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
                  "status": "NESTED",
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
                  "status": "WEIGHT",
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
          "id": "p2-mix",
          "title": "Mixing is legal",
          "subtitle": "phase zones divided by vertical separators — a real timeline row",
          "variant": "neutral",
          "order": 4,
          "span": 2,
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
                  "status": "ZONE",
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
                  "status": "LEAF",
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
                  "status": "COST",
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
      "order": 3
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
              "status": "ORDER 1",
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
              "status": "ORDER 2",
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
              "status": "ORDER 3",
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
              "status": "ANCHOR",
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
              "status": "PACKS",
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
              "status": "PACKS",
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
              "status": "FORWARD",
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
              "status": "FORWARD",
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
              "status": "LATE",
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
      "order": 4
    }
  ]
};
if (typeof document !== 'undefined' && document.documentElement)
  document.documentElement.setAttribute('data-palette', window.__DOC__.palette || 'neutral');
