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
            "Here the flow traces edit → web app → API → ship."
          ]
        }
      ],
      "sections": [
        {
          "id": "intro",
          "title": "Getting started",
          "subtitle": "edit data/pages/overview.yaml, then npm run build",
          "variant": "normal",
          "order": 1,
          "span": 1,
          "columns": 2,
          "children": [
            {
              "id": "edit",
              "order": 1,
              "status": "NEW",
              "title": "Edit the YAML",
              "description": [
                "data/document.yaml — the manifest",
                "data/pages/*.yaml — the content"
              ],
              "detail": "Change titles, add sections and components, then rebuild. Every field is documented in the diagram-builder dialect reference (GLOSSARY.md and reference.md).",
              "variant": "strong",
              "filters": [
                "flow"
              ]
            },
            {
              "id": "build",
              "order": 2,
              "status": "ENTRY",
              "title": "Build & view",
              "description": [
                "npm run build regenerates data.generated.js",
                "open index.html — no server needed"
              ],
              "detail": "The build step reads the manifest + page files and writes data/data.generated.js. Open index.html in any browser; it renders under file:// with zero runtime dependencies.",
              "variant": "normal"
            }
          ]
        },
        {
          "id": "system",
          "title": "Example system",
          "subtitle": "a section can nest other sections — a grid of grids",
          "variant": "envelope",
          "order": 2,
          "span": 1,
          "columns": 1,
          "children": [
            {
              "id": "frontend",
              "title": "Frontend",
              "variant": "safe",
              "columns": 1,
              "children": [
                {
                  "id": "webapp",
                  "status": "INTERNAL",
                  "title": "Web app",
                  "description": [
                    "the user-facing surface"
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
              "id": "backend",
              "title": "Backend",
              "variant": "normal",
              "columns": 1,
              "children": [
                {
                  "id": "api",
                  "status": "INTERNAL",
                  "title": "API",
                  "description": [
                    "handles requests from the web app"
                  ],
                  "detail": "Components auto-flow into the section's columns and wrap down. This backend section is <code>columns: 1</code>, so its two boxes stack.",
                  "variant": "normal",
                  "filters": [
                    "flow"
                  ]
                },
                {
                  "id": "db",
                  "status": "INTERNAL",
                  "title": "Database",
                  "description": [
                    "persistent store"
                  ],
                  "detail": "The <code>store</code> variant gives a data store its own secondary fill.",
                  "variant": "store"
                }
              ]
            }
          ]
        },
        {
          "id": "delivery",
          "title": "Delivery",
          "subtitle": "span == columns makes this a full-width band on its own row",
          "variant": "normal",
          "order": 3,
          "span": 2,
          "columns": 3,
          "children": [
            {
              "id": "sep-pipeline",
              "type": "separator",
              "order": 1,
              "span": 3,
              "style": "dotted",
              "text": "CI/CD pipeline"
            },
            {
              "id": "lane",
              "type": "rail",
              "order": 2,
              "title": "CI/CD"
            },
            {
              "id": "b1",
              "order": 3,
              "status": "UNCHANGED",
              "title": "Build",
              "description": [
                "compile & package"
              ]
            },
            {
              "id": "b2",
              "order": 4,
              "status": "UNCHANGED",
              "title": "Test",
              "description": [
                "run the suite"
              ]
            },
            {
              "id": "b3",
              "order": 5,
              "status": "NEW",
              "title": "Ship",
              "description": [
                "deploy to prod"
              ],
              "detail": "The last step in the example flow — click the <b>Example flow</b> chip above to trace it end to end.",
              "variant": "strong",
              "filters": [
                "flow"
              ]
            }
          ]
        }
      ],
      "name": "Overview",
      "order": 1
    }
  ]
};
