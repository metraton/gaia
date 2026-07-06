// Build step: reads data/document.yaml (manifest) + data/pages/*.yaml,
// resolves visible/order, and emits data/data.generated.js — a plain
// `window.__DOC__ = {...}` assignment, so index.html can load it via a
// normal <script src> with zero runtime fetch/CORS concerns under file://.
//
// Run: npm run build  (or: node engine/build-data.mjs)
// Re-run whenever a YAML file under data/ changes.
import yaml from 'js-yaml';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// This script lives in engine/; the data lives in ../data.
const ROOT = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(ROOT, '..', 'data');

function readYaml(path) {
  return yaml.load(readFileSync(path, 'utf8'));
}

const manifest = readYaml(join(DATA_DIR, 'document.yaml'));
if (!manifest || !Array.isArray(manifest.pages)) {
  throw new Error('document.yaml must have a top-level `pages` list');
}

const pages = manifest.pages
  .filter(p => p.visible !== false)
  .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  .map(entry => {
    if (!entry.file) throw new Error(`page "${entry.id}" is missing "file"`);
    const page = readYaml(join(DATA_DIR, entry.file));
    if (!page || page.id !== entry.id) {
      throw new Error(`manifest id "${entry.id}" does not match page.id "${page && page.id}" in ${entry.file}`);
    }
    // manifest owns name/order/visible; page file owns everything else.
    return { ...page, name: entry.name, order: entry.order };
  });

const doc = {
  title: manifest.title,
  subtitle: manifest.subtitle,
  pages
};

const out = `// GENERATED FILE — do not edit by hand.
// Produced by build-data.mjs from data/document.yaml + data/pages/*.yaml.
window.__DOC__ = ${JSON.stringify(doc, null, 2)};
`;

writeFileSync(join(DATA_DIR, 'data.generated.js'), out, 'utf8');
console.log(`Wrote data/data.generated.js — ${pages.length} visible page(s): ${pages.map(p => p.id).join(', ')}`);
