// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = {
  "title": "Diagram Deck",
  "subtitle": "A portable, data-driven diagram — edit data/ and run npm run build",
  "version": "0.1.0",
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
          "variant": "normal",
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
              "variant": "strong",
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
              "variant": "normal"
            }
          ]
        },
        {
          "id": "section-b",
          "title": "Section B",
          "subtitle": "a section can nest other sections — a grid of grids",
          "variant": "envelope",
          "order": 2,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "group-1",
              "title": "Group 1",
              "variant": "safe",
              "columns": 1,
              "children": [
                {
                  "id": "item-3",
                  "status": "INTERNAL",
                  "title": "Item 3",
                  "description": [
                    "one level of nesting deep"
                  ],
                  "detail": "A nested section is drawn as its own framed zone inside the parent. Its <code>variant</code> (here <code>safe</code>) tints the whole group.",
                  "variant": "ok",
                  "filters": [
                    "flow"
                  ]
                }
              ]
            },
            {
              "id": "group-2",
              "title": "Group 2",
              "variant": "normal",
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
                  "variant": "normal"
                },
                {
                  "id": "item-5",
                  "status": "INTERNAL",
                  "title": "Item 5",
                  "description": [
                    "stacks below Item 4 (columns: 1)"
                  ],
                  "detail": "This group is <code>columns: 1</code>, so its two boxes stack. The <code>store</code> variant gives a box its own secondary fill.",
                  "variant": "store"
                }
              ]
            }
          ]
        },
        {
          "id": "section-c",
          "title": "Section C",
          "subtitle": "span == columns makes this a full-width band on its own row",
          "variant": "normal",
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
              "variant": "strong",
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
          "variant": "normal",
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
              "variant": "normal"
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
              "variant": "ok"
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
              "variant": "strong"
            }
          ]
        },
        {
          "id": "section-e",
          "title": "Section E",
          "subtitle": "a partial merge — Item C spans 2 of 4 columns, not the whole row",
          "variant": "normal",
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
              "variant": "strong"
            }
          ]
        }
      ],
      "name": "Overview",
      "order": 1
    }
  ]
};
