// Prints the plan derived from the deck and its script: each page's slot and
// the second every cue fires. Writes nothing.
//
//   npm run video:plan [-- --pages id,id]
import { readScript, requireDeck } from './deck.mjs';
import { buildPlan, loadTimeline, readAlign, selectedPages } from './timeline.mjs';

const doc = requireDeck();
const script = readScript();
const plan = buildPlan(loadTimeline(doc, script, selectedPages(script)), readAlign());
for (const p of plan.pages) {
  console.log(`${p.page}: ${p.start.toFixed(2)}-${p.end.toFixed(2)} s, timing ${p.method}, ` +
    `shown from the start: ${p.base.join(', ') || 'nothing'}`);
  for (const c of p.cues) {
    const word = c.word === undefined ? '' : ` at "${c.word}"${c.estimated ? ' (estimated)' : ''}`;
    console.log(`  ${c.t.toFixed(2)} s  sentence ${c.s}${word}  ${c.reveal ? 'show ' + c.reveal.join(', ') : c.type !== undefined ? 'type ' + c.type : c.ask !== undefined ? 'ask ' + c.ask : 'chip ' + c.chip}`);
  }
}
console.log(`total ${plan.duration.toFixed(2)} s`);
