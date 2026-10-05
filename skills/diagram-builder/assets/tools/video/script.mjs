// Exports the script per page as plain text, one sentence per line: only what
// is said, readable by any voice. The voice step (npm run video:voice) turns
// each text into the audio the script declares for its page.
//
//   npm run video:script
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { OUT_DIR, audioPath, readScript, requireDeck, scriptTextPath } from './deck.mjs';
import { loadTimeline, voicedPages } from './timeline.mjs';

const doc = requireDeck();
const timeline = loadTimeline(doc, readScript());
mkdirSync(join(OUT_DIR, 'script'), { recursive: true });
voicedPages(timeline).forEach((p, i) => {
  const file = scriptTextPath(p, i);
  writeFileSync(file, p.sentences.join('\n') + '\n');
  console.log(`[video] ${file} -> audio expected at ${audioPath(p)}`);
});
