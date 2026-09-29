// Exports the script per page as plain text, one sentence per line: only what
// is said, readable by any voice. The voice step writes each page's audio to
// the path the script declares for it.
//
//   npm run video:script
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { OUT_DIR, audioPath, readScript, requireDeck } from './deck.mjs';
import { loadTimeline } from './timeline.mjs';

const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
const dir = join(OUT_DIR, 'script');
mkdirSync(dir, { recursive: true });
timeline.pages.forEach((p, i) => {
  const file = join(dir, `${String(i + 1).padStart(2, '0')}-${p.page}.txt`);
  writeFileSync(file, p.sentences.join('\n') + '\n');
  console.log(`[video] ${file} -> audio expected at ${audioPath(p)}`);
});
