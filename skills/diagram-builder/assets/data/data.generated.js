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
            "A component joins a flow by listing the filter key in its own <code>filters</code>."
          ]
        }
      ],
      "sections": [
        {
          "id": "intro",
          "title": "Getting started",
          "subtitle": "edit data/pages/overview.yaml, then run npm run build",
          "variant": "normal",
          "order": 1,
          "layout": {
            "row": 1,
            "span": 2
          },
          "columns": 2,
          "components": [
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
              "variant": "normal",
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
