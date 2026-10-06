// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = {
  "title": "Diagram Deck",
  "subtitle": "A portable, data-driven diagram — edit data/ and run npm run build",
  "version": "0.2.0",
  "look": "brand",
  "palette": "rose-pine",
  "tokens": {
    "row": {
      "cell_h": 130,
      "sep_h": 40,
      "zone_min_h": 180,
      "compact_h": 74
    },
    "space": {
      "base": 8,
      "scale": [
        0.5,
        1,
        2,
        3,
        4,
        6,
        8
      ]
    },
    "frame": {
      "v": 28,
      "h": 40,
      "top": 35,
      "narrow": 8
    },
    "plane_max": 1280,
    "cell_min_w": 120,
    "type": {
      "title": {
        "min_px": 15,
        "vw": 1,
        "max_px": 17,
        "lines": 2
      },
      "desc": {
        "px": 12,
        "lh": 1.4,
        "lines": 3
      },
      "kicker": {
        "px": 10.5,
        "track_em": 0.09
      },
      "section_title": {
        "min_px": 13,
        "vw": 0.85,
        "max_px": 14.5,
        "track_em": 0.1,
        "lines": 2
      },
      "section_sub": {
        "px": 12,
        "lines": 3
      },
      "rail": {
        "px": 13,
        "track_em": 0.09
      },
      "rail_hue": {
        "px": 10.5,
        "track_em": 0,
        "pad_y": 10
      },
      "panel": {
        "title_px": 19,
        "summary_px": 15,
        "kicker_px": 13,
        "kicker_track_em": 0.08
      }
    },
    "indent_step": 32,
    "dim": {
      "box": 0.18,
      "label": 0.34
    },
    "panel": {
      "dock": "bottom-left",
      "inset": 24,
      "width_cols": 2
    },
    "breakpoints": {
      "stack": 1440,
      "two": 1000,
      "one": 640
    },
    "viewport": {
      "w": 1920,
      "h": 1080
    },
    "default_columns": 2
  },
  "css_vars": {
    "--cell-h": "130px",
    "--sep-row-h": "40px",
    "--zone-min-h": "180px",
    "--frame-v": "28px",
    "--frame-h": "40px",
    "--frame-top": "35px",
    "--frame-narrow": "8px",
    "--plane-max": "1280px",
    "--cell-min-w": "120px",
    "--title-min": "15px",
    "--title-vw": "1vw",
    "--title-max": "17px",
    "--title-lines": "2",
    "--desc-px": "12px",
    "--desc-lh": "1.4",
    "--desc-lines": "3",
    "--kicker-px": "10.5px",
    "--kicker-track": "0.09em",
    "--ztitle-min": "13px",
    "--ztitle-vw": "0.85vw",
    "--ztitle-max": "14.5px",
    "--ztitle-track": "0.1em",
    "--ztitle-lines": "2",
    "--zsub-px": "12px",
    "--zsub-lines": "3",
    "--rail-px": "13px",
    "--rail-track": "0.09em",
    "--rail-hue-px": "10.5px",
    "--rail-hue-track": "0em",
    "--rail-hue-pad-y": "10px",
    "--panel-title-px": "19px",
    "--panel-summary-px": "15px",
    "--panel-kicker-px": "13px",
    "--panel-kicker-track": "0.08em",
    "--indent-step": "32px",
    "--dim-box": "0.18",
    "--dim-label": "0.34",
    "--panel-inset": "24px",
    "--s-1": "4px",
    "--s-2": "8px",
    "--s-3": "16px",
    "--s-4": "24px",
    "--s-5": "32px",
    "--s-6": "48px",
    "--s-7": "64px",
    "--panel-left": "24px",
    "--panel-right": "auto",
    "--panel-top": "auto",
    "--panel-bottom": "24px"
  },
  "pages": [
    {
      "id": "p10-flow-phases",
      "form": "flow",
      "columns": 3,
      "filters": [
        {
          "key": "path",
          "label": "Which way does the story run?",
          "steps": [
            "Click the chip to light every step of the story, across its three phases at once.",
            "The story runs left to right: say it, find its phases, decide how it ends.",
            "The kicker STEP n OF 6 says where each step sits, so the direction survives without an arrow."
          ]
        }
      ],
      "sections": [
        {
          "id": "p10-lead",
          "order": 0,
          "span": 3,
          "lead": true,
          "kicker": "STORY · 1 OF 2",
          "title": "What does your story do? This one moves",
          "description": [
            "say it in one sentence first: a story that moves becomes a path of phases"
          ],
          "detail": "Every diagram starts with the story, before any piece. Say it in one sentence and ask two things: does it MOVE or STAND, and does it close on one thing or open into many? This story moves through three phases, so the page is a path read left to right, one phase per section. A story that stands, like a structure or a comparison, would be a board instead."
        },
        {
          "id": "p10-phase-1",
          "title": "Say it",
          "subtitle": "the story in one sentence, before anything is drawn",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "p10-s1",
              "order": 1,
              "kicker": "STEP 1 OF 6",
              "title": "One sentence",
              "description": [
                "what should the reader know",
                "when they leave the page?"
              ],
              "detail": "Before any box, say the story in one sentence: what the reader should know when they leave. If you cannot say it, the diagram cannot either, and no piece will fix that.",
              "variant": "neutral",
              "filters": [
                "path"
              ]
            },
            {
              "id": "p10-s2",
              "order": 2,
              "kicker": "STEP 2 OF 6",
              "title": "Moves or stands?",
              "description": [
                "a process moves; a structure",
                "stands still"
              ],
              "detail": "Ask what the story does. A process, a request, a release MOVES: it goes from one state to the next. A system map, a comparison, an org STANDS: its parts exist at once. This page's story moves, so it reads as a path.",
              "variant": "neutral",
              "filters": [
                "path"
              ]
            }
          ]
        },
        {
          "id": "p10-phase-2",
          "title": "Find its phases",
          "subtitle": "where the story changes, and what happens inside",
          "variant": "neutral",
          "order": 2,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "p10-s3",
              "order": 1,
              "kicker": "STEP 3 OF 6",
              "title": "Where does it change?",
              "description": [
                "each change of stage is",
                "the start of a new phase"
              ],
              "detail": "A story that moves passes through stages. Find where it changes: each change starts a new phase, and each phase is something with steps of its own. This page has three: say it, find its phases, decide how it ends.",
              "variant": "accent",
              "filters": [
                "path"
              ]
            },
            {
              "id": "p10-s4",
              "order": 2,
              "kicker": "STEP 4 OF 6",
              "title": "Count the steps",
              "description": [
                "what happens inside each",
                "phase, in order"
              ],
              "detail": "Inside each phase, list what happens, in order. Keep the count true as you go: this step says STEP 4 OF 6 because the whole story has six, and the reader uses that count to know where they are.",
              "variant": "neutral",
              "filters": [
                "path"
              ]
            }
          ]
        },
        {
          "id": "p10-phase-3",
          "title": "Decide how it ends",
          "subtitle": "how deep one page goes, and what the arrows meant",
          "variant": "neutral",
          "order": 3,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "p10-s5",
              "order": 1,
              "kicker": "STEP 5 OF 6",
              "title": "One page or two?",
              "description": [
                "the overview is one page,",
                "the close-up is the next"
              ],
              "detail": "Decide how deep this page goes. A story told at two depths becomes two pages: the overview first, then the close-up. The same question on both pages ties them together, the way the order question ties the ideas pages together.",
              "variant": "neutral",
              "filters": [
                "path"
              ]
            },
            {
              "id": "p10-s6",
              "order": 2,
              "kicker": "STEP 6 OF 6",
              "title": "What the arrows said",
              "description": [
                "each arrow in your head",
                "becomes words and an order"
              ],
              "detail": "When you picture the story you probably draw arrows. Say what each one means (sends, waits for, depends on) and keep those words: the page tells them in the order of its steps and in their text, not with a line.",
              "variant": "neutral",
              "filters": [
                "path"
              ]
            }
          ]
        }
      ],
      "name": "Story · the story moves",
      "order": 1
    },
    {
      "id": "p12-shapes",
      "form": "flow",
      "columns": 1,
      "filters": [
        {
          "key": "gate",
          "label": "Which boxes are the gates?",
          "steps": [
            "A core chip: declared once in document.yaml, inherited first by every page that does not omit it.",
            "It lights the two gates wherever they appear, with the same label on every page."
          ]
        },
        {
          "key": "loop",
          "label": "Which steps close the loop?",
          "steps": [
            "Six steps in two legs. The outbound leg runs 1 to 3; the return leg runs 4 to 6 in its own section.",
            "Both legs keep their natural <code>order</code>, so one column still reads 1 to 6. The direction is in the kicker: step 6 says <b>back to 1</b>."
          ]
        }
      ],
      "sections": [
        {
          "id": "p12-lead",
          "order": 1,
          "lead": true,
          "kicker": "STORY · 2 OF 2",
          "title": "Does your story come back, or climb?",
          "description": [
            "a story that returns is a ring; a story that grows is a staircase"
          ],
          "detail": "Before any piece, say what the story does in one sentence. This one goes out and comes back (say it, name who acts, test it, tell a listener, hear the gap, say it again), so the page is a ring: an outbound leg and a return leg, with one chip across them. The story below it climbs, so it is a staircase. The shape is decided by the sentence you tell, never by the boxes you have."
        },
        {
          "id": "p12-out",
          "title": "Outbound leg · steps 1 to 3",
          "order": 2,
          "columns": 3,
          "children": [
            {
              "id": "p12-s1",
              "order": 1,
              "kicker": "STEP 1 OF 6 →",
              "title": "Say it once",
              "description": [
                "one sentence, before any box"
              ],
              "filters": [
                "loop"
              ],
              "detail": "The story starts as one sentence said aloud. If it needs two sentences, it is two stories, and each one gets its own page."
            },
            {
              "id": "p12-s2",
              "order": 2,
              "kicker": "STEP 2 OF 6 →",
              "title": "Name who acts",
              "description": [
                "the actors the reader follows"
              ],
              "filters": [
                "loop"
              ],
              "detail": "Each actor you name is something the reader will follow across the page. Name only the actors the sentence needs."
            },
            {
              "id": "p12-s3",
              "order": 3,
              "kicker": "STEP 3 OF 6 ↓",
              "title": "Does it hold?",
              "description": [
                "a gate: still one sentence"
              ],
              "filters": [
                "loop",
                "gate"
              ],
              "detail": "The first gate on the story: if naming the actors split the sentence in two, stop and make two pages. The kicker's arrow turns the corner into the return leg."
            }
          ]
        },
        {
          "id": "p12-back",
          "title": "Return leg · steps 4 to 6",
          "order": 3,
          "columns": 3,
          "children": [
            {
              "id": "p12-s4",
              "order": 1,
              "kicker": "STEP 4 OF 6",
              "title": "Tell a listener",
              "description": [
                "a gate: can they say it back?"
              ],
              "filters": [
                "loop",
                "gate"
              ],
              "detail": "The second gate: someone who did not write the story says it back to you. The return leg is its own section, so it never needs a reversed <code>order</code>."
            },
            {
              "id": "p12-s5",
              "order": 2,
              "kicker": "STEP 5 OF 6",
              "title": "Hear the gap",
              "description": [
                "what they could not repeat"
              ],
              "filters": [
                "loop"
              ],
              "detail": "What the listener dropped is either missing from the story or something the story did not need. Both are decided before any box is drawn."
            },
            {
              "id": "p12-s6",
              "order": 3,
              "kicker": "STEP 6 OF 6 · BACK TO 1",
              "title": "Say it again",
              "description": [
                "the sentence, now tighter"
              ],
              "filters": [
                "loop"
              ],
              "detail": "The ring closes in text: the kicker says where the path goes next, and the chip's steps say it again."
            }
          ]
        },
        {
          "id": "p12-stairs",
          "title": "A story that climbs",
          "order": 4,
          "columns": 3,
          "treatment": [
            "compact"
          ],
          "children": [
            {
              "id": "p12-t3",
              "order": 1,
              "rowspan": 3,
              "kicker": "COST 3",
              "title": "Found on screen",
              "description": [
                "the latest, dearest find"
              ],
              "detail": "This story grows: the later a mistake is found, the more it costs, so each step stands taller than the last. A story that climbs is told as a staircase, from the smallest step to the largest."
            },
            {
              "id": "p12-gap-1",
              "order": 2,
              "type": "spacer"
            },
            {
              "id": "p12-gap-2",
              "order": 3,
              "type": "spacer",
              "rowspan": 2
            },
            {
              "id": "p12-t2",
              "order": 4,
              "rowspan": 2,
              "kicker": "COST 2",
              "title": "Found in the sketch",
              "detail": "A mistake found in the sketch costs a redraw of lines, still cheap. The empty place above this step is written on purpose, so every step stands on the same floor."
            },
            {
              "id": "p12-t1",
              "order": 5,
              "kicker": "COST 1",
              "title": "Found in the story",
              "detail": "A mistake found while saying the story costs one sentence. That is why the story comes first: every later step is dearer."
            }
          ],
          "tokens": {
            "row": {
              "cell_h": 74
            }
          },
          "css_vars": {
            "--cell-h": "74px"
          }
        }
      ],
      "name": "Story · it comes back or climbs",
      "order": 2
    },
    {
      "id": "p7-structure",
      "form": "comparison",
      "columns": 2,
      "sections": [
        {
          "id": "p7-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "IDEAS · 1 OF 4",
          "title": "Name what is distinct before you group it",
          "description": [
            "two different things never share one group just to fill a rectangle"
          ],
          "detail": "With the story said, list its ideas: what is distinct, what goes together, what comes in order, what crosses everything. This page is the first question. One side folds two different things into one group and every check stays green; the other splits them. Only you can tell which is right, because only you know what the story means."
        },
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
              "detail": "A message queue: something that runs, with parts of its own. It is a DISTINCT thing from the policy beside it (a different lifecycle, a different owner, a different way to fail) and nothing in this group says so.",
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
              "detail": "An access policy: a rule, not something that runs. Sharing a frame with the queue makes the two read as siblings of one kind, which is a claim the story never made.",
              "variant": "neutral"
            },
            {
              "id": "p7-fd-frame",
              "order": 3,
              "kicker": "THE FRAME",
              "title": "The frame asserts",
              "description": [
                "one group says these",
                "belong to one thing"
              ],
              "detail": "A group says that everything inside it is part of ONE thing. Here that statement is false, and the reader has no way to recover the boundary the author dropped: the boxes are peers, so any split they suggest is a guess.",
              "variant": "neutral"
            },
            {
              "id": "p7-fd-green",
              "order": 4,
              "kicker": "STILL GREEN",
              "title": "And it passes",
              "description": [
                "2 columns, 2 rows",
                "the rectangle closes"
              ],
              "detail": "This is the uncomfortable half: <code>npm run model</code> reports a closed rectangle and <code>npm run census</code> counts every box where the YAML put it. Both checks are RIGHT, because a fold is not a geometry defect. It is a mistake about the ideas, and no arithmetic reaches it.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p7-distinct",
          "title": "Split into two",
          "subtitle": "the same information — two things, so two groups",
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
                  "title": "Its own group",
                  "description": [
                    "a distinct thing gets",
                    "a frame of its own"
                  ],
                  "detail": "The queue has its own group now, and the frame says the true thing: what is inside belongs to the queue. The question that decided it was simple: is the queue the same kind of thing as the policy? It is not.",
                  "variant": "neutral"
                },
                {
                  "id": "p7-q-part",
                  "order": 2,
                  "kicker": "PART",
                  "title": "Its parts, inside",
                  "description": [
                    "a part belongs to",
                    "the thing above it"
                  ],
                  "detail": "A part of one thing stays inside that thing's group, never in a group of its own. Promoting a part to its own group claims it is a peer of the whole, which is the fold's mistake made in the other direction.",
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
                  "title": "A second group",
                  "description": [
                    "the second thing, named",
                    "and framed apart"
                  ],
                  "detail": "The policy gets its own group and its own name. Nothing places it inside the queue, which is correct: the policy governs the queue, it is not part of it, and 'governs' is a relation the last ideas question names, never a shared frame.",
                  "variant": "neutral"
                },
                {
                  "id": "p7-p-part",
                  "order": 2,
                  "kicker": "PART",
                  "title": "Parts, again",
                  "description": [
                    "same rule, other thing",
                    "parts stay inside"
                  ],
                  "detail": "The same question asked twice is what makes the page readable: every group is a thing, every box inside it is a part of that thing. Once that holds everywhere, the page reads as the idea instead of as a picture of it.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        },
        {
          "id": "p7-judge",
          "title": "Only you can tell",
          "subtitle": "both blocks above pass every check — the difference is meaning, not geometry",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p7-j-blind",
              "order": 1,
              "kicker": "YOUR CALL",
              "title": "The checks cannot see it",
              "description": [
                "they count and measure",
                "they never read meaning"
              ],
              "detail": "<code>npm run model</code> checks arithmetic and <code>npm run census</code> states what each page holds. Neither knows what your story means, so a green run says <em>it is not broken</em> and never <em>it is right</em>. Whether two things are distinct is decided by the person who tells the story.",
              "variant": "neutral"
            },
            {
              "id": "p7-j-why",
              "order": 2,
              "kicker": "THE TEST",
              "title": "Say why, idea by idea",
              "description": [
                "why its own group",
                "why together, why apart"
              ],
              "detail": "Ask it of every idea: why is this its own thing rather than a part of another, why does it sit with these and not those? An idea with no answer is decoration, and decoration is what the fold on the left is made of.",
              "variant": "neutral"
            },
            {
              "id": "p7-j-line",
              "order": 3,
              "kicker": "THE LINE",
              "title": "Diagram or decoration",
              "description": [
                "naming what is distinct",
                "is what separates them"
              ],
              "detail": "Every other choice can be got wrong and still leave a diagram that says something. Get this one wrong and the boxes are arranged rather than meant, which is the whole difference between a diagram and an illustration of one.",
              "variant": "warn"
            }
          ]
        }
      ],
      "name": "Ideas · what is distinct",
      "order": 3
    },
    {
      "id": "p2-cells-or-zones",
      "form": "comparison",
      "columns": 4,
      "sections": [
        {
          "id": "p2-lead",
          "order": 0,
          "span": 4,
          "lead": true,
          "kicker": "IDEAS · 2 OF 4",
          "title": "What goes together is one group",
          "description": [
            "the parts of one thing sit together; different things sit apart"
          ],
          "detail": "Grouping is an idea about your story before it is a piece: 'these are parts of one thing' makes one group, 'these are different things' makes separate groups side by side. This page shows both readings, so you hear what the grouping says before you choose the piece that carries it."
        },
        {
          "id": "p2-cells",
          "title": "Parts of one thing",
          "subtitle": "these four belong together, so they share one group",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 2,
          "children": [
            {
              "id": "p2-c-columns",
              "order": 1,
              "kicker": "PART",
              "title": "They are one thing",
              "description": [
                "four parts of one idea",
                "sit inside one frame"
              ],
              "detail": "When you say 'these go together', you are saying they are parts of ONE thing. Put them in one group and the frame says it for you: the reader sees a single thing with four parts before reading a word.",
              "variant": "neutral"
            },
            {
              "id": "p2-c-span",
              "order": 2,
              "kicker": "PART",
              "title": "They are peers",
              "description": [
                "same group, same size:",
                "no part outranks another"
              ],
              "detail": "Parts of one thing are usually peers. Inside one group they get the same size, so the reader does not look for a ranking the story does not have.",
              "variant": "neutral"
            },
            {
              "id": "p2-c-rowspan",
              "order": 3,
              "kicker": "PART",
              "title": "One can be larger",
              "description": [
                "a part that matters more",
                "can grow inside the group"
              ],
              "detail": "Sometimes one part is bigger than the others: more volume, more cost, more weight. That is still one thing with parts, and the group lets one part be taller or wider without breaking it apart.",
              "variant": "neutral"
            },
            {
              "id": "p2-c-checks",
              "order": 4,
              "kicker": "ASK",
              "title": "Ask: one thing?",
              "description": [
                "if one noun names them all,",
                "they are one group"
              ],
              "detail": "The test is a sentence: can you name these boxes together with one noun, like 'the build steps' or 'the payment options'? If you can, they are one group. If you need two nouns, they are two groups.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p2-zones",
          "title": "Different things",
          "subtitle": "two things side by side: each one gets a group of its own",
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
              "title": "One thing",
              "variant": "neutral",
              "order": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-z-columns",
                  "order": 1,
                  "kicker": "THING A",
                  "title": "Its own frame",
                  "description": [
                    "a distinct thing gets",
                    "a group of its own"
                  ],
                  "detail": "When two things are different, each gets its own group, side by side. The two frames say 'these are two things' before the reader reads either title.",
                  "variant": "neutral"
                },
                {
                  "id": "p2-z-span",
                  "order": 2,
                  "kicker": "THING A",
                  "title": "Its own parts",
                  "description": [
                    "what sits inside belongs",
                    "to it and to nothing else"
                  ],
                  "detail": "Each group holds the parts of its own thing. A part that belongs to both things goes in neither group: it is a relation that crosses them, the last question of the ideas step.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p2-z-lost",
              "title": "Another thing",
              "variant": "neutral",
              "order": 2,
              "columns": 1,
              "children": [
                {
                  "id": "p2-z-rowspan",
                  "order": 1,
                  "kicker": "THING B",
                  "title": "Never folded in",
                  "description": [
                    "a different thing is not",
                    "squeezed into A's group"
                  ],
                  "detail": "Folding a different thing into another group to fill space makes the page lie: the reader believes they are one thing. Keep them apart even when one group ends up with fewer boxes.",
                  "variant": "neutral"
                },
                {
                  "id": "p2-z-checks",
                  "order": 2,
                  "kicker": "THING B",
                  "title": "Side by side",
                  "description": [
                    "two groups on one row say",
                    "these two are compared"
                  ],
                  "detail": "Two groups placed side by side invite a comparison. If the story compares them, that is exactly right; if it does not, give each one its own row.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        },
        {
          "id": "p2-weight",
          "title": "One thing weighs more",
          "subtitle": "when one group carries twice as much, the idea says so first",
          "variant": "neutral",
          "order": 3,
          "span": 3,
          "columns": 3,
          "children": [
            {
              "id": "p2-w-heavy",
              "title": "The larger thing",
              "subtitle": "twice the weight",
              "variant": "neutral",
              "order": 1,
              "span": 2,
              "columns": 2,
              "children": [
                {
                  "id": "p2-w-h1",
                  "order": 1,
                  "kicker": "WEIGHT",
                  "title": "It carries more",
                  "description": [
                    "this group holds more of",
                    "the story: two thirds of it"
                  ],
                  "detail": "Weight is an idea about your story: one group carries more volume, more cost or more attention than its neighbour. Decide it here as a proportion, like 'twice as much'; the data step later writes it as a number and the width follows.",
                  "variant": "neutral"
                },
                {
                  "id": "p2-w-h2",
                  "order": 2,
                  "kicker": "INSIDE",
                  "title": "Groups hold groups",
                  "description": [
                    "a group's parts can be",
                    "groups of their own"
                  ],
                  "detail": "A thing can have parts that are themselves things with parts. Say it as an idea, like 'the platform has two services, each with its own steps', and the groups nest the same way.",
                  "variant": "neutral"
                }
              ]
            },
            {
              "id": "p2-w-light",
              "title": "The smaller thing",
              "subtitle": "half the weight",
              "variant": "neutral",
              "order": 2,
              "span": 1,
              "columns": 1,
              "children": [
                {
                  "id": "p2-w-l1",
                  "order": 1,
                  "kicker": "WEIGHT",
                  "title": "It carries less",
                  "description": [
                    "half of what its neighbour",
                    "carries, and it says so"
                  ],
                  "detail": "A group that carries less says so by taking less room. The difference in size is part of what the page says, so decide it on purpose, never as a leftover.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        },
        {
          "id": "p2-third",
          "title": "What is left over",
          "subtitle": "an idea that fits no group you have",
          "variant": "neutral",
          "order": 4,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "p2-t-band",
              "order": 1,
              "kicker": "ASK",
              "title": "Does it belong?",
              "description": [
                "a thing that fits no group",
                "is a group of its own"
              ],
              "detail": "When an idea does not belong to any group you have, do not fold it into the nearest one. Give it a group of its own, even a small one: that is the honest shape of the story.",
              "variant": "neutral"
            },
            {
              "id": "p2-t-tracks",
              "order": 2,
              "kicker": "ASK",
              "title": "Or is it a relation?",
              "description": [
                "if it touches every group,",
                "it crosses them instead"
              ],
              "detail": "An idea that touches every group is not a group at all: it is something that crosses everything. Keep it for the last question of the ideas step, where relations are named.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p2-mix",
          "title": "Phases are groups too",
          "subtitle": "each phase is a thing with parts; a line only pauses inside one thing",
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
                  "kicker": "PHASE",
                  "title": "A phase is a thing",
                  "description": [
                    "each phase has parts of",
                    "its own, so it is a group"
                  ],
                  "detail": "A phase of a timeline is a distinct thing with steps of its own, so each phase is its own group. The groups stand side by side because the story moves from one to the next.",
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
                  "kicker": "PAUSE",
                  "title": "A line is a pause",
                  "description": [
                    "the dotted lines between",
                    "phases only mark a pause"
                  ],
                  "detail": "The dotted lines beside this group mark a pause in the reading, not a new thing. If the two sides of a line were different things, they would be different groups, not a line.",
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
                  "kicker": "ASK",
                  "title": "Group, or pause?",
                  "description": [
                    "two things: two groups",
                    "one thing, a breath: a line"
                  ],
                  "detail": "The question for every division on the page: are these two different things, or one thing with a breath in the middle? Two things are two groups; one thing with a breath is one group and a line.",
                  "variant": "neutral"
                }
              ]
            }
          ]
        }
      ],
      "name": "Ideas · what goes together",
      "order": 4
    },
    {
      "id": "p3-sequence",
      "form": "flow",
      "columns": 2,
      "filters": [
        {
          "key": "packing",
          "label": "What comes first?",
          "steps": [
            "Click the chip to light the three boxes that say the order of the story.",
            "First, then, always: the order is a decision about the story, not about the screen.",
            "It reads 1 → 2 → 3 here, on a wide screen and on a narrow one."
          ]
        }
      ],
      "sections": [
        {
          "id": "p3-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "IDEAS · 3 OF 4",
          "title": "What comes in order stays in order",
          "description": [
            "decide what comes first; that order holds on every screen"
          ],
          "detail": "Some ideas are a sequence: first this, then that. Write it down as an idea before any piece: which things come in order, and which only sit together. The order you decide is the order the reader meets them, left to right on a wide screen and top to bottom on a narrow one."
        },
        {
          "id": "p3-order",
          "title": "Say what comes first",
          "subtitle": "an order is an idea: first this, then that, and it holds on every screen",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p3-o-1",
              "order": 1,
              "kicker": "FIRST",
              "title": "Name the first",
              "description": [
                "what does the reader meet",
                "first? that is step one"
              ],
              "detail": "An order starts with a decision: what must the reader meet first? Write the sequence as sentences, like 'first they sign up, then they pay, then they receive', before any piece exists. The page will follow it.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            },
            {
              "id": "p3-o-2",
              "order": 2,
              "kicker": "THEN",
              "title": "Then the next",
              "description": [
                "each idea follows the one",
                "it depends on or builds on"
              ],
              "detail": "Each next idea is the one that follows from the previous: it depends on it, happens after it, or builds on it. If two ideas could swap places without changing the story, they are not in order: they only sit together.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            },
            {
              "id": "p3-o-3",
              "order": 3,
              "kicker": "ALWAYS",
              "title": "On every screen",
              "description": [
                "the same order on a wide",
                "screen and on a narrow one"
              ],
              "detail": "The order you decide is the reading order everywhere: left to right on a wide screen, top to bottom on a narrow one. One decision carries both, so the story never reads backwards.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            }
          ]
        },
        {
          "id": "p3-anchor",
          "title": "What must come before what",
          "subtitle": "each idea after what it needs; the rest sit side by side",
          "variant": "neutral",
          "order": 2,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p3-a-anchor",
              "order": 1,
              "rowspan": 2,
              "kicker": "FIRST",
              "title": "The step all need",
              "description": [
                "one step comes first",
                "because the rest rely on it"
              ],
              "detail": "Ideas come in order when each one needs another to have happened. Find the step every other step relies on and tell it first; it stands taller because the steps after it all lean on it.",
              "variant": "accent"
            },
            {
              "id": "p3-a-beside-1",
              "order": 2,
              "kicker": "THEN",
              "title": "What it enables",
              "description": [
                "second, because it needs",
                "the first step done"
              ],
              "detail": "Ask of each idea: what must already be true before it can happen? The idea that makes it true comes before it. That question, not taste, decides the order.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-beside-2",
              "order": 3,
              "kicker": "THEN",
              "title": "What follows",
              "description": [
                "each step needs the one",
                "before it, not a later one"
              ],
              "detail": "If two ideas could happen in either order, they are not a sequence: they go together, side by side, and belong to the page about what goes together.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-under-1",
              "order": 4,
              "kicker": "IN ORDER",
              "title": "One direction",
              "description": [
                "the reader walks forward",
                "and never has to go back"
              ],
              "detail": "A sequence reads in one direction. If the reader must jump back to understand a step, that step is out of order: move it to where what it needs has already been told.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-under-2",
              "order": 5,
              "kicker": "IN ORDER",
              "title": "The run ends",
              "description": [
                "the last of the steps",
                "that rely on the first"
              ],
              "detail": "The steps that rely on the first one end here. Whatever comes next does not need them; it only comes after them, so it starts a new part below a line.",
              "variant": "neutral"
            },
            {
              "id": "p3-a-sep",
              "type": "separator",
              "order": 6,
              "span": 3,
              "style": "dotted",
              "text": "a new part of the story"
            },
            {
              "id": "p3-a-tail",
              "order": 7,
              "span": 3,
              "kicker": "AFTER",
              "title": "The next part",
              "description": [
                "it comes after the steps",
                "but does not need them"
              ],
              "detail": "This idea spans the whole width and is told after the line: it follows the steps above without depending on them. The rule of the ideas step: put in order only what depends on what came before it; everything else starts a new part.",
              "variant": "muted"
            }
          ]
        }
      ],
      "name": "Ideas · what comes in order",
      "order": 5
    },
    {
      "id": "p6-relations",
      "form": "flow",
      "columns": 2,
      "filters": [
        {
          "key": "packing",
          "label": "What comes first?",
          "steps": [
            "Click the chip to light the three boxes of the path, in the order the story tells them.",
            "A path is a relation with a direction: first, then, last.",
            "The same question is asked on the page about order: one relation, two pages."
          ]
        },
        {
          "key": "crosscut",
          "label": "What crosses the sections?",
          "steps": [
            "Click the chip to light boxes in THREE different groups at once.",
            "A theme has no direction: nothing in it comes first, and it ignores the groups.",
            "One box belongs to this relation AND to the path above: an idea can sit in two relations."
          ]
        }
      ],
      "sections": [
        {
          "id": "p6-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "IDEAS · 4 OF 4",
          "title": "What crosses everything is a relation",
          "description": [
            "some ideas run across the groups; name each one as a question"
          ],
          "detail": "The last question about your ideas: what runs across the groups without belonging to one? A relation is asked as a question, like 'Which boxes are the gates?', and answered by lighting every box that shares it. Name the relations now; the piece that carries them comes in the next step."
        },
        {
          "id": "p6-what",
          "title": "Name it as a question",
          "subtitle": "a relation is something several ideas share, across the groups they sit in",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p6-w-arrow",
              "order": 1,
              "kicker": "ASK",
              "title": "What do they share?",
              "description": [
                "a relation is something",
                "several ideas have in common"
              ],
              "detail": "Look across your groups and ask what several ideas share without belonging to one group: an owner, a risk, a status, a path the reader follows. Each answer is a relation, and it is told as a question, like 'Who owns this?' or 'Which steps can fail?'.",
              "variant": "neutral"
            },
            {
              "id": "p6-w-chip",
              "order": 2,
              "kicker": "CROSSES",
              "title": "It keeps the groups",
              "description": [
                "a relation crosses groups",
                "and never breaks them up"
              ],
              "detail": "A relation does not move ideas out of their groups. The groups stay what the story said they are, and the relation runs across them, lit when the reader asks its question.",
              "variant": "neutral"
            },
            {
              "id": "p6-w-arity",
              "order": 3,
              "kicker": "TWO ENDS",
              "title": "Two ends or nothing",
              "description": [
                "one idea alone is not",
                "a relation to anything"
              ],
              "detail": "A relation needs at least two ideas. If only one idea answers the question, it is not a relation: it is a property of that idea, and it belongs in its own box. <code>npm run model</code> refuses a relation with a single member, so the seed states the rule here instead of showing it.",
              "variant": "warn"
            }
          ]
        },
        {
          "id": "p6-flow",
          "title": "A path has a direction",
          "subtitle": "some relations are a route: this first, then this, then that",
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
                "the first idea on the path",
                "comes first in the story"
              ],
              "detail": "A path is a relation whose members come in order. Say where it starts; the first idea is the one the reader meets first when the question is asked.",
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
                "second on the path",
                "and part of a theme too"
              ],
              "detail": "The only idea on this page in TWO relations: it is a step on the path and it also shares the theme below. An idea can answer several questions, so relations overlap rather than divide the page.",
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
                "the last idea on the path",
                "comes last in the reading"
              ],
              "detail": "The far end of the path. When the question is asked, these three ideas light in the order the story tells them. If the path went back or jumped between groups, the question's own steps would say so.",
              "variant": "neutral",
              "filters": [
                "packing"
              ]
            }
          ]
        },
        {
          "id": "p6-left",
          "title": "One group",
          "subtitle": "an idea that shares the theme, and one that does not",
          "variant": "neutral",
          "order": 3,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p6-l-in",
              "order": 1,
              "kicker": "THEME",
              "title": "Shares it here",
              "description": [
                "this idea answers the",
                "theme's question"
              ],
              "detail": "One end of a theme: a relation with no direction. A theme groups by owner, status or risk, anything true of several ideas at once, and nothing in it comes first or last.",
              "variant": "neutral",
              "filters": [
                "crosscut"
              ]
            },
            {
              "id": "p6-l-out",
              "order": 2,
              "kicker": "NOT IN IT",
              "title": "Does not share it",
              "description": [
                "same group, but it does",
                "not answer the question"
              ],
              "detail": "A neighbour in the same group that does not share the theme. Sitting together in a group and sharing a relation are two different ideas, and the page keeps them apart.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p6-right",
          "title": "Another group",
          "subtitle": "the same theme, across the boundary between two groups",
          "variant": "neutral",
          "order": 4,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p6-r-in",
              "order": 1,
              "kicker": "THEME",
              "title": "Shares it there too",
              "description": [
                "the other end, in a",
                "different group"
              ],
              "detail": "The second end, in a group of its own. Nothing ties the two ideas together but the question they both answer, and that is the whole relation: a shared answer, not a shared place.",
              "variant": "neutral",
              "filters": [
                "crosscut"
              ]
            },
            {
              "id": "p6-r-out",
              "order": 2,
              "kicker": "NOT IN IT",
              "title": "Also outside it",
              "description": [
                "being near a member",
                "is not being related"
              ],
              "detail": "Its neighbour shares the theme and it does not, although they sit side by side. Being beside an idea never makes two ideas related; only answering the same question does.",
              "variant": "muted"
            }
          ]
        }
      ],
      "name": "Ideas · what crosses everything",
      "order": 6
    },
    {
      "id": "pieces-1-frame-and-leaves",
      "form": "dashboard",
      "columns": 1,
      "sections": [
        {
          "id": "pieces-1-lead",
          "order": 1,
          "span": 1,
          "lead": true,
          "kicker": "PIECES · 1 OF 4",
          "title": "A section arranges, a component carries",
          "description": [
            "each entry: the piece live, its YAML, what it says, and its neighbour"
          ]
        },
        {
          "id": "piece-section",
          "order": 2,
          "title": "section",
          "subtitle": "“these go together”",
          "columns": 3,
          "children": [
            {
              "id": "piece-section-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-section-frame",
                  "title": "One thing",
                  "columns": 2,
                  "children": [
                    {
                      "id": "piece-section-a",
                      "title": "Part A"
                    },
                    {
                      "id": "piece-section-b",
                      "title": "Part B"
                    }
                  ]
                }
              ]
            },
            {
              "id": "piece-section-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-section-yaml",
                  "kicker": "YAML",
                  "title": "title: One thing",
                  "description": [
                    "columns: 2",
                    "children: [part-a, part-b]"
                  ],
                  "detail": "<code>- id: one-thing</code><br><code>  title: One thing</code><br><code>  columns: 2</code><br><code>  children:</code><br><code>    - { id: part-a, title: Part A }</code><br><code>    - { id: part-b, title: Part B }</code>"
                },
                {
                  "id": "piece-section-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "One thing, its parts",
                  "description": [
                    "The frame says these are one thing; its boxes are the parts."
                  ]
                },
                {
                  "id": "piece-section-neighbour",
                  "kicker": "NEIGHBOUR · SEPARATOR",
                  "title": "Only a pause",
                  "description": [
                    "Choose it when both sides are parts of the same thing."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-component",
          "order": 3,
          "title": "component",
          "subtitle": "a leaf that carries",
          "columns": 3,
          "children": [
            {
              "id": "piece-component-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-component-box",
                  "title": "A box"
                },
                {
                  "id": "piece-component-rail",
                  "type": "rail",
                  "title": "A rail"
                }
              ]
            },
            {
              "id": "piece-component-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-component-yaml",
                  "kicker": "YAML",
                  "title": "type: rail",
                  "description": [
                    "box · separator · rail · spacer"
                  ],
                  "detail": "<code>- { id: a-box, title: A box }</code><br><code>- { id: a-rail, type: rail, title: A rail }</code><br>A component without <code>type</code> is a box."
                },
                {
                  "id": "piece-component-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "A leaf",
                  "description": [
                    "A component carries content and never holds other pieces."
                  ]
                },
                {
                  "id": "piece-component-neighbour",
                  "kicker": "NEIGHBOUR · SECTION",
                  "title": "It has parts",
                  "description": [
                    "Choose a section when the thing has parts of its own."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-box",
          "order": 4,
          "title": "box",
          "subtitle": "the card",
          "columns": 3,
          "children": [
            {
              "id": "piece-box-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-box-card",
                  "kicker": "STEP 1",
                  "title": "A card",
                  "description": [
                    "one short line"
                  ],
                  "detail": "The detail waits behind the click."
                }
              ]
            },
            {
              "id": "piece-box-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-box-yaml",
                  "kicker": "YAML",
                  "title": "title: A card",
                  "description": [
                    "kicker: STEP 1",
                    "description: one short line"
                  ],
                  "detail": "<code>- id: a-card</code><br><code>  kicker: STEP 1</code><br><code>  title: A card</code><br><code>  description: [one short line]</code><br><code>  detail: The detail waits behind the click.</code>"
                },
                {
                  "id": "piece-box-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The card",
                  "description": [
                    "The one piece with a kicker, a title, a description and a detail."
                  ]
                },
                {
                  "id": "piece-box-neighbour",
                  "kicker": "NEIGHBOUR · RAIL",
                  "title": "Only a label",
                  "description": [
                    "Choose a rail when the thing is only a lane or level name."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-separator",
          "order": 5,
          "title": "separator",
          "subtitle": "a pause inside one thing",
          "columns": 3,
          "children": [
            {
              "id": "piece-separator-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-separator-a",
                  "title": "Part A"
                },
                {
                  "id": "piece-separator-b",
                  "title": "Part B"
                },
                {
                  "id": "piece-separator-line",
                  "type": "separator",
                  "span": 2,
                  "style": "dotted",
                  "text": "a pause"
                },
                {
                  "id": "piece-separator-c",
                  "title": "Part C"
                },
                {
                  "id": "piece-separator-d",
                  "title": "Part D"
                }
              ]
            },
            {
              "id": "piece-separator-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-separator-yaml",
                  "kicker": "YAML",
                  "title": "type: separator",
                  "description": [
                    "span: 2",
                    "text: a pause"
                  ],
                  "detail": "<code>- { id: a-pause, type: separator, span: 2, style: dotted, text: a pause }</code><br>Its <code>span</code> is the section's columns, so the line crosses the whole section."
                },
                {
                  "id": "piece-separator-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Same thing, a pause",
                  "description": [
                    "A line inside one section: both sides are parts of it."
                  ]
                },
                {
                  "id": "piece-separator-neighbour",
                  "kicker": "NEIGHBOUR · SECTION",
                  "title": "Different things",
                  "description": [
                    "Choose sections when the two sides are different things."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-rail",
          "order": 6,
          "title": "rail",
          "subtitle": "“label this lane, this level”",
          "columns": 3,
          "children": [
            {
              "id": "piece-rail-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-rail-0",
                  "type": "rail",
                  "indent": 0,
                  "title": "Level one"
                },
                {
                  "id": "piece-rail-1",
                  "type": "rail",
                  "indent": 1,
                  "title": "Level two"
                },
                {
                  "id": "piece-rail-2",
                  "type": "rail",
                  "indent": 2,
                  "title": "Level three"
                }
              ]
            },
            {
              "id": "piece-rail-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-rail-yaml",
                  "kicker": "YAML",
                  "title": "type: rail",
                  "description": [
                    "title: Level two",
                    "indent: 1"
                  ],
                  "detail": "<code>- { id: level-1, type: rail, indent: 0, title: Level one }</code><br><code>- { id: level-2, type: rail, indent: 1, title: Level two }</code><br><code>- { id: level-3, type: rail, indent: 2, title: Level three }</code>"
                },
                {
                  "id": "piece-rail-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "A label",
                  "description": [
                    "A title-only label for a lane or one level of a tree."
                  ]
                },
                {
                  "id": "piece-rail-neighbour",
                  "kicker": "NEIGHBOUR · SECTION TITLE",
                  "title": "It heads a frame",
                  "description": [
                    "Choose a section title when the label heads a group."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-spacer",
          "order": 7,
          "title": "spacer",
          "subtitle": "“nothing goes here, on purpose”",
          "columns": 3,
          "children": [
            {
              "id": "piece-spacer-live",
              "span": 1,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-spacer-a",
                  "title": "One"
                },
                {
                  "id": "piece-spacer-hole",
                  "type": "spacer",
                  "span": 1
                },
                {
                  "id": "piece-spacer-c",
                  "title": "Three"
                }
              ]
            },
            {
              "id": "piece-spacer-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-spacer-yaml",
                  "kicker": "YAML",
                  "title": "type: spacer",
                  "description": [
                    "span: 1"
                  ],
                  "detail": "<code>- { id: one, title: One }</code><br><code>- { id: the-hole, type: spacer, span: 1 }</code><br><code>- { id: three, title: Three }</code>"
                },
                {
                  "id": "piece-spacer-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The gap is meant",
                  "description": [
                    "A declared hole: it closes the grid and draws nothing."
                  ]
                },
                {
                  "id": "piece-spacer-neighbour",
                  "kicker": "NEIGHBOUR · SPAN",
                  "title": "Close the gap",
                  "description": [
                    "Choose span when the gap was not meant: merge into it."
                  ]
                }
              ]
            }
          ]
        }
      ],
      "name": "Pieces · frame and leaves",
      "order": 7
    },
    {
      "id": "pieces-2-slots",
      "form": "dashboard",
      "columns": 1,
      "sections": [
        {
          "id": "pieces-2-lead",
          "order": 1,
          "span": 1,
          "lead": true,
          "kicker": "PIECES · 2 OF 4",
          "title": "Every slot has a character",
          "description": [
            "small, loud, short, unlimited: write each thing in the slot that fits it"
          ]
        },
        {
          "id": "piece-kicker",
          "order": 2,
          "title": "kicker",
          "subtitle": "“this is measured, that is estimated”",
          "columns": 3,
          "children": [
            {
              "id": "piece-kicker-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-kicker-box",
                  "kicker": "MEASURED",
                  "title": "42 ms"
                }
              ]
            },
            {
              "id": "piece-kicker-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-kicker-yaml",
                  "kicker": "YAML",
                  "title": "kicker: MEASURED",
                  "description": [
                    "title: 42 ms"
                  ],
                  "detail": "<code>- { id: latency, kicker: MEASURED, title: 42 ms }</code>"
                },
                {
                  "id": "piece-kicker-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The qualifier",
                  "description": [
                    "The small line above the title: a step, a code, a certainty."
                  ]
                },
                {
                  "id": "piece-kicker-neighbour",
                  "kicker": "NEIGHBOUR · OUTSIDE",
                  "title": "A whole page",
                  "description": [
                    "Choose the dashed frame when a page must split them at a glance."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-title",
          "order": 3,
          "title": "title",
          "subtitle": "the loud line",
          "columns": 3,
          "children": [
            {
              "id": "piece-title-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-title-box",
                  "title": "97%",
                  "description": [
                    "of the checks pass"
                  ]
                }
              ]
            },
            {
              "id": "piece-title-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-title-yaml",
                  "kicker": "YAML",
                  "title": "title: 97%",
                  "description": [
                    "description: [of the checks pass]"
                  ],
                  "detail": "<code>- { id: pass-rate, title: \"97%\", description: [of the checks pass] }</code>"
                },
                {
                  "id": "piece-title-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The loud line",
                  "description": [
                    "The number goes here when the number is the message."
                  ]
                },
                {
                  "id": "piece-title-neighbour",
                  "kicker": "NEIGHBOUR · ROWSPAN",
                  "title": "The proportion",
                  "description": [
                    "Choose height when the proportion is the message."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-description",
          "order": 4,
          "title": "description",
          "subtitle": "short, and it clamps",
          "columns": 3,
          "children": [
            {
              "id": "piece-description-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-description-box",
                  "title": "A card",
                  "description": [
                    "one short line of context"
                  ]
                }
              ]
            },
            {
              "id": "piece-description-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-description-yaml",
                  "kicker": "YAML",
                  "title": "description:",
                  "description": [
                    "[one short line of context]"
                  ],
                  "detail": "<code>- id: a-card</code><br><code>  title: A card</code><br><code>  description: [one short line of context]</code>"
                },
                {
                  "id": "piece-description-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The context",
                  "description": [
                    "A line or two under the title; it clamps, never grows."
                  ]
                },
                {
                  "id": "piece-description-neighbour",
                  "kicker": "NEIGHBOUR · DETAIL",
                  "title": "More to say",
                  "description": [
                    "Choose the detail when it takes more than two lines."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-detail",
          "order": 5,
          "title": "detail",
          "subtitle": "“there is too much to say”",
          "columns": 3,
          "children": [
            {
              "id": "piece-detail-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-detail-box",
                  "title": "Click this card",
                  "description": [
                    "the rest waits behind the click"
                  ],
                  "detail": "This is the detail. It has no limit, so the card never grows: what does not fit moves here instead of shrinking."
                }
              ]
            },
            {
              "id": "piece-detail-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-detail-yaml",
                  "kicker": "YAML",
                  "title": "detail: …",
                  "description": [
                    "the click panel, no limit"
                  ],
                  "detail": "<code>- id: click-this</code><br><code>  title: Click this card</code><br><code>  description: [the rest waits behind the click]</code><br><code>  detail: \"This is the detail. It has no limit, …\"</code>"
                },
                {
                  "id": "piece-detail-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The rest",
                  "description": [
                    "Unlimited text behind a click, so a cell never grows."
                  ]
                },
                {
                  "id": "piece-detail-neighbour",
                  "kicker": "NEIGHBOUR · SECTION",
                  "title": "Parts at once",
                  "description": [
                    "Choose a section of boxes when all parts must be seen."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-section-title",
          "order": 6,
          "title": "section title",
          "subtitle": "names one zone",
          "columns": 3,
          "children": [
            {
              "id": "piece-section-title-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-section-title-zone",
                  "title": "The checks",
                  "subtitle": "run before anyone looks",
                  "columns": 2,
                  "children": [
                    {
                      "id": "piece-section-title-a",
                      "title": "Build"
                    },
                    {
                      "id": "piece-section-title-b",
                      "title": "Model"
                    }
                  ]
                }
              ]
            },
            {
              "id": "piece-section-title-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-section-title-yaml",
                  "kicker": "YAML",
                  "title": "title: The checks",
                  "description": [
                    "subtitle: run before anyone looks"
                  ],
                  "detail": "<code>- id: the-checks</code><br><code>  title: The checks</code><br><code>  subtitle: run before anyone looks</code><br><code>  columns: 2</code><br><code>  children: [ … ]</code>"
                },
                {
                  "id": "piece-section-title-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "A zone's name",
                  "description": [
                    "It names one framed group, not the whole page."
                  ]
                },
                {
                  "id": "piece-section-title-neighbour",
                  "kicker": "NEIGHBOUR · LEAD BAND",
                  "title": "The page's claim",
                  "description": [
                    "Choose the lead band to say what the whole page says."
                  ]
                }
              ]
            }
          ]
        }
      ],
      "name": "Pieces · slots",
      "order": 8
    },
    {
      "id": "pieces-3-size-and-order",
      "form": "dashboard",
      "columns": 1,
      "sections": [
        {
          "id": "pieces-3-lead",
          "order": 1,
          "span": 1,
          "lead": true,
          "kicker": "PIECES · 3 OF 4",
          "title": "Width is reach, height is magnitude",
          "description": [
            "and order is the sequence that survives any screen"
          ]
        },
        {
          "id": "piece-span",
          "order": 2,
          "title": "span",
          "subtitle": "width says reach",
          "columns": 3,
          "children": [
            {
              "id": "piece-span-live",
              "span": 1,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-span-one",
                  "title": "One"
                },
                {
                  "id": "piece-span-wide",
                  "title": "Reaches two",
                  "span": 2
                },
                {
                  "id": "piece-span-all",
                  "title": "Reaches all three",
                  "span": 3
                }
              ]
            },
            {
              "id": "piece-span-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-span-yaml",
                  "kicker": "YAML",
                  "title": "span: 2",
                  "description": [
                    "in a section of columns: 3"
                  ],
                  "detail": "<code>columns: 3</code><br><code>children:</code><br><code>  - { id: one, title: One }</code><br><code>  - { id: wide, title: Reaches two, span: 2 }</code><br><code>  - { id: all, title: Reaches all three, span: 3 }</code>"
                },
                {
                  "id": "piece-span-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Reach",
                  "description": [
                    "A box across more columns covers more ground."
                  ]
                },
                {
                  "id": "piece-span-neighbour",
                  "kicker": "NEIGHBOUR · ROWSPAN",
                  "title": "An amount",
                  "description": [
                    "Choose height when the size is a magnitude."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-rowspan",
          "order": 3,
          "title": "rowspan",
          "subtitle": "“this one is bigger”",
          "columns": 3,
          "children": [
            {
              "id": "piece-rowspan-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-rowspan-tall",
                  "title": "Twice",
                  "rowspan": 2
                },
                {
                  "id": "piece-rowspan-floor",
                  "type": "spacer"
                },
                {
                  "id": "piece-rowspan-short",
                  "title": "Once"
                }
              ]
            },
            {
              "id": "piece-rowspan-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-rowspan-yaml",
                  "kicker": "YAML",
                  "title": "rowspan: 2",
                  "description": [
                    "a spacer over the short bar"
                  ],
                  "detail": "<code>columns: 2</code><br><code>children:</code><br><code>  - { id: twice, title: Twice, rowspan: 2 }</code><br><code>  - { id: floor, type: spacer }</code><br><code>  - { id: once, title: Once }</code>"
                },
                {
                  "id": "piece-rowspan-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Magnitude",
                  "description": [
                    "From one shared floor, a taller box is a bigger amount."
                  ]
                },
                {
                  "id": "piece-rowspan-neighbour",
                  "kicker": "NEIGHBOUR · TITLE",
                  "title": "The number",
                  "description": [
                    "Choose the title when the number is the message."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-band",
          "order": 4,
          "title": "band",
          "subtitle": "“this runs across everything”",
          "columns": 3,
          "children": [
            {
              "id": "piece-band-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-band-a",
                  "title": "A"
                },
                {
                  "id": "piece-band-b",
                  "title": "B"
                },
                {
                  "id": "piece-band-under",
                  "title": "Under everything",
                  "span": 2
                }
              ]
            },
            {
              "id": "piece-band-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-band-yaml",
                  "kicker": "YAML",
                  "title": "span: 2",
                  "description": [
                    "of a section's columns: 2"
                  ],
                  "detail": "<code>columns: 2</code><br><code>children:</code><br><code>  - { id: a, title: A }</code><br><code>  - { id: b, title: B }</code><br><code>  - { id: under, title: Under everything, span: 2 }</code>"
                },
                {
                  "id": "piece-band-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Under all of it",
                  "description": [
                    "It spans every column: a layer with content of its own."
                  ]
                },
                {
                  "id": "piece-band-neighbour",
                  "kicker": "NEIGHBOUR · CHIP",
                  "title": "No content of its own",
                  "description": [
                    "Choose a chip when what crosses only relates boxes."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-lead-band",
          "order": 5,
          "title": "lead band",
          "subtitle": "“this page says X”",
          "columns": 3,
          "children": [
            {
              "id": "piece-lead-band-live",
              "span": 1,
              "columns": 1,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-lead-band-pointer",
                  "kicker": "LIVE AT THE TOP",
                  "title": "This page's first band",
                  "description": [
                    "the full-width box above is one"
                  ]
                }
              ]
            },
            {
              "id": "piece-lead-band-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-lead-band-yaml",
                  "kicker": "YAML",
                  "title": "lead: true",
                  "description": [
                    "kicker: PIECES · 3 OF 4"
                  ],
                  "detail": "<code>- id: pieces-3-lead</code><br><code>  order: 1</code><br><code>  span: 1</code><br><code>  lead: true</code><br><code>  kicker: PIECES · 3 OF 4</code><br><code>  title: Width is reach, height is magnitude</code><br>It is the page's first root child and spans every root column."
                },
                {
                  "id": "piece-lead-band-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The claim first",
                  "description": [
                    "The page states its claim before its parts."
                  ]
                },
                {
                  "id": "piece-lead-band-neighbour",
                  "kicker": "NEIGHBOUR · SECTION TITLE",
                  "title": "One zone",
                  "description": [
                    "Choose a section title to name one zone, not the page."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-order",
          "order": 6,
          "title": "order",
          "subtitle": "“this goes in order”",
          "columns": 3,
          "children": [
            {
              "id": "piece-order-live",
              "span": 1,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-order-1",
                  "order": 1,
                  "kicker": "STEP 1 OF 3",
                  "title": "Draft"
                },
                {
                  "id": "piece-order-2",
                  "order": 2,
                  "kicker": "STEP 2 OF 3",
                  "title": "Check"
                },
                {
                  "id": "piece-order-3",
                  "order": 3,
                  "kicker": "STEP 3 OF 3",
                  "title": "Look"
                }
              ]
            },
            {
              "id": "piece-order-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-order-yaml",
                  "kicker": "YAML",
                  "title": "order: 2",
                  "description": [
                    "kicker: STEP 2 OF 3"
                  ],
                  "detail": "<code>- { id: draft, order: 1, kicker: STEP 1 OF 3, title: Draft }</code><br><code>- { id: check, order: 2, kicker: STEP 2 OF 3, title: Check }</code><br><code>- { id: look, order: 3, kicker: STEP 3 OF 3, title: Look }</code>"
                },
                {
                  "id": "piece-order-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The sequence",
                  "description": [
                    "Reading order, kept when the screen narrows."
                  ]
                },
                {
                  "id": "piece-order-neighbour",
                  "kicker": "NEIGHBOUR · CHIP",
                  "title": "It goes back",
                  "description": [
                    "Choose a chip's steps when the path jumps or returns."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-comparison",
          "order": 7,
          "title": "comparison",
          "subtitle": "one section per option",
          "columns": 3,
          "children": [
            {
              "id": "piece-comparison-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-comparison-a",
                  "title": "Option A",
                  "columns": 1,
                  "children": [
                    {
                      "id": "piece-comparison-a1",
                      "kicker": "COST",
                      "title": "Low"
                    },
                    {
                      "id": "piece-comparison-a2",
                      "kicker": "SPEED",
                      "title": "Slow"
                    }
                  ]
                },
                {
                  "id": "piece-comparison-b",
                  "title": "Option B",
                  "columns": 1,
                  "children": [
                    {
                      "id": "piece-comparison-b1",
                      "kicker": "COST",
                      "title": "High"
                    },
                    {
                      "id": "piece-comparison-b2",
                      "kicker": "SPEED",
                      "title": "Fast"
                    }
                  ]
                }
              ]
            },
            {
              "id": "piece-comparison-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-comparison-yaml",
                  "kicker": "YAML",
                  "title": "title: Option A",
                  "description": [
                    "one section per option"
                  ],
                  "detail": "<code>- id: option-a</code><br><code>  title: Option A</code><br><code>  columns: 1</code><br><code>  children:</code><br><code>    - { id: a-cost, kicker: COST, title: Low }</code><br><code>    - { id: a-speed, kicker: SPEED, title: Slow }</code><br>Option B is the same section with its own values."
                },
                {
                  "id": "piece-comparison-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Row against row",
                  "description": [
                    "Each option has its own parts, lined up to compare."
                  ]
                },
                {
                  "id": "piece-comparison-neighbour",
                  "kicker": "NEIGHBOUR · SECTION",
                  "title": "Pick one",
                  "description": [
                    "Choose one section of peer boxes when options have no parts."
                  ]
                }
              ]
            }
          ]
        }
      ],
      "name": "Pieces · size and order",
      "order": 9
    },
    {
      "id": "pieces-4-colour-and-relation",
      "form": "dashboard",
      "columns": 1,
      "filters": [
        {
          "key": "pair",
          "label": "Which boxes share an answer?",
          "steps": [
            "A chip is a question. Clicking it lights every box that shares the answer."
          ]
        }
      ],
      "sections": [
        {
          "id": "pieces-4-lead",
          "order": 1,
          "span": 1,
          "lead": true,
          "kicker": "PIECES · 4 OF 4",
          "title": "Colour means, treatment draws, a chip relates",
          "description": [
            "three dials and one relation, each saying one thing"
          ]
        },
        {
          "id": "piece-variant",
          "order": 2,
          "title": "variant",
          "subtitle": "colour carries a claim",
          "columns": 3,
          "children": [
            {
              "id": "piece-variant-live",
              "span": 1,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-variant-neutral",
                  "variant": "neutral",
                  "title": "neutral"
                },
                {
                  "id": "piece-variant-good",
                  "variant": "good",
                  "title": "good"
                },
                {
                  "id": "piece-variant-warn",
                  "variant": "warn",
                  "title": "warn"
                },
                {
                  "id": "piece-variant-bad",
                  "variant": "bad",
                  "title": "bad"
                },
                {
                  "id": "piece-variant-accent",
                  "variant": "accent",
                  "title": "accent"
                },
                {
                  "id": "piece-variant-muted",
                  "variant": "muted",
                  "title": "muted"
                }
              ]
            },
            {
              "id": "piece-variant-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-variant-yaml",
                  "kicker": "YAML",
                  "title": "variant: good",
                  "description": [
                    "one value per box"
                  ],
                  "detail": "<code>- { id: hardened, variant: good, title: good }</code><br>The six roles: <code>neutral</code>, <code>good</code>, <code>warn</code>, <code>bad</code>, <code>accent</code>, <code>muted</code>."
                },
                {
                  "id": "piece-variant-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The colour",
                  "description": [
                    "A colour carries one claim, and the page declares it."
                  ]
                },
                {
                  "id": "piece-variant-neighbour",
                  "kicker": "NEIGHBOUR · TREATMENT",
                  "title": "Drawn, not meant",
                  "description": [
                    "Choose a treatment to change the drawing, not the meaning."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-verdict-variant",
          "order": 3,
          "title": "verdict variant",
          "subtitle": "safe or dangerous",
          "columns": 3,
          "children": [
            {
              "id": "piece-verdict-variant-live",
              "span": 1,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-verdict-good",
                  "variant": "good",
                  "title": "Hardened"
                },
                {
                  "id": "piece-verdict-warn",
                  "variant": "warn",
                  "title": "Some risk"
                },
                {
                  "id": "piece-verdict-bad",
                  "variant": "bad",
                  "title": "High risk"
                }
              ]
            },
            {
              "id": "piece-verdict-variant-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-verdict-variant-yaml",
                  "kicker": "YAML",
                  "title": "variant: bad",
                  "description": [
                    "good · warn · bad"
                  ],
                  "detail": "<code>- { id: hardened, variant: good, title: Hardened }</code><br><code>- { id: some-risk, variant: warn, title: Some risk }</code><br><code>- { id: high-risk, variant: bad, title: High risk }</code>"
                },
                {
                  "id": "piece-verdict-variant-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "A verdict",
                  "description": [
                    "Green, amber and red say safe, careful, dangerous."
                  ]
                },
                {
                  "id": "piece-verdict-variant-neighbour",
                  "kicker": "NEIGHBOUR · HUE",
                  "title": "Only kinds",
                  "description": [
                    "Choose a hue when the colour only tells kinds apart."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-hue",
          "order": 4,
          "title": "hue",
          "subtitle": "“these are different kinds”",
          "columns": 3,
          "children": [
            {
              "id": "piece-hue-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-hue-blue",
                  "variant": "blue",
                  "title": "blue"
                },
                {
                  "id": "piece-hue-violet",
                  "variant": "violet",
                  "title": "violet"
                },
                {
                  "id": "piece-hue-gold",
                  "variant": "gold",
                  "title": "gold"
                },
                {
                  "id": "piece-hue-clay",
                  "variant": "clay",
                  "title": "clay"
                }
              ]
            },
            {
              "id": "piece-hue-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-hue-yaml",
                  "kicker": "YAML",
                  "title": "variant: blue",
                  "description": [
                    "blue · violet · gold · clay"
                  ],
                  "detail": "<code>- { id: kind-1, variant: blue, title: blue }</code><br><code>- { id: kind-2, variant: violet, title: violet }</code><br><code>- { id: kind-3, variant: gold, title: gold }</code><br><code>- { id: kind-4, variant: clay, title: clay }</code><br>A legend band after the lead band says what each hue means."
                },
                {
                  "id": "piece-hue-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Different kinds",
                  "description": [
                    "Up to four peers told apart, with no verdict."
                  ]
                },
                {
                  "id": "piece-hue-neighbour",
                  "kicker": "NEIGHBOUR · VERDICT VARIANT",
                  "title": "Safe or not",
                  "description": [
                    "Choose a verdict when the colour must say safe or not."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-treatment",
          "order": 5,
          "title": "treatment",
          "subtitle": "structure, never meaning",
          "columns": 3,
          "children": [
            {
              "id": "piece-treatment-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-treatment-centered",
                  "title": "Centred",
                  "treatment": [
                    "centered"
                  ]
                },
                {
                  "id": "piece-treatment-vertical",
                  "title": "Vertical",
                  "treatment": [
                    "vertical"
                  ]
                }
              ]
            },
            {
              "id": "piece-treatment-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-treatment-yaml",
                  "kicker": "YAML",
                  "title": "treatment: [centered]",
                  "description": [
                    "treatment: [vertical]"
                  ],
                  "detail": "<code>- { id: centred, title: Centred, treatment: [centered] }</code><br><code>- { id: vertical, title: Vertical, treatment: [vertical] }</code><br>The others: <code>outside</code> (dashed frame), <code>plain</code> (frameless wrapper, which holds every entry on these pages), <code>compact</code> (short-row staircase)."
                },
                {
                  "id": "piece-treatment-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "How it is drawn",
                  "description": [
                    "It changes the drawing, never what the piece means."
                  ]
                },
                {
                  "id": "piece-treatment-neighbour",
                  "kicker": "NEIGHBOUR · VARIANT",
                  "title": "It must mean",
                  "description": [
                    "Choose a variant when the look must carry a meaning."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-outside",
          "order": 6,
          "title": "outside",
          "subtitle": "“this is outside the scope”",
          "columns": 3,
          "children": [
            {
              "id": "piece-outside-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-outside-in",
                  "title": "In scope"
                },
                {
                  "id": "piece-outside-out",
                  "title": "Outside",
                  "treatment": [
                    "outside"
                  ]
                }
              ]
            },
            {
              "id": "piece-outside-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-outside-yaml",
                  "kicker": "YAML",
                  "title": "treatment: [outside]",
                  "description": [
                    "dashed frame, no colour"
                  ],
                  "detail": "<code>- { id: in-scope, title: In scope }</code><br><code>- { id: outside, title: Outside, treatment: [outside] }</code>"
                },
                {
                  "id": "piece-outside-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "Out of scope",
                  "description": [
                    "A dashed frame with no colour, so colour stays free."
                  ]
                },
                {
                  "id": "piece-outside-neighbour",
                  "kicker": "NEIGHBOUR · SECTION",
                  "title": "Several, with parts",
                  "description": [
                    "Choose a section when the outside things have parts."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-palette",
          "order": 7,
          "title": "palette",
          "subtitle": "the skin of the whole deck",
          "columns": 3,
          "children": [
            {
              "id": "piece-palette-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-palette-good",
                  "variant": "good",
                  "title": "good"
                },
                {
                  "id": "piece-palette-bad",
                  "variant": "bad",
                  "title": "bad"
                }
              ]
            },
            {
              "id": "piece-palette-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-palette-yaml",
                  "kicker": "YAML",
                  "title": "look: brand",
                  "description": [
                    "picks the rose-pine palette"
                  ],
                  "detail": "<code>look: brand</code> in <code>data/document.yaml</code> picks the <code>rose-pine</code> palette, and these two boxes are painted by it, like everything on this page. A person usually reaches a palette through that one-line look (<code>projector</code>, <code>report</code>, <code>brand</code>), which picks the palette and the text sizes together; how to choose it is in the skill's <code>toolbox.md</code>, under Look. Without a look, <code>palette:</code> names one of <code>neutral</code>, <code>rose-pine</code>, <code>rose-pine-moon</code>, <code>contrast</code>; a document carries a look or a palette, never both."
                },
                {
                  "id": "piece-palette-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "The look",
                  "description": [
                    "It changes how the deck looks, never what it means."
                  ]
                },
                {
                  "id": "piece-palette-neighbour",
                  "kicker": "NEIGHBOUR · VARIANT",
                  "title": "One box",
                  "description": [
                    "Choose a variant when one box, not the deck, changes."
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "piece-chip",
          "order": 8,
          "title": "chip",
          "subtitle": "a relation, lit on click",
          "columns": 3,
          "children": [
            {
              "id": "piece-chip-live",
              "span": 1,
              "columns": 2,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-chip-a",
                  "title": "Shares it",
                  "filters": [
                    "pair"
                  ]
                },
                {
                  "id": "piece-chip-b",
                  "title": "Shares it too",
                  "filters": [
                    "pair"
                  ]
                }
              ]
            },
            {
              "id": "piece-chip-read",
              "span": 2,
              "columns": 3,
              "treatment": [
                "plain"
              ],
              "children": [
                {
                  "id": "piece-chip-yaml",
                  "kicker": "YAML",
                  "title": "filters: [pair]",
                  "description": [
                    "on each box, and the key on the page"
                  ],
                  "detail": "<code>filters:</code><br><code>  - key: pair</code><br><code>    label: Which boxes share an answer?</code><br>and on each member: <code>filters: [pair]</code>. Click the chip at the top of this page."
                },
                {
                  "id": "piece-chip-says",
                  "kicker": "WHAT IT SAYS",
                  "title": "A question",
                  "description": [
                    "Clicking it lights every box that shares the answer."
                  ]
                },
                {
                  "id": "piece-chip-neighbour",
                  "kicker": "NEIGHBOUR · BAND",
                  "title": "It has content",
                  "description": [
                    "Choose a band when what crosses has content of its own."
                  ]
                }
              ]
            }
          ]
        }
      ],
      "name": "Pieces · colour and relation",
      "order": 10
    },
    {
      "id": "p1-merged-cell",
      "form": "dashboard",
      "columns": 2,
      "sections": [
        {
          "id": "p1-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 1 OF 7",
          "title": "Two numbers fill width and height",
          "description": [
            "span says how far a box reaches; rowspan says how big it is"
          ],
          "detail": "The data step: you never place a box, you write its values and the look follows. <code>span</code> is a count of columns, so it fills the box's width, and width reads as reach. <code>rowspan</code> is a count of rows, so it fills its height, and height reads as magnitude. Change a number and the page recalculates."
        },
        {
          "id": "p1-cell",
          "title": "What one box takes",
          "subtitle": "each box you write takes one slot; a number you add makes it bigger",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p1-slot",
              "order": 1,
              "kicker": "YOU WRITE",
              "title": "One box",
              "description": [
                "one entry in the list",
                "one slot on the page"
              ],
              "detail": "Every box you write takes exactly one slot: a fixed height and an equal share of its group's width. You never write a size in pixels. To make a box bigger you write a number, <code>span</code> sideways or <code>rowspan</code> downward, and the look follows.",
              "variant": "neutral"
            },
            {
              "id": "p1-equal",
              "order": 2,
              "kicker": "YOU GET",
              "title": "Equal neighbours",
              "description": [
                "boxes in one group share",
                "one width, whatever they say"
              ],
              "detail": "Boxes in the same group get the same width, so the reader compares them as peers. Writing more words never makes a box wider: what does not fit moves to the detail, to a wider span, or into a group of its own.",
              "variant": "neutral"
            },
            {
              "id": "p1-clamp",
              "order": 3,
              "kicker": "YOU WRITE",
              "title": "Short text",
              "description": [
                "a title of two lines at most",
                "and a description of three"
              ],
              "detail": "The title shows at most two lines and the description three, so every box keeps one slot's height whatever you write. This paragraph is the <code>detail</code>: it has no limit and opens on click, so the longer thing you have to say goes here.",
              "variant": "neutral"
            },
            {
              "id": "p1-tracks",
              "order": 4,
              "kicker": "YOU WRITE",
              "title": "columns: 2",
              "description": [
                "this group asks for two columns",
                "so four boxes fill two rows"
              ],
              "detail": "<code>columns: 2</code> on a group is a value you write, and it decides how many boxes sit side by side. Four boxes in two columns close a 2×2 rectangle; <code>npm run model</code> checks that arithmetic on the data, with no browser.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p1-partial",
          "title": "A wider box",
          "subtitle": "span: 2 of 3 — the value that widens it, and the boxes that make room",
          "variant": "neutral",
          "order": 2,
          "span": 1,
          "columns": 3,
          "children": [
            {
              "id": "p1-t1",
              "order": 1,
              "kicker": "COLUMN",
              "title": "Column 1",
              "description": [
                "one box, one column"
              ],
              "detail": "Three plain boxes say this group really has three columns. Without them the group would shrink to what its content fills, and the wide box below would take the whole row instead of two thirds of it.",
              "variant": "neutral"
            },
            {
              "id": "p1-t2",
              "order": 2,
              "kicker": "COLUMN",
              "title": "Column 2",
              "description": [
                "the second of three"
              ],
              "detail": "A wide box takes columns that already exist, so something beside it has to create them: that is what this row of boxes is for.",
              "variant": "neutral"
            },
            {
              "id": "p1-t3",
              "order": 3,
              "kicker": "COLUMN",
              "title": "Column 3",
              "description": [
                "the third of three"
              ],
              "detail": "With three plain boxes present, the group keeps its three columns, and the value written below reads as two of them.",
              "variant": "neutral"
            },
            {
              "id": "p1-merge",
              "order": 4,
              "span": 2,
              "kicker": "YOU WRITE",
              "title": "span: 2 of 3",
              "description": [
                "it reaches two of three columns",
                "and stays one row tall"
              ],
              "detail": "Write <code>span: 2</code> in a three-column group and the box takes exactly two of the three columns, keeping that share as the page narrows. Write <code>span: 3</code> and it becomes a full-width band on its own row. Width reads as REACH: this box covers two thirds of the row's scope, not two thirds more importance.",
              "variant": "accent"
            },
            {
              "id": "p1-close",
              "order": 5,
              "kicker": "YOU WRITE",
              "title": "The third column",
              "description": [
                "one column stayed open",
                "this box fills it"
              ],
              "detail": "A wide box that leaves the rest of its row empty leaves a hole, and a hole says something. Here one more box fills the remaining column on purpose, so the group is a full rectangle: 3 columns × 2 rows = 6 slots = 1+1+1+2+1.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p1-band",
          "title": "A full-width band",
          "subtitle": "span equal to columns: the box stops sharing and takes the whole row",
          "variant": "neutral",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p1-band-what",
              "order": 1,
              "kicker": "YOU WRITE",
              "title": "span == columns",
              "description": [
                "this group writes span: 2 on",
                "a page of 2 columns"
              ],
              "detail": "A band is the same <code>span</code> value written up to the parent's full column count. The group you are reading is one: <code>span: 2</code> on a page whose root has <code>columns: 2</code>.",
              "variant": "neutral"
            },
            {
              "id": "p1-band-row",
              "order": 2,
              "kicker": "YOU GET",
              "title": "It owns the row",
              "description": [
                "nothing can sit beside it",
                "two bands stack one on the other"
              ],
              "detail": "A full-width band leaves no room beside it, so it takes the first row where the whole width is free. Two bands written one after the other therefore stack top to bottom, which is how this page's last two groups sit.",
              "variant": "neutral"
            },
            {
              "id": "p1-band-base",
              "order": 3,
              "kicker": "YOU GET",
              "title": "A base layer",
              "description": [
                "written last, it reads as the",
                "floor the page rests on"
              ],
              "detail": "Where you write it is part of what it says: a band written last shows as a full-width base under everything above it, which is where a foundation, a shared platform or a timeline belongs. It stays full width on every screen.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p1-ladder",
          "title": "Height is magnitude",
          "subtitle": "rowspan 1·2·3·4 on a shared floor, the colour rising with it — one claim, two values",
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
                "four slots, the tallest bar",
                "bad, the top of the scale"
              ],
              "detail": "Write <code>rowspan: 4</code> and the box is four slots tall; write <code>variant: bad</code> and it is red. Both values say the same thing: this is the largest. It is written FIRST because it is the only bar that reaches the top row; the three spacers before it hold that row open.",
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
                "warn, the amber step"
              ],
              "detail": "Height and colour are two values written on one box to carry one claim. Read the bars by height or by colour and you get the same ranking, so the claim is legible twice.",
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
                "the colour steps up with it"
              ],
              "detail": "<code>rowspan: 2</code> makes the box two slots tall, the gap between them included. Its column does not change: the value adds height and nothing else.",
              "variant": "good"
            },
            {
              "id": "p1-bar-1",
              "order": 10,
              "rowspan": 1,
              "kicker": "1 ROW",
              "title": "rowspan: 1",
              "description": [
                "the base slot, one row",
                "the zero of both scales"
              ],
              "detail": "<code>rowspan: 1</code> is what you get when you write nothing: one slot. It is the smallest height and the quietest colour (<code>neutral</code>). Written LAST, it lands in the bottom row, where every taller bar also ends, and that shared floor is what makes the four heights comparable.",
              "variant": "neutral"
            }
          ]
        }
      ],
      "name": "Data · width and height",
      "order": 11
    },
    {
      "id": "p4-slots",
      "form": "planner",
      "columns": 2,
      "sections": [
        {
          "id": "p4-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 2 OF 7",
          "title": "The text you write fills four slots",
          "description": [
            "put each thing you say in the slot whose character fits it"
          ],
          "detail": "A box's look is filled by its text: a short qualifier goes in the kicker, the loud message in the title, a line or two in the description, and everything else in the detail behind a click. The same words in the wrong slot make the layout fight you."
        },
        {
          "id": "p4-fields",
          "title": "Four slots, four characters",
          "subtitle": "what you write decides what it says; the slot decides how loud it is",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p4-f-kicker",
              "order": 1,
              "kicker": "YOU WRITE",
              "title": "kicker",
              "description": [
                "one short line",
                "shown small, above"
              ],
              "detail": "Write a short qualifier here: a step, a code, a certainty, a kind. The box shows it as one small uppercase line above the title, the quietest text on the card, so it qualifies the title without competing with it.",
              "variant": "neutral"
            },
            {
              "id": "p4-f-title",
              "order": 2,
              "kicker": "YOU WRITE",
              "title": "title",
              "description": [
                "the message, loud",
                "at most two lines"
              ],
              "detail": "Write what the box is ABOUT: the box shows it bold and loud, in at most two lines. When a number is the message, the number goes here.",
              "variant": "neutral"
            },
            {
              "id": "p4-f-desc",
              "order": 3,
              "kicker": "YOU WRITE",
              "title": "description",
              "description": [
                "a list of short lines",
                "three lines at most"
              ],
              "detail": "Write a string, or a list where each item is one line. The box shows at most three lines, and a long line costs two of them, so keep each line short. Anything that does not fit belongs in the <code>detail</code>.",
              "variant": "neutral"
            },
            {
              "id": "p4-f-detail",
              "order": 4,
              "kicker": "YOU WRITE",
              "title": "detail",
              "description": [
                "as long as you need",
                "it opens on a click"
              ],
              "detail": "Write everything else here: it has no limit and opens in the panel when the reader clicks the box. This paragraph is one. When a box has more to say than three short lines, the answer is never a taller box: it is this field.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p4-payloads",
          "title": "One kicker, four payloads",
          "subtitle": "a count, a step, a phase, a kind — the slot shows whatever you write",
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
                "you write a count",
                "the kicker shows it"
              ],
              "detail": "The kicker holds a quantity here: a count of replicas, of owners, of open items. It shows exactly the text you wrote; it does not sort, scale or compare it.",
              "variant": "neutral"
            },
            {
              "id": "p4-p-step",
              "order": 2,
              "kicker": "STEP 2",
              "title": "A step",
              "description": [
                "you write a position",
                "the same slot shows it"
              ],
              "detail": "Here the same slot carries a step. On a path this is what makes the order readable at a glance, together with the order you told the story in.",
              "variant": "neutral"
            },
            {
              "id": "p4-p-phase",
              "order": 3,
              "kicker": "PHASE II",
              "title": "A phase",
              "description": [
                "you write a span of time",
                "still one small line"
              ],
              "detail": "A phase label. Read with the two boxes before it, the point is that the slot has no preferred meaning: a deck that means phases and a deck that means steps write into the same slot.",
              "variant": "neutral"
            },
            {
              "id": "p4-p-class",
              "order": 4,
              "kicker": "STORE",
              "title": "A kind",
              "description": [
                "you write what kind",
                "of thing this box is"
              ],
              "detail": "A kind, not a state. In an architecture deck the kicker says <em>database</em>, <em>queue</em> or <em>gateway</em> far more often than it says <em>healthy</em>, and the slot shows whichever you write.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p4-less",
          "title": "Saying less, on purpose",
          "subtitle": "a field you leave empty is data too — the box stays exactly one slot tall",
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
              "detail": "No kicker, no description: you wrote one line and the box shows one line. <code>treatment: [centered]</code> centres the text, which is what a box with a single short claim usually wants. Leaving fields empty costs nothing: the box keeps its slot.",
              "variant": "neutral"
            },
            {
              "id": "p4-l-kicker",
              "order": 2,
              "kicker": "KICKER ONLY",
              "detail": "You wrote a <code>kicker</code> and nothing else, so the box reads as a pure label. It is the smallest box that still says something, and a fair way to mark a place without claiming anything about it.",
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
              "detail": "Write <code>treatment: [half]</code> on two boxes in a row and they share one slot, one on top of the other. Halves come in pairs: one half alone would leave half a slot empty, so the build refuses it.",
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
              "detail": "The bottom half of the same slot. A half box has room for a title only, so the build refuses a <code>description</code> on it; the longer text goes here, in the <code>detail</code>.",
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
              "note": "⚠ <code>note</code> shows ONLY inside the panel, and nothing on the box hints that it exists — so a warning here is invisible until someone clicks.",
              "detail": "The full box, for comparison with its three neighbours: the same slot, carrying every text field there is. The <code>note</code> is the last of them and the only one in this seed; it shows in the panel in warn colour, below this text. Judge it here: a warning nobody can see from the page may be the wrong place for a warning.",
              "variant": "neutral"
            }
          ]
        }
      ],
      "name": "Data · text in its slot",
      "order": 12
    },
    {
      "id": "p5-channels",
      "form": "mindmap",
      "columns": 2,
      "sections": [
        {
          "id": "p5-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 3 OF 7",
          "title": "One value per channel, one claim per value",
          "description": [
            "variant fills the colour, treatment fills the frame; each says one thing"
          ],
          "detail": "Colour and frame are filled by values you write on a box: a <code>variant</code> fills its colour, a <code>treatment</code> fills its frame. Each channel carries one claim and the page declares it, so the value you write is the meaning the reader sees."
        },
        {
          "id": "p5-core",
          "title": "Five channels, five values",
          "subtitle": "each is filled by one value you write — changing one never changes another",
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
                "you write order"
              ],
              "detail": "The strongest channel: a box that comes first reads as first, a band that comes last reads as the floor. You never write a coordinate; you write <code>order</code>, and the position follows.",
              "variant": "neutral"
            },
            {
              "id": "p5-c-size",
              "order": 2,
              "kicker": "CHANNEL",
              "title": "Size",
              "description": [
                "how much space",
                "you write span, rowspan"
              ],
              "detail": "Two values, two meanings: <code>span</code> fills the width, which reads as REACH, and <code>rowspan</code> fills the height, which reads as MAGNITUDE. Both count whole slots, so size moves in steps.",
              "variant": "neutral"
            },
            {
              "id": "p5-c-colour",
              "order": 3,
              "kicker": "CHANNEL",
              "title": "Colour",
              "description": [
                "the variant role",
                "you write one value"
              ],
              "detail": "One role from a closed list (<code>neutral</code>, <code>good</code>, <code>warn</code>, <code>bad</code>, <code>accent</code>, <code>muted</code>). Readers notice colour first, which is exactly why the page has to say what each value means.",
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
              "detail": "Write <code>treatment: [outside]</code> and the frame turns dashed, and nothing else changes: no fill, no colour. That makes it the cleanest proof that a claim does not need colour to be seen.",
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
              "detail": "The word you write above the title. It is a WORD, so it is read exactly; it is small, so it is read last. That makes it the natural partner for colour, either agreeing with it or dividing the work with it.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p5-double",
          "title": "Two values, one claim",
          "subtitle": "kicker and colour written to agree — read either, get the same answer",
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
                "one claim, two values",
                "nothing new is added"
              ],
              "detail": "Writing the same claim into two channels adds no information; it adds ROBUSTNESS. A reader who skims colour and a reader who reads words reach the same answer, and a projector that flattens the palette does not erase the claim.",
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
              "detail": "You wrote <em>AT RISK</em> as the kicker and <code>bad</code> as the variant. Two values, one claim, and because they agree, neither is free to say anything else about this box.",
              "variant": "bad"
            },
            {
              "id": "p5-d-good",
              "order": 3,
              "kicker": "HARDENED",
              "title": "The other end",
              "description": [
                "same pair, other values",
                "the scale reads twice"
              ],
              "detail": "The opposite end of the same scale: kicker <em>HARDENED</em>, variant <code>good</code>. Writing a claim twice is only worth it when the scale has ends worth telling apart at a glance.",
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
              "detail": "Both channels are now committed to one claim. If a second claim shows up later (a kind, a phase, an owner) it needs a channel you have not written yet, or the page gives up the reinforcement.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p5-split",
          "title": "Two values, two claims",
          "subtitle": "the colour says how it is, the dashed border says where it lives",
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
                "two values, two claims",
                "one box carries both"
              ],
              "detail": "The opposite trade: each value keeps its own claim, so one box says a state AND a location at once. It only works if the reader is told which channel says which; an undeclared split reads as noise.",
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
              "detail": "This box has <code>variant: warn</code> AND <code>treatment: [outside]</code>. The colour says a state (weak); the dashed frame says a location (outside the perimeter: a third-party service, an unmanaged dependency). Two values, two claims, one box.",
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
              "detail": "The control case: the same variant, no <code>outside</code>. The two boxes share the colour value and differ in the border value alone, which proves the two values are independent.",
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
              "detail": "A split doubles what the reader keeps in mind, and it fails silently: a page that never says what its dashed frames mean has simply drawn two kinds of box. That is why the band below exists, and why it is text rather than a row of swatches.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p5-legend",
          "title": "What colour means here",
          "subtitle": "a value means nothing until the page says so — so this page says so",
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
              "detail": "On THIS page <code>good</code> is the upper end of the example scale in the left branch, and nothing more. On another page the same value can mean hardened, done or approved: the palette keeps the value stable, never its reading.",
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
              "detail": "<code>bad</code> here is the lower end of that same scale. It says nothing about the deck or about any box outside that branch.",
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
              "detail": "<code>warn</code> is kept on this page for the two boxes in the right branch, where colour is one of two values in play. Keeping a value for one purpose is itself a declaration: the reader can rule the rest of the page out.",
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
              "detail": "<code>muted</code> marks the two <em>what it costs</em> boxes: commentary about the operation, not part of it. Without this line a reader could take the grey as <em>deprecated</em> or <em>inactive</em>, which is the ambiguity a declaration removes.",
              "variant": "muted"
            }
          ]
        }
      ],
      "name": "Data · colour and frame",
      "order": 13
    },
    {
      "id": "p11-colour-and-rails",
      "form": "dashboard",
      "columns": 2,
      "filters": [
        {
          "key": "gate",
          "label": "Which boxes are the gates?",
          "steps": [
            "A core chip: declared once in document.yaml, inherited first by every page that does not omit it.",
            "It lights the two gates wherever they appear, with the same label on every page."
          ]
        },
        {
          "key": "tree",
          "label": "Which rails draw the tree?",
          "steps": [
            "Four rails, one per level of the deck, each inset one <code>indent</code> step deeper than its parent, each in its own hue.",
            "The indent value moves the drawn frame, not the cell, so the tree reads by depth while every rail keeps its whole cell."
          ]
        }
      ],
      "sections": [
        {
          "id": "p11-lead",
          "order": 1,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 4 OF 7",
          "title": "A value you write becomes a colour",
          "description": [
            "write the same hue on the same actor, and every page paints it the same"
          ],
          "detail": "This is the data step: the look is not drawn, it is filled. A hue on a box, an indent on a rail and <code>copy: true</code> on a command are values in the YAML, and each value decides how its box looks. This first box is the <b>lead band</b> (<code>lead: true</code>, first in <code>order</code>, spanning every root column): its title is the page's claim and its kicker places the page in the tour."
        },
        {
          "id": "p11-legend",
          "title": "The legend band",
          "subtitle": "four hues, four actors, the same on every page",
          "order": 2,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "p11-blue",
              "order": 1,
              "variant": "blue",
              "kicker": "BLUE",
              "title": "The writer",
              "description": [
                "writes the first draft"
              ],
              "detail": "The four categorical hues carry no risk or state, so they are free to name peers. Here each one names an ACTOR of a publishing story: write <code>variant: blue</code> on the writer's box on every page, and the reader recognises the writer before reading a word."
            },
            {
              "id": "p11-violet",
              "order": 2,
              "variant": "violet",
              "kicker": "VIOLET",
              "title": "The editor",
              "description": [
                "cuts what is not needed"
              ],
              "detail": "The value is the whole instruction: <code>variant: violet</code> and the box takes the deck's violet. Change the palette and every violet box changes with it, together, so the editor still reads as one actor."
            },
            {
              "id": "p11-gold",
              "order": 3,
              "variant": "gold",
              "kicker": "GOLD",
              "title": "The reviewer",
              "description": [
                "a gate: approves or returns"
              ],
              "detail": "One hue, one actor. If gold meant the reviewer here and the budget on the next page, the colour would stop naming anything. The reviewer is a gate, so the deck's core chip lights this box.",
              "filters": [
                "gate"
              ]
            },
            {
              "id": "p11-clay",
              "order": 4,
              "variant": "clay",
              "kicker": "CLAY",
              "title": "The publisher",
              "description": [
                "a gate: lets it go out"
              ],
              "detail": "A hue is data, not paint: the same value on the same actor is what keeps this legend true on every page. The publisher is the second gate the deck's core chip lights.",
              "filters": [
                "gate"
              ]
            }
          ]
        },
        {
          "id": "p11-tree",
          "title": "Rails as a tree",
          "subtitle": "one indent step per level",
          "order": 3,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "p11-r0",
              "order": 1,
              "type": "rail",
              "variant": "blue",
              "indent": 0,
              "title": "document",
              "filters": [
                "tree"
              ]
            },
            {
              "id": "p11-r1",
              "order": 2,
              "type": "rail",
              "variant": "violet",
              "indent": 1,
              "title": "page",
              "filters": [
                "tree"
              ]
            },
            {
              "id": "p11-r2",
              "order": 3,
              "type": "rail",
              "variant": "gold",
              "indent": 2,
              "title": "section",
              "filters": [
                "tree"
              ]
            },
            {
              "id": "p11-r3",
              "order": 4,
              "type": "rail",
              "variant": "clay",
              "indent": 3,
              "title": "component",
              "filters": [
                "tree"
              ]
            }
          ]
        },
        {
          "id": "p11-copy",
          "title": "Boxes you paste from",
          "subtitle": "centred beside a taller neighbour",
          "order": 4,
          "span": 1,
          "columns": 2,
          "treatment": [
            "middle"
          ],
          "children": [
            {
              "id": "p11-cmd-build",
              "order": 1,
              "kicker": "COPY · BUILD",
              "title": "npm run build",
              "copy": true,
              "description": [
                "the corner button copies it"
              ],
              "detail": "<code>copy: true</code> puts a button on the box that copies its title; a string copies that string instead. Use it when the title IS the thing the reader pastes: a command, a path, an identifier."
            },
            {
              "id": "p11-cmd-gate",
              "order": 2,
              "kicker": "COPY · MODEL",
              "title": "npm run model",
              "copy": true,
              "description": [
                "the layout, as arithmetic"
              ],
              "detail": "The section carries <code>treatment: [middle]</code>: its compound row stretches it to the tree's height, and <code>middle</code> centres its grid in that height instead of leaving the gap below.",
              "filters": [
                "gate"
              ]
            }
          ]
        }
      ],
      "name": "Data · hues and rails",
      "order": 14
    },
    {
      "id": "p8-does-not-fit",
      "form": "dashboard",
      "columns": 2,
      "sections": [
        {
          "id": "p8-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 5 OF 7",
          "title": "When the text is too long, it moves",
          "description": [
            "a box never grows: the extra words go to the detail, a merge or a group"
          ],
          "detail": "Data fills a box of fixed size. When what you write does not fit, the answer is in the data, never in the box: move the rest into the <code>detail</code>, give the box a wider <code>span</code>, or split it into a group of boxes."
        },
        {
          "id": "p8-never",
          "title": "The box never grows",
          "subtitle": "whatever you write, the box keeps its size — the text is cut, not fitted",
          "variant": "neutral",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "p8-n-fixed",
              "order": 1,
              "kicker": "FIXED",
              "title": "One slot, always",
              "description": [
                "more words you write",
                "never make it taller"
              ],
              "detail": "Every box is one slot tall whatever you write in it. A longer description does not buy a taller box; it buys a hidden remainder the reader never sees.",
              "variant": "neutral"
            },
            {
              "id": "p8-n-clamp",
              "order": 2,
              "kicker": "CLAMP",
              "title": "Cut, not fitted",
              "description": [
                "title 2 lines, body 3",
                "the rest is not shown"
              ],
              "detail": "The title shows 2 lines and the description 3, and a long line costs two of them. That keeps the page even, and it is also why an overlong text does not look broken: it looks FINISHED, one sentence short of its point.",
              "variant": "neutral"
            },
            {
              "id": "p8-n-equal",
              "order": 3,
              "kicker": "EQUAL",
              "title": "Width is the group's",
              "description": [
                "boxes share one width",
                "set by the group, not text"
              ],
              "detail": "A box's width is an equal share of its group, the same for every box in it. Nothing you write in a box widens it, which makes the group's <code>columns</code> value the real lever: fewer columns, wider boxes.",
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
              "detail": "There is deliberately no value for a smaller font, a taller box or a fourth description line. If there were, every crowded page would reach for it and the evenness that makes the whole page readable at a glance would be spent one box at a time.",
              "variant": "bad"
            }
          ]
        },
        {
          "id": "p8-moves",
          "title": "Four places it moves to",
          "subtitle": "each move changes what you write — none of them resizes the box",
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
                "the field with no limit",
                "behind a click"
              ],
              "detail": "The first and best answer: the <code>detail</code> has no limit and opens in the panel. This paragraph is one. Most \"it does not fit\" problems are a description carrying a paragraph that belonged here from the start.",
              "variant": "neutral"
            },
            {
              "id": "p8-m-merge",
              "order": 2,
              "span": 2,
              "kicker": "MOVE 2",
              "title": "Into a wider box: this one",
              "description": [
                "you write span: 2, so the",
                "line has room to be read"
              ],
              "detail": "This box IS the move it names: you write <code>span: 2</code> in a three-column group and the box reaches across two columns. Reach costs the row something: the wide box takes columns its neighbours created, and the three boxes below close the rectangle it left open.",
              "variant": "accent"
            },
            {
              "id": "p8-m-nest",
              "order": 3,
              "kicker": "MOVE 3",
              "title": "One level down",
              "description": [
                "a group with fewer",
                "columns, so wider boxes"
              ],
              "detail": "Put the crowded boxes in a group of their own with fewer <code>columns</code>, and every box in it gets wider. This is the move for a whole region that reads too tight.",
              "variant": "neutral"
            },
            {
              "id": "p8-m-extra",
              "order": 4,
              "kicker": "MOVE 4 · RETIRED",
              "title": "A second claim",
              "description": [
                "one colour value,",
                "the other in words"
              ],
              "detail": "A thing that is a KIND and a STATE at once keeps ONE <code>variant</code>, the claim the colour is for, and writes the other in the kicker. This box has <code>variant: bad</code> and its second claim, RETIRED, in the kicker. Splitting it into two boxes to carry the second claim would halve both widths: the squeeze again, dressed as a fix.",
              "variant": "bad"
            },
            {
              "id": "p8-m-cost",
              "order": 5,
              "kicker": "COST",
              "title": "What it costs",
              "description": [
                "a click, a column,",
                "a level, a word"
              ],
              "detail": "The detail hides the text behind a click; the wide box spends columns a neighbour needed; a nested group adds a level the reader must descend; the kicker spends the one small word the box had. Four prices, and all four are cheaper than a box nobody can read.",
              "variant": "muted"
            }
          ]
        },
        {
          "id": "p8-illegible",
          "title": "A box nobody can read is a defect",
          "subtitle": "even when the arithmetic closes — so the data has to stay short",
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
              "detail": "<code>npm run model</code> proves <code>Σ(spanCols × rowspanRows) == tracks × rows</code> without a browser, and a group of eight unreadable 60px boxes satisfies it perfectly. Closing is a claim about the FILL, never about whether anything in it can be read.",
              "variant": "neutral"
            },
            {
              "id": "p8-i-floor",
              "order": 2,
              "kicker": "FLOOR",
              "title": "Too many columns",
              "description": [
                "a box has a minimum width;",
                "past it, the group stacks"
              ],
              "detail": "Every box has a minimum readable width. When the <code>columns</code> you wrote would make boxes narrower than that, the group drops columns and stacks them instead. If your page stacks sooner than you wanted, you asked for too many columns.",
              "variant": "neutral"
            },
            {
              "id": "p8-i-word",
              "order": 3,
              "kicker": "LONG WORD",
              "title": "The longest word",
              "description": [
                "a title must not break",
                "in the middle of a word"
              ],
              "detail": "A box must be at least as wide as the longest word in its title, or the title breaks mid-word. The fix is in what you write: a shorter word, a wider <code>span</code>, or fewer columns in the group.",
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
              "detail": "Write <code>treatment: [vertical]</code> and the title turns on its side, reading like a rail. It is the one box the long-word rule exempts, because a rotated title is limited by the box's HEIGHT, not its width. A vertical box holds a title only: its side has no room for a description.",
              "variant": "neutral"
            }
          ]
        }
      ],
      "name": "Data · too much to say",
      "order": 15
    },
    {
      "id": "p9-the-hole",
      "form": "timeline",
      "columns": 2,
      "sections": [
        {
          "id": "p9-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 6 OF 7",
          "title": "An empty place is written too",
          "description": [
            "a gap you mean is a spacer in the data; a gap you did not mean is a defect"
          ],
          "detail": "Every place on the page is filled by something you wrote. When a place must stay empty, write that as data: a <code>spacer</code> fills it and says the gap is meant. When the gap was not meant, change the data around it until it closes."
        },
        {
          "id": "p9-speaks",
          "title": "An empty place says something",
          "subtitle": "it says nothing belongs here — and the reader cannot tell that from a mistake",
          "variant": "neutral",
          "order": 1,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "p9-s-claim",
              "order": 1,
              "kicker": "CLAIM",
              "title": "A gap is a statement",
              "description": [
                "the gap says nothing",
                "belongs in this place"
              ],
              "detail": "Every other place on the page carries something you wrote, so an empty one is read as a claim too: <em>nothing goes here, on purpose</em>. Nothing tells that apart from a box you forgot or a column you did not need, which is why a gap counts as a defect until it is declared.",
              "variant": "neutral"
            },
            {
              "id": "p9-s-close",
              "order": 2,
              "kicker": "CLOSE IT",
              "title": "If you did not mean it",
              "description": [
                "write the missing box, widen",
                "a neighbour, drop a column"
              ],
              "detail": "Three ways to close a gap by changing the data: write the missing box, give a neighbour a wider <code>span</code> so it takes the empty place, or lower the group's <code>columns</code> so the place was never asked for. The third is usually right: a gap is very often a column count the content cannot fill.",
              "variant": "neutral"
            },
            {
              "id": "p9-s-declare",
              "order": 3,
              "kicker": "DECLARE IT",
              "title": "If you did",
              "description": [
                "write a spacer; the taper",
                "of a chart is the one exception"
              ],
              "detail": "Write a <code>spacer</code> where the gap is meant, as the band at the bottom of this page does. One asymmetry needs no spacer: the rows a <code>rowspan</code> box touches are allowed to taper, because a ladder of bars 1·2·3·4 tapers by design and the taper IS the chart. Any other gap is closed or declared.",
              "variant": "warn"
            }
          ]
        },
        {
          "id": "p9-lanes",
          "title": "A shared row needs equal lanes",
          "subtitle": "two lanes written three steps each — then the row both of them meet in",
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
              "detail": "The lane's name is a rail: you write <code>type: rail</code> and a title, and it shows as a slim label for the boxes to its right. It has no kicker, no description and no click; the lane's name is all it carries.",
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
              "detail": "The rail takes one column of the row, so a four-column lane carries three steps. The label is written like any other box and takes a place like one.",
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
              "detail": "With this box the lane reaches its last column and the row closes. The other lane has to be written just as long.",
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
              "detail": "The second rail starts a row of its own because the row above is full, and it lands in the first column, which is what makes this row a lane rather than the end of the one above.",
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
              "detail": "Two lanes in one group must be written the same length: <code>npm run model</code> fails a group where one lane reaches the fourth column and another stops at the third, because a short lane is something you wrote, not something the screen did.",
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
              "detail": "Both lanes are three steps long, so the rows compare directly: step 2 of Build sits above step 2 of Ship. That alignment is a claim, and it is only true because the lengths agree.",
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
              "detail": "The foot of a real timeline: a full-width row both lanes hand off to. Its meaning depends on the equal lengths above it: if Build were four steps and Ship two, this row would sit under two different moments and claim a hand-off that never happens together.",
              "variant": "accent"
            }
          ]
        },
        {
          "id": "p9-vlane",
          "title": "A lane labelled down the rows",
          "subtitle": "a vertical rail with rowspan: 2 — and the four boxes that fill the rows it spans",
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
                "the boxes beside it are",
                "one lane, two rows deep"
              ],
              "detail": "Write <code>treatment: [vertical]</code> and <code>rowspan: 2</code> on a rail and its title turns on its side and runs down two rows, so one label serves a block of boxes instead of a single row.",
              "variant": "neutral"
            },
            {
              "id": "p9-v-r1b",
              "order": 3,
              "kicker": "ROW 1",
              "title": "The first row",
              "description": [
                "two columns wide, beside",
                "the label's one"
              ],
              "detail": "The rail takes the first column in both rows, so each row of the lane is two boxes wide. These are ordinary boxes; the lane is drawn by the label's height alone.",
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
              "detail": "The rows a <code>rowspan</code> touches may taper, but this band closes anyway, which is the honest way to write it: the exception is there for a chart that tapers, not for a gap.",
              "variant": "neutral"
            },
            {
              "id": "p9-v-r2b",
              "order": 5,
              "kicker": "HOLDS",
              "title": "What holds it up",
              "description": [
                "these four boxes fill",
                "the rows the rail spans"
              ],
              "detail": "A rail two rows tall needs two rows of boxes beside it. Write the rail alone and there is nothing to label: the group has one row, and the label reads as a box rather than a lane.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "p9-spacer",
          "title": "The gap you meant, written down",
          "subtitle": "type: spacer — an entry that takes its place and shows nothing, so the rectangle closes without inventing content",
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
              "title": "Two rows, one box",
              "description": [
                "the two empty places above",
                "are written, not forgotten"
              ],
              "detail": "This box is two rows tall, and the two places above its neighbours are <code>type: spacer</code>. Without them the two boxes to the left would start in the top row and the band would end on a ragged floor; with them, every box ends in the bottom row. A spacer buys ALIGNMENT, and alignment makes neighbours comparable.",
              "variant": "accent"
            },
            {
              "id": "p9-sp-is",
              "order": 4,
              "kicker": "WHAT IT IS",
              "title": "A declared gap",
              "description": [
                "it takes its place and",
                "shows nothing at all"
              ],
              "detail": "You write a spacer like a box: an <code>id</code>, <code>type: spacer</code>, an <code>order</code>, and a <code>span</code> or <code>rowspan</code> if it is larger. It shows no frame, no text and no click, but it is counted, so a gap you MEANT is told apart from one you forgot.",
              "variant": "neutral"
            },
            {
              "id": "p9-sp-is-not",
              "order": 5,
              "kicker": "WHAT IT IS NOT",
              "title": "Not an empty card",
              "description": [
                "no title, no colour, no",
                "chip — refused by name"
              ],
              "detail": "A spacer takes <em>only</em> <code>id</code>, <code>type</code>, <code>order</code>, <code>span</code> and <code>rowspan</code>. Anything else is refused by name, because each field presumes something drawn: a title would make it an empty card, a <code>variant</code> would colour a frame that is not there, and a chip would light a box nobody can see.",
              "variant": "warn"
            }
          ]
        }
      ],
      "name": "Data · the empty place",
      "order": 16
    },
    {
      "id": "overview",
      "columns": 2,
      "filters": [
        {
          "key": "all",
          "label": "All"
        },
        {
          "key": "flow",
          "label": "What does a chip light?",
          "steps": [
            "Click it to light every box that lists this chip, and dim the rest.",
            "A box joins by writing the chip's key in its own <code>filters</code>: membership is data you write.",
            "Here it lights Why this page → Item 3 → Item 7."
          ]
        }
      ],
      "sections": [
        {
          "id": "overview-lead",
          "order": 0,
          "span": 2,
          "lead": true,
          "kicker": "DATA · 7 OF 7",
          "title": "Values combine on one box",
          "description": [
            "two treatments on one leaf, a reserved chip, a lone box among groups"
          ],
          "detail": "The last data page: values you write can combine, and the look follows every one of them at once. A leaf can carry a colour and two treatments together, a chip can be the reset with no members, and a box can sit alone beside groups. Each case below is data the tour's other pages did not need."
        },
        {
          "id": "section-a",
          "title": "Values that combine",
          "subtitle": "the data the other pages did not need — and the look each combination gets",
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
                "each data page shows one value",
                "the combinations live here"
              ],
              "detail": "Each data page before this one shows ONE kind of value in its cleanest form. The cases where several values meet on one box or one row, or sit at the edge of what is allowed, would muddy those pages, but they are still data you may write. This page holds them, and the five boxes beside this one are its list.",
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
                "a chip with no members",
                "the reset, not a mistake"
              ],
              "detail": "A chip no box lists is a mistake and <code>npm run model</code> fails it, except <code>all</code>: you write it with no members and it shows as the reset that lights everything again. This page is the only one that writes it.",
              "variant": "neutral"
            },
            {
              "id": "case-two-treatments",
              "order": 3,
              "kicker": "CASE 2",
              "title": "Two treatments",
              "description": [
                "one box, two treatments",
                "and a colour, all at once"
              ],
              "detail": "Section J's boxes write <code>[centered, outside]</code> and <code>[half, centered]</code>: a colour AND two treatments on the SAME box, and the look follows all three. That is why <code>treatment</code> is a list.",
              "variant": "neutral"
            },
            {
              "id": "case-lone-box",
              "order": 4,
              "kicker": "CASE 3",
              "title": "A lone box",
              "description": [
                "a box written beside groups",
                "in one row"
              ],
              "detail": "Section I writes a plain box, <code>Card</code>, beside two groups in one row. It keeps the size of a box instead of stretching to a group's share; the model checks that it never grows.",
              "variant": "neutral"
            },
            {
              "id": "case-vertical-in-row",
              "order": 5,
              "kicker": "CASE 4",
              "title": "Vertical in a row",
              "description": [
                "a rail and a line written",
                "directly between groups"
              ],
              "detail": "Section F writes a <code>vertical</code> rail and a <code>vertical</code> separator directly between two groups. They stay as narrow as their text instead of taking an equal share of the row, and the groups beside them never overlap.",
              "variant": "neutral"
            },
            {
              "id": "case-cascade",
              "order": 6,
              "kicker": "CASE 5",
              "title": "Six columns",
              "description": [
                "columns: 6 folds to 2, then 1",
                "as the screen narrows"
              ],
              "detail": "Section H writes <code>columns: 6</code>. On a narrower screen the six boxes fold to two columns, then to one, and never spill off the page. No other page asks for more than four columns, so this is the only place that case is shown.",
              "variant": "neutral"
            }
          ]
        },
        {
          "id": "section-b",
          "title": "Groups inside a group",
          "subtitle": "a group's children can be groups — each value applies at its own level",
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
                  "detail": "A group written inside another shows as its own frame inside the parent. The <code>variant</code> written on it (here <code>good</code>) tints the whole group.",
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
                  "detail": "This group writes <code>treatment: [plain]</code>, so nothing is drawn around it: no frame, no fill. Groups nest as deep as the story needs.",
                  "variant": "neutral"
                },
                {
                  "id": "item-5",
                  "kicker": "INTERNAL",
                  "title": "Item 5",
                  "description": [
                    "stacks below Item 4 (columns: 1)"
                  ],
                  "detail": "This group writes <code>columns: 1</code>, so its two boxes stack. The <code>muted</code> written on this box gives it a quieter fill.",
                  "variant": "muted"
                }
              ]
            }
          ]
        },
        {
          "id": "section-c",
          "title": "A band with a line and a label",
          "subtitle": "span equal to columns: a full-width band on its own row",
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
              "detail": "Write <code>type: rail</code> and you get a label for the row; write <code>type: separator</code> with a <code>text</code> and you get the dotted line above. Neither carries a detail: they label and divide."
            },
            {
              "id": "item-7",
              "order": 4,
              "kicker": "NEW",
              "title": "Item 7",
              "description": [
                "the last step in the example flow"
              ],
              "detail": "Click the <b>What does a chip light?</b> chip above to light Why this page → Item 3 → Item 7 end to end.",
              "variant": "accent",
              "filters": [
                "flow"
              ]
            }
          ]
        },
        {
          "id": "section-d",
          "title": "Height written as rowspan",
          "subtitle": "rowspan 1, 2, 3 — the value you write is the bar's height",
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
                "height = 1 slot"
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
                "height = 2 slots"
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
                "height = 3 slots"
              ],
              "variant": "accent"
            }
          ]
        },
        {
          "id": "section-e",
          "title": "A wider box, earned",
          "subtitle": "Item C writes span: 2 of 4 — and the six plain boxes around it make the four columns real",
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
                "one box of four"
              ]
            },
            {
              "id": "item-b",
              "order": 2,
              "span": 1,
              "kicker": "UNCHANGED",
              "title": "Item B",
              "description": [
                "one box of four"
              ]
            },
            {
              "id": "item-c",
              "order": 3,
              "span": 2,
              "kicker": "NEW",
              "title": "Item C — span 2",
              "description": [
                "takes exactly 2 of the 4 columns"
              ],
              "detail": "Write <code>span: 2</code> in a four-column group and the box takes exactly two columns, keeping that share as the screen narrows; write <code>span: 4</code> and it becomes a full-width band. It stays two of four only while the group really has four columns, which is why the row below is written.",
              "variant": "accent"
            },
            {
              "id": "item-d",
              "order": 4,
              "span": 1,
              "kicker": "COLUMN 1",
              "title": "Item D",
              "description": [
                "the second row proves",
                "the group has four columns"
              ],
              "detail": "A group keeps only as many columns as its plain boxes can fill. Four plain boxes here keep all four; without this row the group would shrink to two columns and Item C's <code>span: 2</code> would fill the whole row."
            },
            {
              "id": "item-e",
              "order": 5,
              "span": 1,
              "kicker": "COLUMN 2",
              "title": "Item E",
              "description": [
                "a plain box, the unit",
                "the columns are counted in"
              ]
            },
            {
              "id": "item-f",
              "order": 6,
              "span": 1,
              "kicker": "COLUMN 3",
              "title": "Item F",
              "description": [
                "one box of four again"
              ]
            },
            {
              "id": "item-g",
              "order": 7,
              "span": 1,
              "kicker": "COLUMN 4",
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
          "subtitle": "a rail and a vertical line written between two groups",
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
                    "the taller group"
                  ]
                },
                {
                  "id": "l-2",
                  "kicker": "STEP",
                  "title": "Step 2",
                  "description": [
                    "four stacked boxes"
                  ]
                },
                {
                  "id": "l-3",
                  "kicker": "STEP",
                  "title": "Step 3",
                  "description": [
                    "make this block tall"
                  ]
                },
                {
                  "id": "l-4",
                  "kicker": "STEP",
                  "title": "Step 4",
                  "description": [
                    "the taller neighbour"
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
                    "a shorter group"
                  ]
                },
                {
                  "id": "r-2",
                  "kicker": "NOTE",
                  "title": "Note B",
                  "description": [
                    "beside the line"
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "section-g",
          "title": "Short stack",
          "subtitle": "columns: 1 — shorter, so the row stretches it",
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
                    "a stacked group"
                  ]
                },
                {
                  "id": "gg-1-box-2",
                  "kicker": "INTERNAL",
                  "title": "Two item",
                  "description": [
                    "with a second box"
                  ]
                },
                {
                  "id": "gg-1-box-3",
                  "kicker": "INTERNAL",
                  "title": "Three item",
                  "description": [
                    "a third stacked box, the one with the most written in it,",
                    "so Group A carries more text than Group B beside it, and",
                    "each group still keeps its own height"
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
                    "stretched taller than its text"
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "section-h",
          "title": "Six columns written",
          "subtitle": "columns: 6 — six across, then two, then one as the screen narrows",
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
                "box 1 of 6"
              ]
            },
            {
              "id": "h-2",
              "kicker": "STEP",
              "title": "Two",
              "description": [
                "box 2 of 6"
              ]
            },
            {
              "id": "h-3",
              "kicker": "STEP",
              "title": "Three",
              "description": [
                "box 3 of 6"
              ]
            },
            {
              "id": "h-4",
              "kicker": "STEP",
              "title": "Four",
              "description": [
                "box 4 of 6"
              ]
            },
            {
              "id": "h-5",
              "kicker": "STEP",
              "title": "Five",
              "description": [
                "box 5 of 6"
              ]
            },
            {
              "id": "h-6",
              "kicker": "STEP",
              "title": "Six",
              "description": [
                "box 6 of 6"
              ]
            }
          ]
        },
        {
          "id": "section-i",
          "title": "Weights and a lone box",
          "subtitle": "Heavy writes span: 2 and Light span: 1; the Card beside them stays a box",
          "variant": "neutral",
          "order": 9,
          "span": 2,
          "columns": 4,
          "children": [
            {
              "id": "heavy",
              "title": "Heavy",
              "subtitle": "span: 2 — twice as wide",
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
                    "the group with more to say"
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
                    "so it is given the width"
                  ]
                },
                {
                  "id": "heavy-4",
                  "kicker": "NEW",
                  "title": "Delta",
                  "description": [
                    "span: 2 against span: 1"
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
                "a lone box beside groups",
                "keeps the size of a box"
              ],
              "detail": "This box is written directly beside two groups in one row. It does not take a group's share of the width: it keeps the size of a box, and the model fails the page if it ever grows."
            },
            {
              "id": "light",
              "title": "Light",
              "subtitle": "span: 1 — half as wide",
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
                    "the lighter group"
                  ]
                }
              ]
            }
          ]
        },
        {
          "id": "section-j",
          "title": "Treatments that combine",
          "subtitle": "a vertical label, two half-slot pairs, and two treatments on one box",
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
              "detail": "Write <code>treatment: [vertical]</code> and the title turns on its side, reading like a <code>rail</code>. The long-word rule does not apply to it, because a rotated title is limited by the box's height."
            },
            {
              "id": "j-h1",
              "order": 2,
              "kicker": "TOP",
              "title": "Half A",
              "treatment": [
                "half"
              ],
              "detail": "Two boxes that write <code>half</code> share ONE slot: this is the top half. From outside, the pair reads as one full box, so nothing around it moves."
            },
            {
              "id": "j-h2",
              "order": 3,
              "kicker": "BOTTOM",
              "title": "Half B",
              "treatment": [
                "half"
              ],
              "detail": "The bottom half of the same slot. The pair fills the slot completely, so no gap appears."
            },
            {
              "id": "j-center",
              "order": 4,
              "kicker": "NEW",
              "title": "Centered",
              "description": [
                "colour and treatments combine"
              ],
              "variant": "good",
              "treatment": [
                "centered",
                "outside"
              ],
              "detail": "This box writes a colour (<code>good</code>) AND two treatments (<code>centered</code>, <code>outside</code>) at once, and the look follows all three: green, centred text, a dashed frame."
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
              "detail": "A second half pair, this one writing a colour (<code>muted</code>) together with TWO treatments."
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
              "detail": "The bottom half of the second pair. Both halves of a pair must write the same <code>span</code>, since they share one slot."
            }
          ]
        }
      ],
      "name": "Data · values that combine",
      "order": 17
    }
  ]
};
if (typeof document !== 'undefined' && document.documentElement)
  document.documentElement.setAttribute('data-palette', window.__DOC__.palette || 'neutral');
